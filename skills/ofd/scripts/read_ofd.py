"""Bounded, offline OFD literal-text reader. No rendering or extraction to disk."""
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
ZLIB_ERROR = getattr(zlib, 'error', RuntimeError)
LIMITS = dict(input_bytes=64*1024*1024, entries=4096, member_bytes=16*1024*1024,
              total_bytes=128*1024*1024, xml_bytes=2*1024*1024, xml_total=32*1024*1024,
              nodes=100000, depth=64, text_chars=8*1024*1024, pages=2000,
              references=10000, ratio=200, output_chars=64*1024*1024)


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


def ofd_nodes(root):
    stack = [root]
    while stack:
        node = stack.pop()
        if not node.tag.startswith('{' + NS + '}'):
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

    def reference(self, source, value):
        self.references += 1
        if self.references > LIMITS['references']:
            raise ReadError('limit: references')
        target = resolve(source, value)
        if target not in self.members or self.members[target].is_dir():
            raise ReadError('missing package reference: ' + target)
        record = dict(source=source, value=value, target=target)
        self.account(record)
        self.evidence.append(record)
        return target

    def xml(self, name, expected=None):
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

            def reject(*_args):
                raise ReadError('DTD/entity forbidden')

            def start(tag, attrs):
                nonlocal root
                self.nodes += 1
                if self.nodes > LIMITS['nodes'] or len(stack) >= LIMITS['depth'] or len(attrs) > 128:
                    raise ReadError('limit: XML structure')
                tag = '{' + tag if '}' in tag else tag
                attrs = {('{' + key if '}' in key else key): value for key, value in attrs.items()}
                node = ET.Element(tag, attrs)
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
                    if len(node):
                        node[-1].tail = (node[-1].tail or '') + value
                    else:
                        node.text = (node.text or '') + value

            parser.StartElementHandler = start
            parser.EndElementHandler = lambda _tag: stack.pop()
            parser.CharacterDataHandler = content
            parser.StartDoctypeDeclHandler = reject
            parser.EntityDeclHandler = reject
            parser.ExternalEntityRefHandler = reject
            parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
            parser.Parse(data, True)
            if root is None or not root.tag.startswith('{' + NS + '}'):
                raise ReadError('unsupported: OFD namespace')
            for node in root.iter():
                if not node.tag.startswith('{' + NS + '}'):
                    key = (name, node.tag)
                    self.namespace_issues[key] = self.namespace_issues.get(key, 0) + 1
            self.cache[name] = root
        if expected and root.tag != q(expected):
            raise ReadError('unexpected XML root: ' + name)
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


def read_ofd(path, *, task_root=None):
    environment()
    data = read_input(Path(path), task_root=task_root)
    package = Package(data)
    result = dict(schema_version='ofd-read-2', status='complete', scope='literal-text-subset',
                  sha256=hashlib.sha256(data).hexdigest(), documents=[], diagnostics=[],
                  limits=LIMITS.copy(), order='XML storage order; not semantic reading order',
                  signature_verification='not_performed', tables=[],
                  members=[dict(path=n, bytes=i.file_size) for n, i in package.members.items()])

    package.account(result)

    def append_to(collection, record):
        package.account(record)
        collection.append(record)

    def issue(code, source, detail=''):
        result['status'] = 'partial'
        append_to(result['diagnostics'], dict(code=code, source=source, detail=detail))

    root = package.xml('OFD.xml', 'OFD')
    package.budget(root)
    if root.get('DocType', 'OFD') != 'OFD' or root.get('Version', '1.0') != '1.0':
        raise ReadError('unsupported: OFD version/type')
    bodies = root.findall(q('DocBody'))
    if not bodies:
        raise ReadError('missing DocBody')
    page_count = 0
    for body in bodies:
        doc_root = single_child(body, 'DocRoot', required=True)
        doc_path = package.reference('OFD.xml', text_of(doc_root))
        document = package.xml(doc_path, 'Document')
        package.budget(document)
        doc = dict(source=doc_path, pages=[], resources=[], metadata=[], template_sources={})
        append_to(result['documents'], doc)
        common = single_child(document, 'CommonData')
        templates = {}
        resource_ids = {}
        if common is not None:
            for entry in common.findall(q('TemplatePage')):
                ident = entry.get('ID')
                if not ident or ident in templates:
                    raise ReadError('duplicate/missing template ID')
                templates[ident] = package.reference(doc_path, entry.get('BaseLoc', ''))
            for kind in ('PublicRes', 'DocumentRes'):
                for entry in common.findall(q(kind)):
                    res_path = package.reference(doc_path, text_of(entry))
                    resource = package.xml(res_path, 'Res')
                    package.budget(resource)
                    base = resource.get('BaseLoc', '')
                    anchor = res_path if not base else resolve(res_path, base.rstrip('/') + '/_anchor')
                    for node in ofd_nodes(resource):
                        if node.tag in (q('Font'), q('MultiMedia'), q('CompositeGraphicUnit')):
                            ident = node.get('ID')
                            if not ident or ident in resource_ids:
                                raise ReadError('duplicate/missing resource ID')
                            resource_ids[ident] = local(node.tag)
                            files = []
                            for child in ofd_nodes(node):
                                if child.tag in (q('FontFile'), q('MediaFile')) and text_of(child):
                                    files.append(package.reference(anchor, text_of(child)))
                            append_to(doc['resources'], dict(source=res_path, kind=local(node.tag),
                                                         attributes=dict(node.attrib), files=files))
        # Preserve related XML as metadata only; never treat its text as page text.
        for kind in ('Attachments', 'Annotations', 'CustomTags', 'Extensions'):
            for entry in document.findall(q(kind)):
                meta_path, nodes = package.metadata(doc_path, text_of(entry))
                append_to(doc['metadata'], dict(kind=kind, source=meta_path, nodes=nodes))
                issue('metadata_only', meta_path, kind)
                for node in ofd_nodes(package.xml(meta_path)):
                    if node.tag in (q('FileLoc'), q('File')) and text_of(node):
                        target = package.reference(meta_path, text_of(node))
                        if kind == 'Annotations':
                            annot_path, annot_nodes = package.metadata(meta_path, text_of(node))
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
                package.budget(page_xml)
                parents = {child: parent for parent in page_xml.iter() for child in parent}
                for ordinal, node in enumerate(page_xml.iter()):
                    name = local(node.tag)
                    if not node.tag.startswith('{' + NS + '}'):
                        continue
                    ancestor = parents.get(node)
                    foreign_ancestor = False
                    in_content = False
                    while ancestor is not None:
                        if ancestor.tag == q('Content'):
                            in_content = True
                        if not ancestor.tag.startswith('{' + NS + '}'):
                            foreign_ancestor = True
                            break
                        ancestor = parents.get(ancestor)
                    if foreign_ancestor:
                        continue
                    if name == 'TextCode' and (parents.get(node) is None or parents[node].tag != q('TextObject')):
                        issue('unexpected_textcode_parent', source)
                    if name.endswith('Object') and not in_content:
                        issue('object_outside_content', source, name)
                        continue
                    if name.endswith('Object'):
                        append_to(page['objects'], dict(kind=name, source=source, ordinal=ordinal, attributes=dict(node.attrib)))
                        if name not in ('TextObject', 'ImageObject', 'PathObject'):
                            issue('uninterpreted_object', source, name)
                    if name == 'TextObject':
                        font = node.get('Font')
                        if font and resource_ids.get(font) != 'Font':
                            issue('unresolved_font', source, font)
                        codes = node.findall(q('TextCode'))
                        if not codes:
                            issue('text_without_textcode', source)
                        if node.find(q('CGTransform')) is not None:
                            issue('glyph_mapping_not_interpreted', source)
                        for code_index, code in enumerate(codes):
                            if len(code):
                                issue('nested_textcode', source)
                            raw_text = code.text or ''
                            decoded, text_issues = decode_text(raw_text)
                            for text_issue in text_issues:
                                issue(text_issue, source)
                            append_to(page['text_runs'], dict(text=decoded, raw_text=raw_text, source=source,
                                object_id=node.get('ID'), object_ordinal=ordinal, code_index=code_index,
                                object_attributes=dict(node.attrib), code_attributes=dict(code.attrib),
                                coordinate_space='raw local millimetres; transforms not applied',
                                template=source != page_path))
                    elif name == 'ImageObject':
                        resource_id = node.get('ResourceID')
                        if resource_ids.get(resource_id) != 'MultiMedia':
                            issue('unresolved_image_resource', source, resource_id or '')
                        append_to(page['images'], dict(source=source, ordinal=ordinal, attributes=dict(node.attrib)))
                        issue('image_not_read', source, 'OCR required for scanned text')
                    elif name in ('Actions', 'Action', 'PageRes'):
                        issue('not_interpreted', source, name)
                    elif name.endswith('Object') and name == 'PathObject':
                        issue('vector_not_interpreted', source, name)
                    elif name not in KNOWN_ELEMENTS and not name.endswith('Object'):
                        issue('unknown_element', source, name)
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
    for (source, tag), count in package.namespace_issues.items():
        issue('foreign_namespace', source, tag + ' count=' + str(count))
    result['references'] = package.evidence
    result['parsed_xml'] = list(package.cache)
    result['unparsed_members'] = [name for name in package.members if name not in package.cache]
    for name in result['unparsed_members']:
        if name.lower().endswith('.xml'):
            issue('unparsed_xml_member', name)
    for doc in result['documents']:
        package.account(doc['template_sources'])
    package.account(result['parsed_xml'])
    package.account(result['unparsed_members'])
    result['coverage'] = 'Only literal TextCode strings in referenced pages/templates; no visual/semantic completeness claim'
    package.archive.close()
    return result


KNOWN_ELEMENTS = set('Page Area PhysicalBox ApplicationBox ContentBox BleedBox Template Content Layer PageBlock TextObject TextCode CGTransform Glyphs FillColor StrokeColor ColorValue Pattern AxialShd RadialShd GouraudShd LaGouraudShd Border Clipped Clip Clips Path AbbreviatedData ImageObject PathObject Actions Action PageRes'.split())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
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
        result = read_ofd(args.input, task_root='.')
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result['status'] == 'complete' else 3
    except (ReadError, OSError, zipfile.BadZipFile, expat.ExpatError, RuntimeError, ValueError, ZLIB_ERROR, struct.error) as exc:
        message = str(exc)
        status = 'unsupported' if message.startswith('unsupported:') else 'failed'
        result = dict(schema_version='ofd-read-2', status=status,
                      diagnostics=[dict(code='read_error', detail=message)])
        if args.check:
            result.update(check_kind='environment', artifact_validation='not_run')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 4 if status == 'unsupported' else 1


if __name__ == '__main__':
    sys.exit(main())
