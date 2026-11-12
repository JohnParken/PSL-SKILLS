# SPDX-License-Identifier: MIT
"""Tolerant BIFF8 worksheet-substream parsing split from ``wps_biff8``.

One malformed record never discards already parsed cells. Structural
contradictions that would silently misbind a value (duplicate address,
coordinate outside the BIFF8 grid, non-finite number, unknown SST index) skip
only that record/cell and add an omission diagnostic. Metadata groups
(visibility, views, merges, formula groups, conditional formatting, validation,
notes) retain bounded raw bytes and continue.

Read-only hard boundaries are unchanged: nothing is executed, no link is
followed, no macro is loaded and no byte is ever written back.
"""
import struct

from wps_biff8_hyperlinks import (HLINK, HLINK_TOOLTIP, MAX_HYPERLINKS,
                                  parse_hlink, parse_hlink_tooltip)
from wps_biff8_cells import (_handle_cell_record, _handle_formula_group,
                             _handle_multi_cell)
from wps_biff8_charts import chart_records_end
from wps_biff8_conditional import conditional_group, validation_group
from wps_biff8_drawing import _handle_chart, _handle_drawing, _handle_object
from wps_biff8_errors import ResourceLimitError
from wps_biff8_headers import column_name
from wps_biff8_merges import MergeOverlapError, validate_merge_ranges
from wps_biff8_noncell import inspect_noncell, inspect_page_break_layout
from wps_biff8_records import bof
from wps_biff8_retain import strings, retain, salvage, salvage_metadata
from wps_sheet_views import (BIFF_VIEW_RECORDS, biff_sheet_view,
                             check_biff_pane)
from wps_visibility import SavedVisibility

SHEET_EOF = 0x000A
WSBOOL = 0x0081
UNCALCED = 0x005E
CELL_RECORDS = (0x0203, 0x027E, 0x0205, 0x0201, 0x00FD, 0x0204, 0x00D6, 0x0006)
MUL_RECORDS = (0x00BD, 0x00BE)
GROUP_DEFINITIONS = (0x04BC, 0x0221)
MERGE_CELLS = 0x00E5
ROW = 0x0208
COLINFO = 0x007D
FILTER_RECORDS = (0x009B, 0x009D, 0x009E)
VIEW_WINDOW = 0x023E
ROW_COLINFO_KINDS = (ROW, COLINFO)
PAGE_BREAK_KINDS = (0x001A, 0x001B)
ORPHAN_RULE_KINDS = (0x01B1, 0x01BE)
STRICT_STRING_RECORD = 0x0207
CONDITIONAL_KINDS = (0x01B0, 0x01B2)


class SheetState:
    """Mutable per-worksheet state; every mutation goes through the reader."""

    def __init__(self, reader, bound):
        self.reader = reader
        self.bound = bound
        self.cells = {}
        self.merges = []
        self.formula_groups = {}
        self.formula_refs = []
        self.last_formula = {}
        self.visibility = SavedVisibility()
        self.views = []
        self.comments = []
        self.hyperlinks = []
        self.pending = None
        self.chart_pending = None
        self.object_ids = set()
        self.ws_bool = False

    def omit(self, record, code, message, **extra):
        """Skip one record/cell, count it and mark value coverage as uncertain."""
        reader = self.reader
        reader.omitted_cell_records += 1
        reader.omissions_affect_cell_values = True
        key = (code, record[1] if record is not None else None)
        event = reader.grouped_issue(key, {
            'code': code, 'sheet': self.bound['name'],
            'record_id': hex(record[1]) if record is not None else None,
            'record_offset': record[0] if record is not None else None,
            'count': 0, 'cell_value_impact': 'omitted_or_unknown',
            'message': message, **extra})
        if event is not None:
            event['count'] = event.get('count', 0) + 1

    def put(self, row, col, xf, kind, value, record, **extra):
        """Add one value cell; return its address or ``None`` when omitted."""
        reader = self.reader
        if not (0 <= row < 65536 and 0 <= col < 256):
            self.omit(record, 'cell_coordinate_out_of_range',
                      'Cell coordinate outside the BIFF8 grid; record skipped',
                      row_zero_based=row, column_zero_based=col)
            return None
        if not (0 <= xf < len(reader.styles)):
            self.omit(record, 'xf_index_out_of_range_fallback',
                      'Cell XF index outside the XF table; style 0 fallback '
                      'applied and the stored value kept',
                      xf_index_raw=xf, style_fallback_applied=True)
            extra['xf_index_raw'] = xf
            extra['style_fallback_applied'] = True
            xf = 0
        address = column_name(col + 1) + str(row + 1)
        if address in self.cells:
            self.omit(record, 'duplicate_cell_omitted',
                      'Duplicate cell address; later record skipped',
                      cell=address)
            return None
        if reader.cell_count >= reader.limits['max_cells']:
            raise ResourceLimitError('Cell limit exceeded')
        style = reader.styles[xf]
        if style.get('valid', True) is False:
            extra['xf_slot_invalid'] = True
            event = reader.grouped_issue(('xf_slot_invalid',), {
                'code': 'xf_slot_invalid_fallback',
                'sheet': self.bound['name'], 'xf_index_raw': xf,
                'count': 0,
                'message': 'Cell refers to a malformed XF slot; index slots are '
                           'preserved, number format falls back to General and '
                           'the stored value is kept.'})
            if event is not None:
                event['count'] += 1
        fmt = style['number_format_id_raw']
        fmt_text = reader.formats.get(fmt)
        display_ok = True
        if fmt_text is not None:
            display_ok = reader.reserve_display_text(
                fmt_text, cache_key=('format', fmt))
        reader.reserve_output_text(value)
        if fmt_text is not None and not display_ok:
            extra['custom_format_raw_omitted'] = True
        self.cells[address] = {
            'type': kind, 'value': value, 'xf_index': xf,
            'number_format_id_raw': fmt,
            'custom_format_raw': fmt_text if display_ok else None,
            'record_offset': record[0], 'record_id': hex(record[1]),
            'cache_status': 'not_applicable', **extra}
        reader.cell_count += 1
        return address


def _handle_merge_cells(reader, state, record, payload):
    name = state.bound['name']
    if len(payload) < 2:
        salvage_metadata(reader, record, name, code='merge_record_invalid')
        reader.issue({'code': 'merge_record_invalid', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'Truncated MergeCells record retained raw.'})
        return
    count = struct.unpack_from('<H', payload)[0]
    if len(payload) != 2 + 8 * count:
        salvage_metadata(reader, record, name, code='merge_record_invalid')
        reader.issue({'code': 'merge_record_invalid', 'sheet': name,
                      'record_offset': record[0],
                      'declared_merge_count': count,
                      'message': 'Malformed merge record length retained raw; '
                                 'merges not expanded.'})
        return
    accepted = min(count, max(0, reader.limits['max_merges']
                              - reader.merge_count))
    for ordinal, start in enumerate(range(2, 2 + 8 * accepted, 8)):
        r1, r2, c1, c2 = struct.unpack_from('<4H', payload, start)
        if not (r1 <= r2 and c1 <= c2 < 256 and (r1, c1) != (r2, c2)):
            reader.issue({'code': 'merge_bounds_invalid', 'sheet': name,
                          'record_offset': record[0], 'merge_ordinal': ordinal,
                          'bounds_zero_based': [r1, r2, c1, c2],
                          'message': 'Invalid merge bounds skipped; later '
                                     'merges retained.'})
            continue
        state.merges.append({'range': column_name(c1 + 1) + str(r1 + 1) + ':'
                             + column_name(c2 + 1) + str(r2 + 1),
                             'bounds_zero_based': [r1, r2, c1, c2],
                             'record_offset': record[0]})
        reader.merge_count += 1
    if accepted < count:
        reader.issue({'code': 'merge_budget_truncated', 'sheet': name,
                      'record_offset': record[0],
                      'skipped_merge_count': count - accepted,
                      'message': 'Merge count budget exhausted; later merged '
                                 'regions are not retained but cell values are '
                                 'unaffected.'})


def _handle_visibility(reader, state, record, kind, payload):
    name = state.bound['name']
    try:
        state.visibility.add(kind, payload, record[0])
    except ValueError as exc:
        reader.issue({'code': 'row_column_metadata_invalid', 'sheet': name,
                      'record_offset': record[0], 'record_id': hex(kind),
                      'message': 'Invalid saved visibility/filter metadata '
                                 'skipped: ' + str(exc)})
        salvage(reader, record, name, code='row_column_metadata_invalid',
                 may_affect=False)
        return False
    return True


def _handle_row_colinfo(reader, state, record, kind, payload):
    """Retain structured Row/ColInfo under a separate bounded budget."""
    name = state.bound['name']
    if reader.structured_row_count >= reader.limits['max_structured_rows']:
        reader.structured_metadata_truncated += 1
        event = reader.grouped_issue(('structured_metadata_truncated', kind), {
            'code': 'structured_metadata_budget_truncated', 'sheet': name,
            'record_offset': record[0], 'record_id': hex(kind), 'count': 0,
            'cell_value_impact': 'none_within_parsed_cells',
            'message': 'Structured Row/ColInfo entry budget exhausted; later '
                       'saved visibility is not retained.'})
        if event is not None:
            event['count'] += 1
        return
    extra = reader.limits['max_structured_bytes']
    if extra and reader.structured_metadata_bytes + len(payload) > extra:
        reader.structured_metadata_truncated += 1
        reader.issue({'code': 'structured_metadata_byte_budget_truncated',
                      'sheet': name, 'record_offset': record[0],
                      'record_id': hex(kind),
                      'cell_value_impact': 'none_within_parsed_cells',
                      'message': 'Structured Row/ColInfo byte budget '
                                 'exhausted; saved visibility beyond the budget '
                                 'is not retained.'})
        return
    if not _handle_visibility(reader, state, record, kind, payload):
        return
    reader.structured_metadata_bytes += len(payload)
    reader.structured_row_count += 1


def _handle_filter(reader, state, record, kind, payload):
    name = state.bound['name']
    if not _handle_visibility(reader, state, record, kind, payload):
        return
    retain(reader, record, 'SavedVisibilityOrFilter', name)
    reader.issue({'sheet': name, 'record_offset': record[0],
                  'code': 'saved_filter_not_evaluated',
                  'message': 'Saved filter metadata retained; conditions not '
                             'decoded or applied, hidden cause unknown. '
                             'Default includes every row.'})


def _handle_view(reader, state, records, index):
    name = state.bound['name']
    start = index - 1
    next_index, view = biff_sheet_view(records, start)
    state.views.append(view)
    for component in records[start:next_index]:
        retain(reader, component,
               BIFF_VIEW_RECORDS.get(component[1], 'ViewRecordOpaque'), name)
    if view.get('issues'):
        reader.issue({'code': 'saved_sheet_view_partially_read', 'sheet': name,
                      'record_offset': records[start][0]})
    return next_index


def _handle_page_break(reader, state, record, kind, payload, layout_state):
    name = state.bound['name']
    previous = layout_state['last_key'][kind]
    try:
        layout, layout_state['last_key'][kind] = inspect_page_break_layout(
            kind, payload, previous, layout_state['ends'][kind])
    except ValueError as exc:
        reader.issue({'code': 'page_break_metadata_invalid', 'sheet': name,
                      'record_offset': record[0], 'record_id': hex(kind),
                      'message': str(exc)})
        salvage(reader, record, name, code='page_break_metadata_invalid',
                 may_affect=False)
        return
    index = retain(reader, record, 'HorizontalPageBreaks'
                    if kind == 0x001B else 'VerticalPageBreaks', name)
    if index is not None:
        reader.metadata[index]['structure'] = layout
    if layout['layout_issues']:
        reader.issue({'code': 'page_break_layout_not_applied', 'sheet': name,
                      'record_offset': record[0], 'record_id': hex(kind),
                      'metadata_index': index,
                      'cell_value_impact': 'none_within_parsed_cells',
                      'message': 'Invalid page-break layout retained raw; see '
                                 'structure.layout_issues. Saved cells are read '
                                 'without applying print layout.'})


def _handle_rule_group(reader, state, records, index, record, kind, payload):
    name = state.bound['name']
    options = {'tolerate_semantics': True}
    try:
        if kind == 0x01B0:
            stop, evidence = conditional_group(records, index - 1, **options)
            metadata_names = {0x01B0: 'CondFmtOpaque', 0x01B1: 'CFOpaque'}
        else:
            stop, evidence = validation_group(
                records, index - 1,
                max(0, reader.limits['max_metadata_entries']
                    - len(reader.metadata)), **options)
            metadata_names = {0x01B2: 'DValOpaque', 0x01BE: 'DvOpaque'}
    except ValueError as exc:
        reader.issue({'code': 'bounded_rule_group_validation_failed',
                      'sheet': name, 'record_offset': record[0],
                      'record_id': hex(kind), 'message': str(exc)})
        salvage_metadata(reader, record, name,
                         code='bounded_rule_group_validation_failed')
        return index
    for component, structure in zip(records[index - 1:stop], evidence):
        component_index = retain(
            reader, component,
            metadata_names.get(component[1], 'RuleGroupOpaque'), name)
        if component_index is not None:
            reader.metadata[component_index]['structure'] = structure
    if any('semantic_issue' in item for item in evidence):
        reader.issue({'code': 'bounded_rule_group_not_applied', 'sheet': name,
                      'record_offset': record[0], 'record_id': hex(kind),
                      'message': 'Rule group boundaries validated; unsupported '
                                 'payload semantics retained raw.'})
    return stop


def _handle_wsbool(reader, state, record, payload):
    name = state.bound['name']
    if len(payload) != 2:
        reader.issue({'code': 'wsbool_malformed', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'WsBool payload must be two bytes; raw '
                                 'retained.'})
        salvage(reader, record, name, code='wsbool_malformed',
                 may_affect=False)
        return
    flags = struct.unpack('<H', payload)[0]
    if state.ws_bool:
        reader.issue({'code': 'wsbool_duplicate', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'Duplicate WsBool retained as metadata.'})
        retain(reader, record, 'WsBoolOpaque', name)
        return
    if flags & 0x0010:
        reader.issue({'code': 'dialog_sheet_substream', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'Dialog sheet substream retained raw; no '
                                 'dialog semantics applied.'})
        retain(reader, record, 'WsBoolOpaque', name)
    state.ws_bool = True


def _handle_uncalced(reader, state, record, payload, sheet_bof_index,
                     record_index):
    name = state.bound['name']
    if record_index != sheet_bof_index + 1:
        reader.issue({'code': 'uncalced_metadata_order', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'Uncalced should immediately follow the '
                                 'worksheet BOF; position retained in the '
                                 'diagnostic.'})
    try:
        evidence = inspect_noncell(UNCALCED, payload, 'worksheet')
    except ValueError as exc:
        reader.issue({'code': 'uncalced_metadata_invalid', 'sheet': name,
                      'record_offset': record[0], 'message': str(exc)})
        salvage(reader, record, name, code='uncalced_metadata_invalid',
                 may_affect=False)
        return
    index = retain(reader, record, 'UncalcedValidated', name)
    if index is not None:
        reader.metadata[index]['structure'] = evidence
    reader.issue({'sheet': name, 'offset': record[0],
                  'code': 'saved_formula_recalculation_pending',
                  'message': 'Uncalced signals saved formulas were pending '
                             'recalculation; cached values remain unverified.'})


def _handle_orphan_substream(reader, state, records, start):
    """Retain a nested BOF..EOF substream without letting it swallow cells."""
    name = state.bound['name']
    depth = 0
    cursor = start
    while cursor < len(records) and cursor - start < 4096:
        kind = records[cursor][1]
        if kind == 0x0809:
            depth += 1
        elif kind == SHEET_EOF:
            depth -= 1
        cursor += 1
        if kind == SHEET_EOF and depth <= 0:
            break
    for component in records[start:cursor]:
        retain(reader, component, 'OrphanSubstreamRecordOpaque', name)
    reader.salvage_data_uncertain = True
    reader.issue({'code': 'orphan_substream_retained', 'sheet': name,
                  'record_offset': records[start][0],
                  'record_count': cursor - start,
                  'cell_value_impact': 'unknown',
                  'message': 'Nested BOF..EOF substream outside a validated '
                             'worksheet object retained raw; it does not '
                             'consume later worksheet cells.'})
    return cursor


def _visibility_fallback(reader, state, name, exc):
    reader.issue({'code': 'visibility_metadata_invalid', 'sheet': name,
                  'message': 'Saved visibility report was inconsistent; raw '
                             'rows/columns retained: ' + str(exc)})
    visibility = state.visibility
    return {'rows': list(visibility.rows.values()),
            'columns': visibility.columns,
            'unspecified_visibility': 'unknown',
            'default_selection': 'all',
            'filters': {'saved_filter_mode': visibility.filter_mode,
                        'column_count': visibility.filter_count,
                        'records': visibility.filter_records,
                        'conditions_evaluated': False},
            'hidden_cause_policy': 'Saved hidden flags do not distinguish '
                                   'manual hiding from filtering',
            'validation_issue': str(exc)}


def _finish_sheet(reader, state, bound, bof_raw, read_status='read'):
    """Validate accumulated metadata and build the sheet result object."""
    name = bound['name']
    if not state.ws_bool:
        reader.issue({'sheet': name, 'code': 'wsbool_missing',
                      'message': 'Mandatory worksheet WsBool absent; sheet '
                                 'semantics unverified. Parsed cell values are '
                                 'retained.'})
    merge_issues = []
    try:
        validate_merge_ranges([item['bounds_zero_based']
                               for item in state.merges])
    except MergeOverlapError as exc:
        merge_issues.append({
            'code': 'merge_overlap_detected', 'sheet': name,
            'merge_index': exc.merge_index,
            'record_offset': state.merges[exc.merge_index]['record_offset'],
            'message': 'Overlapping merges retained as saved ranges; cell '
                       'values unaffected. No merge expansion or erasure '
                       'applied.'})
    except ValueError as exc:
        merge_issues.append({'code': 'merge_record_invalid', 'sheet': name,
                             'message': str(exc)})
    for issue in merge_issues:
        reader.issue(issue)
    counts = {anchor: 0 for anchor in state.formula_groups}
    for row, col, anchor, shared, address in state.formula_refs:
        group = state.formula_groups.get(anchor)
        if group is None:
            reader.issue({'code': 'formula_group_member_mismatch',
                          'sheet': name, 'cell': address,
                          'message': 'Shared/array formula has no retained '
                                     'group definition; saved cache kept.'})
            continue
        first_row, last_row, first_col, last_col = group['bounds_zero_based']
        if not (first_row <= row <= last_row
                and first_col <= col <= last_col):
            reader.issue({'code': 'formula_group_member_mismatch',
                          'sheet': name, 'cell': address,
                          'message': 'Formula lies outside its shared/array '
                                     'range; saved cache kept.'})
            continue
        if shared != (group['kind'] == 'shared'):
            reader.issue({'code': 'formula_group_member_mismatch',
                          'sheet': name, 'cell': address,
                          'message': 'Formula shared flag disagrees with its '
                                     'group; saved cache kept.'})
            continue
        counts[anchor] += 1
        cell = state.cells.get(address)
        if cell is not None and 'formula' in cell:
            cell['formula']['group_anchor'] = group['anchor']
            cell['formula']['group_kind'] = group['kind']
    for anchor, group in state.formula_groups.items():
        observed = counts.get(anchor, 0)
        if group.get('use_count_unspecified'):
            reader.issue({'code': 'formula_group_use_count_unspecified',
                          'sheet': name, 'cell': group['anchor'],
                          'observed_members': observed,
                          'declared_c_use': 0,
                          'cell_value_impact': 'metadata_only',
                          'message': 'Shared formula ShrFmla cUse is zero; '
                                     'membership is validated by range and '
                                     'shared flag, but the declared use count '
                                     'is not enforced and group semantics stay '
                                     'unverified. Saved caches are retained.'})
        elif observed != group['expected_members']:
            reader.issue({'code': 'formula_group_member_mismatch',
                          'sheet': name, 'cell': group['anchor'],
                          'observed_members': observed,
                          'expected_members': group['expected_members'],
                          'message': 'Shared/array group member count differs '
                                     'from the saved declaration; retained '
                                     'caches unaffected.'})
        group['observed_members'] = observed
    try:
        visibility = state.visibility.report()
    except ValueError as exc:
        visibility = _visibility_fallback(reader, state, name, exc)
    if state.hyperlinks:
        reader.issue({'code': 'hyperlink_targets_not_resolved', 'sheet': name,
                      'count': len(state.hyperlinks),
                      'target_resolved': False, 'followed': False,
                      'message': 'BIFF8 hyperlink metadata returned; targets '
                                 'were not resolved, opened or followed.'})
    return {**bound, 'bof_raw_hex': bof_raw, 'cells': state.cells,
            'read_status': read_status,
            'merges': state.merges,
            'formula_groups': list(state.formula_groups.values()),
            'visibility': visibility, 'views': state.views,
            **({'comments': state.comments} if state.comments else {}),
            **({'hyperlinks': state.hyperlinks} if state.hyperlinks else {}),
            **({'merge_validation_issues': merge_issues} if merge_issues
               else {})}


def _scan_substream_end(records, index):
    """Frame-wise scan of a skipped substream to its exclusive EOF boundary."""
    while index < len(records):
        _, kind, _payload = records[index]
        index += 1
        if kind == SHEET_EOF:
            return index
    return index


def _skeleton_sheet(bound, reason, read_status='not_read'):
    return {**bound, 'bof_raw_hex': None, 'cells': {}, 'merges': [],
            'formula_groups': [],
            'visibility': {'rows': [], 'columns': [],
                           'unspecified_visibility': 'unknown',
                           'default_selection': 'all',
                           'filters': {'saved_filter_mode': False,
                                       'column_count': None, 'records': [],
                                       'conditions_evaluated': False}},
            'views': [], 'substream_skipped': reason,
            'read_status': read_status}


def parse_sheet(reader, index, bound):
    """Parse one worksheet substream; always returns a sheet description."""
    records = reader.records
    name = bound['name']
    sheet_bof_index = index
    reader.current_location = {'record_offset': records[index][0],
                               'record_id': hex(records[index][1]),
                               'sheet': name}
    try:
        bof_raw = bof(records[index], 0x0010)
    except ValueError as exc:
        reader.issue({'code': 'worksheet_bof_invalid', 'sheet': name,
                      'record_offset': records[index][0],
                      'message': 'Worksheet substream does not start with a '
                                 'valid BIFF8 worksheet BOF: ' + str(exc)})
        reader.omissions_affect_cell_values = True
        return (_skeleton_sheet(bound, 'invalid_bof'),
                _scan_substream_end(records, index + 1))
    index += 1
    state = SheetState(reader, bound)
    layout_state = {'last_key': {0x001B: None, 0x001A: None},
                    'ends': {0x001B: {}, 0x001A: {}}}
    while index < len(records):
        record = records[index]
        offset, kind, payload = record
        index += 1
        reader.current_location = {'record_offset': offset,
                                   'record_id': hex(kind), 'sheet': name}
        try:
            index = _dispatch_sheet_record(reader, state, records, index,
                                           record, kind, payload,
                                           layout_state,
                                           sheet_bof_index)
        except ResourceLimitError:
            raise
        except ValueError as exc:
            reader.issue({'code': 'worksheet_record_validation_failed',
                          'sheet': name, 'record_offset': offset,
                          'record_id': hex(kind), 'message': str(exc)})
            salvage(reader, record, name,
                     code='worksheet_record_validation_failed')
        if kind == SHEET_EOF:
            return _finish_sheet(reader, state, bound, bof_raw), index
    reader.omissions_affect_cell_values = True
    reader.issue({'code': 'worksheet_eof_missing', 'sheet': name,
                  'record_offset': records[sheet_bof_index][0],
                  'message': 'Worksheet substream ended without EOF; parsed '
                             'cells are retained.'})
    return _finish_sheet(reader, state, bound, bof_raw, 'partial'), index


def _dispatch_sheet_record(reader, state, records, index, record, kind, payload,
                           layout_state, sheet_bof_index):
    """Handle one worksheet record; returns the next record index."""
    name = state.bound['name']
    if state.pending is not None and kind not in GROUP_DEFINITIONS:
        pending = state.pending
        state.pending = None
        cell = state.cells.get(pending)
        if kind == STRICT_STRING_RECORD:
            string_reader = strings(reader, records, index - 1)
            try:
                value = string_reader.string(ignore_reserved_flags=True)
                next_index = string_reader.finish()
            except ValueError as exc:
                reader.issue({'sheet': name, 'cell': pending,
                              'code': 'formula_string_cache_invalid',
                              'record_offset': record[0],
                              'message': 'Malformed String cache for a pending '
                                         'formula; value kept blank: '
                                         + str(exc)})
                reader.missing_formula_cache = True
                reader.issue({'sheet': name, 'cell': pending,
                              'code': 'missing_formula_string_cache',
                              'expected': 'String after Formula and optional '
                                          'group definition'})
                if cell is not None:
                    cell.update(type='blank', value=None,
                                cache_status='missing')
                return index
            if cell is not None:
                if string_reader.last_flags_raw & 0xFE:
                    cell['cache_string_header_flags_raw'] = (
                        string_reader.last_flags_raw)
                    reader.plain_string_flags(record, string_reader.last_flags_raw,
                                              name, pending)
                reader.reserve_output_text(value)
                cell.update(type='text', value=value, cache_status='unverified',
                            cache_record_offset=record[0])
            return next_index
        if cell is not None:
            cell.update(type='blank', value=None, cache_status='missing')
        reader.missing_formula_cache = True
        reader.issue({'sheet': name, 'cell': pending,
                      'code': 'missing_formula_string_cache',
                      'expected': 'String after Formula and optional group '
                                  'definition'})
    if state.chart_pending is not None:
        stop = _handle_chart(reader, state, records, index - 1)
        if stop is not None:
            return stop
    if kind == SHEET_EOF:
        if payload:
            reader.issue({'code': 'worksheet_eof_payload_ignored', 'sheet':
                          name, 'record_offset': record[0],
                          'message': 'Worksheet EOF payload retained raw.'})
            salvage(reader, record, name,
                     code='worksheet_eof_payload_ignored', may_affect=False)
        return index
    if kind == 0x00EC:
        return _handle_drawing(reader, state, records, index - 1)
    if kind == 0x0809:
        return _handle_orphan_substream(reader, state, records, index - 1)
    if kind in CELL_RECORDS:
        return _handle_cell_record(reader, state, records, index, record, kind,
                                   payload)
    if kind in GROUP_DEFINITIONS:
        return _handle_formula_group(reader, state, records, index, record,
                                     kind, payload)
    if kind in MUL_RECORDS:
        return _handle_multi_cell(reader, state, index, record, kind, payload)
    if kind == MERGE_CELLS:
        _handle_merge_cells(reader, state, record, payload)
        return index
    if kind in ROW_COLINFO_KINDS:
        _handle_row_colinfo(reader, state, record, kind, payload)
        return index
    if kind in FILTER_RECORDS:
        _handle_filter(reader, state, record, kind, payload)
        return index
    if kind == VIEW_WINDOW:
        return _handle_view(reader, state, records, index)
    if kind in PAGE_BREAK_KINDS:
        _handle_page_break(reader, state, record, kind, payload, layout_state)
        return index
    if kind == 0x0041:
        return _handle_pane(reader, state, record, index)
    if kind == WSBOOL:
        _handle_wsbool(reader, state, record, payload)
        return index
    if kind == UNCALCED:
        _handle_uncalced(reader, state, record, payload, sheet_bof_index,
                         index - 1)
        return index
    if kind in CONDITIONAL_KINDS:
        return _handle_rule_group(reader, state, records, index, record, kind,
                                  payload)
    if kind in ORPHAN_RULE_KINDS:
        reader.issue({'code': 'orphan_rule_record', 'sheet': name,
                      'record_offset': record[0], 'record_id': hex(kind),
                      'message': 'Conditional-format/validation rule record '
                                 'without its rule group retained raw.'})
        salvage_metadata(reader, record, name, code='orphan_rule_record')
        return index
    if kind == HLINK:
        return _handle_hyperlink(reader, state, record, index)
    if kind == HLINK_TOOLTIP:
        return _handle_hyperlink_tooltip(reader, state, record, index)
    if kind == 0x005D:
        return _handle_object(reader, state, records, index - 1)
    if kind in (0x001C, 0x01B6):
        reader.issue({'code': 'note_record_outside_drawing', 'sheet': name,
                      'record_offset': record[0], 'record_id': hex(kind),
                      'message': 'Note text/link record outside a validated '
                                 'note drawing retained raw.'})
        salvage_metadata(reader, record, name, code='note_record_outside_drawing')
        return index
    if kind == 0x003C:
        reader.issue({'code': 'unexpected_continue_record', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'Continue record outside a string/chain '
                                 'context retained raw.'})
        salvage(reader, record, name, code='unexpected_continue_record')
        return index
    if kind in reader.sheet_metadata:
        retain(reader, record, reader.sheet_metadata[kind], name)
        return index
    return reader.unknown_record(records, index - 1, name)


def _handle_hyperlink(reader, state, record, index):
    """Read one HLink record; malformed records remain raw evidence."""
    name = state.bound['name']
    if len(state.hyperlinks) >= MAX_HYPERLINKS:
        reader.issue({'code': 'hlink_count_budget', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'HLink count budget exceeded; later records '
                                 'retained raw.'})
        salvage_metadata(reader, record, name, code='hlink_count_budget')
        return index
    try:
        item = parse_hlink(record[2], record)
    except ValueError as exc:
        reader.issue({'code': 'hlink_invalid', 'sheet': name,
                      'record_offset': record[0], 'message': str(exc)})
        salvage_metadata(reader, record, name, code='hlink_invalid')
        return index
    if not reader.reserve_display_text(item.get('display'), item.get('target'),
                                       item.get('location'), item.get('moniker')):
        item['display'] = None
        item['target'] = None
        item['location'] = None
        item['moniker'] = None
        item['link_text_omitted'] = True
    if item['ole_moniker_opaque'] or item['unparsed_remaining_fields']:
        retain(reader, record, 'HLinkOpaque', name,
               structure={'target_kind': item['target_kind'],
                          'ole_moniker_opaque': item['ole_moniker_opaque'],
                          'unparsed_remaining_fields':
                              item['unparsed_remaining_fields']})
    state.hyperlinks.append(item)
    return index


def _handle_hyperlink_tooltip(reader, state, record, index):
    """Attach one HLinkTooltip to its matching HLink by Ref8U raw bytes."""
    name = state.bound['name']
    try:
        tooltip = parse_hlink_tooltip(record[2], record)
    except ValueError as exc:
        reader.issue({'code': 'hlink_tooltip_invalid', 'sheet': name,
                      'record_offset': record[0], 'message': str(exc)})
        salvage_metadata(reader, record, name, code='hlink_tooltip_invalid')
        return index
    match = None
    for item in reversed(state.hyperlinks):
        source = item.get('source') or {}
        if (source.get('ref_raw_hex') == tooltip['ref_raw_hex']
                and 'tooltip' not in item):
            match = item
            break
    if match is None:
        reader.issue({'code': 'hlink_tooltip_orphan', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'HLinkTooltip has no matching HLink; retained '
                                 'raw.'})
        salvage_metadata(reader, record, name, code='hlink_tooltip_orphan')
        return index
    if not reader.reserve_display_text(tooltip.get('tooltip')):
        salvage_metadata(reader, record, name, code='hlink_text_budget')
        return index
    match['tooltip'] = tooltip['tooltip']
    match['tooltip_source'] = tooltip['source']
    return index


def _handle_pane(reader, state, record, index):
    """Retain an orphan Pane record; the view reader owns panes in views."""
    name = state.bound['name']
    try:
        active = check_biff_pane(record)
    except ValueError as exc:
        reader.issue({'code': 'pane_metadata_invalid', 'sheet': name,
                      'record_offset': record[0],
                      'record_id': hex(record[1]), 'message': str(exc)})
        salvage(reader, record, name, code='pane_metadata_invalid',
                 may_affect=False)
        return index
    metadata_index = retain(reader, record, 'Pane', name)
    if metadata_index is not None:
        reader.metadata[metadata_index]['structure'] = {
            'x_split_raw': active[0], 'y_split_raw': active[1],
            'active_pane': active[2], 'pane_applied': False}
    return index



# Public helpers used by the reader for positioning and skipped substreams.
skeleton_sheet = _skeleton_sheet
scan_substream_end = _scan_substream_end
