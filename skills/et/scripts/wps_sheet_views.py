"""Read saved worksheet view metadata independently of cell extraction."""
from decimal import Decimal
import re
import struct

from wps_biff8_errors import require
from wps_biff8_headers import column_name


PANE_NAMES = ('bottomRight', 'topRight', 'bottomLeft', 'topLeft')
BIFF_VIEW_RECORDS = {0x023E: 'Window2', 0x088B: 'PLV', 0x00A0: 'Scl', 0x0041: 'Pane', 0x001D: 'Selection'}


def _view(freeze_row, freeze_col, active_pane, selections, issues):
    selected = selections.get(active_pane, set())
    if len(selected) > 1:
        issues.add('conflicting_active_cell')
    return {'freeze_row': freeze_row, 'freeze_col': freeze_col, 'active_pane': active_pane,
            'selected_cell': next(iter(selected)) if len(selected) == 1 else None,
            **({'issues': sorted(issues)} if issues else {})}


def check_biff_pane(record):
    offset, kind, payload = record
    try:
        require(len(payload) == 10, 'Pane payload must be 10 bytes')
        x_split, y_split, _, col_left, pane_id = struct.unpack_from('<4HB', payload)
        require(col_left < 256 and pane_id < 4, 'Invalid Pane colLeft or active pane')
    except ValueError as error:
        error.location = {'record_offset': offset, 'record_id': hex(kind)}
        raise
    return x_split, y_split, PANE_NAMES[pane_id]


def biff_sheet_view(records, start):
    """Read a WINDOW production without borrowing a custom view's Selection."""
    offset, _, window = records[start]
    index = start + 1
    issues = set()
    for optional, size in ((0x088B, 16), (0x00A0, 4)):
        if index < len(records) and records[index][1] == optional:
            payload = records[index][2]
            if len(payload) != size or (optional == 0x088B and struct.unpack_from('<H', payload)[0] != optional):
                issues.add('invalid_optional_view_record')
            index += 1
    active_pane = 'topLeft'
    split = None
    if index < len(records) and records[index][1] == 0x0041:
        x_split, y_split, active_pane = check_biff_pane(records[index])
        split = (y_split, x_split)
        index += 1
    freeze_row = freeze_col = None
    if len(window) != 18:
        issues.add('invalid_window2_length')
    elif window[0] & 0x08:
        if split is not None and (split[0] or split[1]) and split[1] < 256:
            freeze_row, freeze_col = split
        else:
            issues.add('invalid_or_missing_frozen_pane')
            if split is None:
                active_pane = None
    elif window[1] & 0x01:
        issues.add('invalid_window2_freeze_flags')
    else:
        freeze_row = freeze_col = 0

    selections = {}
    while index < len(records) and records[index][1] == 0x001D:
        payload = records[index][2]
        index += 1
        if len(payload) >= 9:
            pane_id, row, col, _, count = struct.unpack_from('<B4H', payload)
            if pane_id < 4 and col < 256 and count <= 1369 and len(payload) == 9 + 6 * count:
                selections.setdefault(PANE_NAMES[pane_id], set()).add(column_name(col + 1) + str(row + 1))
                continue
        issues.add('invalid_selection_record')
        # An invalid record may contradict the saved cell for that pane.
        pane = PANE_NAMES[payload[0]] if payload and payload[0] < 4 else active_pane
        selections.setdefault(pane, set()).add(None)
    return index, {'record_offset': offset, **_view(freeze_row, freeze_col, active_pane, selections, issues)}


def _frozen_count(raw, maximum):
    raw = raw.strip()
    if len(raw) <= 64 and re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]{1,4})?', raw):
        value = Decimal(raw)
        if 0 <= value <= maximum and value == value.to_integral_value():
            return int(value)
    return None


def _ooxml_sheet_view(element, tag, coordinate, issues):
    panes = element.findall(tag('pane'))
    freeze_row = freeze_col = 0
    active_pane = 'topLeft'
    if len(panes) > 1:
        issues.add('duplicate_pane')
        freeze_row = freeze_col = active_pane = None
    elif panes:
        pane = panes[0]
        active_pane = pane.get('activePane', 'topLeft')
        if active_pane not in PANE_NAMES:
            active_pane = None
            issues.add('invalid_active_pane')
        state = pane.get('state', 'split')
        if state in ('frozen', 'frozenSplit'):
            freeze_row = _frozen_count(pane.get('ySplit', '0'), 1048575)
            freeze_col = _frozen_count(pane.get('xSplit', '0'), 16383)
            if freeze_row is None or freeze_col is None or not (freeze_row or freeze_col):
                freeze_row = freeze_col = None
                issues.add('invalid_frozen_pane')
        elif state != 'split':
            freeze_row = freeze_col = None
            issues.add('invalid_pane_state')

    selections = {}
    for selection in element.iterfind(tag('selection')):
        pane = selection.get('pane', 'topLeft')
        if pane not in PANE_NAMES:
            issues.add('invalid_selection_pane')
            continue
        cell = selection.get('activeCell')
        if cell is not None:
            try:
                coordinate(cell)
            except ValueError:
                issues.add('invalid_active_cell')
                cell = None
            selections.setdefault(pane, set()).add(cell)
    return _view(freeze_row, freeze_col, active_pane, selections, issues)


def ooxml_sheet_views(container, tag, coordinate):
    """Return saved views, using the last definition for a repeated workbook view ID."""
    other_settings = any(node.tag != tag('sheetView') for node in container)
    views = {}
    for index, element in enumerate(container.iterfind(tag('sheetView'))):
        raw_id = element.get('workbookViewId', '')
        view_id = int(raw_id) if re.fullmatch(r'[0-9]{1,10}', raw_id) else None
        if view_id is None or view_id > 4294967295:
            view_id = None
        issues = {'invalid_workbook_view_id'} if view_id is None else set()
        other_settings |= any(key != 'workbookViewId' for key in element.attrib) or any(
            child.tag not in (tag('pane'), tag('selection')) or len(child) for child in element)
        views[view_id if view_id is not None else ('unknown', index)] = {
            'workbook_view_id': view_id, **_ooxml_sheet_view(element, tag, coordinate, issues),
        }
    return list(views.values()), other_settings
