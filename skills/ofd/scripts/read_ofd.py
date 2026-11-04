"""Bounded offline OFD XML-structure and stored-text reader; no rendering or disk extraction."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import struct
try:
    import zlib
except ImportError:
    zlib = None
import sys
import zipfile
from xml.parsers import expat
from xml.etree import ElementTree as ET

NS = 'http://www.ofdspec.org/2016'
KNOWN_VERSIONS = ('1.0', '1.1', '1.2')
ZLIB_ERROR = getattr(zlib, 'error', RuntimeError)
LIMITS = dict(input_bytes=64*1024*1024, entries=4096, member_bytes=16*1024*1024,
              total_bytes=128*1024*1024, xml_bytes=2*1024*1024, xml_total=32*1024*1024,
              nodes=100000, depth=64, text_chars=8*1024*1024, pages=2000,
              references=10000, ratio=200, output_chars=64*1024*1024, composite_depth=16,
              xml_events=300000, xml_name_chars=8*1024*1024,
              xml_namespace_chars=4096, xml_name_length=8192)


class ReadError(Exception):
    """A deterministic failure, never a partial success."""


def environment():
    version = tuple(map(int, expat.EXPAT_VERSION.removeprefix('expat_').split('.')))
    if sys.version_info < (3, 10) or version < (2, 6, 0):
        raise ReadError('environment: Python >=3.10 and Expat >=2.6.0 required')
    if zlib is None or zipfile.zlib is None:
        raise ReadError('environment: working zlib/ZIP deflate support required')
    try:
        probe = b'ofd-environment-probe'
        if zlib.decompress(zlib.compress(probe)) != probe:
            raise ReadError('environment: zlib roundtrip failed')
    except ZLIB_ERROR as exc:
        raise ReadError('environment: zlib roundtrip failed') from exc


def local(tag):
    return tag.rsplit('}', 1)[-1]


def q(name):
    return '{' + NS + '}' + name


def is_ofd(tag):
    return tag.rpartition('}')[0] == '{' + NS


def ofd_nodes(root):
    stack = [root]
    while stack:
        node = stack.pop()
        if not is_ofd(node.tag):
            continue
        yield node
        stack.extend(reversed(list(node)))


def text_of(element):
    return (element.text or '').strip()


def single_child(root, name, *, required=False):
    """Reject ambiguity in the single-value containers this reader relies on."""
    children = root.findall(q(name))
    if len(children) > 1:
        raise ReadError('duplicate ' + name)
    if not children:
        if required:
            raise ReadError('missing ' + name)
        return None
    return children[0]


def confined_environment():
    """Require the standard-library platform primitives used by confined reads."""
    if (os.open not in os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW')
            or not hasattr(os, 'O_DIRECTORY')):
        raise ReadError('unsupported: confined input requires dir_fd, O_NOFOLLOW and O_DIRECTORY')


def confined_descriptor(path, task_root):
    """Walk an explicitly trusted task root with directory-relative nofollow opens."""
    confined_environment()
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ReadError('input must stay within task directory')
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    descriptor = os.open(task_root, directory_flags)
    try:
        for component in path.parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        flags = (os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_CLOEXEC', 0))
        return os.open(path.parts[-1], flags, dir_fd=descriptor)
    finally:
        os.close(descriptor)


def read_input(path, *, task_root=None):
    """Read one regular descriptor and reject detected concurrent byte changes.

    The default API trusts its path. A supplied task_root confines relative input
    through nofollow descriptor walks; CLI passes '.' to anchor the current task.
    This is not a filesystem sandbox against privileged concurrent writers.
    """
    if task_root is None:
        flags = (os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_CLOEXEC', 0))
        descriptor = os.open(path, flags)
    else:
        descriptor = confined_descriptor(path, task_root)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReadError('input must be a regular file')
        if before.st_size > LIMITS['input_bytes']:
            raise ReadError('limit: input bytes')
        with os.fdopen(descriptor, 'rb') as handle:
            descriptor = None
            data = handle.read(before.st_size + 1)
            after = os.fstat(handle.fileno())
        identity = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (len(data) != before.st_size
                or any(getattr(before, key) != getattr(after, key) for key in identity)):
            raise ReadError('input changed during read')
        return data
    finally:
        if descriptor is not None:
            os.close(descriptor)


def safe_name(name):
    if (not name or len(name) > 1024 or '\\' in name or '\x00' in name
            or any(ord(c) < 32 for c in name) or ':' in name or '%' in name
            or '?' in name or '#' in name or name.startswith('/')):
        raise ReadError('unsafe package path')
    parts = name.rstrip('/').split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ReadError('noncanonical package path')
    return name


def resolve(source, value):
    value = value.strip()
    if not value or len(value) > 1024:
        raise ReadError('empty/oversized package reference')
    raw = value[1:] if value.startswith('/') else value
    parts = [] if value.startswith('/') else posixpath.dirname(source).split('/')
    if parts == ['']:
        parts = []
    for part in raw.split('/'):
        if part == '.':
            continue
        if part == '..':
            if not parts:
                raise ReadError('package reference escapes root')
            parts.pop()
        else:
            safe_name(part)
            parts.append(part)
    return safe_name('/'.join(parts))


def decode_text(value):
    issues = []
    def replace(match):
        codepoint = int(match.group(1), 16)
        if 0xD800 <= codepoint <= 0xDFFF:
            issues.append('surrogate_text_escape')
            return match.group(0)
        return chr(codepoint)
    decoded = re.sub(r'\\([0-9A-Fa-f]{4})', replace, value)
    if re.search(r'\\(?![0-9A-Fa-f]{4})', value):
        issues.append('uninterpreted_text_escape')
    if '\u00a4' in decoded:
        issues.append('text_placeholder_unresolved')
    return decoded, issues


class Package:
    def __init__(self, data):
        import io
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
                start = offset + 30 + name_size + extra_size
                compressed = self.data[start:start + info.compress_size]
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
                indexes[node] = len(indexes)
                event('start', node=indexes[node])
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
                    event('text', parent=indexes[node], value=value)
                    if len(node):
                        node[-1].tail = (node[-1].tail or '') + value
                    else:
                        node.text = (node.text or '') + value

            def end(_tag):
                event('end', node=indexes[stack[-1]])
                stack.pop()

            def comment(value):
                self.chars += len(value)
                if self.chars > LIMITS['text_chars']:
                    raise ReadError('limit: XML characters')
                event('comment', parent=indexes[stack[-1]] if stack else None, value=value)

            def instruction(target, value):
                self.chars += len(target) + len(value)
                if self.chars > LIMITS['text_chars']:
                    raise ReadError('limit: XML characters')
                event('processing_instruction', parent=indexes[stack[-1]] if stack else None,
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
                lexical_codes = {expat.errors.codes[getattr(expat.errors, code)] for code in (
                    'XML_ERROR_INVALID_TOKEN', 'XML_ERROR_UNBOUND_PREFIX', 'XML_ERROR_RESERVED_PREFIX_XML',
                    'XML_ERROR_RESERVED_PREFIX_XMLNS', 'XML_ERROR_RESERVED_NAMESPACE_URI')}
                if require_ofd or expected or exc.code not in lexical_codes:
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
        # Conservative allowance for repeated attributes, IDs, source paths and JSON escaping.
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


def catalogue_structure(package, documents, issue):
    """Read normalized XML structure without activating actions or opaque payloads.

    The catalogue describes storage once per node. Page text_runs describe actual
    occurrences and remain a separate view. Structural coverage is not XSD or
    rendering/semantic certification.
    """
    original_parts = set(package.cache)

    for source, root in list(package.cache.items()):
        declare_payloads(package, source, root)
    if set(package.opaque_members) & set(package.cache):
        raise ReadError('conflicting XML part and opaque payload roles')

    for source, info in package.members.items():
        if info.is_dir() or source in package.cache or source in package.opaque_members:
            continue
        xml_named = source.lower().endswith(('.xml', '.xsd'))
        if not xml_named:
            # Only a bounded header of unclassified members is examined. Known
            # attachments, fonts, media and signature payloads never reach here.
            with package.archive.open(source) as handle:
                header = handle.read(64)
            if header.startswith((b'\xff\xfe', b'\xfe\xff')):
                prefix = header.decode('utf-16', errors='ignore').lstrip()
            else:
                prefix = header.decode('utf-8-sig', errors='ignore').lstrip()
            if not (prefix.startswith('<?xml') or NS in prefix):
                continue
        package.xml(source, require_ofd=False)

    # A payload may not simultaneously be interpreted as an OFD XML part.
    if set(package.opaque_members) & set(package.cache):
        raise ReadError('conflicting XML part and opaque payload roles')

    occurrence_index = {}
    for document_index, document in enumerate(documents):
        for page_index, page in enumerate(document['pages']):
            for run_index, run in enumerate(page['text_runs']):
                key = (run['source'], run['storage_node'])
                occurrence_index.setdefault(key, []).append(
                    dict(document=document_index, page=page_index, run=run_index))

    parts = []
    text_sources = []
    character_field_count = 0
    identifiers = []
    reference_fields = []
    validated_files = {}
    for reference in package.evidence:
        key = (reference['source'], reference['value'])
        validated_files.setdefault(key, set()).add(reference['target'])
    for source, root in package.cache.items():
        indexes = package.node_indexes[source]
        parents = {child: parent for parent in root.iter() for child in parent}
        nodes = []
        for node in root.iter():
            parent = parents.get(node)
            index = indexes[node]
            record = dict(index=index, parent=indexes[parent] if parent is not None else None,
                          tag=node.tag, attributes=dict(node.attrib), text=node.text or '', tail=node.tail or '')
            character_field_count += bool(record['text']) + bool(record['tail'])
            package.account(record)
            nodes.append(record)
            if 'ID' in node.attrib:
                ident = dict(source=source, storage_node=index, tag=node.tag, value=node.get('ID'))
                package.account(ident)
                identifiers.append(ident)
            fields = [(name, value, 'attribute') for name, value in node.attrib.items()
                      if name in ('BaseLoc', 'FileRef', 'ResourceID', 'Font', 'DrawParam', 'ColorSpace', 'TemplateID', 'URI')]
            if local(node.tag) in ('DocRoot', 'PublicRes', 'DocumentRes', 'PageRes', 'FileLoc', 'FontFile',
                                   'MediaFile', 'Annotations', 'Attachments', 'CustomTags', 'Extensions', 'Signatures', 'SchemaLoc'):
                fields.append((node.tag, node.text or '', 'text'))
            for field, value, kind in fields:
                targets = sorted(validated_files.get((source, value.strip()), ()))
                reference = dict(source=source, storage_node=index, field=field, value=value, kind=kind,
                                 matching_file_targets=targets,
                                 interpretation='matching_file_value_only' if targets else 'raw_context_required')
                package.account(reference)
                reference_fields.append(reference)
            if node.tag != q('TextCode'):
                continue
            ancestors = []
            ancestor = parent
            while ancestor is not None:
                ancestors.insert(0, indexes[ancestor])
                ancestor = parents.get(ancestor)
            raw = ''.join(node.itertext())
            decoded, problems = decode_text(raw)
            trusted = (parent is not None and parent.tag == q('TextObject')
                       and all(is_ofd(item.tag)
                               for item in parents_chain(node, parents)))
            if not trusted:
                problems.append('uninterpreted_textcode_context')
            if len(node):
                problems.append('nested_textcode')
            for problem in problems:
                issue(problem, source, 'storage_node=' + str(index))
            text_record = dict(source=source, storage_node=index, root_tag=root.tag,
                               object_id=parent.get('ID') if parent is not None else None,
                               raw_text=raw, text=decoded if trusted else None,
                               code_attributes=dict(node.attrib), ancestors=ancestors,
                               occurrences=occurrence_index.get((source, index), []),
                               interpretation='complete' if not problems else 'partial')
            package.account(text_record)
            text_sources.append(text_record)
        part = dict(source=source, sha256=package.xml_digests[source], nodes=nodes,
                    events=package.xml_events[source], active_route=source in original_parts,
                    namespace_mode=package.xml_modes[source])
        # Node records are already charged; charge all event strings and wrapper.
        package.account(dict(source=source, sha256=part['sha256'], events=part['events'],
                             active_route=part['active_route'], namespace_mode=part['namespace_mode']))
        parts.append(part)
        if source not in original_parts:
            issue('additional_xml_part_catalogued', source, root.tag)
        if part['namespace_mode'] == 'raw_names':
            issue('xml_namespace_unresolved', source, 'Raw XML names retained; not interpreted as OFD objects')

    payloads = [dict(source=source, role=role, bytes=package.members[source].file_size)
                for source, role in package.opaque_members.items()]
    package.account(payloads)
    structure = dict(status='complete', scope='normalized XML storage structure, not schema/semantic certification',
                     xml_parts=parts, opaque_payloads=payloads,
                     character_field_count=character_field_count,
                     identifiers=identifiers, reference_fields=reference_fields,
                     reference_validation='Supported file routes only; raw candidate fields do not imply resolved links',
                     namespace_resolution='partial' if 'raw_names' in package.xml_modes.values() else 'complete',
                     member_inventory='all ZIP members; non-XML payloads are opaque')
    unclassified = [name for name, info in package.members.items()
                    if not info.is_dir() and name not in package.cache and name not in package.opaque_members]
    structure['unclassified_members'] = unclassified
    package.account(unclassified)
    missing_indexes = sum(len(document['resource_gaps']) for document in documents)
    structure['missing_resource_index_count'] = missing_indexes
    package.account(dict(missing_resource_index_count=missing_indexes))
    if missing_indexes:
        structure['status'] = 'partial'
    if unclassified:
        structure['status'] = 'partial'
        for name in unclassified:
            issue('unclassified_payload', name, 'Member inventoried; payload format not interpreted')
    return structure, text_sources


def parents_chain(node, parents):
    """Yield the node and its actual ancestors without inferring context from local names."""
    while node is not None:
        yield node
        node = parents.get(node)


def read_ofd(path, *, task_root=None, version_policy="known"):
    environment()
    if version_policy not in ('known', 'compatible'):
        raise ReadError('invalid version policy')
    data = read_input(Path(path), task_root=task_root)
    package = Package(data)
    result = dict(schema_version='ofd-read-3', status='complete', scope='normalized-xml-structure-and-stored-text',
                  sha256=hashlib.sha256(data).hexdigest(), documents=[], diagnostics=[],
                  limits=LIMITS.copy(), order='XML storage order; not semantic reading order',
                  signature_verification='not_performed', tables=[],
                  tables_assessment=dict(status='not_interpreted',
                                         scope='Explicit XML remains in structure; no layout or semantic grid inferred'),
                  members=[dict(path=n, bytes=i.file_size, compressed_bytes=i.compress_size,
                                compression_method=i.compress_type, declared_crc32=format(i.CRC, '08x'),
                                flags=i.flag_bits, is_directory=i.is_dir()) for n, i in package.members.items()])

    package.account(result)

    def append_to(collection, record):
        package.account(record)
        collection.append(record)

    def issue(code, source, detail=''):
        result['status'] = 'partial'
        append_to(result['diagnostics'], dict(code=code, source=source, detail=detail))

    root = package.xml('OFD.xml', 'OFD')
    package.budget(root)
    version = root.get('Version', '1.0')
    doc_type = root.get('DocType', 'OFD')
    if doc_type not in ('OFD', 'OFD-A'):
        raise ReadError('unsupported: OFD document type')
    if (version not in KNOWN_VERSIONS
            and not (version_policy == 'compatible' and re.fullmatch(r'1\.[0-9]{1,3}', version, re.ASCII))):
        raise ReadError('unsupported: OFD version')
    result['format'] = dict(version=version, declared_version=root.get('Version'),
                            doc_type=doc_type, version_policy=version_policy,
                            interpretation='XML storage and literal text model; no schema, rendering or version conformance certification')
    package.account(result['format'])
    if version not in KNOWN_VERSIONS:
        issue('version_common_subset', 'OFD.xml', version)
    if doc_type == 'OFD-A':
        issue('archival_profile_not_validated', 'OFD.xml', 'No OFD-A conformance validation')
    if 'Version' not in root.attrib:
        issue('missing_version_assumed', 'OFD.xml', '1.0')
    if 'DocType' not in root.attrib:
        issue('missing_document_type_assumed', 'OFD.xml', 'OFD')
    bodies = root.findall(q('DocBody'))
    if not bodies:
        raise ReadError('missing DocBody')
    page_count = 0
    for body in bodies:
        doc_root = single_child(body, 'DocRoot')
        versions = single_child(body, 'Versions')
        if doc_root is None and versions is None:
            raise ReadError('missing DocRoot')
        doc_path = package.reference('OFD.xml', text_of(doc_root)) if doc_root is not None else 'OFD.xml'
        doc = dict(source=doc_path, pages=[], resources=[], resource_gaps=[], metadata=[], template_sources={})
        append_to(result['documents'], doc)
        doc['info'] = []
        doc_info = single_child(body, 'DocInfo')
        if doc_info is not None:
            package.budget(doc_info)
            for info_node in ofd_nodes(doc_info):
                append_to(doc['info'], dict(tag=info_node.tag, attributes=dict(info_node.attrib), text=info_node.text or ''))
        doc['revision_entries'] = []
        if versions is not None:
            revision_ids = set()
            for entry in versions:
                if entry.tag != q('Version'):
                    issue('unknown_revision_entry', 'OFD.xml', entry.tag)
                    continue
                ident = entry.get('ID')
                if not ident or ident in revision_ids:
                    raise ReadError('duplicate/missing revision ID')
                revision_ids.add(ident)
                revision_path, nodes = package.metadata('OFD.xml', entry.get('BaseLoc', ''), 'DocVersion')
                append_to(doc['revision_entries'], dict(attributes=dict(entry.attrib), source=revision_path, nodes=nodes))
            if not revision_ids:
                raise ReadError('empty Versions')
            issue('revision_snapshots_not_activated', 'OFD.xml', 'Stored snapshots are catalogued, not overlaid onto the active document')
        if doc_root is None:
            issue('no_active_docroot', 'OFD.xml', 'Revision storage retained; no active revision selected implicitly')
            continue
        document = package.xml(doc_path, 'Document')
        package.budget(document)
        common = single_child(document, 'CommonData')
        templates = {}
        resource_ids = {}
        resource_defs = {}
        resource_records = set()

        def load_resources(source, value, ids, definitions, scope, kind):
            # Only absence of a resource index is recoverable. Path validation,
            # directories, malformed/present XML and payload-role conflicts still fail.
            res_path = package.reference(source, value, missing_ok=True)
            if res_path is None:
                target = resolve(source, value)
                append_to(doc['resource_gaps'], dict(kind=kind, source=source, value=value,
                                                     target=target, scope=scope))
                issue('missing_resource_index', source, kind + ': ' + target)
                return
            resource = package.xml(res_path, 'Res')
            package.budget(resource)
            base = resource.get('BaseLoc', '')
            anchor = res_path if not base else resolve(res_path, base.rstrip('/') + '/_anchor')
            families = {'Fonts': 'Font', 'MultiMedias': 'MultiMedia',
                        'CompositeGraphicUnits': 'CompositeGraphicUnit',
                        'DrawParams': 'DrawParam', 'ColorSpaces': 'ColorSpace'}
            for group in resource:
                if group.tag not in {q(name) for name in families}:
                    issue('unknown_resource_group', res_path, group.tag)
                    continue
                for node in group:
                    if node.tag != q(families[local(group.tag)]):
                        issue('unknown_resource', res_path, node.tag)
                        continue
                    ident = node.get('ID')
                    if not ident or (ident in definitions and definitions[ident] != (res_path, node)):
                        raise ReadError('duplicate/missing resource ID')
                    ids[ident] = local(node.tag)
                    definitions[ident] = (res_path, node)
                    key = (res_path, ident, scope)
                    if key in resource_records:
                        continue
                    resource_records.add(key)
                    files = []
                    for child in node:
                        if child.tag in (q('FontFile'), q('MediaFile')) and text_of(child):
                            files.append(package.reference(anchor, text_of(child)))
                    append_to(doc['resources'], dict(source=res_path, kind=local(node.tag),
                                                     attributes=dict(node.attrib), files=files, scope=scope))

        if common is not None:
            for entry in common.findall(q('TemplatePage')):
                ident = entry.get('ID')
                if not ident or ident in templates:
                    raise ReadError('duplicate/missing template ID')
                templates[ident] = package.reference(doc_path, entry.get('BaseLoc', ''))
            for kind in ('PublicRes', 'DocumentRes'):
                for entry in common.findall(q(kind)):
                    load_resources(doc_path, text_of(entry), resource_ids, resource_defs, 'document', kind)
        # Preserve related XML as metadata only; never treat its text as page text.
        for kind in ('Attachments', 'Annotations', 'CustomTags', 'Extensions'):
            for entry in document.findall(q(kind)):
                meta_path, nodes = package.metadata(doc_path, text_of(entry), kind)
                append_to(doc['metadata'], dict(kind=kind, source=meta_path, nodes=nodes))
                issue('metadata_only', meta_path, kind)
                if kind == 'CustomTags':
                    for custom_tag in package.xml(meta_path).findall(q('CustomTag')):
                        for child_name in ('FileLoc', 'SchemaLoc'):
                            location = single_child(custom_tag, child_name)
                            if location is None:
                                continue
                            value = text_of(location)
                            if child_name == 'SchemaLoc' and re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', value):
                                issue('external_schema_not_followed', meta_path, 'External schema kept as data; never fetched')
                                continue
                            custom_path = package.reference(meta_path, value)
                            package.xml(custom_path, require_ofd=False)
                for node in ofd_nodes(package.xml(meta_path)):
                    if node.tag in (q('FileLoc'), q('File')) and text_of(node):
                        target = package.reference(meta_path, text_of(node))
                        if kind == 'Annotations':
                            annot_path, annot_nodes = package.metadata(meta_path, text_of(node), 'PageAnnot')
                            append_to(doc['metadata'], dict(kind='PageAnnotations', source=annot_path, nodes=annot_nodes))
        signature = single_child(body, 'Signatures')
        if signature is not None:
            sig_path, nodes = package.metadata('OFD.xml', text_of(signature), 'Signatures')
            append_to(doc['metadata'], dict(kind='Signatures', source=sig_path, nodes=nodes))
            issue('signatures_not_verified', sig_path)
            for node in package.xml(sig_path).findall(q('Signature')):
                detail_path, detail_nodes = package.metadata(sig_path, node.get('BaseLoc', ''), 'Signature')
                append_to(doc['metadata'], dict(kind='Signature', source=detail_path, nodes=detail_nodes))
        pages = single_child(document, 'Pages', required=True)
        if pages is None or not len(pages):
            raise ReadError('missing/empty Pages')
        page_ids = set()
        for entry in pages:
            if entry.tag != q('Page') or not entry.get('ID') or entry.get('ID') in page_ids:
                raise ReadError('invalid/duplicate page ID')
            page_ids.add(entry.get('ID'))
            page_count += 1
            if page_count > LIMITS['pages']:
                raise ReadError('limit: pages')
            page_path = package.reference(doc_path, entry.get('BaseLoc', ''))
            page = dict(number=len(doc['pages'])+1, id=entry.get('ID'), source=page_path,
                        text_runs=[], images=[], template_refs=[], objects=[])
            append_to(doc['pages'], page)
            used = set()

            def visit(source, is_template=False):
                if source in used:
                    issue('duplicate_template_reference', source)
                    return
                used.add(source)
                page_xml = package.xml(source, 'Page')
                local_ids = dict(resource_ids)
                local_defs = dict(resource_defs)
                for page_resource in page_xml.findall(q('PageRes')):
                    load_resources(source, text_of(page_resource), local_ids, local_defs, source, 'PageRes')
                if not is_template:
                    page['content_present'] = single_child(page_xml, 'Content') is not None
                else:
                    single_child(page_xml, 'Content')
                for template in page_xml.findall(q('Template')):
                    if is_template:
                        issue('ignored_template_in_template', source, template.get('TemplateID', ''))
                        continue
                    ident = template.get('TemplateID')
                    if ident not in templates:
                        raise ReadError('missing template ID reference')
                    target = templates[ident]
                    append_to(page['template_refs'], dict(id=ident, source=target, attributes=dict(template.attrib)))
                    visit(target, is_template=True)
                if source != page_path:
                    doc['template_sources'][source] = True
                def walk_objects(container, object_source, chain=(), active=()):
                    package.budget(container)
                    parents = {child: parent for parent in container.iter() for child in parent}
                    for ordinal, node in enumerate(container.iter()):
                        name = local(node.tag)
                        if not is_ofd(node.tag):
                            continue
                        ancestor = parents.get(node)
                        ancestor_chain = []
                        foreign_ancestor = False
                        in_content = False
                        while ancestor is not None:
                            ancestor_chain.insert(0, ancestor)
                            if ancestor.tag == q('Content'):
                                in_content = True
                            if not is_ofd(ancestor.tag):
                                foreign_ancestor = True
                                break
                            ancestor = parents.get(ancestor)
                        if foreign_ancestor:
                            continue
                        if name == 'TextCode' and (parents.get(node) is None or parents[node].tag != q('TextObject')):
                            issue('unexpected_textcode_parent', object_source)
                        if name.endswith('Object') and not in_content:
                            issue('object_outside_content', object_source, name)
                            continue
                        if name.endswith('Object'):
                            append_to(page['objects'], dict(kind=name, source=object_source, ordinal=ordinal, attributes=dict(node.attrib)))
                            if name not in ('TextObject', 'ImageObject', 'PathObject', 'CompositeObject'):
                                issue('uninterpreted_object', object_source, name)
                        if name == 'TextObject':
                            font = node.get('Font')
                            if font and local_ids.get(font) != 'Font':
                                issue('unresolved_font', object_source, font)
                            codes = node.findall(q('TextCode'))
                            if not codes:
                                issue('text_without_textcode', object_source)
                            if node.find(q('CGTransform')) is not None:
                                issue('glyph_mapping_not_interpreted', object_source)
                            for code_index, code in enumerate(codes):
                                if len(code):
                                    issue('nested_textcode', object_source)
                                raw_text = code.text or ''
                                decoded, text_issues = decode_text(raw_text)
                                for text_issue in text_issues:
                                    issue(text_issue, object_source)
                                append_to(page['text_runs'], dict(text=decoded, raw_text=raw_text, source=object_source,
                                    object_id=node.get('ID'), object_ordinal=ordinal, code_index=code_index,
                                    storage_node=package.node_indexes[object_source][code],
                                    object_attributes=dict(node.attrib), code_attributes=dict(code.attrib),
                                    coordinate_space='raw local millimetres; transforms not applied',
                                    template=is_template, occurrence=list(chain),
                                        ancestry=[dict(tag=ancestor_node.tag, attributes=dict(ancestor_node.attrib))
                                                  for ancestor_node in ancestor_chain]))
                        elif name == 'CompositeObject':
                            ident = node.get('ResourceID')
                            if local_ids.get(ident) != 'CompositeGraphicUnit':
                                issue('unresolved_composite_resource', object_source, ident or '')
                            else:
                                target_source, target = local_defs[ident]
                                key = (target_source, ident)
                                if key in active:
                                    raise ReadError('cyclic composite reference')
                                if len(active) >= LIMITS['composite_depth']:
                                    raise ReadError('limit: composite depth')
                                single_child(target, 'Content', required=True)
                                issue('composite_geometry_not_applied', object_source, ident)
                                step = dict(source=object_source, object_id=node.get('ID'),
                                            resource_id=ident, attributes=dict(node.attrib))
                                walk_objects(target, target_source, chain + (step,), active + (key,))
                        elif name == 'ImageObject':
                            resource_id = node.get('ResourceID')
                            if local_ids.get(resource_id) != 'MultiMedia':
                                issue('unresolved_image_resource', object_source, resource_id or '')
                            append_to(page['images'], dict(source=object_source, ordinal=ordinal, attributes=dict(node.attrib)))
                            issue('image_not_read', object_source, 'OCR required for scanned text')
                        elif name in ('Actions', 'Action'):
                            issue('not_interpreted', object_source, name)
                        elif name.endswith('Object') and name == 'PathObject':
                            issue('vector_not_interpreted', object_source, name)
                        elif name not in KNOWN_ELEMENTS and not name.endswith('Object'):
                            issue('unknown_element', object_source, name)

                walk_objects(page_xml, source)
            visit(page_path)
            if not page['content_present']:
                if page['template_refs']:
                    issue('page_content_absent_with_templates', page_path, 'No page Content; template text is separate')
                else:
                    issue('blank_page_no_content', page_path, 'No Content means blank page; no text to extract')
            elif not any(run['text'].strip() for run in page['text_runs']):
                issue('no_explainable_text', page_path, 'May be blank, outlined or scanned; completeness unknown; OCR may be required')
        for node in document:
            if local(node.tag) not in ('CommonData', 'Pages', 'Attachments', 'Annotations', 'CustomTags', 'Extensions'):
                issue('document_feature_not_interpreted', doc_path, local(node.tag))
    result['structure'], result['text_sources'] = catalogue_structure(package, result['documents'], issue)
    for member in result['members']:
        validation = 'xml_crc_checked' if member['path'] in package.cache else 'metadata_only'
        package.account(dict(payload_validation=validation))
        member['payload_validation'] = validation
    text_gap_codes = {
        'surrogate_text_escape', 'uninterpreted_text_escape', 'text_placeholder_unresolved',
        'uninterpreted_textcode_context', 'nested_textcode', 'text_without_textcode',
        'glyph_mapping_not_interpreted', 'image_not_read', 'vector_not_interpreted',
        'uninterpreted_object', 'unknown_element', 'unresolved_composite_resource',
        'object_outside_content', 'unexpected_textcode_parent', 'no_explainable_text',
        'version_common_subset', 'missing_version_assumed', 'missing_resource_index',
    }
    gaps = sorted({diagnostic['code'] for diagnostic in result['diagnostics']
                   if diagnostic['code'] in text_gap_codes})
    if package.namespace_issues:
        gaps.append('foreign_namespace')
    result['text_assessment'] = dict(
        status='empty' if not result['text_sources'] else ('partial' if gaps else 'complete'), gap_codes=gaps,
        stored_textcode_count=len(result['text_sources']),
        page_occurrence_count=sum(len(page['text_runs']) for doc in result['documents'] for page in doc['pages']),
        xml_character_field_count=result['structure']['character_field_count'],
        scope='Stored OFD TextCode interpretation; other character data is in structure; no OCR, glyph inference or visible reading order')
    package.account(result['text_assessment'])
    for (source, tag), count in package.namespace_issues.items():
        issue('foreign_namespace', source, tag + ' count=' + str(count))
    result['references'] = package.evidence
    result['parsed_xml'] = list(package.cache)
    result['unparsed_members'] = [name for name in package.members if name not in package.cache]
    for name in result['unparsed_members']:
        if name.lower().endswith('.xml') and name not in package.opaque_members:
            issue('unparsed_xml_member', name)
    for doc in result['documents']:
        package.account(doc['template_sources'])
    package.account(result['parsed_xml'])
    package.account(result['unparsed_members'])
    result['coverage'] = 'Normalized XML structure and all stored OFD TextCode sources; opaque payloads not decoded; no schema/rendering certification'
    package.archive.close()
    return result


KNOWN_ELEMENTS = set((
    'Page Area PhysicalBox ApplicationBox ContentBox BleedBox Template Content Layer PageBlock '
    'TextObject TextCode CGTransform Glyphs FillColor StrokeColor ColorValue Pattern AxialShd RadialShd '
    'GouraudShd LaGouraudShd Border Clipped Clip Clips Path AbbreviatedData ImageObject PathObject '
    'CompositeObject CompositeGraphicUnit Thumbnail Substitution Actions Action PageRes'
).split())


def project_output(result, mode, *, document=None, page=None, max_chars=12000, source_sha256=None):
    """Return an explicit bounded view, retaining the full source outcome/diagnostics."""
    if mode == 'full':
        return result
    if mode not in ('summary', 'page-text'):
        raise ReadError('unknown output view')
    if type(max_chars) is not int or not 512 <= max_chars <= 2 * 1024 * 1024:
        raise ReadError('view budget must be 512..2097152 characters')
    if result.get('status') not in ('complete', 'partial'):
        return result
    if source_sha256 is not None and (not isinstance(source_sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', source_sha256, re.ASCII)):
        raise ReadError('source fingerprint must contain exactly 64 hexadecimal digits')
    if source_sha256 is not None and source_sha256.lower() != result['sha256']:
        raise ReadError('source fingerprint changed; restart the scoped read')
    documents = result['documents']
    if document is not None and (type(document) is not int or not 1 <= document <= len(documents)):
        raise ReadError('document index outside available documents')
    if mode == 'page-text' and (document is None or page is None):
        raise ReadError('page-text requires explicit document and page indices')
    if mode == 'summary' and page is not None:
        raise ReadError('page index requires page-text view')
    if mode == 'page-text' and (type(page) is not int or page < 1):
        raise ReadError('page index must be a positive integer')
    view = {key: result[key] for key in ('status', 'diagnostics', 'format', 'text_assessment', 'signature_verification')}
    view.update(schema_version='ofd-view-1', source_schema_version=result['schema_version'], source_sha256=result['sha256'],
                mode=mode, scope=dict(document=document, page=page, indices='1-based storage order'),
                coverage='overview_only_no_text' if mode == 'summary' else 'selected_page_occurrences_only',
                source_assessment_scope='entire source; not a completeness claim for selected text',
                structure_status=result['structure']['status'], namespace_resolution=result['structure']['namespace_resolution'],
                omitted_top_level_fields=[key for key in result if key not in
                                          ('status', 'diagnostics', 'format', 'text_assessment', 'signature_verification', 'documents')],
                documents=[])
    for index, doc in enumerate(documents, 1):
        if document is not None and index != document:
            continue
        entry = dict(index=index, source=doc['source'], page_count=len(doc['pages']),
                     omitted_fields=[key for key in doc if key not in ('source', 'pages')])
        if mode == 'summary':
            entry.update(page_text_runs=sum(len(item['text_runs']) for item in doc['pages']),
                         images=sum(len(item['images']) for item in doc['pages']))
        else:
            if not 1 <= page <= len(doc['pages']):
                raise ReadError('page index outside selected document')
            selected = doc['pages'][page - 1]
            entry['pages'] = [{key: selected[key] for key in ('number', 'id', 'source', 'text_runs')}]
            entry['pages'][0]['omitted_fields'] = [key for key in selected if key not in ('number', 'id', 'source', 'text_runs')]
        view['documents'].append(entry)
    if len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1 > max_chars:
        raise ReadError('view or diagnostics exceed output budget; increase max-output-chars or request full output')
    return view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--version-policy', choices=('known', 'compatible'), default='known')
    parser.add_argument('--view', choices=('full', 'summary', 'page-text'), default='full')
    parser.add_argument('--document', type=int)
    parser.add_argument('--page', type=int)
    parser.add_argument('--max-output-chars', type=int)
    parser.add_argument('--source-sha256')
    args = parser.parse_args(argv)
    scoped = args.view != 'full'
    extra_view_options = any(value is not None for value in (args.document, args.page, args.max_output_chars, args.source_sha256))
    if (not scoped and extra_view_options) or (args.check and (scoped or extra_view_options)):
        parser.error('view options require a non-full view and cannot be used with --check')
    if args.view == 'page-text' and (args.document is None or args.page is None):
        parser.error('page-text requires --document and --page')
    if args.view == 'summary' and args.page is not None:
        parser.error('--page requires page-text')
    if args.source_sha256 is not None and not re.fullmatch(r'[0-9a-fA-F]{64}', args.source_sha256, re.ASCII):
        parser.error('source-sha256 must contain exactly 64 hexadecimal digits')
    try:
        environment()
        confined_environment()
        if args.check:
            print('check_kind=environment artifact_validation=not_run')
            print('environment-ok Python>=3.10 Expat>=2.6.0 stdlib-only', flush=True)
            return 0
        if not args.input:
            parser.error('input required unless --check')
        # Root '.' is opened once; every relative component is nofollow-opened.
        result = read_ofd(args.input, task_root='.', version_policy=args.version_policy)
        if scoped:
            result = project_output(result, args.view, document=args.document, page=args.page,
                                    max_chars=12000 if args.max_output_chars is None else args.max_output_chars,
                                    source_sha256=args.source_sha256)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result['status'] == 'complete' else 3
    except (ReadError, OSError, zipfile.BadZipFile, expat.ExpatError, RuntimeError, ValueError, ZLIB_ERROR, struct.error) as exc:
        message = str(exc)
        status = 'unsupported' if message.startswith('unsupported:') else 'failed'
        result = dict(schema_version='ofd-read-3', status=status,
                      diagnostics=[dict(code='read_error', detail=message)])
        if args.check:
            result.update(check_kind='environment', artifact_validation='not_run')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 4 if status == 'unsupported' else 1


if __name__ == '__main__':
    sys.exit(main())
