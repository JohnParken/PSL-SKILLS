"""Bounded ZIP/XML storage with explicit resource roles and shared budgets."""
import hashlib
import json
import posixpath
import stat
import struct
import zipfile
from xml.parsers import expat
from xml.etree import ElementTree as ET

from ofd_common import (
    LIMITS, ReadError, is_ofd, local, ofd_nodes, q, resolve, safe_name, text_of, zlib,
)


class Package:
    def __init__(self, data, *, retain_structure=True):
        import io
        self.retain_structure = retain_structure
        self.data = data
        self.archive = zipfile.ZipFile(io.BytesIO(data))
        infos = self.archive.infolist()
        if len(infos) > LIMITS['entries']:
            raise ReadError('limit: entries')
        self.members = {}
        total = 0
        for info in infos:
            if info.orig_filename != info.filename:
                raise ReadError('unsafe original ZIP member name')
            name = safe_name(info.filename)
            if name in self.members:
                raise ReadError('duplicate ZIP member')
            self.members[name] = info
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ReadError('special ZIP member')
            if info.flag_bits & 1:
                raise ReadError('unsupported: encrypted ZIP')
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ReadError('unsupported: ZIP compression')
            if info.compress_type == zipfile.ZIP_STORED and info.compress_size != info.file_size:
                raise ReadError('inconsistent stored ZIP member size')
            total += info.file_size
            if (info.file_size > LIMITS['member_bytes'] or total > LIMITS['total_bytes']
                    or info.file_size > max(1, info.compress_size)*LIMITS['ratio']):
                raise ReadError('limit: ZIP expansion')
        if 'OFD.xml' not in self.members or sum(posixpath.basename(name) == 'OFD.xml' for name in self.members) != 1:
            raise ReadError('exactly one root OFD.xml required')
        self.namespace_issues = {}
        self.cache = {}
        self.xml_total = self.nodes = self.chars = self.references = 0
        self.evidence = []
        self.expanded_nodes = 0
        self.output_cost = 0
        self.serialized_cost = 0
        self.xml_events = {}
        self.event_count = 0
        self.name_chars = 0
        self.xml_digests = {}
        self.opaque_members = {}
        self.node_indexes = {}
        self.expected_roots = {}
        self.payload_classified = set()
        self.xml_modes = {}

    def reference(self, source, value, *, missing_ok=False):
        self.references += 1
        if self.references > LIMITS['references']:
            raise ReadError('limit: references')
        target = resolve(source, value)
        if target not in self.members:
            if target + '/' in self.members:
                raise ReadError('package reference points to directory: ' + target)
            if missing_ok:
                return None
            raise ReadError('missing package reference: ' + target)
        if self.members[target].is_dir():
            raise ReadError('missing package reference: ' + target)
        if self.retain_structure:
            record = dict(source=source, value=value, target=target)
            self.account(record)
            self.evidence.append(record)
        return target

    def xml(self, name, expected=None, *, require_ofd=True):
        if name in self.opaque_members:
            raise ReadError('conflicting XML part and opaque payload roles')
        if name in self.cache:
            root = self.cache[name]
        else:
            if name not in self.members:
                raise ReadError('missing XML: ' + name)
            size = self.members[name].file_size
            self.xml_total += size
            if size > LIMITS['xml_bytes'] or self.xml_total > LIMITS['xml_total']:
                raise ReadError('limit: XML bytes')
            info = self.members[name]
            # Bound the read even when dishonest size headers describe a huge DEFLATE stream.
            with self.archive.open(name) as handle:
                data = handle.read(size + 1)
            if len(data) != size:
                raise ReadError('inconsistent ZIP member size')
            if info.compress_type == zipfile.ZIP_DEFLATED:
                offset = info.header_offset
                name_size, extra_size = struct.unpack_from('<HH', self.data, offset + 26)
                compressed_start = offset + 30 + name_size + extra_size
                compressed = self.data[compressed_start:compressed_start + info.compress_size]
                decoder = zlib.decompressobj(-15)
                inflated = decoder.decompress(compressed, size + 1)
                if (len(inflated) != size or not decoder.eof or decoder.unused_data
                        or decoder.unconsumed_tail or inflated != data):
                    raise ReadError('inconsistent DEFLATE stream')
            parser = expat.ParserCreate(namespace_separator='}')
            parser.buffer_text = True
            parser.buffer_size = 65536
            stack = []
            root = None
            events = []
            indexes = {}

            def event(kind, **values):
                self.event_count += 1
                if self.event_count > LIMITS['xml_events']:
                    raise ReadError('limit: XML events')
                if self.retain_structure:
                    events.append(dict(kind=kind, **values))

            def reject(*_args):
                raise ReadError('DTD/entity forbidden')

            def start(tag, attrs):
                nonlocal root
                self.nodes += 1
                if self.nodes > LIMITS['nodes'] or len(stack) >= LIMITS['depth'] or len(attrs) > 128:
                    raise ReadError('limit: XML structure')
                tag = '{' + tag if '}' in tag else tag
                attrs = {('{' + key if '}' in key else key): value for key, value in attrs.items()}
                if (len(tag) > LIMITS['xml_name_length']
                        or any(len(key) > LIMITS['xml_name_length'] for key in attrs)):
                    raise ReadError('limit: XML name length')
                if any((key == 'xmlns' or key.startswith('xmlns:'))
                       and len(value) > LIMITS['xml_namespace_chars'] for key, value in attrs.items()):
                    raise ReadError('limit: XML namespace declaration')
                self.name_chars += len(tag) + sum(len(key) + len(value) for key, value in attrs.items())
                if self.name_chars > LIMITS['xml_name_chars']:
                    raise ReadError('limit: expanded XML names and attributes')
                node = ET.Element(tag, attrs)
                if self.retain_structure:
                    indexes[node] = len(indexes)
                event('start', node=indexes.get(node))
                if stack:
                    stack[-1].append(node)
                else:
                    root = node
                stack.append(node)

            def content(value):
                self.chars += len(value)
                if self.chars > LIMITS['text_chars']:
                    raise ReadError('limit: XML characters')
                if stack:
                    node = stack[-1]
                    event('text', parent=indexes.get(node), value=value)
                    if len(node):
                        node[-1].tail = (node[-1].tail or '') + value
                    else:
                        node.text = (node.text or '') + value

            def end(_tag):
                event('end', node=indexes.get(stack[-1]))
                stack.pop()

            def comment(value):
                self.chars += len(value)
                if self.chars > LIMITS['text_chars']:
                    raise ReadError('limit: XML characters')
                event('comment', parent=indexes.get(stack[-1]) if stack else None, value=value)

            def instruction(target, value):
                self.chars += len(target) + len(value)
                if self.chars > LIMITS['text_chars']:
                    raise ReadError('limit: XML characters')
                event('processing_instruction', parent=indexes.get(stack[-1]) if stack else None,
                      target=target, value=value)

            def namespace_start(prefix, uri):
                if (len(uri or '') > LIMITS['xml_namespace_chars']
                        or len(prefix or '') > LIMITS['xml_name_length']):
                    raise ReadError('limit: XML namespace declaration')
                event('namespace_start', prefix=prefix, uri=uri)

            def configure(parser):
                parser.buffer_text = True
                parser.buffer_size = 65536
                parser.StartElementHandler = start
                parser.EndElementHandler = end
                parser.CharacterDataHandler = content
                parser.CommentHandler = comment
                parser.ProcessingInstructionHandler = instruction
                parser.XmlDeclHandler = lambda version, encoding, standalone: event(
                    'declaration', version=version, encoding=encoding, standalone=standalone)
                parser.StartNamespaceDeclHandler = namespace_start
                parser.EndNamespaceDeclHandler = lambda prefix: event('namespace_end', prefix=prefix)
                parser.StartDoctypeDeclHandler = reject
                parser.EntityDeclHandler = reject
                parser.ExternalEntityRefHandler = reject
                parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
                return parser

            before_parse = (self.nodes, self.chars, self.event_count, self.name_chars)
            namespace_mode = 'expanded_names'
            try:
                configure(parser).Parse(data, True)
            except expat.ExpatError as exc:
                lexical_errors = {getattr(expat.errors, code) for code in (
                    'XML_ERROR_INVALID_TOKEN', 'XML_ERROR_UNBOUND_PREFIX', 'XML_ERROR_RESERVED_PREFIX_XML',
                    'XML_ERROR_RESERVED_PREFIX_XMLNS', 'XML_ERROR_RESERVED_NAMESPACE_URI')}
                if require_ofd or expected or expat.ErrorString(exc.code) not in lexical_errors:
                    raise ReadError('invalid XML part: ' + name + ': ' + str(exc)) from exc
                # Only extra, untyped XML may be retained as XML 1.0 lexical
                # names when namespace processing is impossible. No name is
                # repaired and no such node becomes an active OFD object.
                self.nodes, self.chars, self.event_count, self.name_chars = before_parse
                stack.clear()
                events.clear()
                indexes.clear()
                root = None
                try:
                    configure(expat.ParserCreate()).Parse(data, True)
                except expat.ExpatError as lexical_exc:
                    raise ReadError('invalid XML part: ' + name + ': ' + str(lexical_exc)) from lexical_exc
                namespace_mode = 'raw_names'
            if root is None:
                raise ReadError('empty XML document')
            for node in root.iter():
                if not is_ofd(node.tag):
                    key = (name, node.tag)
                    self.namespace_issues[key] = self.namespace_issues.get(key, 0) + 1
            self.cache[name] = root
            if self.retain_structure:
                self.xml_events[name] = events
                self.xml_digests[name] = hashlib.sha256(data).hexdigest()
                self.node_indexes[name] = indexes
                self.xml_modes[name] = namespace_mode
        if require_ofd and not is_ofd(root.tag):
            raise ReadError('unsupported: OFD namespace')
        if expected and root.tag != q(expected):
            raise ReadError('unexpected XML root: ' + name)
        if expected:
            self.expected_roots.setdefault(name, set()).add(expected)
            declare_payloads(self, name, root)
        return root

    def account(self, record):
        self.serialized_cost += len(json.dumps(record, ensure_ascii=True))
        if self.serialized_cost > LIMITS["output_chars"] - 1024*1024:
            raise ReadError("limit: expanded output")

    def budget(self, root):
        count = 0
        chars = 0
        for node in root.iter():
            count += 1
            chars += len(node.tag) + len(node.text or "") + len(node.tail or "")
            chars += sum(len(key) + len(value) for key, value in node.attrib.items())
        # Structural exports duplicate attributes and ancestry. Text reading does
        # not construct those records; account() bounds its actual retained data.
        if self.retain_structure:
            self.output_cost += count * 8192 + chars * 8
            if self.output_cost > LIMITS["output_chars"]:
                raise ReadError("limit: expanded output")
        self.expanded_nodes += count
        if self.expanded_nodes > LIMITS["nodes"]:
            raise ReadError("limit: expanded nodes")

    def metadata(self, source, value, expected=None):
        path = self.reference(source, value)
        root = self.xml(path, expected)
        self.budget(root)
        return path, [dict(element=local(node.tag), tag=node.tag, attributes=dict(node.attrib),
                           text=node.text or '') for node in root.iter()]


def declare_payloads(package, source, root):
    # Only an active, typed OFD route can declare opaque payloads. A stray
    # XML file is data, not authority to hide another member from the scan.
    role = local(root.tag)
    if (source in package.payload_classified or role not in ('Res', 'Attachments', 'Signature')
            or role not in package.expected_roots.get(source, set())):
        return
    package.payload_classified.add(source)
    parents = {child: parent for parent in root.iter() for child in parent}
    for node in ofd_nodes(root):
        name = local(node.tag)
        parent = parents.get(node)
        role = None
        anchor = source
        grandparent = parents.get(parent)
        if (root.tag == q('Res') and grandparent is not None
                and parents.get(grandparent) is root
                and ((name == 'FontFile' and parent.tag == q('Font') and grandparent.tag == q('Fonts'))
                     or (name == 'MediaFile' and parent.tag == q('MultiMedia') and grandparent.tag == q('MultiMedias')))):
            role = 'resource_payload'
            if root.tag == q('Res') and root.get('BaseLoc'):
                anchor = resolve(source, root.get('BaseLoc').rstrip('/') + '/_anchor')
        elif (root.tag == q('Signature')
              and ((name == 'SignedValue' and parent is root)
                   or (name == 'BaseLoc' and parent is not None and parent.tag == q('Seal')
                       and grandparent is not None and grandparent.tag == q('SignedInfo')
                       and parents.get(grandparent) is root))):
            role = 'signature_payload'
        elif (root.tag == q('Attachments') and name in ('FileLoc', 'File')
              and parent is not None and parent.tag == q('Attachment') and grandparent is root):
            role = 'attachment'
        if role and text_of(node):
            target = package.reference(anchor, text_of(node))
            if target in package.cache:
                raise ReadError('conflicting XML part and opaque payload roles')
            package.opaque_members.setdefault(target, role)
