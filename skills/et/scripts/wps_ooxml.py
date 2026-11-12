"""Bounded OOXML archive and XML helpers for the ET spreadsheet reader.
Read guards ported from docx office/archiveio.py and xmlio.py.
"""
from __future__ import annotations
import posixpath
import re
import stat
import struct
import unicodedata
import zipfile
from xml.etree import ElementTree as ET
from xml.parsers import expat

CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"

MAX_PART_BYTES = 32 * 1024 * 1024
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 4096
MAX_PATH_BYTES = 4096
MAX_PATH_COMPONENTS = 128


def check_archive(zf: zipfile.ZipFile) -> None:
    """Check names, types and declared sizes before decompressing any members."""
    _check_entries(zf.infolist())


def _check_entries(entries) -> None:
    if len(entries) > MAX_MEMBERS:
        raise ValueError("Archive exceeds the 4096 member limit")
    seen = set()
    namespace = {}
    total = 0
    for item in entries:
        name = item.filename
        path = name[:-1] if name.endswith("/") else name
        if (
            name != item.orig_filename or not path or "\\" in path or "\x00" in path or ":" in path
            or path.startswith("/") or any(p in ("", ".", "..") for p in path.split("/"))
        ):
            raise ValueError(f"unsafe archive entry: {name!r}")
        for component in path.split("/"):
            stem = component.split(".", 1)[0].upper()
            if (any(character in component for character in '<>"|?*')
                    or component.endswith((" ", "."))
                    or stem in {"CON", "PRN", "AUX", "NUL"}
                    or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem)):
                raise ValueError(f"Nonportable archive entry: {name!r}")
        if path in seen:
            raise ValueError(f"Duplicate ZIP members: {name!r}")
        seen.add(path)
        segments = path.split("/")
        if len(segments) > MAX_PATH_COMPONENTS or len(path.encode("utf-8")) > MAX_PATH_BYTES:
            raise ValueError(f"ZIP path depth or length limit exceeded: {name!r}")
        children = namespace
        for index, component in enumerate(segments):
            canonical = unicodedata.normalize("NFC", component).casefold()
            node = children.setdefault(canonical, {"spelling": component, "children": {}, "kind": None})
            if node["spelling"] != component:
                raise ValueError(f"Case or Unicode alias in ZIP names: {name!r}")
            is_directory = index < len(segments) - 1 or item.is_dir()
            if is_directory:
                if node["kind"] == "file":
                    raise ValueError(f"File/directory ZIP name conflict: {name!r}")
                node["kind"] = "directory"
            else:
                if node["kind"] == "directory":
                    raise ValueError(f"File/directory ZIP name conflict: {name!r}")
                node["kind"] = "file"
            children = node["children"]
        if stat.S_ISLNK(item.external_attr >> 16):
            raise ValueError(f"symlink archive entry not allowed: {name!r}")
        if item.flag_bits & 1:
            raise ValueError("Encrypted ZIP members are unsupported")
        if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("Only stored and deflated ZIP members are supported")
        if item.is_dir() and item.file_size:
            raise ValueError("ZIP directory entries must not contain data")
        total += item.file_size
        if item.file_size > MAX_PART_BYTES or total > MAX_PACKAGE_BYTES:
            raise ValueError("Archive exceeds part (32 MiB) or total (128 MiB) size limit")


def read_part(zf: zipfile.ZipFile, name: str) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > MAX_PART_BYTES:
        raise ValueError(f"Part exceeds 32 MiB: {name}")
    with zf.open(info) as stream:
        data = stream.read(min(info.file_size + 1, MAX_PART_BYTES + 1))
    if len(data) > MAX_PART_BYTES:
        raise ValueError(f"Part exceeds 32 MiB: {name}")
    if len(data) != info.file_size:
        raise ValueError(f"ZIP member size mismatch: {name}")
    return data


MAX_CENTRAL_BYTES = 4 * 1024 * 1024
MAX_EOCD_BYTES = 22 + 65535
MAX_ARCHIVE_BYTES = MAX_PACKAGE_BYTES + MAX_CENTRAL_BYTES + MAX_EOCD_BYTES
EOCD = struct.Struct("<4s4H2LH")
CENTRAL = struct.Struct("<4s6H3L5H2L")


def _read_exact(stream, length):
    data = stream.read(length)
    if len(data) != length:
        raise ValueError("Truncated ZIP metadata")
    return data


def _preflight(stream, file_size):
    if file_size < EOCD.size or file_size > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP compressed file size exceeds limits or is truncated")
    tail_size = min(file_size, MAX_EOCD_BYTES)
    stream.seek(file_size - tail_size)
    tail = _read_exact(stream, tail_size)
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or position + EOCD.size > len(tail):
        raise ValueError("Missing or ambiguous ZIP end record")
    values = EOCD.unpack_from(tail, position)
    _, disk, central_disk, disk_count, count, size, offset, comment_length = values
    end_offset = file_size - tail_size + position
    if position + EOCD.size + comment_length != tail_size:
        raise ValueError("ZIP trailing bytes or inconsistent comment length are unsupported")
    if disk or central_disk or disk_count != count:
        raise ValueError("Multi-disk or inconsistent ZIP entry counts are unsupported")
    if count == 0xFFFF or size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        raise ValueError("ZIP64 archives are unsupported")
    # zipfile consults this locator even when the legacy EOCD has no sentinel.
    # Do not let embedded metadata replace the bounded directory we inspected.
    if end_offset >= 20:
        stream.seek(end_offset - 20)
        if _read_exact(stream, 4) == b"PK\x06\x07":
            raise ValueError("ZIP64 locator records are unsupported")
    if count > MAX_MEMBERS:
        raise ValueError("ZIP central directory exceeds the member limit")
    if size > MAX_CENTRAL_BYTES:
        raise ValueError("ZIP central directory exceeds the byte limit")
    if offset + size != end_offset:
        raise ValueError("ZIP central directory offset/size mismatch or concatenated prefix")
    stream.seek(offset)
    central = _read_exact(stream, size)
    cursor = actual_count = 0
    first_local = None
    while cursor < size:
        if size - cursor < CENTRAL.size:
            raise ValueError("Truncated ZIP central directory record")
        fields = CENTRAL.unpack_from(central, cursor)
        if fields[0] != b"PK\x01\x02":
            raise ValueError("Invalid ZIP central directory record signature")
        actual_count += 1
        if actual_count > MAX_MEMBERS or actual_count > count:
            raise ValueError("ZIP central directory member count mismatch or limit exceeded")
        needed, compressed, expanded = fields[2], fields[8], fields[9]
        name_length, extra_length, comment_length, member_disk = fields[10:14]
        local_offset = fields[16]
        if needed >= 45 or compressed == 0xFFFFFFFF or expanded == 0xFFFFFFFF or local_offset == 0xFFFFFFFF:
            raise ValueError("ZIP64 or unsupported ZIP extraction versions are unsupported")
        if member_disk:
            raise ValueError("Multi-disk ZIP members are unsupported")
        record_end = cursor + CENTRAL.size + name_length + extra_length + comment_length
        if record_end > size or local_offset + 30 > offset:
            raise ValueError("ZIP central record lengths or local-header offsets are inconsistent")
        extra_cursor = cursor + CENTRAL.size + name_length
        extra_end = extra_cursor + extra_length
        while extra_cursor < extra_end:
            if extra_end - extra_cursor < 4:
                raise ValueError("Truncated ZIP extra-field header")
            identifier, length = struct.unpack_from("<HH", central, extra_cursor)
            extra_cursor += 4
            if identifier == 1:
                raise ValueError("ZIP64 extra fields are unsupported")
            if length > extra_end - extra_cursor:
                raise ValueError("Truncated ZIP extra-field payload")
            extra_cursor += length
        first_local = local_offset if first_local is None else min(first_local, local_offset)
        cursor = record_end
    if actual_count != count:
        raise ValueError("ZIP central directory entry count does not match the end record")
    if not count:
        if offset:
            raise ValueError("Empty ZIP archives cannot have prefixes")
    else:
        if first_local != 0:
            raise ValueError("ZIP prefixes and concatenated archives are unsupported")
        stream.seek(0)
        if _read_exact(stream, 4) != b"PK\x03\x04":
            raise ValueError("Invalid first ZIP local-header signature")
    stream.seek(0)


MAX_XML_BYTES = 16 * 1024 * 1024
MAX_XML_DEPTH = 128
# Bounds sized for one 200000-cell workbook plus its shared strings and style
# tables. They still bound allocation but no longer reject a legal large sheet.
MAX_XML_ELEMENTS = 2_000_000
# Bound allocations not represented by the element count or input byte size.
# Namespace URIs in normal OOXML are short; this ceiling is checked in a
# namespace-unaware pass before Expat can multiply a URI into expanded names.
MAX_XML_NAMESPACE_URI_CHARS = 2048
# Sized for 200000 value cells plus richly formatted shared strings: the expanded
# (URI+local-name) counter is a cumulative allocation policy, not DOM storage.
MAX_XML_EXPANDED_NAME_CHARS = 128 * 1024 * 1024
MAX_XML_ATTRIBUTES_PER_ELEMENT = 1024
MAX_XML_ATTRIBUTES = 2_000_000
MAX_XML_NODES = 8_000_000
ParseError = ValueError

# Diagnostics stay aggregated and bounded. Unknown extension metadata is normal
# in real OOXML packages, so an issue cap must never fail a readable package.
MAX_ISSUE_CODES = 256
MAX_ISSUE_SAMPLES = 8
MAX_ISSUE_VALUE_CHARS = 200
RESERVED_ISSUE_KEYS = frozenset(('code', 'count', 'evaluated', 'samples', 'message'))


def split_tag(tag):
    """Split an ElementTree Clark tag into (namespace, local name)."""
    if not isinstance(tag, str) or not tag.startswith('{'):
        return '', tag if isinstance(tag, str) else ''
    end = tag.find('}')
    if end < 0:
        return '', tag
    return tag[1:end], tag[end + 1:]


def _bounded_issue_value(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else str(value)
    return text[:MAX_ISSUE_VALUE_CHARS]


class IssueLog:
    """Aggregate non-fatal reading degradations by stable code; never raises.

    Each code keeps one entry with a total ``count``, the first occurrence's
    scalar position fields at the entry top level (existing consumers read
    ``issue['cell']`` etc.) and up to ``MAX_ISSUE_SAMPLES`` detail samples.
    """

    def __init__(self):
        self._entries = {}
        self._order = []
        self.truncated = False
        self.suppressed = 0
        # Coverage-relevant state is recorded independently of the bounded
        # diagnostic entries so code-cap/truncation cannot change coverage.
        self.flags = set()

    def mark(self, flag):
        """Record coverage state that must survive diagnostic truncation."""
        self.flags.add(flag)

    def marked(self, flag):
        return flag in self.flags

    def add(self, code, message=None, **detail):
        entry = self._entries.get(code)
        if entry is None:
            if len(self._entries) >= MAX_ISSUE_CODES:
                self.truncated = True
                self.suppressed += 1
                return None
            entry = {'code': code, 'count': 0, 'evaluated': False, 'samples': []}
            if isinstance(message, str) and message:
                entry['message'] = message[:MAX_ISSUE_VALUE_CHARS]
            for key, value in detail.items():
                if key not in RESERVED_ISSUE_KEYS:
                    entry[key] = _bounded_issue_value(value)
            self._entries[code] = entry
            self._order.append(code)
        entry['count'] += 1
        if detail and len(entry['samples']) < MAX_ISSUE_SAMPLES:
            entry['samples'].append({key: _bounded_issue_value(value)
                                     for key, value in detail.items()
                                     if key not in RESERVED_ISSUE_KEYS})
        return entry

    def count(self, code):
        entry = self._entries.get(code)
        return entry['count'] if entry else 0

    def has(self, code):
        return code in self._entries

    def issues(self):
        result = []
        for code in self._order:
            entry = dict(self._entries[code])
            entry['samples'] = [dict(sample) for sample in entry['samples']]
            result.append(entry)
        return result

    def __bool__(self):
        return bool(self._order)

    def __len__(self):
        return len(self._order)


class XmlBudget:
    """Cumulative allocation counters for one package/read/validation operation.

    Use a fresh instance for each independent operation and pass it to all XML
    parts retained by that operation. Each parse consumes budget, including
    repeated parses and failed inputs; this is intentionally fail-closed.
    The same constants also remain enforced independently for each XML part.
    """

    def __init__(self):
        self.elements = 0
        self.attributes = 0
        self.nodes = 0
        self.expanded_name_chars = 0

    def _consume(self, kind, amount, limit):
        value = getattr(self, kind) + amount
        setattr(self, kind, value)
        if value > limit:
            raise ValueError(f"XML shared {kind.replace('_', ' ')} limit exceeded")


def _checked(data, *, budget=None):
    if budget is not None and not isinstance(budget, XmlBudget):
        raise ValueError("budget must be an XmlBudget instance or None")
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, bytes):
        raise ValueError("XML input must be bytes or text")
    if len(data) > MAX_XML_BYTES:
        raise ValueError("XML exceeds the 16 MiB limit")
    if expat.version_info < (2, 6, 0):
        raise ValueError("XML input requires Python with Expat >= 2.6.0")
    parser = expat.ParserCreate()
    depth = elements = attributes = nodes = namespace_chars = 0
    text_open = in_cdata = False

    def reject(*args):
        raise ValueError("DTD and entity declarations are forbidden")

    def add_nodes(count):
        nonlocal nodes
        nodes += count
        if nodes > MAX_XML_NODES:
            raise ValueError("XML node limit exceeded")
        if budget is not None:
            budget._consume("nodes", count, MAX_XML_NODES)

    def start(name, attrs):
        nonlocal depth, elements, attributes, namespace_chars, text_open
        depth += 1
        elements += 1
        if depth > MAX_XML_DEPTH or elements > MAX_XML_ELEMENTS:
            raise ValueError("XML nesting or element limit exceeded")
        attributes += len(attrs)
        if len(attrs) > MAX_XML_ATTRIBUTES_PER_ELEMENT or attributes > MAX_XML_ATTRIBUTES:
            raise ValueError("XML attribute limit exceeded")
        if budget is not None:
            budget._consume("elements", 1, MAX_XML_ELEMENTS)
            budget._consume("attributes", len(attrs), MAX_XML_ATTRIBUTES)
        add_nodes(1 + len(attrs))
        text_open = False
        for key, value in attrs.items():
            if key == "xmlns" or key.startswith("xmlns:"):
                if len(value) > MAX_XML_NAMESPACE_URI_CHARS:
                    raise ValueError("XML namespace URI limit exceeded")
                namespace_chars += len(key) + len(value)
                if namespace_chars > MAX_XML_EXPANDED_NAME_CHARS:
                    raise ValueError("XML expanded name limit exceeded")
                if budget is not None:
                    budget._consume("expanded_name_chars", len(key) + len(value),
                                    MAX_XML_EXPANDED_NAME_CHARS)

    def end(name):
        nonlocal depth, text_open
        depth -= 1
        text_open = False

    def characters(value):
        nonlocal text_open
        # Expat may split one DOM text node into callbacks at entity references
        # or internal buffer boundaries. Count contiguous character data once.
        if value and not in_cdata and not text_open:
            add_nodes(1)
            text_open = True

    def miscellaneous(*args):
        nonlocal text_open
        add_nodes(1)
        text_open = False

    def start_cdata():
        nonlocal in_cdata, text_open
        add_nodes(1)
        in_cdata = True
        text_open = False

    def end_cdata():
        nonlocal in_cdata, text_open
        in_cdata = False
        text_open = False

    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.CommentHandler = miscellaneous
    parser.ProcessingInstructionHandler = miscellaneous
    parser.StartCdataSectionHandler = start_cdata
    parser.EndCdataSectionHandler = end_cdata
    try:
        parser.Parse(data, True)
        # The first pass bounds raw attributes and namespace URI lengths. Only
        # then enable expansion, which otherwise allocates URI * local-name
        # combinations before the application receives a start-element event.
        expanded_chars = namespace_chars

        def expanded_start(name, attrs):
            nonlocal expanded_chars
            amount = len(name) + sum(len(key) for key in attrs)
            expanded_chars += amount
            if expanded_chars > MAX_XML_EXPANDED_NAME_CHARS:
                raise ValueError("XML expanded name limit exceeded")
            if budget is not None:
                budget._consume("expanded_name_chars", amount, MAX_XML_EXPANDED_NAME_CHARS)

        names_parser = expat.ParserCreate(namespace_separator="}")
        names_parser.StartDoctypeDeclHandler = reject
        names_parser.EntityDeclHandler = reject
        names_parser.ExternalEntityRefHandler = reject
        names_parser.StartElementHandler = expanded_start
        names_parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc
    return data


def fromstring(data, *, budget=None):
    try:
        return ET.fromstring(_checked(data, budget=budget))
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc


# --- Tolerant OPC (content types and relationships) helpers -----------------
TRANSITIONAL_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
STRICT_REL = 'http://purl.oclc.org/ooxml/officeDocument/relationships/'
REL_BASES = ((TRANSITIONAL_REL, 'transitional'), (STRICT_REL, 'strict'))
MAX_FOREIGN_WRAPPER_DEPTH = 8
MAX_FORMATS = 4096


def is_security_error(exc):
    """True when the error is a hard XML/parser safety boundary, not data variance."""
    message = str(exc)
    return ('DTD and entity' in message or 'requires Python with Expat' in message)


def iter_main_children(parent, namespaces, max_depth=MAX_FOREIGN_WRAPPER_DEPTH):
    """Yield value-bearing children in ``namespaces``, through foreign wrappers."""
    for child in parent:
        namespace, _local = split_tag(child.tag)
        if namespace in namespaces:
            yield child
        elif max_depth > 0 and len(child):
            yield from iter_main_children(child, namespaces, max_depth - 1)


def relationship_kind(type_uri):
    """Return (kind, conformance) for a known officeDocument relationship URI."""
    if not isinstance(type_uri, str):
        return None, None
    for base, conformance in REL_BASES:
        if type_uri.startswith(base):
            kind = type_uri[len(base):].strip('/')
            return (kind or None), conformance
    return None, None


def rels_path(base):
    if not base:
        return '_rels/.rels'
    return posixpath.join(posixpath.dirname(base), '_rels', posixpath.basename(base) + '.rels')


def _decode_target(base, target):
    """Return (package_path, repaired) or (None, False) for an unusable target."""
    if not isinstance(target, str) or not target:
        return None, False
    raw = target.strip()
    if not raw or any(character in raw for character in ('\\', '\x00', ':', '?', '#')):
        return None, False
    from urllib.parse import unquote
    decoded = unquote(raw)
    repaired = decoded != raw
    if not decoded or any(character in decoded for character in ('\\', '\x00', ':', '?', '#')):
        return None, False
    joined = decoded[1:] if decoded.startswith('/') else posixpath.join(
        posixpath.dirname(base), decoded)
    path = posixpath.normpath(joined)
    if path in ('', '.', '..') or path.startswith('../') or path.startswith('/'):
        return None, False
    return path, repaired


class ContentTypes:
    """Best-effort saved OPC content types, with default-extension lookup."""

    def __init__(self):
        self.overrides = {}
        self.defaults = {}

    def get(self, part):
        if part in self.overrides:
            return self.overrides[part]
        extension = part.rsplit('.', 1)[-1].lower() if '.' in part else ''
        return self.defaults.get(extension)

    def __bool__(self):
        return bool(self.overrides or self.defaults)


def _refuse_macro_content_type(content_type):
    lowered = (content_type or '').lower()
    if 'macroenabled' in lowered or '/vba' in lowered or lowered.endswith('vba'):
        raise ValueError('Macro content type unsupported; workbook values cannot be read safely')


def content_types(package, budget, report=None, reader=None):
    """Read [Content_Types].xml tolerantly; macro content types stay a hard reject."""
    report = IssueLog() if report is None else report
    reader = read_part if reader is None else reader
    result = ContentTypes()
    names = package.namelist()
    if '[Content_Types].xml' not in names:
        report.add('content_types_part_missing',
                   'Package content types are unavailable; body parts are read by structure')
        return result
    try:
        root = fromstring(reader(package, '[Content_Types].xml'), budget=budget)
    except ValueError as exc:
        if is_security_error(exc):
            raise
        report.add('content_types_part_not_interpreted',
                   'Content types were ignored after a bounded read failure',
                   part='[Content_Types].xml', reason=str(exc)[:120])
        return result
    if root.tag != '{' + CONTENT_TYPES + '}Types':
        report.add('content_types_root_not_interpreted', element=str(root.tag)[:120])
        return result
    for item in root:
        namespace, local = split_tag(item.tag)
        if namespace != CONTENT_TYPES or local not in ('Override', 'Default'):
            report.add('content_types_element_not_interpreted',
                       element=str(item.tag)[:120], part='[Content_Types].xml')
            continue
        content_type = item.get('ContentType')
        if local == 'Override':
            part_name = item.get('PartName')
            if not isinstance(part_name, str) or not part_name:
                report.add('content_type_override_not_interpreted', reason='missing PartName')
                continue
            _refuse_macro_content_type(content_type)
            if not isinstance(content_type, str) or not content_type:
                report.add('content_type_override_not_interpreted', part=part_name[:120],
                           reason='missing ContentType')
                continue
            normalized = part_name[1:] if part_name.startswith('/') else part_name
            if not part_name.startswith('/'):
                report.add('content_type_part_name_repaired', part=part_name[:120])
            if normalized in result.overrides:
                report.add('duplicate_content_type_override_ignored', part=normalized[:120])
                continue
            result.overrides[normalized] = content_type
        else:
            extension = item.get('Extension')
            if not isinstance(extension, str) or not extension:
                report.add('content_type_default_not_interpreted', reason='missing Extension')
                continue
            _refuse_macro_content_type(content_type)
            if not isinstance(content_type, str) or not content_type:
                report.add('content_type_default_not_interpreted', extension=extension[:32])
                continue
            result.defaults.setdefault(extension.lower(), content_type)
    return result


def relationships(package, base, budget, report=None, scope='part', sheet=None, reader=None):
    """Read one .rels part tolerantly and classify known officeDocument relations."""
    report = IssueLog() if report is None else report
    reader = read_part if reader is None else reader
    path = rels_path(base)
    if path not in package.namelist():
        return {}
    try:
        root = fromstring(reader(package, path), budget=budget)
    except ValueError as exc:
        if is_security_error(exc):
            raise
        report.add('relationships_part_not_interpreted',
                   'Relationships were ignored after a bounded read failure',
                   part=path, sheet=sheet, reason=str(exc)[:120])
        return {}
    if root.tag != '{' + RELATIONSHIPS + '}Relationships':
        report.add('relationships_root_not_interpreted', part=path, sheet=sheet,
                   element=str(root.tag)[:120])
        return {}
    result = {}
    for index, item in enumerate(root):
        namespace, local = split_tag(item.tag)
        if namespace != RELATIONSHIPS or local != 'Relationship':
            report.add('relationship_element_not_interpreted', part=path, sheet=sheet,
                       element=str(item.tag)[:120])
            continue
        relationship_id = item.get('Id')
        if not isinstance(relationship_id, str) or not relationship_id:
            report.add('relationship_id_missing', part=path, sheet=sheet, index=index)
            relationship_id = '#%d' % index
        if relationship_id in result:
            report.add('duplicate_relationship_id_ignored', part=path, sheet=sheet,
                       relationship_id=relationship_id[:120])
            continue
        type_uri = item.get('Type')
        kind, conformance = relationship_kind(type_uri)
        if kind is None:
            report.add('unknown_relationship_type_ignored', part=path, sheet=sheet,
                       relationship_id=relationship_id[:120],
                       relationship_type=(type_uri or '')[:160])
        target_mode = item.get('TargetMode', 'Internal')
        resolved = target_mode == 'Internal'
        external = not resolved
        target_path = None
        target_raw = None
        if external:
            raw_target = item.get('Target')
            if isinstance(raw_target, str):
                target_raw = raw_target[:32767]
            if target_mode != 'External':
                report.add('relationship_target_mode_not_interpreted', part=path, sheet=sheet,
                           relationship_id=relationship_id[:120], target_mode=str(target_mode)[:60])
            report.add('external_relationship_not_followed',
                       'External relationship target is recorded only as unresolved and not '
                       'followed', part=path, sheet=sheet, relationship_id=relationship_id[:120],
                       relationship_type=(kind or type_uri or '')[:160], target_mode='External',
                       target_resolved=False, followed=False)
        else:
            target_path, repaired = _decode_target(base, item.get('Target'))
            if target_path is None:
                resolved = False
                report.add('relationship_target_unresolved',
                           'Relationship target is empty or escapes the package',
                           part=path, sheet=sheet, relationship_id=relationship_id[:120],
                           relationship_type=(kind or type_uri or '')[:160])
            elif repaired:
                report.add('relationship_target_repaired', part=path, sheet=sheet,
                           relationship_id=relationship_id[:120], target=target_path[:160])
        entry = {
            'id': relationship_id, 'type': type_uri, 'kind': kind,
            'conformance': conformance, 'path': target_path, 'target_mode': target_mode,
            'resolved': resolved, 'external': external,
        }
        if target_raw is not None:
            entry['target_raw'] = target_raw
        result[relationship_id] = entry
    return result
