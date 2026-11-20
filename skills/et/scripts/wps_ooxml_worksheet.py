"""Bounded tolerant SpreadsheetML worksheet, row, cell and string parsing.

This module owns the value-bearing worksheet layer: cell/row extraction, tolerant
inline/shared-string text, column/merge/hyperlink/view metadata and best-effort
comments. Every malformed optional or value-bearing item is recorded as an
aggregated issue and skipped where necessary; only XML/ZIP safety faults abort.
"""
from dataclasses import dataclass
from decimal import Decimal
import re

from wps_ooxml import (
    TRANSITIONAL_REL, STRICT_REL, fromstring, is_security_error, read_part,
    split_tag,
)
from wps_ooxml_stream import prepare_worksheet
from wps_ooxml_escape import decode_ooxml_text
from wps_numeric import interpret_number
from wps_ooxml_text import MAX_CELL_TEXT, TextReader, is_main
from wps_ooxml_worksheet_meta import (
    apply_row_column_styles as _apply_row_column_styles,
    column_style_for,
    read_columns as _read_columns,
    read_filters as _read_filters,
    read_hyperlinks as _read_hyperlinks,
    read_merges as _read_merges,
    read_sheet_relations,
    read_sheet_views as _read_sheet_views,
    report_unknown_worksheet_elements as _report_unknown_worksheet_elements,
)
from wps_iso_date import parse_iso_date
from wps_sheet_comments import read_comments

TRANS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
STRICT = 'http://purl.oclc.org/ooxml/spreadsheetml/main'
MAIN_NAMESPACES = (TRANS, STRICT)
REL_NAMESPACE_BASES = (TRANSITIONAL_REL[:-1], STRICT_REL[:-1])
MAX_CELLS = 200000
MAX_ROWS = 200000
MAX_SST = 200000
MAX_MERGES = 65536
MAX_MERGE_OVERLAP_CHECKS = 1024
MAX_HYPERLINKS = 4096
MAX_COLUMN_RANGES = 16384
ERRORS = {'#NULL!', '#DIV/0!', '#VALUE!', '#REF!', '#NAME?', '#NUM!', '#N/A', '#GETTING_DATA'}
CELL_TYPES = ('n', 's', 'inlineStr', 'str', 'e', 'b', 'd')


def need(test, message):
    if not test:
        raise ValueError(message)


def integer(value, maximum):
    need(isinstance(value, str) and re.fullmatch(r'[0-9]{1,10}', value) is not None,
         'Invalid bounded integer')
    result = int(value)
    need(result <= maximum, 'Integer outside supported bounds')
    return result


def coordinate(value):
    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', value or '')
    need(match is not None, 'Invalid cell coordinate')
    col = 0
    for character in match[1]:
        col = col * 26 + ord(character) - 64
    row = int(match[2])
    need(row <= 1048576 and col <= 16384, 'Cell coordinate outside OOXML bounds')
    return row - 1, col - 1


def cell_range(value):
    ends = value.split(':')
    need(len(ends) in (1, 2), 'Invalid cell range')
    first, last = coordinate(ends[0]), coordinate(ends[-1])
    need(first[0] <= last[0] and first[1] <= last[1], 'Reversed cell range')
    return [first[0], last[0], first[1], last[1]]


def column_name(index):
    number = index + 1
    letters = ''
    while number:
        number, digit = divmod(number - 1, 26)
        letters = chr(65 + digit) + letters
    return letters


def saved_dimension(raw, field):
    """Return bounded, saved dimension metadata without affecting cell parsing."""
    if raw is None:
        return {}
    if len(raw) <= 64 and re.fullmatch(
            r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]{1,4})?', raw.strip()):
        value = Decimal(raw)
        if value >= 0:
            return {field: str(value)}
    return {field + '_status': 'uninterpreted'}


def main_children(element, local):
    return [child for child in element if is_main(child, local)]


def note_foreign_attributes(report, element, sheet=None, cell=None):
    """Record (never reject) namespaced extension attributes on parsed elements."""
    for key in element.attrib:
        namespace, local = split_tag(key)
        if not namespace or namespace == 'http://www.w3.org/XML/1998/namespace':
            continue
        if namespace in REL_NAMESPACE_BASES:
            continue
        report.add('foreign_attribute_not_interpreted', element=str(element.tag)[:120],
                   attribute=local[:60], sheet=sheet, cell=cell)


def relationship_id(element):
    for base in REL_NAMESPACE_BASES:
        value = element.get('{' + base + '}id')
        if isinstance(value, str) and value:
            return value
    return None


def load_part_xml(package, name, budget, report, code, **detail):
    """Parse one part; bounded read/XML failures become issues instead of aborts."""
    try:
        raw = read_part(package, name)
    except (ValueError, KeyError) as exc:
        if is_security_error(exc):
            raise
        report.add(code, 'Part was not read after a bounded read failure', part=name,
                   reason=str(exc)[:120], **detail)
        return None
    try:
        return fromstring(raw, budget=budget)
    except ValueError as exc:
        if is_security_error(exc):
            raise
        report.add(code, 'Part was not interpreted after a bounded XML failure', part=name,
                   reason=str(exc)[:120], **detail)
        return None


@dataclass(frozen=True)
class PackageEnv:
    """Package-level XML access, budget, and shared diagnostic tracking."""

    package: object
    budget: object
    report: object
    types: object
    text_reader: object
    used_parts: set
    used_comment_parts: set
    main_part: str


@dataclass(frozen=True)
class WorkbookStyles:
    """Workbook-wide strings, style formats, number formats, and date system."""

    namespace: str
    conformance: str
    strings: object
    styles: object
    formats: object
    number_formats: object
    date_system: object
    string_encoding: tuple = ()


class SheetContext:
    """Immutable workbook facts plus mutable shared reader state for one worksheet."""

    def __init__(self, package_env: PackageEnv, workbook_styles: WorkbookStyles,
                 sheet_part: str, sheet_name: str):
        self.package = package_env.package
        self.budget = package_env.budget
        self.report = package_env.report
        self.types = package_env.types
        self.text_reader = package_env.text_reader
        self.used_parts = package_env.used_parts
        self.used_comment_parts = package_env.used_comment_parts
        self.main_part = package_env.main_part
        self.namespace = workbook_styles.namespace
        self.conformance = workbook_styles.conformance
        self.strings = workbook_styles.strings
        self.string_encoding = workbook_styles.string_encoding
        self.styles = workbook_styles.styles
        self.formats = workbook_styles.formats
        self.number_formats = workbook_styles.number_formats
        self.date_system = workbook_styles.date_system
        self.sheet_part = sheet_part
        self.sheet_name = sheet_name
        self.rows = []
        self.columns = []
        self.merges = []
        self.filters = []
        self.hyperlinks = []
        self.views = []
        self.cells = {}
        self.total_cells = 0
        self.uncertain = False

    @property
    def tag(self):
        return lambda name: '{' + self.namespace + '}' + name


def decode_cell_text(context, raw, sheet, ref):
    """Decode one saved OOXML text scalar and charge its retained characters."""
    decoded, evidence = decode_ooxml_text(raw)
    value = context.text_reader.budget_text(decoded, sheet, ref)
    if evidence and evidence.get('invalid_sequences'):
        context.report.add('ooxml_escape_invalid_surrogate', sheet=sheet, cell=ref,
                           count=evidence['invalid_sequences'])
    return value, evidence, value != decoded


def parse_cell(context, node, ref, row_number):
    """Return one tolerant cell item, or None when its saved value is unusable."""
    report = context.report
    sheet = context.sheet_name
    note_foreign_attributes(report, node, sheet=sheet, cell=ref)
    values = main_children(node, 'v')
    forms = main_children(node, 'f')
    inline_strings = main_children(node, 'is')
    for child in node:
        if not (is_main(child, 'v') or is_main(child, 'f') or is_main(child, 'is')):
            report.add('unknown_cell_element_ignored', sheet=sheet, cell=ref,
                       element=str(child.tag)[:120])
    for bucket, label in ((values, 'value'), (forms, 'formula'),
                          (inline_strings, 'inline string')):
        if len(bucket) > 1:
            report.add('duplicate_cell_payload_ignored', sheet=sheet, cell=ref, kind=label)
    raw_node = values[0] if values else None
    formula = forms[0] if forms else None
    inline_node = inline_strings[0] if inline_strings else None
    for payload, label in ((raw_node, 'value'), (formula, 'formula')):
        if payload is not None and len(payload):
            report.add('nested_cell_payload_not_interpreted', sheet=sheet, cell=ref,
                       kind=label)
            if label == 'value':
                context.uncertain = True
    raw_present = raw_node is not None
    raw = (raw_node.text or '') if raw_node is not None else None
    raw_type = node.get('t', 'n')
    if not isinstance(raw_type, str):
        raw_type = 'n'
    cell_type = raw_type if raw_type in CELL_TYPES else None
    kind = 'blank'
    value = None
    status = 'not_applicable'
    iso_date = None
    source_cell_type = None
    inline_formatting = {}
    sst_index = None
    text_encoding = None
    text_truncated = False
    if inline_node is not None:
        kind = 'text'
        value, inline_formatting = context.text_reader.cell_text(inline_node)
        text_encoding = inline_formatting.pop('text_encoding', None)
        original = value
        value = context.text_reader.budget_text(value, sheet, ref)
        if value != original:
            text_truncated = True
            if 'segments' in inline_formatting:
                del inline_formatting['segments']
                inline_formatting['segments_status'] = 'text_budget_exceeded'
        if formula is None:
            if raw_present:
                report.add('cell_value_and_inline_string_conflict', sheet=sheet, cell=ref)
        else:
            report.add('cell_formula_with_inline_string', sheet=sheet, cell=ref)
    elif cell_type == 's' and raw_present:
        try:
            sst_index = integer(raw, MAX_SST)
        except ValueError:
            sst_index = None
        if sst_index is None or sst_index >= len(context.strings):
            report.add('shared_string_index_outside_collection', sheet=sheet, cell=ref)
            context.uncertain = True
            return None
        kind = 'text'
        value = context.strings[sst_index]
        original = value
        value = context.text_reader.budget_text(value, sheet, ref)
        text_truncated = value != original
        if sst_index < len(context.string_encoding) and context.string_encoding[sst_index]:
            evidence = context.string_encoding[sst_index]
            text_encoding = {key: value for key, value in evidence.items()
                             if key not in ('raw_text', 'raw_text_truncated')}
            text_encoding['source'] = 'shared_string'
            text_encoding['sst_index'] = sst_index
    elif cell_type == 'd' and raw_present:
        try:
            iso_date = parse_iso_date(raw)
        except ValueError as exc:
            report.add('iso_date_value_not_interpreted', sheet=sheet, cell=ref,
                       reason=str(exc)[:120])
            iso_date = None
        kind = 'text'
        value, text_encoding, text_truncated = decode_cell_text(
            context, raw, sheet, ref)
        source_cell_type = 'd'
    elif raw_present:
        if cell_type is None:
            report.add('unknown_cell_type_text_retained', sheet=sheet, cell=ref,
                       cell_type=raw_type[:20])
            kind = 'text'
            value, text_encoding, text_truncated = decode_cell_text(
                context, raw, sheet, ref)
        elif cell_type == 'str':
            kind = 'text'
            value, text_encoding, text_truncated = decode_cell_text(
                context, raw, sheet, ref)
        else:
            parsed = parse_typed_value(context, cell_type, raw, ref)
            if parsed is None:
                context.uncertain = True
                return None
            kind, value, out_of_bounds = parsed
            if out_of_bounds:
                report.add('numeric_value_outside_interpretation_bounds', sheet=sheet,
                           cell=ref)
    elif formula is not None:
        status = 'missing'
        report.mark('missing_formula_cache')
        report.add('missing_formula_cache', sheet=sheet, cell=ref)
    elif cell_type is None:
        report.add('unknown_cell_type_without_value', sheet=sheet, cell=ref,
                   cell_type=raw_type[:20])
    elif cell_type != 'n':
        report.add('cell_value_missing', sheet=sheet, cell=ref, cell_type=cell_type)
    item = {
        'type': kind, 'value': value, 'cell_style_explicit': node.get('s') is not None,
        'xf_index': 0, 'number_format_id_raw': 0, 'custom_format_raw': None,
        'cache_status': status, 'source': {'part': context.sheet_part, 'cell': ref},
    }
    if sst_index is not None:
        item['sst_index'] = sst_index
    if inline_formatting:
        item['rich_text'] = inline_formatting
    if text_encoding:
        item['text_encoding'] = text_encoding
    if text_truncated:
        item['text_truncated'] = True
    if source_cell_type is not None:
        item['source_cell_type'] = source_cell_type
    if iso_date is not None:
        item['iso_date'] = iso_date
    style_valid = apply_cell_style(context, item, node.get('s'), ref)
    if formula is not None:
        formula_text = formula.text or ''
        if len(formula_text) > MAX_CELL_TEXT:
            report.add('formula_text_length_limit_reached', sheet=sheet, cell=ref)
            formula_text = formula_text[:MAX_CELL_TEXT]
        report.add('formula_expression_not_decoded', sheet=sheet, cell=ref)
        item['formula'] = {
            'encoding': 'stored_ooxml_expression', 'text': formula_text,
            'attributes_raw': dict(formula.attrib), 'expression_evaluated': False,
            'cache_may_be_stale': True,
        }
        if status != 'missing':
            item['cache_status'] = 'error_unverified' if kind == 'error' else 'unverified'
    if kind == 'number':
        item['raw_numeric_text'] = raw
        interpret_numeric(context, item, style_valid, ref)
    return item


def parse_typed_value(context, cell_type, raw, ref):
    """Interpret a saved scalar; return (kind, value, out_of_bounds) or None to skip."""
    report = context.report
    sheet = context.sheet_name
    if cell_type == 'b':
        if raw in ('0', '1', 'false', 'true'):
            return 'boolean', raw in ('1', 'true'), False
        report.add('boolean_cell_value_not_interpreted', sheet=sheet, cell=ref)
        return None
    if cell_type == 'e':
        if raw not in ERRORS:
            report.add('unknown_error_text_retained', sheet=sheet, cell=ref)
        return 'error', context.text_reader.raw_text(raw, sheet, ref), False
    if cell_type == 'str':
        return 'text', context.text_reader.raw_text(raw, sheet, ref), False
    if cell_type == 'n':
        lexical = re.fullmatch(
            r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]{1,4})?', raw)
        if lexical is None or len(raw) > 1000:
            report.add('numeric_cell_value_not_interpreted', sheet=sheet, cell=ref)
            return None
        value = Decimal(raw)
        if not value.is_finite() or abs(value.adjusted()) > 1000:
            context.uncertain = True
            return 'number', value, True
        return 'number', value, False
    report.add('cell_value_not_interpreted', sheet=sheet, cell=ref,
               cell_type=cell_type)
    return None


def apply_cell_style(context, item, raw_style, ref):
    """Resolve the bounded XF index; an invalid index falls back to an unknown style."""
    if raw_style is None:
        return True
    style = 0
    try:
        style = integer(raw_style, 4095)
    except ValueError:
        context.report.add('cell_style_index_not_interpreted', sheet=context.sheet_name,
                           cell=ref, raw=str(raw_style)[:20])
        return False
    if context.styles and style >= len(context.styles):
        context.report.add('cell_style_index_outside_collection', sheet=context.sheet_name,
                           cell=ref, raw=str(raw_style)[:20])
        return False
    if not context.styles and style != 0:
        context.report.add('cell_style_index_without_styles_part', sheet=context.sheet_name,
                           cell=ref, raw=str(raw_style)[:20])
        return False
    item['xf_index'] = style
    if context.styles:
        format_id = context.styles[style]['number_format_id_raw']
        item['number_format_id_raw'] = format_id
        format_text = context.formats.get(format_id)
        display_ok = True
        if format_text is not None:
            display_ok = context.text_reader.reserve_display_text(
                format_text, cache_key=('format', format_id))
        item['custom_format_raw'] = format_text if display_ok else None
        if format_text is not None and not display_ok:
            item['custom_format_raw_omitted'] = True
    return True


def interpret_numeric(context, item, style_valid, ref):
    """Add a bounded number-format interpretation; never changes the saved value."""
    report = context.report
    sheet = context.sheet_name
    if not style_valid:
        item['numeric_interpretation'] = {'status': 'unknown',
                                          'reason': 'cell_style_index_not_interpreted',
                                          'source_value_unchanged': True}
        report.add('number_style_inheritance_not_resolved', sheet=sheet, cell=ref)
        return
    if not context.styles:
        item['numeric_interpretation'] = {
            'status': 'resolved', 'number_format_id': 0,
            'number_format_source': {'kind': 'no_styles_part'}}
        item['numeric_interpretation'].update(
            interpret_number(item['value'], 0, None, context.date_system))
        return
    resolved = context.number_formats.resolve(item['xf_index'])
    if resolved['status'] != 'resolved':
        item['numeric_interpretation'] = resolved
        report.add('number_style_inheritance_not_resolved', sheet=sheet, cell=ref,
                   reason=resolved.get('reason'))
        return
    effective = resolved['number_format_id']
    interpreted = interpret_number(item['value'], effective,
                                   context.formats.get(effective), context.date_system)
    interpreted['number_format_source'] = resolved['source']
    item['numeric_interpretation'] = interpreted


def parse_row(context, row_node, seen_rows):
    """Parse one saved row and its cells; returns False when the row position is unknown."""
    report = context.report
    sheet = context.sheet_name
    raw_row = row_node.get('r')
    if raw_row is None:
        row_number = context.rows[-1]['row_zero_based'] + 2 if context.rows else 1
        report.add('row_reference_inferred', sheet=sheet, assigned_row=row_number)
        if row_number > 1048576:
            report.add('row_reference_not_inferred', sheet=sheet)
            context.uncertain = True
            return
    else:
        try:
            row_number = integer(raw_row, 1048576)
        except ValueError:
            row_number = 0
        if row_number < 1:
            report.add('row_reference_not_interpreted', sheet=sheet, raw=str(raw_row)[:20])
            context.uncertain = True
            return
    duplicate_row = row_number in seen_rows
    if duplicate_row:
        report.add('duplicate_row_cells_recovered', sheet=sheet, row=row_number,
                   metadata_policy='first_row_retained', cell_policy='first_readable_value_retained')
        context.uncertain = True
    seen_rows.add(row_number)
    note_foreign_attributes(report, row_node, sheet=sheet)
    raw_hidden = row_node.get('hidden')
    if raw_hidden is None:
        hidden_saved = False
    elif raw_hidden in ('0', '1', 'false', 'true'):
        hidden_saved = raw_hidden in ('1', 'true')
    else:
        hidden_saved = False
        report.add('saved_visibility_flag_not_interpreted', sheet=sheet, row=row_number,
                   raw=raw_hidden[:20])
    if not duplicate_row:
        context.rows.append({'row_zero_based': row_number - 1,
                             'style_index_raw': row_node.get('s'),
                             'custom_format_raw': row_node.get('customFormat'),
                             'hidden_saved': hidden_saved, 'hidden_cause': 'unknown',
                             'source': {'part': context.sheet_part, 'row': row_number},
                             **saved_dimension(row_node.get('ht'), 'height_pt')})
    previous_column = -1
    for cell in row_node:
        if not is_main(cell, 'c'):
            report.add('row_element_not_interpreted', sheet=sheet,
                       element=str(cell.tag)[:120])
            context.uncertain = True
            continue
        ref = cell.get('r')
        if ref is None:
            previous_column += 1
            report.add('cell_reference_inferred', sheet=sheet, row=row_number,
                       assigned_column=previous_column + 1)
            if previous_column > 16383:
                report.add('cell_reference_not_inferred', sheet=sheet, row=row_number)
                context.uncertain = True
                continue
            ref = column_name(previous_column) + str(row_number)
        try:
            row_index, column_index = coordinate(ref)
        except ValueError:
            report.add('cell_reference_not_interpreted', sheet=sheet, raw=str(ref)[:40])
            context.uncertain = True
            continue
        if row_index != row_number - 1:
            report.add('cell_row_mismatch', sheet=sheet, cell=ref, row=row_number)
        previous_column = column_index
        if ref in context.cells:
            report.add('duplicate_cell_ignored', sheet=sheet, cell=ref)
            context.uncertain = True
            continue
        if context.total_cells >= MAX_CELLS:
            report.add('cell_count_limit_reached', sheet=sheet, kept=MAX_CELLS)
            context.uncertain = True
            context.truncated = True
            return
        context.total_cells += 1
        item = parse_cell(context, cell, ref, row_number)
        if item is None:
            context.uncertain = True
            continue
        context.cells[ref] = item


def read_sheet_views(context, ws_root):
    return _read_sheet_views(context, ws_root, main_children, coordinate)


def read_columns(context, ws_root):
    _read_columns(context, ws_root, main_children, integer, saved_dimension, MAX_COLUMN_RANGES)


def read_merges(context, ws_root):
    _read_merges(context, ws_root, main_children, cell_range, MAX_MERGES, MAX_MERGE_OVERLAP_CHECKS)


def read_hyperlinks(context, ws_root, relations):
    _read_hyperlinks(context, ws_root, relations, main_children, cell_range, coordinate, relationship_id, MAX_HYPERLINKS)


def read_filters(context, ws_root):
    _read_filters(context, ws_root, main_children, cell_range)


def apply_row_column_styles(context):
    _apply_row_column_styles(context, coordinate)


def report_unknown_worksheet_elements(context, ws_root):
    _report_unknown_worksheet_elements(context, ws_root, MAIN_NAMESPACES)


def read_worksheet(context):
    """Read one worksheet part; returns None when the part/root is unusable."""
    report = context.report
    sheet = context.sheet_name
    context.truncated = False
    seen_rows = set()

    def consume(row_node):
        if context.truncated:
            return
        if not is_main(row_node, 'row'):
            report.add('sheet_data_element_not_interpreted', sheet=sheet,
                       element=str(row_node.tag)[:120])
            context.uncertain = True
            return
        if len(context.rows) >= MAX_ROWS:
            report.add('row_metadata_limit_reached', sheet=sheet, kept=MAX_ROWS)
            context.uncertain = True
            context.truncated = True
            return
        parse_row(context, row_node, seen_rows)

    # Validate the immutable part before callbacks. Non-security malformed XML
    # retains the existing unread-part behavior; safety failures still propagate.
    try:
        raw = read_part(context.package, context.sheet_part)
    except (ValueError, KeyError) as exc:
        if is_security_error(exc):
            raise
        report.add('worksheet_part_not_read',
                   'Part was not read after a bounded read failure',
                   part=context.sheet_part, reason=str(exc)[:120], sheet=sheet)
        return None
    try:
        parse_rows = prepare_worksheet(raw, budget=context.budget)
    except ValueError as exc:
        if is_security_error(exc):
            raise
        report.add('worksheet_part_not_read',
                   'Part was not interpreted after a bounded XML failure',
                   part=context.sheet_part, reason=str(exc)[:120], sheet=sheet)
        return None
    ws_root = parse_rows(consume)
    if ws_root is None:
        return None
    namespace, local = split_tag(ws_root.tag)
    if local != 'worksheet' or namespace not in MAIN_NAMESPACES:
        report.add('worksheet_root_not_interpreted', sheet=sheet,
                   element=str(ws_root.tag)[:120])
        return None
    if namespace != context.namespace:
        report.add('spreadsheet_conformance_namespace_variance', sheet=sheet)
    worksheet_tag = lambda name: '{' + namespace + '}' + name
    sheet_data = main_children(ws_root, 'sheetData')
    if len(sheet_data) > 1:
        report.add('duplicate_sheet_data_recovered', sheet=sheet, count=len(sheet_data),
                   cell_policy='first_readable_value_retained')
        context.uncertain = True
    if not sheet_data:
        report.add('worksheet_sheet_data_missing', sheet=sheet)
        context.uncertain = True
    read_columns(context, ws_root)
    read_merges(context, ws_root)
    relations, _ = read_sheet_relations(context, ws_root)
    read_hyperlinks(context, ws_root, relations)
    read_filters(context, ws_root)
    context.views = read_sheet_views(context, ws_root)
    report_unknown_worksheet_elements(context, ws_root)
    if getattr(context, 'cell_window', False) and any(
            entry['style_index_raw'] is not None for entry in context.rows + context.columns):
        raise ValueError('Unsupported parse-page optimization: row/column inherited styles')
    apply_row_column_styles(context)
    comments = read_comments(context.package, context.sheet_part, context.conformance,
                             context.namespace, context.types, context.budget,
                             context.text_reader.text_value, context.used_comment_parts,
                             ws_root, report=report, relations=relations,
                             sheet_name=sheet)
    if comments:
        report.add('comment_layout_not_decoded', sheet=sheet, part=context.sheet_part)
    chart_reader = getattr(context, 'chart_reader', None)
    charts = chart_reader.worksheet(ws_root, relations, sheet) if chart_reader else None
    return {**({'charts': charts} if charts is not None else {}),
            'cells': context.cells, 'rows': context.rows, 'columns': context.columns,
            'merges': context.merges, 'filters': context.filters,
            'hyperlinks': context.hyperlinks, 'views': context.views,
            'comments': comments, 'truncated': context.truncated}
