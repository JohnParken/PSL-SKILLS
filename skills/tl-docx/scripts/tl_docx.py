"""Read, create, edit and validate DOCX with python-docx."""

import argparse
import base64
import binascii
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
from zipfile import BadZipFile

if __name__ == '__main__' and sys.flags.isolated:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

API_VERSION = 1
MAX_SPEC = 16 * 1024 * 1024
MIN_BUDGET = 512
CAPABILITIES = ['read-docx', 'read-dotx', 'source-bound-pages', 'create-docx', 'edit-docx', 'validate', 'archive',
                'threaded-comments', 'versioned-output', 'resume-cleanup', 'pagination-audit',
                'raw-ooxml', 'revision-views', 'report-export', 'table-header-mapping', 'template-replacement',
                'native-role-styles', 'parse-cache', 'tracked-text-replacement', 'part-bundle-roundtrip', 'layout-audit', 'lossless-record-fragments', 'content-first-read']



def encode(payload):
    """Encode one complete JSON response using the byte-budget representation."""
    return (json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')


def strict_object(pairs):
    """Reject duplicate specification and cursor keys."""
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('JSON objects cannot have duplicate keys')
    return result


def invalid_constant(value):
    """Reject non-JSON NaN and infinity tokens."""
    raise ValueError('Unsupported JSON number ' + value)


def decode(payload):
    """Decode strict UTF-8 JSON input."""
    return json.loads(payload.decode('utf-8'), object_pairs_hook=strict_object, parse_constant=invalid_constant)


def cursor_start(cursor, binding):
    """Reject cursors from a different source or selected view."""
    if cursor is None:
        return 0
    try:
        if len(cursor) > 512:
            raise ValueError('Cursor is too long')
        value = decode(base64.b64decode(cursor, altchars=b'-_', validate=True))
        if (isinstance(value, list) and len(value) == 2 and value[0] == binding
                and type(value[1]) is int and value[1] >= 0):
            return value[1]
    except (ValueError, binascii.Error) as exc:
        raise ValueError('Invalid read cursor') from exc
    raise ValueError('Read cursor belongs to another document or view')


def page(meta, records, budget, cursor, binding):
    """Produce the largest contiguous page that fits the UTF-8 byte budget."""
    start = cursor_start(cursor, binding)
    if start and start >= len(records):
        raise ValueError('Read cursor is outside the selected records')

    def frame(end):
        more = end < len(records)
        token = base64.urlsafe_b64encode(encode([binding, end])).decode('ascii') if more else None
        return {**meta, 'start': start, 'end': end, 'total': len(records), 'has_more': more,
                'next_cursor': token, 'records': records[start:end]}

    complete = frame(len(records))
    if budget is None or len(encode(complete)) <= budget:
        return complete
    low, high = start + 1, len(records) - 1
    best = None
    while low <= high:
        middle = (low + high) // 2
        candidate = frame(middle)
        if len(encode(candidate)) <= budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ValueError('Byte budget cannot hold response metadata and the next complete record')
    return best


def read_result(package, args, raw_report=None, cache_meta=None):
    """Assemble one full or budgeted report with explicit coverage."""
    from tl_read import report  # pylint: disable=import-outside-toplevel
    from tl_read_selection import body_paragraphs, select_body, selected_issues, scoped_coverage  # pylint: disable=import-outside-toplevel

    if args.format == 'structure':
        from tl_read_structure import structure  # pylint: disable=import-outside-toplevel
        records, issues, coverage = structure(package, revisions=args.revisions or 'accepted',
                                              include_related=bool(args.include_related), parts=args.xml_part)
    else:
        records, issues, coverage = raw_report if raw_report is not None else report(
            package, revisions=args.revisions, include_related=args.include_related is not False)
    enhanced = args.report or args.compact_json or args.table_header or getattr(args, 'content', False)
    if enhanced:
        from tl_read_tables import enrich_tables, table_header_arg  # pylint: disable=import-outside-toplevel
        from copy import deepcopy  # pylint: disable=import-outside-toplevel
        records = deepcopy(records)
        overrides = dict(map(table_header_arg, args.table_header))
        if len(overrides) != len(args.table_header):
            raise ValueError('A table header may be overridden only once')
        issues = issues + enrich_tables(package, records, overrides)
        records = enhanced_records(package, records, compact=args.compact_json and args.tables != 'rows', points=args.report or getattr(args, 'content', False))
    document_coverage = coverage
    document_issues = issues
    document_counts = dict(Counter(record['type'] for record in records))
    selection = args.paragraph_range or args.section or ('headings' if args.headings else 'summary' if args.summary else 'full')
    if args.headings or args.summary:
        records = [item for _, item in body_paragraphs(records, package.main) if item.get('heading_level') is not None]
    elif args.paragraph_range or args.section:
        records = select_body(records, package.main, args)
    if args.headings or args.paragraph_range or args.section:
        issues = selected_issues(issues, records, package.main)
        coverage = scoped_coverage(coverage, records, issues, selection)
    if getattr(args, 'content', False):
        from tl_read_content import content_records  # pylint: disable=import-outside-toplevel
        records = content_records(records, details=args.details)
    counts = dict(Counter(record['type'] for record in records))
    meta = {'status': 'partial' if issues else 'ok', 'api_version': API_VERSION, 'sha256': package.sha256,
            'coverage': {**coverage, 'counts': document_counts if args.summary else counts}, 'issues': issues, 'view': selection}
    if args.headings or args.paragraph_range or args.section:
        meta.update(document_coverage=document_coverage, document_issues=document_issues)
    if args.summary:
        meta['summary'] = {'heading_count': len(records), 'content_records_included': False}
    binding_fields = [API_VERSION, 'read-view-v4', package.sha256, args.headings, args.summary, args.paragraph_range,
                      args.section.strip() if args.section else None]
    if enhanced or args.revisions or args.include_related is not None or args.format == 'structure' or args.section_occurrence:
        options = {'format': args.format, 'revisions': args.revisions, 'include_related': args.include_related,
                   'parts': args.xml_part, 'report': args.report, 'compact': args.compact_json, 'tables': args.tables,
                   'table_header': args.table_header, 'section_occurrence': args.section_occurrence}
        meta.update(options=options)
        binding_fields.append(options)
    if getattr(args, 'content', False):
        meta.update(view='content', selection=selection, details=args.details, projection='content-v1')
        meta['coverage']['projection'] = {'mode': 'content', 'structural_definitions_omitted': True,
                                          'duplicate_representations_removed': True, 'layout_reproduced': False}
        if not args.details:
            from tl_read_content import diagnostic_summary  # pylint: disable=import-outside-toplevel
            meta['issues'] = diagnostic_summary(meta['issues'])
            if 'document_issues' in meta:
                meta['document_issues'] = diagnostic_summary(meta['document_issues'])
            meta['diagnostics'] = {'mode': 'counts_by_code', 'full_details_option': '--content --details'}
        binding_fields.extend(['content-v1', args.details])
    if args.format == 'structure':
        meta['view'] = 'structure'
    elif enhanced and not getattr(args, 'content', False):
        meta['view'] = 'compact' if args.compact_json and args.tables != 'rows' else 'report'
    if args.compact_json and args.tables != 'rows':
        meta['table_rows_omitted'] = sum(item['row_count'] for item in records if item['type'] == 'table') - counts.get('table_row', 0)
    if cache_meta is not None:
        meta['cache'] = cache_meta
    if getattr(args, 'fragment_records', False):
        from tl_read_fragments import fragment_records  # pylint: disable=import-outside-toplevel
        meta.update(fragment_mode='json-record-v1', source_record_total=len(records))
        records, _ = fragment_records(records, meta, args.max_output_bytes)
        binding_fields.extend(['json-record-v1', args.max_output_bytes])
    binding = sha256(encode(binding_fields)).hexdigest()
    budget = args.max_output_bytes if args.max_output_bytes is not None or args.cursor is None else 12_000
    return page(meta, records, budget, args.cursor, binding)


def cached_read(root, package, args):
    """Cache only when explicitly requested, preserving default read-only behavior."""
    from tl_read_cache import cached_report, release  # pylint: disable=import-outside-toplevel
    raw, cache = cached_report(root, package, revisions=args.revisions,
                               include_related=args.include_related is not False, token=args.cache_token)
    try:
        result = read_result(package, args, raw_report=raw, cache_meta=cache)
    except Exception:
        if cache['status'] != 'reused':
            release(root, cache['token'])
        raise
    if not result['has_more']:
        result['cache'] = release(root, cache['token'])
    return result


def enhanced_records(package, records, *, compact, points):
    """Project previews or split chart points without dropping full report rows."""
    from tl_report_objects import drawing_anchor, locate_series  # pylint: disable=import-outside-toplevel
    result = []
    for item in records:
        if compact and ((item['type'] == 'table_row' and item['row_index'] > 2)
                        or (item['type'] == 'paragraph' and 'table_index' in item)):
            continue
        if points and item['type'] == 'chart_series':
            values = item.pop('points', [])
            locate_series(item, values)
            item['point_count'] = len(values)
            result.append(item)
            for value in values:
                result.append({**value, 'type': 'chart_point', 'part': item['part'],
                               'path': f'{item["path"]}/point[{value["index"]}]', 'chart_index': item['chart_index'],
                               'series_index': item['series_index']})
        else:
            if points and item['type'] in ('textbox', 'image', 'chart'):
                anchor = drawing_anchor(package, item)
                if anchor is not None:
                    item['anchor'] = anchor
            if points and item['type'] == 'chart' and isinstance(item.get('workbook_source'), dict):
                source = item['workbook_source']
                if source.get('external'):
                    item['workbook_source'] = {key: value for key, value in source.items() if key != 'target'}
            result.append(item)
    return result


def plain_report(result):
    """Project selected records to readable UTF-8 text."""
    lines = []
    for item in result['records']:
        if item['type'] == 'table_row':
            lines.append('\t'.join(cell.get('text', '').replace('\n', ' ') for cell in item['cells']))
        elif item['type'] == 'textbox' and item.get('text'):
            lines.append(item['text'])
        elif item['type'] == 'paragraph' and item.get('text') and 'table_index' not in item:
            lines.append(item['text'])
    return ('\n'.join(lines) + '\n').encode('utf-8')


def layout_report(body, part):
    """Report page geometry errors and local risks visible in saved OOXML."""
    from docx.oxml.ns import qn  # pylint: disable=import-outside-toplevel

    issues = []
    warnings = []
    breaks = [node for node in body.iter(qn('w:br')) if node.get(qn('w:type')) == 'page']
    sections = list(body.iter(qn('w:sectPr')))

    def enabled(node):
        return node is not None and node.get(qn('w:val'), '1') not in ('0', 'false', 'off')

    paragraph_breaks = [node for node in body.iter(qn('w:pageBreakBefore')) if enabled(node)]
    for position, para in enumerate(body.findall(qn('w:p')), 1):
        props = para.find(qn('w:pPr'))
        if props is not None and enabled(props.find(qn('w:pageBreakBefore'))) and any(
            child.get(qn('w:type')) == 'page' for child in para.iter(qn('w:br'))
        ):
            warnings.append({'code': 'paragraph_has_two_page_break_mechanisms', 'paragraph': position})
    for position, table in enumerate(body.findall(qn('w:tbl')), 1):
        rows = table.findall(qn('w:tr'))
        headers = [enabled(row.find(f'./{qn("w:trPr")}/{qn("w:tblHeader")}')) for row in rows]
        if len(rows) > 1 and all(headers):
            warnings.append({'code': 'all_rows_marked_repeat_header', 'table': position})
        elif any(headers) and headers != sorted(headers, reverse=True):
            warnings.append({'code': 'noncontiguous_repeat_headers', 'table': position})
    return issues, {'sections': len(sections), 'explicit_page_breaks': len(breaks),
                    'page_break_before': len(paragraph_breaks), 'warnings': warnings}


def validate(package):
    """Check XML, relationships and static layout without opening the editor."""
    from docx.oxml.ns import qn  # pylint: disable=import-outside-toplevel
    from tl_package import OFFICE_REL, TYPES, rels_member  # pylint: disable=import-outside-toplevel
    from tl_pagination import audit_pagination  # pylint: disable=import-outside-toplevel
    from tl_layout_audit import audit_layout  # pylint: disable=import-outside-toplevel

    for name, kind in package.types.items():
        if name.lower().endswith(('.xml', '.rels')) or kind.endswith(('+xml', '/xml')):
            package.xml(name)
    body = package.xml(package.main).find(qn('w:body'))
    issues, layout = layout_report(body, package.main)
    audit = audit_layout(package)
    layout['audit'] = audit
    issues.extend(item for item in audit['issues'] if item['level'] == 'error')
    styles = next((rel['target'] for rel in package.relations(package.main).values()
                   if rel['type'] == OFFICE_REL + 'styles' and not rel['external'] and rel['target'] in package.names), None)
    layout['pagination_warnings'] = audit_pagination(body, package.xml(styles) if styles else None, package.main)
    for source in sorted({''} | package.names - {TYPES}):
        if rels_member(source) in package.names:
            for rid, item in package.relations(source).items():
                if not item['external'] and item['target'] not in package.names:
                    issues.append({'code': 'missing_relationship_target', 'part': source, 'id': rid})
    return {'status': 'invalid' if issues else 'verified', 'issues': issues, 'verification': 'static_ooxml',
            'layout': layout}


def finish(root, data, summary, *, name, protected, status, exact_name, task_id=None, archive_requested=True):
    """Publish a verified file and archive it with a separate result state."""
    from archive_delivery import choose_delivery_output, finalize_delivery, validate_delivery_output  # pylint: disable=import-outside-toplevel
    from tl_recovery import publish, task_lock  # pylint: disable=import-outside-toplevel

    warnings = summary.get('layout', {}).get('pagination_warnings', [])
    layout_warnings = [f"layout-warning: {item['code']} {item['location']} {item['message']}" for item in warnings[:100]]
    if len(warnings) > 100:
        layout_warnings.append(f'layout-warning: {len(warnings) - 100} further entries; use validate for full diagnostics')
        summary['layout']['pagination_warnings'] = warnings[:100]
        summary['layout']['omitted_pagination_warnings'] = len(warnings) - 100

    def archive(candidate):
        result = (finalize_delivery(candidate, protected, status=status, result=summary) if archive_requested else
                  {'status': status, 'output': candidate, 'archive_status': 'not_required', 'warnings': [], 'result': summary})
        result['warnings'].extend(layout_warnings)
        return result

    with task_lock(root, wait=True):
        validate_delivery_output(name, protected)
        for _ in range(4):
            candidate = name if exact_name else str(choose_delivery_output(Path(name), protected_inputs=protected))
            receipt, result = publish(root, candidate, data, status=status, finalize=archive, task_id=task_id)
            if exact_name or not receipt.get('output_collision') or not receipt['cleanup_complete']:
                break
    cleanup = {key: receipt[key] for key in ('cleanup_complete', 'manifest_removed')}
    cleanup['deliverables_unchanged'] = receipt['deliverable_unchanged']
    task_state = {'publication': receipt, 'cleanup': cleanup, 'task_manifest': receipt['task_manifest']}
    if not receipt['published']:
        return {'status': 'error', **task_state, 'archive_status': 'not_required',
                'task_status': 'failed_cleaned' if receipt['cleanup_complete'] else 'cleanup_incomplete'}, 1
    if not receipt['deliverable_unchanged']:
        return {'status': 'error', **task_state, 'archive_status': 'unknown', 'task_status': 'recovery_pending'}, 1
    if result is None:
        result = {'status': status, 'output': candidate, 'archive_status': 'unknown', 'warnings': layout_warnings, 'result': summary}
    if result.get('temporary_residuals'):
        cleanup['cleanup_complete'] = False
        cleanup['archive_cleanup_unresolved'] = True
    result.update(task_state, sha256=sha256(data).hexdigest(),
                  task_status='completed' if cleanup['cleanup_complete'] else 'cleanup_incomplete')
    return result, 0 if cleanup['cleanup_complete'] else 3


def export_report(root, args, result):
    """Publish a selected JSON or plain-text report without replacing files."""
    from tl_io import output_name  # pylint: disable=import-outside-toplevel
    mode = args.format if args.action == 'read' else 'json'
    name = output_name(args.output, {'text': '.txt', 'xml': '.xml'}.get(mode, '.json'))
    if mode == 'xml':
        payload = result['data']
    else:
        payload = plain_report(result) if mode == 'text' else encode(
            {key: value for key, value in result.items() if key != 'status'} if args.action == 'unpack' else result)
    from tl_package import MAX_INPUT  # pylint: disable=import-outside-toplevel
    if len(payload) > MAX_INPUT:
        raise ValueError('Report export exceeds the 64 MiB limit; use a narrower view or paging')
    details = {'read_status': result['status'], 'bytes': len(payload), 'issues': result.get('issues', [])}
    for field in ('start', 'end', 'total', 'has_more', 'next_cursor', 'view', 'part'):
        if field in result:
            details[field] = result[field]
    return finish(root, payload, details, name=name, protected=[args.source], status='exported', exact_name=True, archive_requested=args.delivery)


def read_xml_bytes(package, names):
    """Validate a single XML member and return its stored bytes verbatim."""
    part = names[0] if names else package.main
    content_type = package.types.get(part, '')
    if part not in package.names or not (part.lower().endswith(('.xml', '.rels')) or content_type.endswith(('/xml', '+xml'))):
        raise ValueError('Select an existing XML package part')
    package.xml(part)
    return {'status': 'ok', 'view': 'xml', 'part': part, 'data': package.read(part)}


def execute(args):
    """Run one command after importing the only direct third-party component."""
    from docx import Document, __version__ as version  # pylint: disable=import-outside-toplevel
    from tl_io import output_name, read_task, task_root  # pylint: disable=import-outside-toplevel
    from tl_package import Package  # pylint: disable=import-outside-toplevel

    if sys.version_info < (3, 10) or version != '1.2.0':
        raise RuntimeError('Python >=3.10 and python-docx 1.2.0 are required')
    if args.action == 'capabilities':
        return {'status': 'ok', 'api_version': API_VERSION, 'python_docx': version, 'capabilities': CAPABILITIES}, 0
    with task_root() as root:
        if args.action == 'check':
            return self_check(args, Document, Package, version), 0
        if args.action == 'release-cache':
            from tl_read_cache import release  # pylint: disable=import-outside-toplevel
            return release(root, args.token), 0
        if args.action == 'resume':
            from tl_recovery import resume  # pylint: disable=import-outside-toplevel
            try:
                result = resume(root, args.manifest)
                return result, 0 if result['cleanup']['cleanup_complete'] else 3
            except (OSError, ValueError, RuntimeError) as exc:
                return {'status': 'error', 'task_status': 'recovery_pending', 'task_manifest': args.manifest,
                        'cleanup': {'cleanup_complete': False, 'manifest_removed': False}, 'error': str(exc)}, 1
        if args.action == 'archive' and Path(args.source).suffix.lower() in ('.json', '.txt', '.xml'):
            return archive_report(root, args)
        package = None
        if args.action != 'create':
            package = Package(read_task(root, args.source))
            if not args.source.lower().endswith(package.extension):
                raise ValueError('Document extension does not match its package content type')
        if args.action == 'read':
            if args.cache or args.cache_token:
                result = cached_read(root, package, args)
            else:
                result = read_xml_bytes(package, args.xml_part) if args.format == 'xml' else read_result(package, args)
            return export_report(root, args, result) if args.output else (result, 0)
        if args.action == 'unpack':
            from tl_roundtrip import unpack_package  # pylint: disable=import-outside-toplevel
            bundle = unpack_package(package)
            if args.output:
                return export_report(root, args, {'status': 'ok', **bundle})
            return bundle, 0
        if args.action == 'validate':
            result = validate(package)
            strict_status = (1 if result['status'] != 'verified' else
                             2 if (args.strict_pagination and result['layout']['pagination_warnings']
                                   or args.strict_layout and any(item['level'] == 'warning'
                                       for item in result['layout']['audit']['issues'])) else 0)
            if args.output:
                receipt, status = export_report(root, args, result)
                return receipt, 1 if strict_status == 1 else status or strict_status
            return result, strict_status
        if args.action == 'archive':
            from archive_delivery import archive_delivery, archive_exit_code  # pylint: disable=import-outside-toplevel
            output_name(args.source, extension=package.extension)
            if package.sha256 != args.sha256 or validate(package)['status'] != 'verified':
                raise ValueError('Archive retry requires the exact verified published file')
            result = archive_delivery([args.source])
            return result, archive_exit_code(result)
        return write_document(root, args, package)


def archive_report(root, args):
    """Retry a report archive using the exact digest from its export receipt."""
    from archive_delivery import archive_delivery, archive_exit_code  # pylint: disable=import-outside-toplevel
    from tl_io import output_name, read_task  # pylint: disable=import-outside-toplevel
    name = output_name(args.source, Path(args.source).suffix.lower())
    if sha256(read_task(root, name)).hexdigest() != args.sha256:
        raise ValueError('Archive retry requires the exact exported report')
    result = archive_delivery([name])
    return result, archive_exit_code(result)


def write_document(root, args, package):
    """Prepare and validate a document before creating any publication files."""
    from tl_io import output_name, read_task, suggested_name  # pylint: disable=import-outside-toplevel
    from tl_package import Package  # pylint: disable=import-outside-toplevel

    payload = sys.stdin.buffer.read(MAX_SPEC + 1)
    if len(payload) > MAX_SPEC:
        raise ValueError('JSON specification exceeds 16 MiB')
    specification = decode(payload)
    protected = [args.source] if package is not None else []
    extension = package.extension if args.action in ('comments', 'replace', 'repack') else '.docx'
    name = output_name(args.output, extension) if args.output is not None else None

    def read_image(path):
        data = read_task(root, path, 16 * 1024 * 1024)
        protected.append(path)
        return data

    if args.action == 'repack':
        from tl_roundtrip import repack_package  # pylint: disable=import-outside-toplevel
        data, details = repack_package(package, specification)
    elif args.action == 'edit':
        from tl_edit import edit_document  # pylint: disable=import-outside-toplevel
        data, details = edit_document(package, specification, read_image)
    elif args.action == 'replace':
        from tl_replace import replace_document  # pylint: disable=import-outside-toplevel
        if args.require_no_placeholders or args.parts:
            if not isinstance(specification, dict):
                raise ValueError('Replacement specification must be a JSON object')
            if args.require_no_placeholders:
                specification['require_no_placeholders'] = True
            if args.parts:
                if 'parts' in specification:
                    raise ValueError('Select parts using either JSON or --parts')
                specification['parts'] = args.parts
        data, details = replace_document(package, specification)
    elif args.action == 'comments':
        from tl_comments import manage_comments  # pylint: disable=import-outside-toplevel
        if isinstance(specification, dict) and specification.get('action') == 'list' and name is not None:
            raise ValueError('Listing comments does not take an output filename')
        data, details = manage_comments(package, specification)
        if data is None:
            return {'status': 'partial' if details['diagnostics'] else 'ok', **details}, 0
    else:
        from tl_create import build_document  # pylint: disable=import-outside-toplevel
        document = build_document(specification, read_image)
        stream = BytesIO()
        document.save(stream)
        data, details = stream.getvalue(), {'blocks': len(specification['blocks'])}
        Package(data).document()
    if name is None:
        if package is None:
            title = specification.get('metadata', {}).get('title') or next(
                (block.get('text') or ''.join(run.get('text', '') for run in block.get('runs', []))
                 for block in specification['blocks'] if block['type'] == 'title'), '')
            name = suggested_name(title, '', '文档', extension)
        else:
            name = suggested_name(Path(args.source).stem, '_批注版' if args.action == 'comments' else '_修订版', '文档副本', extension)
    verification = validate(Package(data))
    if verification['status'] != 'verified':
        return {'status': 'error', 'verification': verification, 'archive_status': 'not_required'}, 1
    return finish(root, data, {**details, **verification}, name=name, protected=protected,
                  status='verified' if args.action == 'create' else 'edited', exact_name=args.exact_name, task_id=args.task_id)


def self_check(args, constructor, package_type, version):
    """Exercise an in-memory Word round trip and optionally the write primitives."""
    probe = constructor()
    probe.add_paragraph('tl-docx probe')
    stream = BytesIO()
    probe.save(stream)
    if package_type(stream.getvalue()).document().paragraphs[0].text != 'tl-docx probe':
        raise RuntimeError('python-docx round-trip self-check failed')
    from xml.parsers.expat import version_info as expat_version  # pylint: disable=import-outside-toplevel
    if args.write:
        from archive_delivery import environment_check  # pylint: disable=import-outside-toplevel
        from tl_io import task_root  # pylint: disable=import-outside-toplevel
        from tl_recovery import task_lock  # pylint: disable=import-outside-toplevel
        with task_root() as root, task_lock(root, wait=False):
            environment_check()
            os.fsync(root)
    return {'status': 'environment-ok', 'api_version': API_VERSION, 'python_docx': version,
            'check_kind': 'environment', 'artifact_validation': 'not_run',
            'expat': '.'.join(map(str, expat_version)), 'template_dom_available': expat_version >= (2, 6, 0),
            'structure_available': expat_version >= (2, 6, 0),
            'task_write_check': 'passed' if args.write else 'not_requested'}


def arguments(argv):
    """Parse the public command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('check').add_argument('--write', action='store_true')
    actions.add_parser('capabilities')
    reader = actions.add_parser('read')
    reader.add_argument('source')
    view = reader.add_mutually_exclusive_group()
    view.add_argument('--summary', action='store_true')
    view.add_argument('--headings', action='store_true')
    view.add_argument('--paragraph-range', metavar='START:END')
    view.add_argument('--section', metavar='HEADING')
    reader.add_argument('--section-occurrence', type=int)
    reader.add_argument('--format', choices=('json', 'text', 'structure', 'xml'), default='json')
    reader.add_argument('--revisions', choices=('accepted', 'original', 'all'))
    related = reader.add_mutually_exclusive_group()
    related.add_argument('--include-related', dest='include_related', action='store_true', default=None)
    related.add_argument('--body-only', dest='include_related', action='store_false')
    reader.add_argument('--xml-part', action='append', default=[])
    report_view = reader.add_mutually_exclusive_group()
    report_view.add_argument('--report', action='store_true')
    report_view.add_argument('--compact-json', action='store_true')
    reader.add_argument('--tables', choices=('summary', 'rows'))
    reader.add_argument('--table-header', action='append', default=[])
    reader.add_argument('-o', '--output')
    reader.add_argument('--delivery', action='store_true')
    reader.add_argument('--max-output-bytes', type=int)
    reader.add_argument('--cursor')
    reader.add_argument('--fragment-records', action='store_true')
    reader.add_argument('--content', action='store_true')
    reader.add_argument('--details', action='store_true')
    cache = reader.add_mutually_exclusive_group()
    cache.add_argument('--cache', action='store_true')
    cache.add_argument('--cache-token')
    actions.add_parser('release-cache').add_argument('token')
    validator = actions.add_parser('validate')
    validator.add_argument('source')
    validator.add_argument('--strict-pagination', action='store_true')
    validator.add_argument('--strict-layout', action='store_true')
    validator.add_argument('-o', '--output')
    validator.add_argument('--delivery', action='store_true')
    archiver = actions.add_parser('archive')
    archiver.add_argument('source')
    archiver.add_argument('--sha256', required=True)
    actions.add_parser('resume').add_argument('manifest')
    unpacker = actions.add_parser('unpack')
    unpacker.add_argument('source')
    unpacker.add_argument('-o', '--output')
    unpacker.add_argument('--delivery', action='store_true')
    for action in ('create', 'edit', 'comments', 'replace', 'repack'):
        writer = actions.add_parser(action)
        if action != 'create':
            writer.add_argument('source')
        writer.add_argument('output', nargs='?')
        writer.add_argument('--exact-name', action='store_true')
        writer.add_argument('--task-id', help='optional unique 32-digit lowercase hexadecimal recovery identifier')
        if action == 'replace':
            writer.add_argument('--parts', nargs='+')
            writer.add_argument('--require-no-placeholders', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'read':
        if args.details and not args.content:
            parser.error('--details requires --content')
        if args.content and (args.format != 'json' or any((args.report, args.compact_json, args.summary, args.headings, args.tables, args.table_header))):
            parser.error('--content requires JSON without structure, XML, summary, headings or report table options')
        if args.fragment_records and (args.format not in ('json', 'structure') or args.max_output_bytes is None):
            parser.error('--fragment-records requires JSON/structure and an explicit --max-output-bytes')
        if (args.cache or args.cache_token) and (args.format != 'json' or args.output):
            parser.error('Parsing cache requires JSON stdout content/report output, without -o')
        if args.format == 'text' and (args.cursor is not None or args.max_output_bytes is not None or args.summary):
            parser.error('Text output cannot use cursors, a byte budget or summary mode')
        if args.max_output_bytes is not None and args.max_output_bytes < MIN_BUDGET:
            parser.error(f'Byte budget must be at least {MIN_BUDGET}')
        if args.section_occurrence is not None and (not args.section or args.section_occurrence < 1):
            parser.error('--section-occurrence requires --section and a positive index')
        if args.xml_part and args.format not in ('structure', 'xml'):
            parser.error('--xml-part requires --format structure or xml')
        if args.format in ('structure', 'xml') and any((args.headings, args.summary, args.paragraph_range, args.section,
                                                       args.table_header, args.report, args.compact_json, args.tables)):
            parser.error('The XML views cannot be combined with report selectors')
        if args.format == 'xml' and (args.revisions or args.include_related is not None or args.cursor or args.max_output_bytes
                                     or len(args.xml_part) > 1):
            parser.error('The raw XML view returns one original part without revision or paging options')
        if args.format == 'text' and any((args.report, args.compact_json, args.tables, args.table_header)):
            parser.error('Report table options require JSON output')
        if args.tables and not args.compact_json:
            parser.error('--tables requires --compact-json')
    if getattr(args, 'delivery', False) and not args.output:
        parser.error('--delivery requires an explicit report output filename')
    if getattr(args, 'exact_name', False) and args.output is None:
        parser.error('--exact-name requires an explicit output filename')
    if getattr(args, 'task_id', None) is not None and not re.fullmatch(r'[0-9a-f]{32}', args.task_id):
        parser.error('--task-id requires 32 lowercase hexadecimal digits')
    return args


def main(argv=None):
    """Emit exactly one business result and separate error diagnostics."""
    args = arguments(argv)
    try:
        payload, status = execute(args)
    except (ImportError, OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError, SyntaxError, OverflowError, BadZipFile) as exc:
        print(f'tl-docx {args.action}: {exc}', file=sys.stderr)
        payload = {'status': 'error', 'action': args.action, 'error': str(exc)}
        status = 1
    if args.action == 'read' and args.format == 'text' and not args.output and status == 0:
        if payload['issues']:
            print('Partial read: ' + ', '.join(sorted({issue['code'] for issue in payload['issues']})), file=sys.stderr)
        sys.stdout.buffer.write(plain_report(payload))
    elif args.action == 'read' and args.format == 'xml' and not args.output and status == 0:
        sys.stdout.buffer.write(payload['data'])
    else:
        sys.stdout.buffer.write(encode(payload))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
