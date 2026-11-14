"""Bounded standalone OOXML text reader.
Read guards and text traversal ported from docx office/archiveio.py, xmlio.py,
helpers and read_docx.py; only read functions are retained. No adjacent skill imports.
"""
from __future__ import annotations
import io
import re
import stat
import struct
import unicodedata
import zipfile
import zlib
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element
from xml.parsers import expat

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


def preflight_archive(stream, file_size):
    """Validate the ZIP central-directory bounds before constructing ZipFile."""
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
MAX_XML_ELEMENTS = 200_000
# Bound allocations not represented by the element count or input byte size.
# Namespace URIs in normal OOXML are short; this ceiling is checked in a
# namespace-unaware pass before Expat can multiply a URI into expanded names.
MAX_XML_NAMESPACE_URI_CHARS = 2048
MAX_XML_EXPANDED_NAME_CHARS = 32 * 1024 * 1024
MAX_XML_ATTRIBUTES_PER_ELEMENT = 1024
MAX_XML_ATTRIBUTES = 200_000
MAX_XML_NODES = 400_000
ParseError = ValueError


class XmlBudgetExceeded(ValueError):
    """A shared XML parsing limit has been reached."""


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
            raise XmlBudgetExceeded(f"XML shared {kind.replace('_', ' ')} limit exceeded")


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


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
WPS_MAIN_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
}


STRICT_W = 'http://purl.oclc.org/ooxml/wordprocessingml/main'
STRICT_R = 'http://purl.oclc.org/ooxml/officeDocument/relationships'

def _word_part(package, name, budget):
    root = fromstring(read_part(package, name), budget=budget)
    strict = getattr(package, '_wps_strict_word', False)
    expected_w, rejected_w = (STRICT_W, W) if strict else (W, STRICT_W)
    expected_r, rejected_r = (STRICT_R, R) if strict else (R, STRICT_R)
    for node in root.iter():
        for value in (node.tag, *node.attrib):
            if value.startswith('{'+rejected_w+'}') or value.startswith('{'+rejected_r+'}'):
                raise ValueError('unsupported mixed Strict/Transitional Word namespaces')
        relation_type = node.get('Type') if node.tag == '{'+RELATIONSHIPS+'}Relationship' else None
        if relation_type and relation_type.startswith(rejected_r+'/'):
            raise ValueError('unsupported mixed Strict/Transitional relationship types')
        # Normalize only validated expanded names in this in-memory read tree.
        if strict:
            if node.tag.startswith('{'+expected_w+'}'):
                node.tag = '{'+W+'}'+node.tag.split('}',1)[1]
            attributes = {}
            for key, value in node.attrib.items():
                if key.startswith('{'+expected_w+'}'):
                    key='{'+W+'}'+key.split('}',1)[1]
                elif key.startswith('{'+expected_r+'}'):
                    key='{'+R+'}'+key.split('}',1)[1]
                if key in attributes:
                    raise ValueError('Duplicate normalized Word attribute')
                attributes[key]=value
            node.attrib.clear()
            node.attrib.update(attributes)
            if relation_type and relation_type.startswith(expected_r+'/'):
                node.set('Type',R+relation_type[len(expected_r):])
    return root


def _check_wps_package(package, budget):
    """Recognize a narrow OOXML subset, without claiming full format validation."""
    required = {"[Content_Types].xml", "_rels/.rels"}
    if not required.issubset(package.namelist()):
        raise ValueError("unsupported WPS ZIP content: required OOXML document parts are missing")
    types = fromstring(read_part(package, "[Content_Types].xml"), budget=budget)
    if types.tag != f"{{{CONTENT_TYPES}}}Types":
        raise ValueError("unsupported WPS ZIP content: invalid OOXML content-types root")
    relationships = fromstring(read_part(package, "_rels/.rels"), budget=budget)
    if relationships.tag != f"{{{RELATIONSHIPS}}}Relationships":
        raise ValueError("unsupported WPS ZIP content: invalid OOXML relationships root")
    raw_main = [node for node in relationships if node.tag == f"{{{RELATIONSHIPS}}}Relationship"
                and node.get("Type") in (OFFICE_DOCUMENT, STRICT_R+'/officeDocument')]
    if len(raw_main) != 1:
        raise ValueError('unsupported ambiguous Word document dialect')
    package._wps_strict_word = raw_main[0].get('Type') == STRICT_R+'/officeDocument'
    relationships = _word_part(package, '_rels/.rels', budget)
    main = [node for node in relationships if node.tag == f"{{{RELATIONSHIPS}}}Relationship"
            and node.get("Type") == OFFICE_DOCUMENT]
    if (len(main) != 1 or main[0].get("TargetMode", "Internal") != "Internal"
            or not main[0].get("Target")):
        raise ValueError("unsupported WPS ZIP content: ambiguous or unsupported main document relationship")
    main_part = resolve_internal_target(main[0].get("Target"), "")
    declarations = [node.get("ContentType") for node in types
                    if node.tag == f"{{{CONTENT_TYPES}}}Override"
                    and node.get("PartName") == "/" + main_part]
    if len(declarations) != 1 or declarations[0] not in WPS_MAIN_TYPES:
        raise ValueError("unsupported WPS ZIP content: ambiguous or unsupported main content type")
    if main_part not in package.namelist():
        raise ValueError("Missing Word main document part: " + main_part)
    # Check every member's bounded length and CRC, including unparsed binary parts.
    for name in package.namelist():
        read_part(package, name)
    return main_part


_REVISION_TAGS = {
    "accepted": {"del", "moveFrom"},
    "original": {"ins", "moveTo"},
    "all": set(),
}
_BLOCK_WRAPPERS = {"sdt", "sdtContent", "customXml", "ins", "del", "moveFrom", "moveTo", "smartTag"}


def _tag(local: str) -> str:
    return f"{{{W}}}{local}"


def _paragraph_text(p: Element, revisions: str) -> str:
    out: list[str] = []

    def visit(node: Element, blocked: bool = False) -> None:
        # Foreign namespaces and MC branches have no Word text semantics here.
        if not node.tag.startswith("{" + W + "}"):
            return
        name = node.tag.rsplit("}", 1)[-1]
        blocked = blocked or name in _REVISION_TAGS[revisions]
        if blocked or name == "txbxContent":
            return
        if name in ("t", "delText"):
            if node.text:
                out.append(node.text)
            return
        if name == "tab":
            out.append("\t")
            return
        if name in ("br", "cr"):
            out.append("\n")
            return
        if name in ("noBreakHyphen", "softHyphen"):
            # Preserve the stored Unicode character, without page/line layout.
            out.append("\u2011" if name == "noBreakHyphen" else "\u00ad")
            return
        for child in node:
            # Nested paragraphs belong to separate blocks and are handled by block traversal.
            if child.tag == _tag("p"):
                continue
            visit(child, blocked)

    visit(p)
    return "".join(out)


def _blocks(node: Element, revisions: str) -> list[str]:
    result: list[str] = []
    for child in node:
        name = child.tag.rsplit("}", 1)[-1]
        if child.tag == _tag("p"):
            result.append(_paragraph_text(child, revisions))
        elif child.tag == _tag("tbl"):
            result.extend(_table(child, revisions))
        elif child.tag.startswith("{" + W + "}") and name in _BLOCK_WRAPPERS:
            if name not in _REVISION_TAGS[revisions]:
                result.extend(_blocks(child, revisions))
    return result


def _table(tbl: Element, revisions: str) -> list[str]:
    rows: list[str] = []
    for tr in tbl.findall(_tag("tr")):
        cells = []
        for tc in tr.findall(_tag("tc")):
            cells.append("\n".join(_blocks(tc, revisions)))
        rows.append("\t".join(cells))
    return rows


def _paragraph_metadata(node, revisions, styles, style_definitions_unreliable=False):
    result = []
    for child in node:
        if child.tag == _tag('p'):
            issue_count = len(styles.issues)
            properties = child.find(_tag('pPr')) if style_definitions_unreliable else styles.properties(child)
            if len(styles.issues) > issue_count:
                # A partial inheritance chain cannot prove a stored heading.
                # Direct paragraph evidence remains usable without that chain.
                properties = child.find(_tag('pPr'))
            outline = None if properties is None else properties.find(_tag('outlineLvl'))
            value = None if outline is None else outline.get(_tag('val'))
            result.append({'outline_level': int(value) if value is not None and value.isdigit() and 0 <= int(value) <= 9 else None})
        elif child.tag == _tag('tbl'):
            result.extend({'outline_level': 9} for _ in child.findall(_tag('tr')))
        elif child.tag.startswith('{' + W + '}') and child.tag.rsplit('}', 1)[-1] in _BLOCK_WRAPPERS:
            if child.tag.rsplit('}', 1)[-1] not in _REVISION_TAGS[revisions]:
                result.extend(_paragraph_metadata(child, revisions, styles, style_definitions_unreliable))
    return result


def _part_text(root: Element, part: str, revisions: str) -> str:
    if part.endswith("footnotes.xml") or part.endswith("endnotes.xml"):
        kind = "footnote" if part.endswith("footnotes.xml") else "endnote"
        elements = [e for e in root if e.tag == _tag(kind) and e.get(_tag("type")) not in ("separator", "continuationSeparator")]
        chunks = []
        for e in elements:
            blocks = _blocks(e, revisions)
            if blocks:
                chunks.append("\n".join(blocks))
        return "\n".join(chunks)
    if part.endswith("comments.xml"):
        chunks = []
        for e in root.findall(_tag("comment")):
            blocks = _blocks(e, revisions)
            if blocks:
                chunks.append("\n".join(blocks))
        return "\n".join(chunks)
    return "\n".join(_blocks(root, revisions))


R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MAX_RELATED_REFERENCES = 100000
MAX_RELATED_REGIONS = 4096
RELATED_TYPES = {
    'header': ('hdr', 'application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml'),
    'footer': ('ftr', 'application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml'),
    'footnotes': ('footnotes', 'application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml'),
    'endnotes': ('endnotes', 'application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml'),
    'comments': ('comments', 'application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml'),
}


def _visible_elements(root, revisions):
    """Word elements in the requested stored revision view, no foreign wrappers."""
    stack = [root]
    while stack:
        node = stack.pop()
        if not node.tag.startswith('{' + W + '}'):
            continue
        local = node.tag.rsplit('}', 1)[-1]
        if local in _REVISION_TAGS[revisions] or local == 'txbxContent':
            continue
        yield node
        stack.extend(reversed(list(node)))


def resolve_internal_target(target, source_part):
    """Resolve a bounded internal OPC part URI relative to its source part."""
    # Resolve an internal OPC URI without permitting schemes, escapes or aliases.
    if not target or any(c in target for c in ('\\', '%', '?', '#', ':', '\x00')):
        raise ValueError('unsupported related-part target URI')
    components = [] if target.startswith('/') or not source_part else source_part.split('/')[:-1]
    relative = target[1:] if target.startswith('/') else target
    for component in relative.split('/'):
        if component in ('', '.'):
            raise ValueError('unsupported noncanonical related-part target')
        if component == '..':
            if not components:
                raise ValueError('Related target escapes package')
            components.pop()
        else:
            components.append(component)
    if not components:
        raise ValueError('Invalid related-part target')
    return '/'.join(components)


def _related_target(target):
    return resolve_internal_target(target, 'word/document.xml')


def read_bytes(data, revisions="accepted", include_related=False, hidden_text="include", recover_textboxes=False):
    """Read immutable bytes; unknown programming exceptions are not swallowed."""
    limitations = ["no-layout-or-list-number-calculation", "no-field-evaluation", "stored-revision-view-only"]
    limitations.append("textbox-text-returned-without-layout-or-anchor" if recover_textboxes
                       else "no-textboxes-or-drawings")
    result = {"status": "unsupported", "format": "ooxml-word", "text": None,
              "paragraphs": [], "partial": False, "producer_verified": False,
              "diagnostics": [], "scope": "main-plus-related" if include_related else "main-body",
              "revisions": revisions, "limitations": limitations}
    if recover_textboxes and not include_related:
        result["diagnostics"] = [{"stream": None, "offset": None,
                                  "message": "Textbox recovery requires separate related-story output"}]
        return result
    if revisions not in _REVISION_TAGS or expat.version_info < (2, 6, 0):
        result["diagnostics"] = [{"stream": None, "offset": None, "message":
            "unsupported revision view" if revisions not in _REVISION_TAGS else "OOXML requires Expat >= 2.6.0"}]
        return result
    part = "ZIP"
    issues = []

    def record_omissions(root, name):
        from wps_ooxml_omissions import content_omissions
        issues.extend(content_omissions(root, name, _BLOCK_WRAPPERS, revisions))
        unsupported = {"txbxContent", "drawing", "object", "pict", "altChunk", "instrText", "fldSimple", "fldChar", "sym",
                       "rPrChange", "pPrChange", "tblPrChange", "trPrChange", "tcPrChange"}
        if recover_textboxes:
            unsupported.remove("txbxContent")
        found = sorted({node.tag.rsplit("}", 1)[-1] for node in root.iter()
                        if node.tag.startswith("{" + W + "}")
                        and node.tag.rsplit("}", 1)[-1] in unsupported})
        foreign = sorted({node.tag for node in root.iter()
                          if not node.tag.startswith("{" + W + "}")})
        if foreign:
            issues.append({"stream": name, "offset": None, "location": name,
                           "code": "uninterpreted_namespace", "elements": foreign[:32],
                           "distinct_elements": len(foreign),
                           "message": "Foreign namespace or markup-compatibility content omitted; no Choice/Fallback branch selected"})
        if found:
            issues.append({"stream": name, "offset": None, "location": name,
                           "code": "uninterpreted_content", "elements": found,
                           "message": "Uninterpreted content or field instructions; retained text is partial, cached field text is not evaluated"})

    try:
        if not isinstance(data, bytes):
            raise TypeError("read_bytes expects immutable bytes")
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ValueError("ZIP compressed file size limit exceeded")
        source = io.BytesIO(data)
        preflight_archive(source, len(data))
        budget = XmlBudget()
        with zipfile.ZipFile(source) as package:
            check_archive(package)
            part = "[Content_Types].xml / _rels/.rels"
            part = _check_wps_package(package, budget)
            document = _word_part(package, part, budget)
            result["ooxml_conformance"] = "strict" if getattr(package, "_wps_strict_word", False) else "transitional"
            if document.tag != _tag("document") or document.find(_tag("body")) is None:
                raise ValueError("invalid w:document or missing w:body")
            from wps_word_visibility import Visibility
            styles = None
            style_part = None
            from wps_ooxml_related import read_related, relationship_name
            relationship_part = relationship_name(part)
            if relationship_part in package.namelist():
                relationships = _word_part(package, relationship_part, budget)
                relation_ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
                if relationships.tag != '{'+relation_ns+'}Relationships':
                    raise ValueError('Invalid styles relationship root')
                relation_ids = set()
                for relation in relationships:
                    identity = relation.get('Id')
                    if relation.tag != '{'+relation_ns+'}Relationship' or not identity or identity in relation_ids:
                        raise ValueError('Invalid/duplicate styles relationship ID')
                    relation_ids.add(identity)
                style_relations = [x for x in relationships if x.get('Type') == 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles']
                if len(style_relations) > 1:
                    raise ValueError('Duplicate styles relationships')
                if style_relations:
                    relationship = style_relations[0]
                    if relationship.get('TargetMode', 'Internal') != 'Internal':
                        raise ValueError('unsupported external styles relationship')
                    style_part = resolve_internal_target(relationship.get('Target'), part)
                    styles = _word_part(package, style_part, budget)
                    if styles.tag != _tag('styles'):
                        raise ValueError('Invalid styles root')
                    content_types = fromstring(read_part(package, '[Content_Types].xml'), budget=budget)
                    declarations = [x.get('ContentType') for x in content_types if x.get('PartName') == '/' + style_part]
                    if declarations != ['application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml']:
                        raise ValueError('Invalid styles content type')

            visibility = Visibility(hidden_text, styles, revisions, style_part=style_part)
            visibility.apply(document, part, "/document")
            record_omissions(document, part)
            pieces = _blocks(document.find(_tag("body")), revisions)
            from wps_word_styles import WordStyles
            style_resolver = WordStyles(styles)
            result['paragraph_metadata'] = _paragraph_metadata(
                document.find(_tag('body')), revisions, style_resolver, visibility.style_metadata_unknown)
            if visibility.style_metadata_unknown:
                style_resolver.issues.append({'code': 'word_heading_inheritance_unresolved',
                                             'message': 'Ambiguous style definitions; only direct paragraph outline levels retained'})
            result['heading_diagnostics'] = style_resolver.issues
            from wps_ooxml_tables import TableCollector
            table_collector = TableCollector(revisions, _paragraph_text)
            table_collector.collect(document, part, '/document')
            textbox_collector = None
            textbox_table_collector = None
            if recover_textboxes:
                from wps_ooxml_textboxes import TextboxCollector
                textbox_table_collector = TableCollector(revisions, _paragraph_text)
                textbox_collector = TextboxCollector(revisions, _blocks, textbox_table_collector)
                textbox_collector.collect(document, part, '/document')
            if not include_related:
                kinds = sorted({node.tag.rsplit('}', 1)[-1] for node in _visible_elements(document, revisions)
                                if node.tag in {_tag(x) for x in ('headerReference', 'footerReference',
                                                               'footnoteReference', 'endnoteReference', 'commentReference')}})
                if kinds:
                    result['omitted_regions'] = [{'type': kind, 'source': {'part': part}} for kind in kinds]
                    issues.append({'stream': part, 'location': part, 'offset': None,
                                   'code': 'related_content_not_requested', 'elements': kinds,
                                   'message': 'Referenced headers, footers or notes were not requested; use include_related'})
            related = (read_related(package, document, revisions, budget, record_omissions, issues,
                                     table_collector, visibility, textbox_collector, main_part=part)
                       if include_related else None)
            issues.extend(table_collector.issues)
            if textbox_table_collector is not None:
                issues.extend(textbox_table_collector.issues)
            issues.extend(visibility.issues)
            result['visibility'] = {'policy': hidden_text, 'runs': visibility.records, 'purpose': 'stored-content-reading-not-authorization'}
            result['tables'] = table_collector.tables
            result['table_schema'] = 'word_logical_tables_v1'
            if include_related:
                if textbox_collector is not None and textbox_collector.stories:
                    related.extend(textbox_collector.stories)
                    issues.append({'stream': part, 'offset': None,
                                   'location': 'related_stories',
                                   'code': 'textbox_text_recovered_unmapped',
                                   'count': len(textbox_collector.stories),
                                   'message': ('Stored textbox text was returned as separate stories; '
                                               'anchors, layout, branch selection and reading order remain unknown')})
                result['related_stories'] = related
                result['related_scope'] = ('explicit-main-document-references-plus-stored-textboxes'
                                           if recover_textboxes else 'explicit-main-document-references-only')
                result['related_page_mapping'] = 'unknown'
            result.update(status="partial" if issues else "ok", partial=bool(issues),
                          diagnostics=issues, text="\n".join(pieces), paragraphs=pieces)
            return result
    except (ValueError, zipfile.BadZipFile, zlib.error, KeyError) as exc:
        message = str(exc)
        part = getattr(exc, "wps_part", part)
        if "limit" in message or "exceeds" in message:
            result["status"] = "resource_limit"
        elif "Encrypted" in message:
            result["status"] = "encrypted"
        elif "unsupported" in message.lower() or "forbidden" in message.lower():
            result["status"] = "unsupported"
        else:
            result["status"] = "corrupt"
        result["diagnostics"] = [{"stream": part, "offset": None, "location": part, "message": message}]
        return result


def package_kind(data: bytes) -> str:
    """Identify whether an OOXML package represents a word document or spreadsheet."""
    stream = io.BytesIO(data)
    preflight_archive(stream, len(data))
    with zipfile.ZipFile(stream) as package:
        check_archive(package)
        names = set(package.namelist())
        if "word/document.xml" in names:
            return "word"
        if "xl/workbook.xml" in names:
            return "spreadsheet"
        if "[Content_Types].xml" in names:
            ct = read_part(package, "[Content_Types].xml")
            if b"spreadsheetml" in ct or b"application/vnd.openxmlformats-officedocument.spreadsheetml" in ct:
                return "spreadsheet"
            if b"wordprocessingml" in ct:
                return "word"
        raise ValueError("Unsupported OOXML main content type")
