"""Narrow MS-XLS non-cell record validators for BIFF8 data-only reading.

Never an unknown-record fallback. The caller still checks substream ordering,
duplicates, encryption and complete BIFF stream boundaries.
"""
import struct


def inspect_page_break_layout(kind, payload, previous_key, ends_by_axis):
    """Inspect already count/length-bounded page-break entries, without applying them.

    The caller enforces exact framing and the shared raw metadata budget.
    Layout anomalies are counted by kind, keeping diagnostics bounded even
    for many invalid entries. No entry is normalized or used to hide cells.
    """
    problems = {}
    count = struct.unpack_from('<H', payload)[0]
    for index, start in enumerate(range(2, len(payload), 6)):
        axis, interval_start, interval_end = struct.unpack_from('<3H', payload, start)
        key = (axis, interval_start)
        previous_end = ends_by_axis.get(axis)
        if kind == 0x001B:
            bounds_ok = interval_start <= interval_end < 256
            bounds_message = 'Invalid horizontal page-break columns'
        else:
            bounds_ok = axis < 256 and interval_start <= interval_end
            bounds_message = 'Invalid vertical page-break column or rows'
        checks = (
            (bounds_ok, 'invalid_bounds', bounds_message),
            (previous_key is None or previous_key < key, 'unordered_entries',
             'Page-break entries are not sorted by axis and start'),
            (previous_end is None or interval_start > previous_end, 'overlapping_intervals',
             'Overlapping page-break intervals on the same axis'),
        )
        for valid, code, message in checks:
            if not valid:
                if code not in problems:
                    problems[code] = {'code': code, 'message': message,
                                      'first_entry_index': index, 'count': 0}
                problems[code]['count'] += 1
        previous_key = key
        # Keep the furthest prior end so nested bad intervals cannot hide overlap.
        ends_by_axis[axis] = max(interval_end, previous_end) if previous_end is not None else interval_end
    return {
        'entry_count': count,
        'layout_checks_passed': not problems,
        'layout_issues': list(problems.values()),
        'layout_applied': False,
    }, previous_key


def inspect_noncell(kind, payload, scope):
    if scope == 'globals' and kind == 0x0086:
        # WriteProtect §2.4.350: presence-only global write protection.
        if payload:
            raise ValueError('WriteProtect has no payload')
        return {'kind': 'WriteProtect', 'affects_stored_cell_values': False,
                'write_protected_raw': True, 'effective_edit_permissions': 'unknown'}
    if scope == 'worksheet' and kind == 0x005E:
        # Uncalced §2.4.331: reserved (2 bytes) MUST be zero.
        if payload != b'\x00\x00':
            raise ValueError('Uncalced reserved field must be exactly two zero bytes')
        return {'kind': 'Uncalced', 'affects_stored_cell_values': False,
                'saved_formula_recalculation_pending': True,
                'cache_freshness': 'unverified'}
    raise ValueError('No declared BIFF8 non-cell metadata rule')
