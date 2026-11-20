"""Catalogue stored XML evidence and project bounded reading views."""
import base64
import hashlib
import hmac
import json
import re

from ofd_common import NS, ReadError, decode_text, is_ofd, local, q
from ofd_package import declare_payloads

_REF_ATTRS = (
    'BaseLoc', 'FileRef', 'ResourceID', 'Font', 'DrawParam', 'ColorSpace', 'TemplateID', 'URI',
)
_REF_TEXT_TAGS = (
    'DocRoot', 'PublicRes', 'DocumentRes', 'PageRes', 'FileLoc', 'FontFile',
    'MediaFile', 'Annotations', 'Attachments', 'CustomTags', 'Extensions', 'Signatures', 'SchemaLoc',
)


def _discover_additional_xml_parts(package):
    """Parse remaining XML members in the ZIP that were not reached by DocRoot/DocBody."""
    for source, root in list(package.cache.items()):
        declare_payloads(package, source, root)
    if set(package.opaque_members) & set(package.cache):
        raise ReadError('conflicting XML part and opaque payload roles')

    for source, info in package.members.items():
        if info.is_dir() or source in package.cache or source in package.opaque_members:
            continue
        if not source.lower().endswith(('.xml', '.xsd')):
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


def _catalogue_textcode_node(node, source, root_tag, index, parent, parents, indexes, occurrence_index, issue):
    """Extract stored TextCode evidence record while keeping trust boundary."""
    ancestors = []
    ancestor = parent
    while ancestor is not None:
        ancestors.insert(0, indexes[ancestor])
        ancestor = parents.get(ancestor)
    raw = ''.join(node.itertext())
    decoded, problems = decode_text(raw)
    trusted = (parent is not None and parent.tag == q('TextObject')
               and all(is_ofd(item.tag) for item in parents_chain(node, parents)))
    if not trusted:
        problems.append('uninterpreted_textcode_context')
    if len(node):
        problems.append('nested_textcode')
    for problem in problems:
        issue(problem, source, 'storage_node=' + str(index))
    return dict(
        source=source, storage_node=index, root_tag=root_tag,
        object_id=parent.get('ID') if parent is not None else None,
        raw_text=raw, text=decoded if trusted else None,
        code_attributes=dict(node.attrib), ancestors=ancestors,
        occurrences=occurrence_index.get((source, index), []),
        interpretation='complete' if not problems else 'partial',
    )


def _catalogue_xml_part(source, root, package, original_parts, validated_files,
                        occurrence_index, identifiers, reference_fields, text_sources, issue):
    """Account and collect normalized nodes, references, identifiers, and TextCode entries for one XML part."""
    indexes = package.node_indexes[source]
    parents = {child: parent for parent in root.iter() for child in parent}
    nodes = []
    character_field_count = 0
    for node in root.iter():
        parent = parents.get(node)
        index = indexes[node]
        record = dict(index=index, parent=indexes[parent] if parent is not None else None,
                      tag=node.tag, attributes=dict(node.attrib),
                      text=node.text or '', tail=node.tail or '')
        character_field_count += bool(record['text']) + bool(record['tail'])
        package.account(record)
        nodes.append(record)
        if 'ID' in node.attrib:
            ident = dict(source=source, storage_node=index, tag=node.tag, value=node.get('ID'))
            package.account(ident)
            identifiers.append(ident)
        fields = [(name, value, 'attribute') for name, value in node.attrib.items() if name in _REF_ATTRS]
        if local(node.tag) in _REF_TEXT_TAGS:
            fields.append((node.tag, node.text or '', 'text'))
        for field, value, kind in fields:
            targets = sorted(validated_files.get((source, value.strip()), ()))
            reference = dict(source=source, storage_node=index, field=field, value=value, kind=kind,
                             matching_file_targets=targets,
                             interpretation='matching_file_value_only' if targets else 'raw_context_required')
            package.account(reference)
            reference_fields.append(reference)
        if node.tag == q('TextCode'):
            text_record = _catalogue_textcode_node(
                node, source, root.tag, index, parent, parents, indexes, occurrence_index, issue)
            package.account(text_record)
            text_sources.append(text_record)
    part = dict(source=source, sha256=package.xml_digests[source], nodes=nodes,
                events=package.xml_events[source], active_route=source in original_parts,
                namespace_mode=package.xml_modes[source])
    # Node records are already charged; charge all event strings and wrapper.
    package.account(dict(source=source, sha256=part['sha256'], events=part['events'],
                         active_route=part['active_route'], namespace_mode=part['namespace_mode']))
    if source not in original_parts:
        issue('additional_xml_part_catalogued', source, root.tag)
    if part['namespace_mode'] == 'raw_names':
        issue('xml_namespace_unresolved', source, 'Raw XML names retained; not interpreted as OFD objects')
    return part, character_field_count


def catalogue_structure(package, documents, issue):
    """Read normalized XML structure without activating actions or opaque payloads.

    The catalogue describes storage once per node. Page text_runs describe actual
    occurrences and remain a separate view. Structural coverage is not XSD or
    rendering/semantic certification.
    """
    original_parts = set(package.cache)
    _discover_additional_xml_parts(package)

    occurrence_index = {}
    for document_index, document in enumerate(documents):
        for page_index, page in enumerate(document['pages']):
            for run_index, run in enumerate(page['text_runs']):
                occurrence_index.setdefault((run['source'], run['storage_node']), []).append(
                    dict(document=document_index, page=page_index, run=run_index))

    validated_files = {}
    for reference in package.evidence:
        validated_files.setdefault((reference['source'], reference['value']), set()).add(reference['target'])

    parts, text_sources, identifiers, reference_fields = [], [], [], []
    character_field_count = 0
    for source, root in package.cache.items():
        part, chars_added = _catalogue_xml_part(
            source, root, package, original_parts, validated_files,
            occurrence_index, identifiers, reference_fields, text_sources, issue)
        parts.append(part)
        character_field_count += chars_added

    payloads = [dict(source=source, role=role, bytes=package.members[source].file_size)
                for source, role in package.opaque_members.items()]
    package.account(payloads)
    structure = dict(
        status='complete', scope='normalized XML storage structure, not schema/semantic certification',
        xml_parts=parts, opaque_payloads=payloads, character_field_count=character_field_count,
        identifiers=identifiers, reference_fields=reference_fields,
        reference_validation='Supported file routes only; raw candidate fields do not imply resolved links',
        namespace_resolution='partial' if 'raw_names' in package.xml_modes.values() else 'complete',
        member_inventory='all ZIP members; non-XML payloads are opaque',
    )
    unclassified = [name for name, info in package.members.items()
                    if not info.is_dir() and name not in package.cache and name not in package.opaque_members]
    structure['unclassified_members'] = unclassified
    package.account(unclassified)
    missing_indexes = sum(len(document['resource_gaps']) for document in documents)
    structure['missing_resource_index_count'] = missing_indexes
    package.account(dict(missing_resource_index_count=missing_indexes))
    if missing_indexes or unclassified:
        structure['status'] = 'partial'
    for name in unclassified:
        issue('unclassified_payload', name, 'Member inventoried; payload format not interpreted')
    return structure, text_sources


def parents_chain(node, parents):
    """Yield the node and its actual ancestors without inferring context from local names."""
    while node is not None:
        yield node
        node = parents.get(node)


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
    if source_sha256 is not None and (not isinstance(source_sha256, str)
                                      or not re.fullmatch(r'[0-9a-fA-F]{64}', source_sha256, re.ASCII)):
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
    view.update(schema_version='ofd-view-1', source_schema_version=result['schema_version'],
                source_sha256=result['sha256'], mode=mode,
                scope=dict(document=document, page=page, indices='1-based storage order'),
                coverage='overview_only_no_text' if mode == 'summary' else 'selected_page_occurrences_only',
                source_assessment_scope='entire source; not a completeness claim for selected text',
                structure_status=result['structure']['status'],
                namespace_resolution=result['structure']['namespace_resolution'],
                omitted_top_level_fields=[key for key in result if key not in
                                          ('status', 'diagnostics', 'format', 'text_assessment',
                                           'signature_verification', 'documents')],
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
            entry['pages'][0]['omitted_fields'] = [
                key for key in selected if key not in ('number', 'id', 'source', 'text_runs')]
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


def _select_reading_records(result, document, page, page_end, text_layer, reading_format):
    """Select document/page scope and flatten runs according to text_layer and reading_format."""
    documents = result['documents']
    source_range = result.get('range')
    if source_range is None:
        if document > len(documents):
            raise ReadError('document index outside range')
        selected_doc = documents[document - 1]
    else:
        if source_range.get('document') != document:
            raise ReadError('cursor source or scope changed')
        if page < source_range.get('page_start', 0) or page_end > source_range.get('page_end', 0):
            raise ReadError('requested pages were not parsed')
        selected_doc = documents[0]
    pages = selected_doc['pages']
    if page_end > selected_doc.get('page_count', len(pages)):
        raise ReadError('page index outside selected document')
    records, excluded_templates = [], 0
    for selected_page in pages:
        if not page <= selected_page['number'] <= page_end:
            continue
        page_text = []
        for run in selected_page['text_runs']:
            if (text_layer == 'page' and run['template']) or (text_layer == 'template' and not run['template']):
                excluded_templates += 1
                continue
            if reading_format == 'content':
                page_text.append(run['text'])
            else:
                records.append(dict(document=document, page=selected_page['number'], index=len(records),
                                    text=run['text'], source=run['source'], template=run['template']))
        if reading_format == 'content':
            records.append(dict(page=selected_page['number'], text=''.join(page_text)))
    return records, documents, selected_doc, excluded_templates


def _resolve_reading_cursor(cursor, binding, records):
    """Validate and unpack starting record index and character offset from a cursor."""
    if cursor is None:
        return 0, 0
    payload = _cursor_payload(cursor)
    if payload.get('format') != binding.get('format') or any(
            payload.get(key) != value for key, value in binding.items()):
        raise ReadError('cursor source or scope changed')
    position, offset = payload.get('index'), payload.get('offset')
    if (type(position) is not int or type(offset) is not int or position < 0 or position > len(records)
            or offset < 0 or (position == len(records) and offset != 0)
            or (position < len(records) and offset > len(records[position]['text']))):
        raise ReadError('cursor outside reading range')
    return position, offset


def _build_reading_view_header(result, selected_doc, documents, records, excluded_templates,
                               document, page, page_end, text_layer, reading_format, max_chars):
    """Construct the initial bounded reading view header and validate base budget."""
    view = dict(
        schema_version='ofd-reading-1', source_sha256=result['sha256'], status=result['status'],
        diagnostics=result['diagnostics'],
        scope=dict(document=document, page=page, page_end=page_end,
                   text_layer=text_layer, selection='1-based page storage order'),
        coverage='selected_page_text_occurrences_only',
        selected_occurrences=len(records), excluded_occurrences=excluded_templates,
        parsed_member_count=result.get('parsed_member_count', len(result.get('parsed_members', []))),
        unexamined_member_count=result.get('unexamined_member_count', len(result.get('unexamined_members', []))),
        omitted_member_inventory=True, records=[], done=False,
    )
    record_fields = ('document', 'page', 'index', 'source', 'template')
    if reading_format == 'content':
        view['schema_version'] = 'ofd-content-2'
        for field in ('selected_occurrences', 'excluded_occurrences', 'parsed_member_count',
                      'unexamined_member_count', 'omitted_member_inventory'):
            del view[field]
        view['scope'].pop('selection')
        view['scope']['document_count'] = result.get('document_count', len(documents))
        view['scope']['page_count'] = selected_doc.get('page_count', len(selected_doc['pages']))
        if not view['diagnostics']:
            del view['diagnostics']
        title = selected_doc.get('title') or None
        if selected_doc.get('title_omitted') or (
                title is not None and len(json.dumps(title, ensure_ascii=False)) > max_chars // 8):
            view['title_omitted'] = True
            title = None
        view['title'] = title
        record_fields = ('page',)
    if len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1 > max_chars:
        raise ReadError('reading metadata or diagnostics exceed output budget')
    return view, record_fields


def _fit_record_to_reading_budget(view, record, record_fields, position, offset,
                                  total_records, binding, max_chars):
    """Append next record (or binary-searched prefix) within max_chars; return (position, offset, stop)."""
    available = record['text'][offset:]
    output_record = {key: record[key] for key in record_fields}
    if not available:
        view['records'].append(dict(output_record, text='', character_offset=offset))
        position += 1
        offset = 0
        view['done'] = position >= total_records
        if not view['done']:
            view['next_cursor'] = _make_cursor(dict(binding, index=position, offset=0))
        else:
            view.pop('next_cursor', None)
        if len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1 > max_chars:
            view['records'].pop()
            view.pop('next_cursor', None)
            view['done'] = False
            if not view['records']:
                raise ReadError('reading record metadata exceeds output budget')
            return position - 1, 0, True
        return position, offset, False
    view['records'].append(dict(output_record, text=available, character_offset=offset))
    full_next_position = position + (offset + len(available) == len(record['text']))
    view['done'] = full_next_position >= total_records
    if not view['done']:
        view['next_cursor'] = _make_cursor(dict(binding, index=full_next_position, offset=0))
    else:
        view.pop('next_cursor', None)
    if len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1 <= max_chars:
        if view['done']:
            view.pop('next_cursor', None)
        return full_next_position, 0, False
    view['records'].pop()
    view.pop('next_cursor', None)
    view['done'] = False
    low, high = 0, max(0, len(available) - 1)
    while low < high:
        middle = (low + high + 1) // 2
        view['records'].append(dict(output_record, text=available[:middle], character_offset=offset))
        view['done'] = position >= total_records
        if not view['done']:
            view['next_cursor'] = _make_cursor(dict(binding, index=position, offset=offset + middle))
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
        return position, offset, True
    view['records'].append(dict(output_record, text=available[:low], character_offset=offset))
    completed = offset + low == len(record['text'])
    position += completed
    offset = 0 if completed else offset + low
    if view['records'] and position < total_records:
        view['next_cursor'] = _make_cursor(dict(binding, index=position, offset=offset))
    view['done'] = position >= total_records
    return position, offset, not completed


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
    records, documents, selected_doc, excluded_templates = _select_reading_records(
        result, document, page, page_end, text_layer, reading_format)
    binding = dict(sha256=result['sha256'], document=document, page=page,
                   page_end=page_end, text_layer=text_layer)
    if reading_format == 'content':
        binding['format'] = 'ofd-content-2'
    position, offset = _resolve_reading_cursor(cursor, binding, records)
    view, record_fields = _build_reading_view_header(
        result, selected_doc, documents, records, excluded_templates,
        document, page, page_end, text_layer, reading_format, max_chars)
    while position < len(records):
        position, offset, stop = _fit_record_to_reading_budget(
            view, records[position], record_fields, position, offset,
            len(records), binding, max_chars)
        if stop:
            break
    view['done'] = position >= len(records)
    if view['done']:
        view.pop('next_cursor', None)
    else:
        view['next_cursor'] = _make_cursor(dict(binding, index=position, offset=offset))
    if len(json.dumps(view, ensure_ascii=False, allow_nan=False)) + 1 > max_chars:
        raise ReadError('reading output exceeds budget')
    return view
