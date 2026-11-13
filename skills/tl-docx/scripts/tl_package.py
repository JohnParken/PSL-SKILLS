"""Bounded OPC access for the python-docx reader and controlled editor."""

from hashlib import sha256
from io import BytesIO
import posixpath
import stat
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn

MAX_INPUT = 64 * 1024 * 1024
MAX_UNCOMPRESSED = 192 * 1024 * 1024
MAX_PART = 32 * 1024 * 1024
MAX_MEMBERS = 4096
TYPES = '[Content_Types].xml'
CT = '{http://schemas.openxmlformats.org/package/2006/content-types}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
OFFICE_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
MAIN_TYPES = {
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml': '.docx',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml': '.dotx',
}


def parse(data):
    """Use the component's XML parser and refuse DTDs in input parts."""
    try:
        root = parse_xml(data)
    except (SyntaxError, ValueError) as exc:
        raise ValueError('Malformed XML part') from exc
    if root.getroottree().docinfo.doctype:
        raise ValueError('DTD is not supported in document parts')
    return root


def safe_member(name):
    """Validate a canonical ZIP part name without extracting it."""
    return bool(name) and '\\' not in name and ':' not in name and not any(
        ord(char) < 32 for char in name
    ) and all(piece not in ('', '.', '..') for piece in name.split('/'))


def rels_member(source):
    """Return the OPC relationships member for a source member."""
    parent, name = posixpath.split(source)
    return posixpath.join(parent, '_rels', name + '.rels')


def internal_target(source, target):
    """Resolve a package URI without permitting path traversal."""
    uri = urlsplit(target)
    if uri.scheme or uri.netloc or uri.query or uri.fragment or not uri.path:
        raise ValueError('Invalid internal document relationship')
    path = unquote(uri.path, errors='strict')
    pieces = [] if path.startswith('/') else source.split('/')[:-1]
    for piece in path.removeprefix('/').split('/'):
        if piece == '..' and pieces:
            pieces.pop()
        elif safe_member(piece):
            pieces.append(piece)
        else:
            raise ValueError('Unsafe internal document relationship')
    return '/'.join(pieces)


class Package:
    """Read a bounded in-memory document without dereferencing external links."""

    def __init__(self, data):
        if len(data) > MAX_INPUT:
            raise ValueError('Document exceeds the input size limit')
        self.data = data
        self.sha256 = sha256(data).hexdigest()
        self._zip = ZipFile(BytesIO(data))
        self._xml = {}
        self._rels = {}
        self.names = self._inspect_members()
        self.types = self._content_types()
        roots = [rel['target'] for rel in self.relations('').values()
                 if rel['type'] == OFFICE_REL + 'officeDocument' and not rel['external']]
        if len(roots) != 1 or self.types.get(roots[0]) not in MAIN_TYPES:
            raise ValueError('Expected one supported DOCX or DOTX main part')
        self.main = roots[0]
        self.extension = MAIN_TYPES[self.types[self.main]]
        main = self.xml(self.main)
        if main.tag != qn('w:document') or main.find(qn('w:body')) is None:
            raise ValueError('The main document has no supported Word body')

    def _inspect_members(self):
        names = set()
        folded = set()
        expanded = 0
        entries = self._zip.infolist()
        if len(entries) > MAX_MEMBERS:
            raise ValueError('Too many package members')
        for info in entries:
            name = info.filename.removesuffix('/')
            expanded += info.file_size
            if (not safe_member(name) or name.casefold() in folded or '\x00' in info.orig_filename
                    or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16)
                    or info.compress_type not in (ZIP_DEFLATED, ZIP_STORED)):
                raise ValueError('Invalid, duplicate, encrypted or unsupported package member')
            if info.file_size > MAX_PART or expanded > MAX_UNCOMPRESSED:
                raise ValueError('Package exceeds the expanded size limit')
            folded.add(name.casefold())
            if not info.is_dir():
                names.add(name)
        if TYPES not in names or '_rels/.rels' not in names:
            raise ValueError('Missing OPC content types or root relationships')
        return names

    def _content_types(self):
        root = self.xml(TYPES)
        if root.tag != CT + 'Types':
            raise ValueError('Invalid OPC content types')
        defaults = {}
        overrides = {}
        for item in root:
            if item.tag == CT + 'Default':
                mapping, key = defaults, item.get('Extension', '').lower()
            elif item.tag == CT + 'Override':
                mapping, key = overrides, unquote(item.get('PartName', '')).removeprefix('/')
            else:
                continue
            value = item.get('ContentType')
            if not key or not value or key in mapping:
                raise ValueError('Invalid or duplicate OPC content type')
            mapping[key] = value
        return {name: overrides.get(name) or defaults.get(name.rsplit('.', 1)[-1].lower(), '') for name in self.names}

    def read(self, name):
        """Read one validated member including its ZIP CRC check."""
        if name not in self.names:
            raise ValueError('Referenced document part is missing')
        return self._zip.read(name)

    def xml(self, name):
        """Return the cached parsed OOXML part."""
        if name not in self._xml:
            self._xml[name] = parse(self.read(name))
        return self._xml[name]

    def relations(self, source):
        """Return relationship records indexed by their source-local ID."""
        if source not in self._rels:
            self._rels[source] = self._read_relations(source)
        return self._rels[source]

    def _read_relations(self, source):
        name = rels_member(source)
        if name not in self.names:
            return {}
        root = self.xml(name)
        if root.tag != REL + 'Relationships':
            raise ValueError('Invalid OPC relationships')
        result = {}
        for item in root.findall(REL + 'Relationship'):
            rid, kind, target = (item.get(field) for field in ('Id', 'Type', 'Target'))
            mode = item.get('TargetMode', 'Internal')
            if not all((rid, kind, target)) or rid in result or mode not in ('Internal', 'External'):
                raise ValueError('Invalid or duplicate OPC relationship')
            result[rid] = {'type': kind, 'target': target if mode == 'External' else internal_target(source, target),
                           'external': mode == 'External'}
        return result

    def related(self, source, rid):
        """Look up one relationship without following it."""
        return self.relations(source).get(rid)

    def document(self):
        """Load the package through python-docx after screening its parts."""
        if self.extension != '.docx':
            raise ValueError('Controlled editing requires a DOCX input')
        if any(name.lower().startswith('_xmlsignatures/') or 'digital-signature' in kind
               for name, kind in self.types.items()):
            raise ValueError('Digitally signed documents cannot be edited')
        for name, kind in self.types.items():
            if name.lower().endswith(('.xml', '.rels')) or kind.endswith(('+xml', '/xml')):
                self.xml(name)
        try:
            return Document(BytesIO(self.data))
        except (BadZipFile, KeyError, SyntaxError, ValueError, RecursionError) as exc:
            raise ValueError('The package could not be opened by python-docx') from exc

    def overlay(self, replacements):
        """Change only explicitly selected part payloads, preserving all others."""
        target = BytesIO()
        with ZipFile(target, 'w', compression=ZIP_DEFLATED) as output:
            for info in self._zip.infolist():
                data = replacements.get(info.filename)
                output.writestr(info.filename, self._zip.read(info) if data is None else data)
            for name in replacements.keys() - self.names:
                if not safe_member(name):
                    raise ValueError('Invalid new package part')
                output.writestr(name, replacements[name])
        return target.getvalue()
