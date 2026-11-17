"""Bounded read-only preflight for original PPTX and nested OPC packages.

No member is extracted or executed. Opaque binary members are preserved as data;
this is structural validation, not malware scanning or Office visual validation.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import stat
import struct
import unicodedata
from urllib.parse import unquote
import zlib
import zipfile

from lxml import etree

from hyperlink_contract import HYPERLINK_REL_TYPE, parse_hyperlink_target
from pptx_opc_validation import canonical_opc_part_path, resolve_internal_opc_target
from tl_guard import ContractError

REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
PML_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main'
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_XML_NODES = 300000
MAX_XML_DEPTH = 100


@dataclass
class PackageBudget:
    """One cumulative budget, shared with every embedded ZIP."""

    expanded: int = 0
    members: int = 0
    xml_nodes: int = 0
    archives: int = 0


class _BudgetTreeBuilder(etree.TreeBuilder):
    """Enforce XML work limits before allocating each element, not afterwards."""

    def __init__(self, budget):
        super().__init__()
        self.budget = budget
        self.depth = 0

    def _count(self):
        self.budget.xml_nodes += 1
        if self.budget.xml_nodes > MAX_XML_NODES or self.depth > MAX_XML_DEPTH:
            raise ContractError('Package XML complexity budget exceeded')

    def start(self, tag, attrs, nsmap=None):
        self.depth += 1
        self._count()
        namespaces = {key or None: value for key, value in nsmap.items()} if nsmap else None
        return super().start(tag, attrs, namespaces)

    def end(self, tag):
        result = super().end(tag)
        self.depth -= 1
        return result

    def comment(self, text):
        self._count()
        return super().comment(text)

    def pi(self, target, text):
        raise ContractError('Package XML processing instructions are forbidden')

    def doctype(self, name, public_id, system_id):
        raise ContractError('Package XML DTD and entity declarations are forbidden')


def _safe_xml(payload: bytes, budget: PackageBudget):
    """Reject DTD/entity/PI and enforce cumulative budgets while parsing."""
    parser = etree.XMLParser(target=_BudgetTreeBuilder(budget), resolve_entities=False,
                             load_dtd=False, no_network=True, huge_tree=False)
    try:
        return etree.fromstring(payload, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ContractError('Invalid package XML') from exc


def safe_part_name(name: str) -> str:
    """Reject traversal before OPC URI normalization can erase its segments."""
    if (not isinstance(name, str) or name.startswith('/')
            or any(part in ('', '.', '..') for part in unquote(name).split('/'))):
        raise ContractError('Unsafe package member path')
    canonical = canonical_opc_part_path(name)
    if canonical is None:
        raise ContractError('Invalid OPC member name')
    return canonical


def _member_name(info: zipfile.ZipInfo) -> str:
    """Check file type and canonical path; safe empty directories are allowed."""
    if info.orig_filename != info.filename:
        raise ContractError('Truncated package member name')
    mode = stat.S_IFMT(info.external_attr >> 16)
    if (info.flag_bits & 1 or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
            or (mode == stat.S_IFDIR and not info.is_dir())
            or (info.is_dir() and (info.file_size != 0 or mode == stat.S_IFREG))):
        raise ContractError('Unsafe, encrypted or non-file package member')
    return safe_part_name(info.filename[:-1] if info.is_dir() else info.filename)


def _xml_content_type(content_type):
    if not isinstance(content_type, str):
        return False
    mime = content_type.split(';', 1)[0].strip().lower()
    return mime in {'application/xml', 'text/xml'} or mime.endswith('+xml')


def is_xml_part(name, payload, content_type=None):
    """Recognize XML by canonical name, MIME or encoded prolog, not raw suffix."""
    if safe_part_name(name).endswith(('.xml', '.rels', '.svg')) or _xml_content_type(content_type):
        return True
    prefix = payload
    for signature in (b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff', b'\xff\xfe', b'\xfe\xff', b'\xef\xbb\xbf'):
        if prefix.startswith(signature):
            prefix = prefix[len(signature):]
            break
    # XML leading whitespace uses only ASCII codepoints; stripping NUL bytes
    # too recognizes UTF-16/32 without decoding an entire opaque binary member.
    return prefix.lstrip(b' \t\r\n\x00').startswith(b'<')


def _content_types(entries, budget):
    """Resolve declared XML parts even when their filename extension is opaque."""
    defaults, overrides = {}, {}
    content = next((data for name, data in entries.items() if safe_part_name(name) == '[content_types].xml'), None)
    if content is None:
        return defaults, overrides
    root = _safe_xml(content, budget)
    if root.tag != f'{{{CT_NS}}}Types':
        raise ContractError('Invalid OPC content-types root')
    for row in root:
        mime = row.get('ContentType')
        if not mime:
            raise ContractError('Missing OPC content type')
        if row.tag == f'{{{CT_NS}}}Default':
            extension = row.get('Extension', '').lower()
            if not extension or '/' in extension or extension in defaults:
                raise ContractError('Invalid or duplicate default content type')
            defaults[extension] = mime
        elif row.tag == f'{{{CT_NS}}}Override':
            part = row.get('PartName', '')
            if not part.startswith('/'):
                raise ContractError('Invalid content-type override name')
            key = safe_part_name(part[1:])
            if key in overrides:
                raise ContractError('Duplicate OPC-equivalent content-type override')
            overrides[key] = mime
        else:
            raise ContractError('Invalid content-type declaration')
    return defaults, overrides


def _relationships(name, root, names):
    """Verify graph edges, preserving passive links without fetching them."""
    name = safe_part_name(name)
    if name != '_rels/.rels':
        parent, _, filename = name.rpartition('/')
        if parent.rsplit('/', 1)[-1] != '_rels' or not filename.endswith('.rels'):
            raise ContractError('Invalid relationship part location')
        owner = parent.removesuffix('_rels') + filename.removesuffix('.rels')
        if owner not in names:
            raise ContractError('Dangling package relationship source')
    if root.tag != f'{{{REL_NS}}}Relationships':
        raise ContractError('Invalid OPC relationships root')
    seen = set()
    for relationship in root:
        ident = relationship.get('Id')
        target = relationship.get('Target', '')
        mode = relationship.get('TargetMode', 'Internal')
        if (relationship.tag != f'{{{REL_NS}}}Relationship' or not ident or ident in seen
                or not target or not relationship.get('Type') or mode not in {'Internal', 'External'}):
            raise ContractError('Invalid or duplicate package relationship')
        seen.add(ident)
        if mode == 'External':
            if (relationship.get('Type') != HYPERLINK_REL_TYPE
                    or parse_hyperlink_target(target).kind != 'external'):
                raise ContractError('External resource relationship requires explicit local materialization')
        elif resolve_internal_opc_target(name, target) not in names:
            raise ContractError('Dangling or unsafe package relationship')


def _preflight_directory(payload, remaining_members):
    """Count central records before ZipFile allocates one object per record.

    ZIP/ZIP64 fixed records are decoded directly, with no decompression or
    extraction. Leading self-extracting bytes are handled by the same central
    directory offset adjustment used by standard ZIP readers.
    """
    footer = payload.rfind(b'PK\x05\x06', max(0, len(payload) - 65557))
    if footer < 0 or footer + 22 > len(payload):
        raise ContractError('Missing ZIP central directory footer')
    fields = struct.unpack_from('<4s4H2LH', payload, footer)
    _, disk, central_disk, disk_count, count, central_size, central_offset, comment_size = fields
    if disk or central_disk or disk_count != count or footer + 22 + comment_size != len(payload):
        raise ContractError('Invalid or multi-disk ZIP footer')
    central_end = footer
    locator = footer - 20
    if locator >= 0 and payload[locator:locator + 4] == b'PK\x06\x07':
        _, zip64_disk, declared_zip64_offset, disks = struct.unpack_from('<4sLQL', payload, locator)
        zip64_start = payload.rfind(b'PK\x06\x06', 0, locator)
        if zip64_start < 0 or zip64_start + 56 > locator or zip64_disk or disks != 1:
            raise ContractError('Invalid ZIP64 directory locator')
        record = struct.unpack_from('<4sQ2H2L4Q', payload, zip64_start)
        _, record_size, _made, _needed, disk, central_disk, disk_count, count, central_size, central_offset = record
        if (record_size < 44 or zip64_start + 12 + record_size != locator
                or disk or central_disk or disk_count != count
                or declared_zip64_offset > zip64_start):
            raise ContractError('Invalid ZIP64 directory record')
        central_end = zip64_start
    elif count == 65535 or central_size == 0xffffffff or central_offset == 0xffffffff:
        raise ContractError('Missing required ZIP64 directory record')
    if count > remaining_members:
        raise ContractError('Package member budget exceeded before directory allocation')
    position = central_end - central_size
    if position < 0 or central_offset > position:
        raise ContractError('Invalid ZIP central directory offset')
    observed = 0
    while position < central_end:
        if position + 46 > central_end or payload[position:position + 4] != b'PK\x01\x02':
            raise ContractError('Invalid ZIP central directory entry')
        name_size, extra_size, entry_comment_size = struct.unpack_from('<3H', payload, position + 28)
        position += 46 + name_size + extra_size + entry_comment_size
        observed += 1
        if observed > remaining_members:
            raise ContractError('Package member budget exceeded before directory allocation')
        if position > central_end:
            raise ContractError('Truncated ZIP central directory entry')
    if observed != count:
        raise ContractError('ZIP central directory member count mismatch')


def inspect_package(payload: bytes, *, budget=None, depth=0, require_presentation=True):
    """Read a bounded package into memory and return structural evidence.

    ZIP metadata and actual decompressed length are both checked. Nested ZIPs
    share the parent budget and are never unpacked onto disk. External images,
    linked workbooks and other fetchable resources stop preflight rather than
    being silently removed; supported user-authored hyperlinks remain passive.
    """
    if len(payload) > MAX_ARCHIVE_BYTES or depth > 3:
        raise ContractError('Archive size or nesting budget exceeded')
    budget = PackageBudget() if budget is None else budget
    budget.archives += 1
    entries = {}
    roots = {}
    names = set()
    directories = set()
    filesystem_names = set()
    filesystem_files = set()
    try:
        _preflight_directory(payload, MAX_MEMBERS - budget.members)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            budget.members += len(infos)
            if budget.members > MAX_MEMBERS:
                raise ContractError('Package member budget exceeded')
            for info in infos:
                key = _member_name(info)
                filesystem_key = unicodedata.normalize('NFC', key).casefold()
                if key in names or key in directories or filesystem_key in filesystem_names:
                    raise ContractError('Duplicate or OPC-equivalent package member')
                filesystem_names.add(filesystem_key)
                if info.is_dir():
                    directories.add(key)
                    continue
                names.add(key)
                filesystem_files.add(filesystem_key)
                if info.file_size > MAX_MEMBER_BYTES or info.file_size < 0:
                    raise ContractError('Package member size budget exceeded')
                budget.expanded += info.file_size
                if budget.expanded > MAX_EXPANDED_BYTES:
                    raise ContractError('Cumulative decompressed package budget exceeded')
                with archive.open(info) as stream:
                    content = stream.read(MAX_MEMBER_BYTES + 1)
                if len(content) != info.file_size or len(content) > MAX_MEMBER_BYTES:
                    raise ContractError('Package member length mismatch')
                entries[info.filename] = content
                if zipfile.is_zipfile(io.BytesIO(content)):
                    inspect_package(content, budget=budget, depth=depth + 1, require_presentation=False)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, OSError, UnicodeError, struct.error, zlib.error) as exc:
        raise ContractError('Invalid or unsupported ZIP container') from exc
    for name in filesystem_names:
        components = name.split('/')
        if any('/'.join(components[:index]) in filesystem_files for index in range(1, len(components))):
            raise ContractError('Package file/directory path conflict')
    if require_presentation and ('[content_types].xml' not in names or '_rels/.rels' not in names):
        raise ContractError('Missing OPC root parts')
    if require_presentation and 'ppt/presentation.xml' not in names:
        raise ContractError('Missing presentation part')
    defaults, overrides = _content_types(entries, budget)
    if set(overrides) - names:
        raise ContractError('Dangling content-type override')
    for name, content in entries.items():
        key = safe_part_name(name)
        content_type = overrides.get(key, defaults.get(key.rsplit('.', 1)[-1]))
        if is_xml_part(name, content, content_type):
            roots[key] = _safe_xml(content, budget)
    for name, root in roots.items():
        if name.endswith('.rels'):
            _relationships(name, root, names)
    presentation = roots.get('ppt/presentation.xml')
    slide_count = None
    if presentation is not None:
        if presentation.tag != f'{{{PML_NS}}}presentation':
            raise ContractError('Invalid presentation XML root')
        slide_count = len(presentation.findall(f'{{{PML_NS}}}sldIdLst/{{{PML_NS}}}sldId'))
    return {'entries': entries, 'part_count': len(entries), 'slide_count': slide_count,
            'expanded_bytes': budget.expanded, 'nested_archives': budget.archives - 1,
            'xml_nodes': budget.xml_nodes, 'structural_validation': 'passed',
            'visual_validation': 'not_run', 'malware_scan': 'not_run'}
