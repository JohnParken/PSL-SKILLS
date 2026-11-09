"""Bounded offline OFD XML-structure and stored-text reader; no rendering or disk extraction."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import zipfile
from xml.parsers import expat

# -I excludes the script directory. Load only this installation's bundled modules.
_SCRIPT_ROOT = str(Path(__file__).resolve().parent)
sys.path.insert(0, _SCRIPT_ROOT)

# Preserve the existing API names and shared exception/limit identities.
from ofd_common import (
    KNOWN_VERSIONS, LIMITS, NS, ReadError, ZLIB_ERROR,
    confined_descriptor, confined_environment, decode_text, environment,
    is_ofd, local, ofd_nodes, q, read_input, resolve, safe_name, single_child, text_of,
)
from ofd_package import Package, declare_payloads
from ofd_structure import catalogue_structure, parents_chain


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
                            obj = dict(kind=name, source=object_source, ordinal=ordinal,
                                       storage_node=package.node_indexes[object_source][node],
                                       attributes=dict(node.attrib))
                            if name == 'TextObject':
                                obj['glyph_transform_nodes'] = [package.node_indexes[object_source][child]
                                                                for child in node.findall(q('CGTransform'))]
                            append_to(page['objects'], obj)
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
