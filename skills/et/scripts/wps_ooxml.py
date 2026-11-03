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
                if key.startswith('{'+expected_w+'}'): key='{'+W+'}'+key.split('}',1)[1]
                elif key.startswith('{'+expected_r+'}'): key='{'+R+'}'+key.split('}',1)[1]
                if key in attributes: raise ValueError('Duplicate normalized Word attribute')
                attributes[key]=value
            node.attrib.clear(); node.attrib.update(attributes)
            if relation_type and relation_type.startswith(expected_r+'/'):
                node.set('Type',R+relation_type[len(expected_r):])
    return root


def _check_wps_package(package, budget):
    """Recognize a narrow OOXML subset, without claiming full format validation."""
    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    if not required.issubset(package.namelist()):
        raise ValueError("unsupported WPS ZIP content: required OOXML document parts are missing")
    types = fromstring(read_part(package, "[Content_Types].xml"), budget=budget)
    if types.tag != f"{{{CONTENT_TYPES}}}Types":
        raise ValueError("unsupported WPS ZIP content: invalid OOXML content-types root")
    declarations = [node.get("ContentType") for node in types
                    if node.tag == f"{{{CONTENT_TYPES}}}Override"
                    and node.get("PartName") == "/word/document.xml"]
    if len(declarations) != 1 or declarations[0] not in WPS_MAIN_TYPES:
        raise ValueError("unsupported WPS ZIP content: ambiguous or unsupported main content type")
    relationships = fromstring(read_part(package, "_rels/.rels"), budget=budget)
    if relationships.tag != f"{{{RELATIONSHIPS}}}Relationships":
        raise ValueError("unsupported WPS ZIP content: invalid OOXML relationships root")
    raw_main = [node for node in relationships if node.tag == f"{{{RELATIONSHIPS}}}Relationship"
                and node.get("Type") in (OFFICE_DOCUMENT, STRICT_R+'/officeDocument')]
    if len(raw_main) != 1: raise ValueError('unsupported ambiguous Word document dialect')
    package._wps_strict_word = raw_main[0].get('Type') == STRICT_R+'/officeDocument'
    relationships = _word_part(package, '_rels/.rels', budget)
    main = [node for node in relationships if node.tag == f"{{{RELATIONSHIPS}}}Relationship"
            and node.get("Type") == OFFICE_DOCUMENT]
    if (len(main) != 1 or main[0].get("TargetMode", "Internal") != "Internal"
            or main[0].get("Target") not in ("word/document.xml", "/word/document.xml")):
        raise ValueError("unsupported WPS ZIP content: ambiguous or unsupported main document relationship")
    # Check every member's bounded length and CRC, including unparsed binary parts.
    for name in package.namelist():
        read_part(package, name)


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


def _related_target(target):
    # Resolve an internal OPC URI without permitting schemes, escapes or aliases.
    if not target or any(c in target for c in ('\\', '%', '?', '#', ':', '\x00')):
        raise ValueError('unsupported related-part target URI')
    components = [] if target.startswith('/') else ['word']
    for component in target.lstrip('/').split('/'):
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


def _read_related(package, document, revisions, budget, record_omissions, issues, table_collector=None, visibility=None):
    """Follow main-part relationships and explicit IDs; never enumerate filenames."""
    rel_name = 'word/_rels/document.xml.rels'
    relationships = {}
    if rel_name in package.namelist():
        root = _word_part(package, rel_name, budget)
        if root.tag != '{' + RELATIONSHIPS + '}Relationships':
            raise ValueError('Invalid document relationships root')
        for item in root:
            if item.tag != '{' + RELATIONSHIPS + '}Relationship':
                raise ValueError('Invalid document relationship element')
            identity = item.get('Id')
            if not identity or identity in relationships:
                raise ValueError('Missing/duplicate document relationship ID')
            relationships[identity] = item
    types = fromstring(read_part(package, '[Content_Types].xml'), budget=budget)
    declarations = {}
    for item in types:
        if item.tag == '{' + CONTENT_TYPES + '}Override':
            name = item.get('PartName')
            if name in declarations:
                raise ValueError('Duplicate part content-type declaration')
            declarations[name] = item.get('ContentType')
    header_owners = {}
    for section_index, section in enumerate(document.iter(_tag('sectPr'))):
        seen = set()
        for child in section:
            if child.tag not in (_tag('headerReference'), _tag('footerReference')):
                continue
            signature = (child.tag, child.get(_tag('type')))
            if signature in seen:
                raise ValueError('Duplicate header/footer type within section')
            seen.add(signature)
            header_owners[id(child)] = section_index
    requests = {}
    note_requests = {}
    reference_count = 0
    for index, node in enumerate(_visible_elements(document, revisions)):
        local = node.tag.rsplit('}', 1)[-1]
        if local not in ('headerReference', 'footerReference', 'footnoteReference', 'endnoteReference', 'commentReference'):
            continue
        reference_count += 1
        if reference_count > MAX_RELATED_REFERENCES:
            raise ValueError('Related reference count limit exceeded')
        reference = {'source_part': 'word/document.xml', 'element_index': index, 'kind': local,
                     'page': None, 'cp': None}
        if local in ('headerReference', 'footerReference'):
            if id(node) not in header_owners:
                raise ValueError('Header/footer reference outside section properties')
            reference['section_index'] = header_owners[id(node)]
            kind = 'header' if local == 'headerReference' else 'footer'
            identity = node.get('{' + R + '}id')
            variant = node.get(_tag('type'))
            if variant not in ('default', 'first', 'even'):
                raise ValueError('Invalid header/footer reference type')
            reference.update(relationship_id=identity, variant=variant)
            key = (kind, identity)
            requests.setdefault(key, []).append(reference)
        else:
            kind = {'footnoteReference': 'footnotes', 'endnoteReference': 'endnotes', 'commentReference': 'comments'}[local]
            value = node.get(_tag('id'))
            if value is None or not re.fullmatch(r'[0-9]{1,10}', value) or int(value) > 2147483647:
                raise ValueError('Invalid related text reference ID')
            identity = str(int(value))
            reference['id'] = identity
            note_requests.setdefault(kind, {}).setdefault(identity, []).append(reference)
    for kind in note_requests:
        matches = [(identity, item) for identity, item in relationships.items() if item.get('Type') == R + '/' + kind]
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous ' + kind + ' relationship')
        requests[(kind, matches[0][0])] = []
    stories = []
    loaded_parts = set()
    for (kind, identity), references in requests.items():
        relationship = relationships.get(identity)
        if relationship is None or relationship.get('Type') != R + '/' + kind:
            raise ValueError('Missing/wrong related-part relationship: ' + str(identity))
        if relationship.get('TargetMode', 'Internal') != 'Internal':
            raise ValueError('unsupported external related-part relationship')
        part = _related_target(relationship.get('Target'))
        if part not in package.namelist():
            raise ValueError('Missing related part: ' + part)
        if (kind, part) in loaded_parts:
            raise ValueError('unsupported aliases to related part: ' + part)
        loaded_parts.add((kind, part))
        expected_root, expected_type = RELATED_TYPES[kind]
        if declarations.get('/' + part) != expected_type:
            raise ValueError('Missing/wrong related-part content type: ' + part)
        root = _word_part(package, part, budget)
        if root.tag != _tag(expected_root):
            raise ValueError('Wrong related-part XML root: ' + part)
        entries = [(None, root, references)]
        if kind in note_requests:
            singular = {'footnotes': 'footnote', 'endnotes': 'endnote', 'comments': 'comment'}[kind]
            definitions = {}
            for child in root:
                if child.tag != _tag(singular):
                    raise ValueError('Unexpected related text entry: ' + part)
                value = child.get(_tag('id'))
                if value is None or not re.fullmatch(r'-?[0-9]{1,10}', value) or not -2147483648 <= int(value) <= 2147483647:
                    raise ValueError('Invalid related text definition ID: ' + part)
                key = str(int(value))
                if key in definitions:
                    raise ValueError('Duplicate related text definition ID: ' + part)
                if len(definitions) >= MAX_RELATED_REGIONS:
                    raise ValueError('Related definition count limit exceeded')
                definitions[key] = child
            entries = []
            for key, refs in note_requests[kind].items():
                if key not in definitions or definitions[key].get(_tag('type'), 'normal') != 'normal':
                    raise ValueError('Missing/non-normal related text definition: ' + part + ' id=' + key)
                entries.append((key, definitions[key], refs))
        for key, entry, refs in entries:
            if len(stories) >= MAX_RELATED_REGIONS:
                raise ValueError('Related region count limit exceeded')
            first_issue = len(issues)
            visibility_issue_start = len(visibility.issues) if visibility is not None else 0
            if visibility is not None: visibility.apply(entry, part, "/" + kind + "[" + str(key) + "]")
            record_omissions(entry, part)
            nested = sorted({node.tag.rsplit('}', 1)[-1] for node in _visible_elements(entry, revisions)
                             if node.tag in (_tag('footnoteReference'), _tag('endnoteReference'),
                                             _tag('commentReference'), _tag('headerReference'), _tag('footerReference'))})
            if nested:
                issues.append({'stream': part, 'offset': None, 'code': 'related_nested_reference_not_followed',
                               'elements': nested, 'message': 'Only explicit main-document references are followed; nested references not resolved'})
            story_issues = issues[first_issue:]
            if visibility is not None:
                story_issues += visibility.issues[visibility_issue_start:]
            paragraphs = _blocks(entry, revisions)
            if table_collector is not None:
                table_issue_start = len(table_collector.issues)
                table_collector.collect(entry, part, '/' + kind + '[' + str(key) + ']')
                story_issues += table_collector.issues[table_issue_start:]
            stories.append({'type': kind, 'source': {'part': part, 'id': key, 'relationship_id': identity},
                            'text': '\n'.join(paragraphs), 'paragraphs': paragraphs,
                            'references': refs, 'anchor_status': 'relationship_or_id_verified',
                            'page_mapping': 'unknown', 'cp_range': None,
                            'status': 'partial' if story_issues else 'ok', 'issues': story_issues})
    return stories


def read_bytes(data, revisions="accepted", include_related=False, hidden_text="include"):
    """Read immutable bytes; unknown programming exceptions are not swallowed."""
    result = {"status": "unsupported", "format": "ooxml-word", "text": None,
              "paragraphs": [], "partial": False, "producer_verified": False,
              "diagnostics": [], "scope": "main-plus-related" if include_related else "main-body",
              "revisions": revisions,
              "limitations": ["no-layout-or-list-number-calculation", "no-textboxes-or-drawings",
                              "no-field-evaluation", "stored-revision-view-only"]}
    if revisions not in _REVISION_TAGS or expat.version_info < (2, 6, 0):
        result["diagnostics"] = [{"stream": None, "offset": None, "message":
            "unsupported revision view" if revisions not in _REVISION_TAGS else "OOXML requires Expat >= 2.6.0"}]
        return result
    part = "ZIP"
    issues = []

    def record_omissions(root, name):
        unsupported = {"txbxContent", "drawing", "object", "pict", "altChunk", "instrText", "fldSimple", "fldChar", "sym", "noBreakHyphen", "softHyphen", "rPrChange", "pPrChange", "tblPrChange", "trPrChange", "tcPrChange"}
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
        _preflight(source, len(data))
        budget = XmlBudget()
        with zipfile.ZipFile(source) as package:
            check_archive(package)
            part = "[Content_Types].xml / _rels/.rels"
            _check_wps_package(package, budget)
            part = "word/document.xml"
            document = _word_part(package, part, budget)
            result["ooxml_conformance"] = "strict" if package._wps_strict_word else "transitional"
            if document.tag != _tag("document") or document.find(_tag("body")) is None:
                raise ValueError("invalid w:document or missing w:body")
            from wps_word_visibility import Visibility
            styles = None
            relationship_part = 'word/_rels/document.xml.rels'
            if relationship_part in package.namelist():
                relationships = _word_part(package, relationship_part, budget)
                relation_ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
                if relationships.tag != '{'+relation_ns+'}Relationships': raise ValueError('Invalid styles relationship root')
                relation_ids = set()
                for relation in relationships:
                    identity = relation.get('Id')
                    if relation.tag != '{'+relation_ns+'}Relationship' or not identity or identity in relation_ids: raise ValueError('Invalid/duplicate styles relationship ID')
                    relation_ids.add(identity)
                style_relations = [x for x in relationships if x.get('Type') == 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles']
                if len(style_relations) > 1: raise ValueError('Duplicate styles relationships')
                if style_relations:
                    relationship = style_relations[0]
                    if relationship.get('TargetMode', 'Internal') != 'Internal': raise ValueError('unsupported external styles relationship')
                    style_part = _related_target(relationship.get('Target'))
                    styles = _word_part(package, style_part, budget)
                    if styles.tag != _tag('styles'): raise ValueError('Invalid styles root')
                    content_types = fromstring(read_part(package, '[Content_Types].xml'), budget=budget)
                    declarations = [x.get('ContentType') for x in content_types if x.get('PartName') == '/' + style_part]
                    if declarations != ['application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml']: raise ValueError('Invalid styles content type')

            visibility = Visibility(hidden_text, styles, revisions)
            visibility.apply(document, part, "/document")
            record_omissions(document, part)
            pieces = _blocks(document.find(_tag("body")), revisions)
            from wps_ooxml_tables import TableCollector
            table_collector = TableCollector(revisions, _paragraph_text)
            table_collector.collect(document, part, '/document')
            related = _read_related(package, document, revisions, budget, record_omissions, issues, table_collector, visibility) if include_related else None
            issues.extend(table_collector.issues)
            issues.extend(visibility.issues)
            result['visibility'] = {'policy': hidden_text, 'runs': visibility.records, 'purpose': 'stored-content-reading-not-authorization'}
            result['tables'] = table_collector.tables
            result['table_schema'] = 'word_logical_tables_v1'
            if include_related:
                result['related_stories'] = related
                result['related_scope'] = 'explicit-main-document-references-only'
                result['related_page_mapping'] = 'unknown'
            result.update(status="partial" if issues else "ok", partial=bool(issues),
                          diagnostics=issues, text="\n".join(pieces), paragraphs=pieces)
            return result
    except (ValueError, zipfile.BadZipFile, zlib.error, KeyError) as exc:
        message = str(exc)
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
