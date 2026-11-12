"""Read OFD text by page by default; request --view full for XML structure. Offline and read-only."""
import argparse
import base64
import hashlib
import hmac
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


def _read_ofd(path, *, task_root=None, version_policy="known", document=None,
              page_start=None, page_end=None, text_only=False):
    environment()
    if version_policy not in ('known', 'compatible'):
        raise ReadError('invalid version policy')
    range_document = document
    ranged = any(value is not None for value in (range_document, page_start, page_end))
    if ranged and (type(range_document) is not int or range_document < 1
                   or type(page_start) is not int or page_start < 1
                   or (page_end is not None and (type(page_end) is not int or page_end < page_start))):
        raise ReadError('invalid read range')
    data = read_input(Path(path), task_root=task_root)
    package = Package(data, retain_structure=not text_only)
    result = dict(schema_version='ofd-read-3', status='complete', scope='normalized-xml-structure-and-stored-text',
                  sha256=hashlib.sha256(data).hexdigest(), documents=[], diagnostics=[],
                  limits=LIMITS.copy(), order='XML storage order; not semantic reading order',
                  signature_verification='not_performed', tables=[],
                  tables_assessment=dict(status='not_interpreted',
                                         scope='Explicit XML remains in structure; no layout or semantic grid inferred'),
                  members=[] if ranged else [
                      dict(path=n, bytes=i.file_size, compressed_bytes=i.compress_size,
                           compression_method=i.compress_type, declared_crc32=format(i.CRC, '08x'),
                           flags=i.flag_bits, is_directory=i.is_dir())
                      for n, i in package.members.items()])

    package.account(result)
    diagnostic_groups = {}

    def append_to(collection, record):
        package.account(record)
        collection.append(record)

    def issue(code, source, detail=''):
        result['status'] = 'partial'
        key = (code, source, detail)
        if text_only and key in diagnostic_groups:
            record = diagnostic_groups[key]
            record['count'] = record.get('count', 1) + 1
            return
        record = dict(code=code, source=source, detail=detail)
        append_to(result['diagnostics'], record)
        if text_only:
            diagnostic_groups[key] = record

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
    if ranged:
        result['document_count'] = len(bodies)
    if ranged and range_document > len(bodies):
        raise ReadError('document index outside range')
    for body_index, body in enumerate(bodies, 1):
        if ranged and body_index != range_document:
            continue
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
            for info_node in (() if text_only else ofd_nodes(doc_info)):
                append_to(doc['info'], dict(tag=info_node.tag, attributes=dict(info_node.attrib), text=info_node.text or ''))
            if ranged:
                titles = doc_info.findall(q('Title'))
                if len(titles) == 1 and len(titles[0]) == 0:
                    title = text_of(titles[0])
                    title_fields = ({'title': title} if len(json.dumps(title, ensure_ascii=False)) <= 256
                                    else {'title_omitted': True})
                    package.account(title_fields)
                    doc.update(title_fields)
        doc['revision_entries'] = []
        if versions is not None and not ranged:
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
                    if not text_only:
                        append_to(doc['resources'], dict(source=res_path, kind=local(node.tag),
                                                         attributes=dict(node.attrib), files=files, scope=scope))

        if common is not None:
            for entry in common.findall(q('TemplatePage')):
                ident = entry.get('ID')
                if not ident or ident in templates:
                    raise ReadError('duplicate/missing template ID')
                value = entry.get('BaseLoc', '')
                target = package.reference(doc_path, value, missing_ok=text_only)
                if target is None:
                    # Selected pages that use this path still fail in xml().
                    # Successful scoped reads retain this unrelated index gap.
                    target = resolve(doc_path, value)
                    issue('missing_unselected_template', doc_path,
                          'Declared template absent outside the returned page dependencies: ' + target)
                templates[ident] = target
            for kind in ('PublicRes', 'DocumentRes'):
                for entry in common.findall(q(kind)):
                    load_resources(doc_path, text_of(entry), resource_ids, resource_defs, 'document', kind)
        # Preserve related XML as metadata only; never treat its text as page text.
        for kind in (() if ranged else ('Attachments', 'Annotations', 'CustomTags', 'Extensions')):
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
                        package.reference(meta_path, text_of(node))
                        if kind == 'Annotations':
                            annot_path, annot_nodes = package.metadata(meta_path, text_of(node), 'PageAnnot')
                            append_to(doc['metadata'], dict(kind='PageAnnotations', source=annot_path, nodes=annot_nodes))
        signature = None if ranged else single_child(body, 'Signatures')
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
        if ranged and page_end is not None and page_end > len(pages):
            raise ReadError('page end index outside selected document')
        if ranged and page_start > len(pages):
            raise ReadError('page index outside selected document')
        for entry in pages:
            if entry.tag != q('Page') or not entry.get('ID') or entry.get('ID') in page_ids:
                raise ReadError('invalid/duplicate page ID')
            page_ids.add(entry.get('ID'))
            page_count += 1
            if page_count > LIMITS['pages']:
                raise ReadError('limit: pages')
            if ranged and not (page_start <= page_count <= (page_end or page_start)):
                continue
            page_path = package.reference(doc_path, entry.get('BaseLoc', ''))
            page_number = page_count if ranged else len(doc['pages']) + 1
            page = dict(number=page_number, id=entry.get('ID'), source=page_path,
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
                    template_record = dict(id=ident, source=target)
                    if not text_only:
                        template_record['attributes'] = dict(template.attrib)
                    append_to(page['template_refs'], template_record)
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
                            if not text_only:
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
                            if not text_only:
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
                                record = dict(text=decoded, source=object_source, template=is_template)
                                if not text_only:
                                    record.update(raw_text=raw_text, object_id=node.get('ID'),
                                        object_ordinal=ordinal, code_index=code_index,
                                        storage_node=package.node_indexes[object_source][code],
                                        object_attributes=dict(node.attrib), code_attributes=dict(code.attrib),
                                        coordinate_space='raw local millimetres; transforms not applied', occurrence=list(chain),
                                        ancestry=[dict(tag=ancestor_node.tag, attributes=dict(ancestor_node.attrib))
                                                  for ancestor_node in ancestor_chain])
                                append_to(page['text_runs'], record)
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
                                next_chain = chain
                                if not text_only:
                                    step = dict(source=object_source, object_id=node.get('ID'),
                                                resource_id=ident, attributes=dict(node.attrib))
                                    next_chain += (step,)
                                walk_objects(target, target_source, next_chain, active + (key,))
                        elif name == 'ImageObject':
                            resource_id = node.get('ResourceID')
                            if local_ids.get(resource_id) != 'MultiMedia':
                                issue('unresolved_image_resource', object_source, resource_id or '')
                            if not text_only:
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
        if ranged:
            doc['page_count'] = page_count
        for node in (() if ranged else document):
            if local(node.tag) not in ('CommonData', 'Pages', 'Attachments', 'Annotations', 'CustomTags', 'Extensions'):
                issue('document_feature_not_interpreted', doc_path, local(node.tag))
    if text_only:
        # No structure catalogue, XML event log, object attributes, ancestry,
        # cross-indexes or member inventories are built on the reading path.
        for (source, tag), count in package.namespace_issues.items():
            issue('foreign_namespace', source, tag + ' count=' + str(count))
        result['range'] = dict(document=range_document, page_start=page_start,
                               page_end=page_start if page_end is None else page_end)
        result['parsed_member_count'] = len(package.cache)
        result['unexamined_member_count'] = len(package.members) - len(package.cache)
        package.archive.close()
        return result
    if ranged:
        result['structure'] = dict(status='not_read', scope='selected pages only; full structure not read',
                                   xml_parts=[], opaque_payloads=[], character_field_count=0,
                                   identifiers=[], reference_fields=[], namespace_resolution='selected_xml_only',
                                   member_inventory='ZIP metadata inspected; non-selected XML payloads not read')
        result['text_sources'] = []
    else:
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
    if ranged:
        result['text_assessment'] = dict(status='selected_range_only', gap_codes=gaps,
            stored_textcode_count=sum(len(page['text_runs']) for doc in result['documents'] for page in doc['pages']),
            page_occurrence_count=sum(len(page['text_runs']) for doc in result['documents'] for page in doc['pages']),
            scope='Selected page occurrences only; unselected XML, annotations and structure were not checked')
        result['scope'] = 'selected-page-stored-text-only'
        result['range'] = dict(document=range_document, page_start=page_start,
                               page_end=page_start if page_end is None else page_end)
        result['coverage'] = 'Selected document/page range; no full-document XML coverage claim'
        result['unexamined_members'] = [name for name in package.members if name not in package.cache]
        result['parsed_members'] = list(package.cache)
    package.account(result['text_assessment'])
    for (source, tag), count in package.namespace_issues.items():
        issue('foreign_namespace', source, tag + ' count=' + str(count))
    result['references'] = package.evidence
    result['parsed_xml'] = list(package.cache)
    result['unparsed_members'] = [name for name in package.members if name not in package.cache]
    for name in result['unparsed_members']:
        if not ranged and name.lower().endswith('.xml') and name not in package.opaque_members:
            issue('unparsed_xml_member', name)
    for doc in result['documents']:
        package.account(doc['template_sources'])
    package.account(result['parsed_xml'])
    package.account(result['unparsed_members'])
    if not ranged:
        result['coverage'] = 'Normalized XML structure and all stored OFD TextCode sources; opaque payloads not decoded; no schema/rendering certification'
    package.archive.close()
    return result


KNOWN_ELEMENTS = set((
    'Page Area PhysicalBox ApplicationBox ContentBox BleedBox Template Content Layer PageBlock '
    'TextObject TextCode CGTransform Glyphs FillColor StrokeColor ColorValue Pattern AxialShd RadialShd '
    'GouraudShd LaGouraudShd Border Clipped Clip Clips Path AbbreviatedData ImageObject PathObject '
    'CompositeObject CompositeGraphicUnit Thumbnail Substitution Actions Action PageRes'
).split())


def capabilities():
    """Describe the interface transition without reading a document."""
    return dict(interface_version=2, default_view='reading', default_reading_format='content',
                default_document=1, default_page=1, default_max_output_chars=12000,
                reading_schema='ofd-content-2', detail_schema='ofd-reading-1',
                structure_schema='ofd-read-3', structure_api='read_ofd_structure',
                structure_cli='--view full', read_only=True)


def read_ofd_structure(path, *, task_root=None, version_policy='known', document=None,
                       page_start=None, page_end=None):
    """Explicit compatibility API for the former read_ofd() structural result."""
    return _read_ofd(path, task_root=task_root, version_policy=version_policy,
                     document=document, page_start=page_start, page_end=page_end)


def read_ofd(path, *, task_root=None, version_policy='known', view='reading', document=None,
             page_start=None, page_end=None, text_layer='all', reading_format=None,
             max_chars=None, cursor=None):
    """Read bounded page text by default; interface v2 requires explicit full structure."""
    if view == 'full':
        if (text_layer != 'all' or reading_format is not None or max_chars is not None
                or cursor is not None):
            raise ReadError('reading options require reading view')
        return read_ofd_structure(path, task_root=task_root, version_policy=version_policy,
                                  document=document, page_start=page_start, page_end=page_end)
    if view != 'reading':
        raise ReadError('API view must be reading or full')
    document = 1 if document is None else document
    page_start = 1 if page_start is None else page_start
    result = _read_ofd(path, task_root=task_root, version_policy=version_policy,
                       document=document, page_start=page_start, page_end=page_end, text_only=True)
    return project_reading(result, document=document, page=page_start, page_end=page_end,
                           text_layer=text_layer, reading_format='content' if reading_format is None else reading_format,
                           max_chars=12000 if max_chars is None else max_chars, cursor=cursor)


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


def _cursor_payload(cursor):
    """Decode a canonical, checksummed navigation cursor."""
    if not isinstance(cursor, str) or len(cursor) > 4096 or cursor.count('.') != 1:
        raise ReadError('invalid cursor')
    encoded, checksum = cursor.split('.')
    try:
        raw = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        supplied = base64.urlsafe_b64decode(checksum + '=' * (-len(checksum) % 4))
        if (base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=') != encoded
                or base64.urlsafe_b64encode(supplied).decode('ascii').rstrip('=') != checksum
                or not hmac.compare_digest(hashlib.sha256(raw).digest(), supplied)):
            raise ValueError('non-canonical or mismatched cursor')
        payload = json.loads(raw.decode('utf-8'))
        if json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode() != raw:
            raise ValueError('non-canonical cursor body')
        expected = {'sha256', 'document', 'page', 'page_end', 'text_layer', 'index', 'offset'}
        if (not isinstance(payload, dict) or set(payload) not in (expected, expected | {'format'})
                or not isinstance(payload['sha256'], str)
                or any(type(payload[key]) is not int for key in ('document', 'page', 'page_end', 'index', 'offset'))
                or payload['text_layer'] not in ('all', 'page', 'template')
                or ('format' in payload and payload['format'] != 'ofd-content-2')):
            raise ValueError('invalid cursor fields')
        return payload
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadError('invalid cursor') from exc


def _make_cursor(payload):
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')
    checksum = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode('ascii').rstrip('=')
    return encoded + '.' + checksum


def project_reading(result, *, document, page, page_end=None, text_layer='all',
                    max_chars=12000, cursor=None, reading_format='detail'):
    """Return a compact, resumable projection of selected page text runs."""
    if reading_format not in ('detail', 'content'):
        raise ReadError('invalid reading format')
    if text_layer not in ('all', 'page', 'template'):
        raise ReadError('invalid text layer')
    if type(document) is not int or type(page) is not int or document < 1 or page < 1:
        raise ReadError('invalid reading scope')
    if page_end is None:
        page_end = page
    if type(page_end) is not int or page_end < page:
        raise ReadError('invalid page range')
    if type(max_chars) is not int or not 512 <= max_chars <= 2 * 1024 * 1024:
        raise ReadError('view budget must be 512..2097152 characters')
    if result.get('status') not in ('complete', 'partial'):
        return result
    documents = result['documents']
    source_range = result.get('range')
    if source_range is None:
        if document > len(documents):
            raise ReadError('document index outside range')
        selected_doc = documents[document - 1]
    else:
        if source_range.get('document') != document:
            raise ReadError('cursor source or scope changed')
        if (page < source_range.get('page_start', 0)
                or page_end > source_range.get('page_end', 0)):
            raise ReadError('requested pages were not parsed')
        selected_doc = documents[0]
    pages = selected_doc['pages']
    if page_end > selected_doc.get('page_count', len(pages)):
        raise ReadError('page index outside selected document')
    records = []
    excluded_templates = 0
    for selected_page in pages:
        if not page <= selected_page['number'] <= page_end:
            continue
        page_text = []
        for run in selected_page['text_runs']:
            if text_layer == 'page' and run['template']:
                excluded_templates += 1
                continue
            if text_layer == 'template' and not run['template']:
                excluded_templates += 1
                continue
            if reading_format == 'content':
                page_text.append(run['text'])
            else:
                records.append(dict(document=document, page=selected_page['number'],
                                    index=len(records), text=run['text'], source=run['source'],
                                    template=run['template']))
        if reading_format == 'content':
            records.append(dict(page=selected_page['number'], text=''.join(page_text)))
    position = 0
    offset = 0
    binding = dict(sha256=result['sha256'], document=document, page=page,
                   page_end=page_end, text_layer=text_layer)
    if reading_format == 'content':
        binding['format'] = 'ofd-content-2'
    if cursor is not None:
        payload = _cursor_payload(cursor)
        if payload.get('format') != binding.get('format') or any(payload.get(key) != value for key, value in binding.items()):
            raise ReadError('cursor source or scope changed')
        position = payload.get('index')
        offset = payload.get('offset')
        if (type(position) is not int or type(offset) is not int or position < 0
                or position > len(records) or offset < 0
                or (position == len(records) and offset != 0)
                or (position < len(records) and offset > len(records[position]['text']))):
            raise ReadError('cursor outside reading range')
    view = dict(schema_version='ofd-reading-1', source_sha256=result['sha256'],
                status=result['status'], diagnostics=result['diagnostics'],
                scope=dict(document=document, page=page, page_end=page_end,
                           text_layer=text_layer, selection='1-based page storage order'),
                coverage='selected_page_text_occurrences_only',
                selected_occurrences=len(records), excluded_occurrences=excluded_templates,
                parsed_member_count=result.get('parsed_member_count', len(result.get('parsed_members', []))),
                unexamined_member_count=result.get('unexamined_member_count', len(result.get('unexamined_members', []))),
                omitted_member_inventory=True,
                records=[], done=False)
    record_fields = ('document', 'page', 'index', 'source', 'template')
    if reading_format == 'content':
        view['schema_version'] = 'ofd-content-2'
        for field in ('selected_occurrences', 'excluded_occurrences', 'parsed_member_count',
                      'unexamined_member_count', 'omitted_member_inventory'):
            del view[field]
        view['scope'].pop('selection')
        view['scope']['document_count'] = result.get('document_count', len(documents))
        view['scope']['page_count'] = selected_doc.get('page_count', len(pages))
        if not view['diagnostics']:
            del view['diagnostics']
        title = selected_doc.get('title') or None
        if selected_doc.get('title_omitted') or (title is not None and
                len(json.dumps(title, ensure_ascii=False)) > max_chars // 8):
            view['title_omitted'] = True
            title = None
        view['title'] = title
        record_fields = ('page',)
    base_size = len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1
    if base_size > max_chars:
        raise ReadError('reading metadata or diagnostics exceed output budget')
    while position < len(records):
        record = records[position]
        available = record['text'][offset:]
        output_record = {key: record[key] for key in record_fields}
        if not available:
            candidate = dict(output_record, text='', character_offset=offset)
            view['records'].append(candidate)
            position += 1
            offset = 0
            view['done'] = position >= len(records)
            if not view['done']:
                view['next_cursor'] = _make_cursor(dict(binding, index=position, offset=0))
            else:
                view.pop('next_cursor', None)
            if len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1 > max_chars:
                view['records'].pop()
                position -= 1
                view.pop('next_cursor', None)
                view['done'] = False
                if not view['records']:
                    raise ReadError('reading record metadata exceeds output budget')
                break
            continue
        full_record = dict(output_record, text=available, character_offset=offset)
        view['records'].append(full_record)
        full_next_position = position + (offset + len(available) == len(record['text']))
        view['done'] = full_next_position >= len(records)
        if not view['done']:
            view['next_cursor'] = _make_cursor(dict(binding, index=full_next_position, offset=0))
        else:
            view.pop('next_cursor', None)
        if len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1 <= max_chars:
            position = full_next_position
            offset = 0
            if view['done']:
                view.pop('next_cursor', None)
            continue
        view['records'].pop()
        view.pop('next_cursor', None)
        view['done'] = False
        low = 0
        high = max(0, len(available) - 1)
        while low < high:
            middle = (low + high + 1) // 2
            next_position = position
            next_offset = offset + middle
            candidate = dict(output_record, text=available[:middle], character_offset=offset)
            view['records'].append(candidate)
            view['done'] = next_position >= len(records)
            if not view['done']:
                view['next_cursor'] = _make_cursor(dict(binding, index=next_position,
                                                        offset=next_offset))
            encoded_size = len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1
            view['records'].pop()
            view.pop('next_cursor', None)
            view['done'] = False
            if encoded_size <= max_chars:
                low = middle
            else:
                high = middle - 1
        if low == 0:
            if not view['records']:
                raise ReadError('reading budget cannot fit one character and its cursor')
            break
        candidate = dict(output_record, text=available[:low], character_offset=offset)
        view['records'].append(candidate)
        completed = offset + low == len(record['text'])
        position += completed
        offset = 0 if completed else offset + low
        if len(view['records']) and position < len(records):
            view['next_cursor'] = _make_cursor(dict(binding, index=position, offset=offset))
        view['done'] = position >= len(records)
        if not completed:
            break
    view['done'] = position >= len(records)
    if view['done']:
        view.pop('next_cursor', None)
    else:
        view['next_cursor'] = _make_cursor(dict(binding, index=position, offset=offset))
    if len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1 > max_chars:
        raise ReadError('reading output exceeds budget')
    return view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--capabilities', action='store_true', help='show interface version and defaults without reading input')
    parser.add_argument('--version-policy', choices=('known', 'compatible'), default='known')
    parser.add_argument('--view', choices=('full', 'summary', 'page-text', 'reading'), help='default: reading; full explicitly requests XML structure')
    parser.add_argument('--document', type=int, help='1-based document, default 1 for reading')
    parser.add_argument('--page', type=int, help='1-based page, default 1 for reading; continue subsequent pages explicitly')
    parser.add_argument('--page-end', type=int)
    parser.add_argument('--text-layer', choices=('all', 'page', 'template'), default='all')
    parser.add_argument('--reading-format', choices=('detail', 'content'), help='default: content; detail adds provenance')
    parser.add_argument('--cursor')
    parser.add_argument('--max-output-chars', type=int)
    parser.add_argument('--source-sha256')
    args = parser.parse_args(argv)
    view = args.view or 'reading'
    scoped = view != 'full'
    extra_view_options = any(value is not None for value in (
        args.document, args.page, args.page_end, args.max_output_chars, args.source_sha256,
        args.cursor, args.reading_format
    )) or args.text_layer != 'all'
    if args.capabilities:
        if args.input or args.check or args.view is not None or extra_view_options:
            parser.error('--capabilities cannot be combined with input, --check or view options')
        print(json.dumps(capabilities(), ensure_ascii=False))
        return 0
    if (not scoped and extra_view_options) or (args.check and (args.view is not None or extra_view_options)):
        parser.error('view options require a non-full view and cannot be used with --check')
    if view == 'page-text' and (args.document is None or args.page is None):
        parser.error('page-text requires --document and --page')
    if view != 'reading' and (
            args.page_end is not None or args.cursor is not None
            or args.text_layer != 'all' or args.reading_format is not None):
        parser.error('--page-end, --cursor, --text-layer, and --reading-format require --view reading')
    if view == 'reading' and args.source_sha256 is not None:
        parser.error('--source-sha256 is not used with reading; cursor carries the source fingerprint')
    if view == 'summary' and args.page is not None:
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
        if view == 'reading':
            result = read_ofd(args.input, task_root='.', version_policy=args.version_policy,
                              document=args.document, page_start=args.page,
                              page_end=args.page_end, text_layer=args.text_layer,
                              max_chars=args.max_output_chars, cursor=args.cursor,
                              reading_format=args.reading_format)
        else:
            result = read_ofd_structure(args.input, task_root='.', version_policy=args.version_policy)
        if scoped and view != 'reading':
            result = project_output(result, view, document=args.document, page=args.page,
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
