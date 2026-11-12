"""Bounded tolerant OOXML spreadsheet package/workbook reader.

Content-first policy: value-bearing cells are returned even when the package also
carries extension namespaces, unknown relationships, unrelated parts or malformed
optional metadata. Every degradation becomes an aggregated issue and the result
becomes partial; only ZIP/XML safety faults and unreadable workbook framing stay
hard failures. Worksheet value parsing lives in wps_ooxml_worksheet.
"""
import io
import re
import zipfile
import zlib

from wps_ooxml import (
    IssueLog, XmlBudget, _preflight, check_archive, content_types, fromstring, read_part,
    relationships, rels_path, split_tag,
)
from wps_ooxml_text import TextReader
from wps_ooxml_worksheet import (
    MAIN_NAMESPACES, MAX_SST, STRICT, SheetContext, coordinate, integer,
    is_main, load_part_xml, need, note_foreign_attributes, read_worksheet,
    relationship_id,
)

# Re-exported for existing callers (comments reader, VML notes and tests).
__all__ = ['package_kind', 'read_bytes', 'coordinate', 'integer', 'relationships']
from wps_style_formats import NumberFormats, read_style_sheet

BOOK_TYPES = frozenset((
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml',
))
WORD_TYPES = frozenset((
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml',
))
WORKSHEET_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'
SHARED_STRINGS_TYPE = (
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml')
STYLES_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml'
MAX_SHEETS = 128


def _pick_workbook(package, types, budget, report):
    """Locate one main workbook relationship without failing on extra OPC relations."""
    root_rels = relationships(package, '', budget, report=report)
    mains = [relation for relation in root_rels.values()
             if relation.get('kind') == 'officeDocument' and relation.get('path')]
    for relation in root_rels.values():
        if relation.get('kind') != 'officeDocument':
            report.add('root_relationship_not_followed',
                       relationship_type=str(relation.get('type'))[:160],
                       part='_rels/.rels',
                       relationship_id=str(relation.get('id'))[:120])
    need(mains, 'Missing officeDocument relationship')
    if len(mains) > 1:
        report.add('ambiguous_office_document_relationship',
                   'Several officeDocument relationships are present; the first spreadsheet '
                   'candidate was read', count=len(mains))
        for relation in mains:
            if types.get(relation['path']) in BOOK_TYPES:
                return relation
    return mains[0]


def package_kind(data):
    """Validate safe OPC framing and determine main family without suffix guessing."""
    stream = io.BytesIO(data)
    _preflight(stream, len(data))
    with zipfile.ZipFile(stream) as package:
        check_archive(package)
        budget = XmlBudget()
        report = IssueLog()
        types = content_types(package, budget, report=report)
        main = _pick_workbook(package, types, budget, report)
        main_type = types.get(main['path'])
        if main_type in BOOK_TYPES:
            return 'spreadsheet'
        if main_type in WORD_TYPES:
            return 'word'
        root = fromstring(read_part(package, main['path']), budget=budget)
        namespace, local = split_tag(root.tag)
        if local == 'workbook' and namespace in MAIN_NAMESPACES:
            return 'spreadsheet'
        if local == 'document' and namespace in (
                'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                'http://purl.oclc.org/ooxml/wordprocessingml/main'):
            return 'word'
        raise ValueError('Unsupported OOXML main content type')


def _read_shared_strings(package, path, types, namespace, budget, report, text_reader,
                         used_parts):
    """Read saved shared strings; entry positions are preserved for index references."""
    strings = []
    shared_formatting = []
    shared_encoding = []
    used_parts.add(path)
    root = load_part_xml(package, path, budget, report, 'shared_strings_part_not_read')
    if root is None:
        return strings, shared_formatting, shared_encoding
    root_namespace, root_local = split_tag(root.tag)
    if root_local != 'sst' or root_namespace not in MAIN_NAMESPACES:
        report.add('shared_strings_root_not_interpreted', element=str(root.tag)[:120])
        return strings, shared_formatting, shared_encoding
    if root_namespace != namespace:
        report.add('spreadsheet_conformance_namespace_variance', part='sharedStrings')
    content_type = types.get(path)
    if content_type is not None and content_type != SHARED_STRINGS_TYPE:
        report.add('shared_strings_content_type_mismatch', part=path,
                   content_type=str(content_type)[:120])
    entries = list(root)
    for item in entries:
        if not is_main(item, 'si'):
            report.add('shared_strings_element_not_interpreted',
                       element=str(item.tag)[:120])
            continue
        if len(strings) >= MAX_SST:
            report.add('shared_string_count_limit_reached', kept=MAX_SST,
                       declared=len(entries))
            break
        text, formatting = text_reader.cell_text(item)
        encoding = formatting.pop('text_encoding', None)
        if formatting:
            shared_formatting.append({'sst_index': len(strings), **formatting})
        strings.append(text)
        shared_encoding.append(encoding)
    return strings, shared_formatting, shared_encoding


def read_bytes(data):
    part = 'ZIP'
    try:
        stream = io.BytesIO(data)
        _preflight(stream, len(data))
        with zipfile.ZipFile(stream) as package:
            check_archive(package)
            budget = XmlBudget()
            report = IssueLog()
            types = content_types(package, budget, report=report)
            used_parts = {'[Content_Types].xml', '_rels/.rels'}
            main = _pick_workbook(package, types, budget, report)
            need(main.get('resolved') is not False, 'Main workbook relationship is unresolved')
            part = main['path']
            used_parts.add(part)
            used_parts.add(rels_path(part))
            main_type = types.get(part)
            if main_type is not None and main_type not in BOOK_TYPES:
                raise ValueError('Unsupported spreadsheet main content type')
            if main_type is None:
                report.add('workbook_content_type_missing',
                           'Workbook is identified by its saved structure')
            root = fromstring(read_part(package, part), budget=budget)
            namespace, local = split_tag(root.tag)
            need(local == 'workbook' and namespace in MAIN_NAMESPACES,
                 'Unsupported workbook root element')
            conformance = 'strict' if namespace == STRICT else 'transitional'
            if main.get('conformance') and main['conformance'] != conformance:
                report.add('workbook_conformance_namespace_mismatch',
                           'Relationship and workbook namespaces use different conformances')
            note_foreign_attributes(report, root)
            text_reader = TextReader(namespace, report)
            date_system = '1900'
            sheet_nodes = []
            seen_workbook_property = False
            seen_sheets = False
            for child in root:
                child_namespace, child_name = split_tag(child.tag)
                if child_namespace in MAIN_NAMESPACES and child_name == 'workbookPr':
                    if seen_workbook_property:
                        report.add('duplicate_workbook_property_ignored',
                                   element='workbookPr')
                        continue
                    seen_workbook_property = True
                    raw_date = child.get('date1904')
                    if raw_date in ('0', 'false'):
                        date_system = '1900'
                    elif raw_date in ('1', 'true'):
                        date_system = '1904'
                    elif raw_date is not None:
                        report.add('workbook_date_flag_not_interpreted',
                                   raw=str(raw_date)[:20])
                elif child_namespace in MAIN_NAMESPACES and child_name == 'sheets':
                    if seen_sheets:
                        report.add('duplicate_workbook_sheet_collection_ignored')
                        continue
                    seen_sheets = True
                    sheet_nodes = list(child)
                else:
                    report.add('workbook_metadata_not_interpreted',
                               element=str(child.tag)[:120])
            book_rels = relationships(package, part, budget, report=report, scope='workbook')
            related = {}
            for relationship, relation in book_rels.items():
                kind = relation.get('kind')
                if relation.get('external') or kind is None:
                    continue
                path = relation.get('path')
                if path is None:
                    continue
                if kind not in ('worksheet', 'sharedStrings', 'styles'):
                    report.add('workbook_relationship_not_followed',
                               relationship_kind=str(kind)[:60],
                               relationship_id=relationship)
                    continue
                related[relationship] = (kind, path)
            strings = []
            shared_string_formatting = []
            shared_string_encoding = []
            shared_paths = [path for kind, path in related.values()
                            if kind == 'sharedStrings']
            if len(shared_paths) > 1:
                report.add('duplicate_shared_strings_relationships',
                           'Only the first saved shared strings part is read',
                           count=len(shared_paths))
            if shared_paths:
                strings, shared_string_formatting, shared_string_encoding = _read_shared_strings(
                    package, shared_paths[0], types, namespace, budget, report,
                    text_reader, used_parts)
            styles = []
            style_xfs = []
            formats = {}
            style_paths = [path for kind, path in related.values() if kind == 'styles']
            if len(style_paths) > 1:
                report.add('duplicate_styles_relationships',
                           'Only the first saved styles part is read', count=len(style_paths))
            if style_paths:
                style_path = style_paths[0]
                used_parts.add(style_path)
                content_type = types.get(style_path)
                if content_type is not None and content_type != STYLES_TYPE:
                    report.add('styles_content_type_mismatch', part=style_path,
                               content_type=str(content_type)[:120])
                styles, style_xfs, formats = read_style_sheet(
                    package, style_path, namespace, budget, report)
            number_formats = NumberFormats(styles, style_xfs)
            sheets = []
            used_sheet_relationships = set()
            used_comment_parts = set()
            used_names = set()
            data_uncertain = False
            sheet_index = 0

            def unique_sheet_name(raw_name):
                nonlocal sheet_index
                sheet_index += 1
                if (isinstance(raw_name, str) and 1 <= len(raw_name) <= 31
                        and not re.search(r'[\\/\[\]:*?]', raw_name)):
                    if raw_name.casefold() not in used_names:
                        used_names.add(raw_name.casefold())
                        return raw_name
                    report.add('duplicate_sheet_name_retained', sheet=raw_name)
                else:
                    report.add('sheet_name_not_interpreted', raw=str(raw_name)[:32])
                base = 'Sheet' + str(sheet_index)
                candidate = base
                suffix = 2
                while candidate.casefold() in used_names:
                    candidate = base + '_' + str(suffix)
                    suffix += 1
                used_names.add(candidate.casefold())
                return candidate

            for sheet in sheet_nodes:
                if len(sheets) >= MAX_SHEETS:
                    report.add('sheet_count_limit_reached', kept=MAX_SHEETS)
                    data_uncertain = True
                    break
                if not is_main(sheet, 'sheet'):
                    report.add('sheet_declaration_not_interpreted',
                               element=str(sheet.tag)[:120])
                    continue
                note_foreign_attributes(report, sheet)
                name = unique_sheet_name(sheet.get('name'))
                try:
                    sheet_id = integer(sheet.get('sheetId'), 4294967295)
                except ValueError:
                    report.add('sheet_id_not_interpreted', sheet=name,
                               raw=str(sheet.get('sheetId'))[:20])
                    sheet_id = 0
                if sheet_id and any(entry['sheet_id'] == sheet_id for entry in sheets):
                    report.add('duplicate_sheet_id_retained', sheet=name,
                               sheet_id=sheet_id)
                state = sheet.get('state', 'visible')
                if state not in ('visible', 'hidden', 'veryHidden'):
                    report.add('sheet_hidden_state_not_interpreted', sheet=name,
                               raw=str(state)[:20])
                    state = 'visible'
                record = {
                    'name': name,
                    'hidden_state': ('visible', 'hidden', 'veryHidden').index(state),
                    'sheet_id': sheet_id, 'source': None, 'cells': {}, 'merges': [],
                    'hyperlinks': [], 'comments': [], 'views': [],
                    'visibility': {
                        'rows': [], 'columns': [],
                        'unspecified_visibility': 'unknown',
                        'default_selection': 'all',
                        'filters': {'records': [], 'conditions_evaluated': False},
                        'hidden_cause_policy': 'Saved flags do not identify hiding cause',
                    },
                }
                sheets.append(record)
                relationship = relationship_id(sheet)
                if relationship is None:
                    report.add('worksheet_relationship_missing', sheet=name)
                    data_uncertain = True
                    continue
                if relationship in used_sheet_relationships:
                    report.add('duplicate_worksheet_relationship_retained', sheet=name,
                               relationship_id=str(relationship)[:120])
                used_sheet_relationships.add(relationship)
                relation = book_rels.get(relationship)
                if relation is None:
                    report.add('worksheet_relationship_unknown', sheet=name,
                               relationship_id=str(relationship)[:120])
                    data_uncertain = True
                    continue
                if relation.get('external') or relation.get('path') is None:
                    report.add('worksheet_part_not_followed', sheet=name,
                               relationship_id=str(relationship)[:120])
                    data_uncertain = True
                    continue
                if relation.get('kind') != 'worksheet':
                    report.add('worksheet_relationship_kind_not_worksheet', sheet=name,
                               relationship_kind=str(relation.get('kind'))[:60])
                    data_uncertain = True
                    continue
                sheet_part = relation['path']
                used_parts.add(sheet_part)
                record['source'] = sheet_part
                content_type = types.get(sheet_part)
                if content_type is not None and content_type != WORKSHEET_TYPE:
                    report.add('worksheet_content_type_mismatch', sheet=name,
                               part=sheet_part, content_type=str(content_type)[:120])
                context = SheetContext(
                    package=package, budget=budget, report=report, types=types,
                    sheet_part=sheet_part, sheet_name=name, namespace=namespace,
                    conformance=conformance, strings=strings, styles=styles,
                    string_encoding=shared_string_encoding,
                    formats=formats, number_formats=number_formats,
                    date_system=date_system, text_reader=text_reader,
                    used_parts=used_parts, used_comment_parts=used_comment_parts,
                    main_part=part,
                )
                parsed = read_worksheet(context)
                if parsed is None:
                    data_uncertain = True
                    continue
                if parsed['truncated'] or context.uncertain:
                    data_uncertain = True
                record['cells'] = parsed['cells']
                record['merges'] = parsed['merges']
                record['hyperlinks'] = parsed['hyperlinks']
                record['comments'] = parsed['comments']
                record['views'] = parsed['views']
                record['visibility']['rows'] = parsed['rows']
                record['visibility']['columns'] = parsed['columns']
                record['visibility']['filters'] = {
                    'records': parsed['filters'], 'conditions_evaluated': False}
            for relationship, (kind, path) in related.items():
                if kind == 'worksheet' and relationship not in used_sheet_relationships:
                    report.add('unbound_worksheet_relationship_ignored',
                               relationship_id=str(relationship)[:120])
            for name in package.namelist():
                if package.getinfo(name).is_dir():
                    continue
                if name not in used_parts:
                    report.add('unrelated_package_part_not_read', part=name)
            referenced_sst = {cell['sst_index'] for sheet in sheets
                              for cell in sheet['cells'].values() if 'sst_index' in cell}
            if shared_string_formatting:
                shared_string_formatting = [item for item in shared_string_formatting
                                            if item['sst_index'] in referenced_sst]
            shared_string_encoding = [
                {'sst_index': index, **evidence}
                for index, evidence in enumerate(shared_string_encoding)
                if evidence and index in referenced_sst]
            if data_uncertain or text_reader.uncertain:
                coverage = 'unverified_due_to_best_effort_salvage'
            elif report.marked('missing_formula_cache'):
                coverage = 'missing_formula_caches'
            else:
                coverage = 'complete_within_declared_sheets'
            issues = report.issues()
            return {
                'status': 'partial' if issues else 'ok', 'partial': bool(issues),
                'format': 'ooxml-spreadsheet', 'conformance': conformance,
                'schema_version': 'ooxml_spreadsheet_readonly_v1',
                'spreadsheet_parsed': True, 'date_system': date_system, 'sheets': sheets,
                'styles': styles, 'style_xfs': style_xfs,
                'custom_number_formats': {
                    str(key): value for key, value in formats.items()
                    if key not in text_reader.omitted_display_formats},
                **({'custom_number_formats_omitted':
                    sorted(str(key) for key in text_reader.omitted_display_formats)}
                   if text_reader.omitted_display_formats else {}),
                'shared_string_formatting': shared_string_formatting,
                **({'shared_string_encoding': shared_string_encoding}
                   if shared_string_encoding else {}),
                'issues': issues, 'issues_truncated': report.truncated,
                'text_budget': {
                    'unit': 'unicode_code_points',
                    'limit': text_reader.limit,
                    'retained': getattr(text_reader, 'total_text', 0),
                    'limited': bool(getattr(text_reader, 'text_limited', False)),
                    'display_units': getattr(text_reader, 'display_text', 0),
                    'display_limit': text_reader.display_limit,
                    'display_limited': bool(
                        getattr(text_reader, 'display_limited', False)),
                },
                'cell_value_coverage': coverage,
                'omissions_affect_cell_values': bool(
                    data_uncertain or text_reader.uncertain
                    or report.marked('missing_formula_cache')),
                'cell_count': sum(len(sheet['cells']) for sheet in sheets),
                'formulas_executed': False, 'external_links_followed': False,
                'producer_verified': False,
            }
    except (ValueError, KeyError, zipfile.BadZipFile, zlib.error) as exc:
        message = str(exc)
        status = ('resource_limit' if 'limit' in message.lower() else
                  'unsupported' if 'unsupported' in message.lower() or 'External' in message
                  else 'corrupt')
        return {'status': status, 'partial': False, 'format': 'ooxml-spreadsheet',
                'text': None, 'location': {'part': part}, 'message': message,
                'producer_verified': False}
