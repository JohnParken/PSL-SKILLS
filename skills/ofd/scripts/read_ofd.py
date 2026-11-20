"""Read OFD text by page by default; request --view full for XML structure. Offline and read-only."""
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
from ofd_structure import (
    _cursor_payload, _make_cursor, catalogue_structure, parents_chain,
    project_output, project_reading,
)

KNOWN_ELEMENTS = set((
    'Page Area PhysicalBox ApplicationBox ContentBox BleedBox Template Content Layer PageBlock '
    'TextObject TextCode CGTransform Glyphs FillColor StrokeColor ColorValue Pattern AxialShd RadialShd '
    'GouraudShd LaGouraudShd Border Clipped Clip Clips Path AbbreviatedData ImageObject PathObject '
    'CompositeObject CompositeGraphicUnit Thumbnail Substitution Actions Action PageRes'
).split())

_TEXT_GAP_CODES = frozenset((
    'surrogate_text_escape', 'uninterpreted_text_escape', 'text_placeholder_unresolved',
    'uninterpreted_textcode_context', 'nested_textcode', 'text_without_textcode',
    'glyph_mapping_not_interpreted', 'image_not_read', 'vector_not_interpreted',
    'uninterpreted_object', 'unknown_element', 'unresolved_composite_resource',
    'object_outside_content', 'unexpected_textcode_parent', 'no_explainable_text',
    'version_common_subset', 'missing_version_assumed', 'missing_resource_index',
))


def _validate_ofd_root_and_format(package, result, version_policy, ranged, range_document, issue):
    """Parse OFD.xml, validate root attributes/version policy, and return DocBody elements."""
    root = package.xml('OFD.xml', 'OFD')
    package.budget(root)
    version = root.get('Version', '1.0')
    doc_type = root.get('DocType', 'OFD')
    if doc_type not in ('OFD', 'OFD-A'):
        raise ReadError('unsupported: OFD document type')
    if (version not in KNOWN_VERSIONS
            and not (version_policy == 'compatible' and re.fullmatch(r'1\.[0-9]{1,3}', version, re.ASCII))):
        raise ReadError('unsupported: OFD version')
    result['format'] = dict(
        version=version, declared_version=root.get('Version'), doc_type=doc_type,
        version_policy=version_policy,
        interpretation='XML storage and literal text model; no schema, rendering or version conformance certification',
    )
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
    if ranged:
        result['document_count'] = len(bodies)
    if ranged and range_document > len(bodies):
        raise ReadError('document index outside range')
    return bodies


def _read_doc_info_and_versions(package, body, doc, ranged, text_only, append_to, issue):
    """Catalogue DocInfo attributes/title and passive DocVersion snapshots."""
    doc['info'] = []
    doc_info = single_child(body, 'DocInfo')
    if doc_info is not None:
        package.budget(doc_info)
        for info_node in (() if text_only else ofd_nodes(doc_info)):
            append_to(doc['info'], dict(tag=info_node.tag, attributes=dict(info_node.attrib),
                                        text=info_node.text or ''))
        if ranged:
            titles = doc_info.findall(q('Title'))
            if len(titles) == 1 and len(titles[0]) == 0:
                title = text_of(titles[0])
                title_fields = ({'title': title} if len(json.dumps(title, ensure_ascii=False)) <= 256
                                else {'title_omitted': True})
                package.account(title_fields)
                doc.update(title_fields)
    doc['revision_entries'] = []
    versions = single_child(body, 'Versions')
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
        issue('revision_snapshots_not_activated', 'OFD.xml',
              'Stored snapshots are catalogued, not overlaid onto the active document')


def _load_doc_resources(package, doc, source, value, ids, definitions, resource_records,
                        scope, kind, text_only, append_to, issue):
    """Load Res parts while keeping absent index recovery distinct from path/XML faults."""
    # Only absence of a resource index is recoverable. Path validation,
    # directories, malformed/present XML and payload-role conflicts still fail.
    res_path = package.reference(source, value, missing_ok=True)
    if res_path is None:
        target = resolve(source, value)
        append_to(doc['resource_gaps'], dict(kind=kind, source=source, value=value, target=target, scope=scope))
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


def _read_doc_metadata_and_signatures(package, body, document, doc, doc_path, ranged, append_to, issue):
    """Catalogue passive metadata XML trees and signature parts without activating them."""
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
                        package.xml(package.reference(meta_path, value), require_ofd=False)
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


def _record_text_object(node, object_source, ordinal, chain, ancestor_chain, local_ids,
                        package, page, is_template, text_only, append_to, issue):
    """Extract literal TextCode runs from a validated TextObject."""
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
            record.update(
                raw_text=raw_text, object_id=node.get('ID'), object_ordinal=ordinal,
                code_index=code_index, storage_node=package.node_indexes[object_source][code],
                object_attributes=dict(node.attrib), code_attributes=dict(code.attrib),
                coordinate_space='raw local millimetres; transforms not applied',
                occurrence=list(chain),
                ancestry=[dict(tag=ancestor_node.tag, attributes=dict(ancestor_node.attrib))
                          for ancestor_node in ancestor_chain],
            )
        append_to(page['text_runs'], record)


def _walk_page_objects(container, object_source, package, page, local_ids, local_defs,
                       text_only, is_template, append_to, issue, chain=(), active=()):
    """Traverse Content objects recursively with cycle/depth protection."""
    package.budget(container)
    parents = {child: parent for parent in container.iter() for child in parent}
    for ordinal, node in enumerate(container.iter()):
        name = local(node.tag)
        if not is_ofd(node.tag):
            continue
        ancestor = parents.get(node)
        ancestor_chain, foreign_ancestor, in_content = [], False, False
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
                           storage_node=package.node_indexes[object_source][node], attributes=dict(node.attrib))
                if name == 'TextObject':
                    obj['glyph_transform_nodes'] = [package.node_indexes[object_source][child]
                                                    for child in node.findall(q('CGTransform'))]
                append_to(page['objects'], obj)
            if name not in ('TextObject', 'ImageObject', 'PathObject', 'CompositeObject'):
                issue('uninterpreted_object', object_source, name)
        if name == 'TextObject':
            _record_text_object(node, object_source, ordinal, chain, ancestor_chain, local_ids,
                                package, page, is_template, text_only, append_to, issue)
        elif name == 'CompositeObject':
            ident = node.get('ResourceID')
            if local_ids.get(ident) != 'CompositeGraphicUnit':
                issue('unresolved_composite_resource', object_source, ident or '')
                continue
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
                next_chain = chain + (dict(source=object_source, object_id=node.get('ID'),
                                           resource_id=ident, attributes=dict(node.attrib)),)
            _walk_page_objects(target, target_source, package, page, local_ids, local_defs,
                               text_only, is_template, append_to, issue, next_chain, active + (key,))
        elif name == 'ImageObject':
            resource_id = node.get('ResourceID')
            if local_ids.get(resource_id) != 'MultiMedia':
                issue('unresolved_image_resource', object_source, resource_id or '')
            if not text_only:
                append_to(page['images'], dict(source=object_source, ordinal=ordinal, attributes=dict(node.attrib)))
            issue('image_not_read', object_source, 'OCR required for scanned text')
        elif name in ('Actions', 'Action'):
            issue('not_interpreted', object_source, name)
        elif name == 'PathObject':
            issue('vector_not_interpreted', object_source, name)
        elif name not in KNOWN_ELEMENTS and not name.endswith('Object'):
            issue('unknown_element', object_source, name)


def _visit_page_tree(source, page_path, package, doc, page, templates, resource_ids, resource_defs,
                     resource_records, used, text_only, append_to, issue, is_template=False):
    """Read a page or single-level template XML and record its content and template links."""
    if source in used:
        issue('duplicate_template_reference', source)
        return
    used.add(source)
    page_xml = package.xml(source, 'Page')
    local_ids = dict(resource_ids)
    local_defs = dict(resource_defs)
    for page_resource in page_xml.findall(q('PageRes')):
        _load_doc_resources(package, doc, source, text_of(page_resource), local_ids, local_defs,
                            resource_records, source, 'PageRes', text_only, append_to, issue)
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
        _visit_page_tree(target, page_path, package, doc, page, templates, resource_ids, resource_defs,
                         resource_records, used, text_only, append_to, issue, is_template=True)
    if source != page_path:
        doc['template_sources'][source] = True
    _walk_page_objects(page_xml, source, package, page, local_ids, local_defs,
                       text_only, is_template, append_to, issue)


def _read_single_doc_body(package, body, result, ranged, range_document, page_start, page_end,
                          page_count, text_only, append_to, issue):
    """Parse one DocBody including common resources, metadata, and selected pages."""
    doc_root = single_child(body, 'DocRoot')
    if doc_root is None and single_child(body, 'Versions') is None:
        raise ReadError('missing DocRoot')
    doc_path = package.reference('OFD.xml', text_of(doc_root)) if doc_root is not None else 'OFD.xml'
    doc = dict(source=doc_path, pages=[], resources=[], resource_gaps=[], metadata=[], template_sources={})
    append_to(result['documents'], doc)
    _read_doc_info_and_versions(package, body, doc, ranged, text_only, append_to, issue)
    if doc_root is None:
        issue('no_active_docroot', 'OFD.xml', 'Revision storage retained; no active revision selected implicitly')
        return page_count
    document = package.xml(doc_path, 'Document')
    package.budget(document)
    common = single_child(document, 'CommonData')
    templates, resource_ids, resource_defs, resource_records = {}, {}, {}, set()
    if common is not None:
        for entry in common.findall(q('TemplatePage')):
            ident = entry.get('ID')
            if not ident or ident in templates:
                raise ReadError('duplicate/missing template ID')
            value = entry.get('BaseLoc', '')
            target = package.reference(doc_path, value, missing_ok=text_only)
            if target is None:
                # Selected pages that use this path still fail in xml().
                target = resolve(doc_path, value)
                issue('missing_unselected_template', doc_path,
                      'Declared template absent outside the returned page dependencies: ' + target)
            templates[ident] = target
        for kind in ('PublicRes', 'DocumentRes'):
            for entry in common.findall(q(kind)):
                _load_doc_resources(package, doc, doc_path, text_of(entry), resource_ids, resource_defs,
                                    resource_records, 'document', kind, text_only, append_to, issue)
    _read_doc_metadata_and_signatures(package, body, document, doc, doc_path, ranged, append_to, issue)
    pages = single_child(document, 'Pages', required=True)
    if pages is None or not len(pages):
        raise ReadError('missing/empty Pages')
    if ranged and page_end is not None and page_end > len(pages):
        raise ReadError('page end index outside selected document')
    if ranged and page_start > len(pages):
        raise ReadError('page index outside selected document')
    page_ids = set()
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
        _visit_page_tree(page_path, page_path, package, doc, page, templates, resource_ids,
                         resource_defs, resource_records, set(), text_only, append_to, issue)
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
    return page_count


def _finalize_ofd_assessment(package, result, ranged, range_document, page_start, page_end, text_only, issue):
    """Build final structure/coverage/text_assessment metadata and close ZIP handle."""
    if text_only:
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
    gaps = sorted({diagnostic['code'] for diagnostic in result['diagnostics']
                   if diagnostic['code'] in _TEXT_GAP_CODES})
    if package.namespace_issues:
        gaps.append('foreign_namespace')
    if ranged:
        occ = sum(len(page['text_runs']) for doc in result['documents'] for page in doc['pages'])
        result['text_assessment'] = dict(
            status='selected_range_only', gap_codes=gaps, stored_textcode_count=occ, page_occurrence_count=occ,
            scope='Selected page occurrences only; unselected XML, annotations and structure were not checked')
        result['scope'] = 'selected-page-stored-text-only'
        result['range'] = dict(document=range_document, page_start=page_start,
                               page_end=page_start if page_end is None else page_end)
        result['coverage'] = 'Selected document/page range; no full-document XML coverage claim'
        result['unexamined_members'] = [name for name in package.members if name not in package.cache]
        result['parsed_members'] = list(package.cache)
    else:
        result['text_assessment'] = dict(
            status='empty' if not result['text_sources'] else ('partial' if gaps else 'complete'),
            gap_codes=gaps, stored_textcode_count=len(result['text_sources']),
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
    result = dict(
        schema_version='ofd-read-3', status='complete', scope='normalized-xml-structure-and-stored-text',
        sha256=hashlib.sha256(data).hexdigest(), documents=[], diagnostics=[], limits=LIMITS.copy(),
        order='XML storage order; not semantic reading order', signature_verification='not_performed',
        tables=[], tables_assessment=dict(status='not_interpreted',
                                          scope='Explicit XML remains in structure; no layout or semantic grid inferred'),
        members=[] if ranged else [
            dict(path=n, bytes=i.file_size, compressed_bytes=i.compress_size,
                 compression_method=i.compress_type, declared_crc32=format(i.CRC, '08x'),
                 flags=i.flag_bits, is_directory=i.is_dir())
            for n, i in package.members.items()],
    )
    package.account(result)
    diagnostic_groups = {}

    def append_to(collection, record):
        package.account(record)
        collection.append(record)

    def issue(code, source, detail=''):
        result['status'] = 'partial'
        key = (code, source, detail)
        if text_only and key in diagnostic_groups:
            diagnostic_groups[key]['count'] = diagnostic_groups[key].get('count', 1) + 1
            return
        record = dict(code=code, source=source, detail=detail)
        append_to(result['diagnostics'], record)
        if text_only:
            diagnostic_groups[key] = record

    bodies = _validate_ofd_root_and_format(package, result, version_policy, ranged, range_document, issue)
    page_count = 0
    for body_index, body in enumerate(bodies, 1):
        if ranged and body_index != range_document:
            continue
        page_count = _read_single_doc_body(
            package, body, result, ranged, range_document, page_start, page_end,
            page_count, text_only, append_to, issue)
    return _finalize_ofd_assessment(
        package, result, ranged, range_document, page_start, page_end, text_only, issue)


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
        if text_layer != 'all' or reading_format is not None or max_chars is not None or cursor is not None:
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
