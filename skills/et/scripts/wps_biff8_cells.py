# SPDX-License-Identifier: MIT
"""Tolerant BIFF8 cell and formula record decoding.

One malformed value-bearing record is skipped with an omission diagnostic;
already parsed cells are kept. Unknown formula cache/boolean/error variants
retain a stable placeholder value plus raw bytes and a diagnostic. Nothing is
executed or recalculated.
"""
import struct

from wps_biff8_errors import ResourceLimitError
from wps_biff8_formula_text import formula_text_fields
from wps_biff8_records import ERRORS
from wps_biff8_retain import salvage_metadata, strings
from wps_biff8_values import number, rk_value, unicode_string


def _handle_cell_record(reader, state, records, index, record, kind, payload):
    """Parse one value-bearing cell record; return the next index."""
    if len(payload) < 6:
        state.omit(record, 'cell_value_record_omitted',
                   'Truncated cell record header; record skipped')
        return index
    row, col, xf = struct.unpack_from('<3H', payload)
    if kind == 0x0203:
        if len(payload) != 14:
            state.omit(record, 'cell_value_record_omitted',
                       'Number payload must be 14 bytes; record skipped')
            return index
        try:
            value = number(payload[6:])
        except ValueError:
            state.omit(record, 'nonfinite_number_omitted',
                       'Non-finite stored number skipped; record is not a '
                       'usable saved value')
            return index
        state.put(row, col, xf, 'number', value, record,
                  raw_numeric_hex=payload[6:].hex())
    elif kind == 0x027E:
        if len(payload) != 10:
            state.omit(record, 'cell_value_record_omitted',
                       'RK payload must be 10 bytes; record skipped')
            return index
        try:
            value = rk_value(payload[6:])
        except ValueError:
            state.omit(record, 'nonfinite_number_omitted',
                       'Non-finite RK value skipped; record is not a usable '
                       'saved value')
            return index
        state.put(row, col, xf, 'number', value, record,
                  raw_numeric_hex=payload[6:].hex())
    elif kind == 0x0201:
        if len(payload) != 6:
            state.omit(record, 'cell_value_record_omitted',
                       'Blank payload must be 6 bytes; record skipped')
            return index
        state.put(row, col, xf, 'blank', None, record)
    elif kind == 0x00FD:
        if len(payload) != 10:
            state.omit(record, 'cell_value_record_omitted',
                       'LabelSst payload must be 10 bytes; record skipped')
            return index
        string_index = struct.unpack_from('<I', payload, 6)[0]
        if string_index >= len(reader.strings):
            state.omit(record, 'sst_index_out_of_range_omitted',
                       'Shared-string index outside the parsed SST; record '
                       'skipped', sst_index=string_index)
            return index
        state.put(row, col, xf, 'text', reader.strings[string_index], record,
                  sst_index=string_index)
    elif kind == 0x0204:
        if len(payload) < 9:
            state.omit(record, 'cell_value_record_omitted',
                       'Truncated Label fixed header; record skipped')
            return index
        string_reader = strings(reader, records, index - 1, 6)
        value = string_reader.string(ignore_reserved_flags=True)
        index = string_reader.finish()
        address = state.put(row, col, xf, 'text', value, record)
        if address and string_reader.last_flags_raw & 0xFE:
            state.cells[address]['string_header_flags_raw'] = (
                string_reader.last_flags_raw)
            reader.plain_string_flags(record, string_reader.last_flags_raw,
                                      state.bound['name'], address)
    elif kind == 0x00D6:
        _handle_rstring(reader, state, record, payload, row, col, xf)
    elif kind == 0x0205:
        if len(payload) != 8:
            state.omit(record, 'cell_value_record_omitted',
                       'BoolErr payload must be 8 bytes; record skipped')
            return index
        _handle_boolerr(reader, state, record, payload, row, col, xf)
    else:
        index = _handle_formula(reader, state, records, index, record, payload,
                                row, col, xf)
    return index


def _handle_rstring(reader, state, record, payload, row, col, xf):
    """Read a BIFF8 RString: plain text first, then bounded rich runs."""
    if len(payload) < 9:
        state.omit(record, 'cell_value_record_omitted',
                   'Truncated RString header; record skipped')
        return
    try:
        value, end = unicode_string(payload, 6, ignore_reserved_flags=True)
    except ValueError as exc:
        state.omit(record, 'rstring_text_invalid',
                   'RString text could not be decoded: ' + str(exc))
        return
    runs = []
    runs_ok = True
    run_bytes = b''
    if end + 2 > len(payload):
        runs_ok = False
    else:
        run_count = struct.unpack_from('<H', payload, end)[0]
        run_start = end + 2
        run_bytes = payload[run_start:run_start + 4 * run_count]
        runs_ok = (len(run_bytes) == 4 * run_count
                   and run_start + len(run_bytes) == len(payload))
        if runs_ok:
            runs = [{'character_offset_raw': struct.unpack_from('<H', run_bytes,
                                                                 offset)[0],
                     'font_index_raw': struct.unpack_from('<H', run_bytes,
                                                           offset + 2)[0]}
                    for offset in range(0, len(run_bytes), 4)]
    address = state.put(row, col, xf, 'text', value, record)
    if not address:
        return
    cell = state.cells[address]
    cell['string_header_flags_raw'] = payload[8]
    if runs_ok:
        cell['rich_text_runs'] = runs
        cell['runs_raw_hex'] = run_bytes.hex()
    else:
        cell['runs_status'] = 'invalid'
        reader.issue({'code': 'rstring_format_runs_invalid',
                      'sheet': state.bound['name'], 'cell': address,
                      'record_offset': record[0],
                      'cell_value_impact': 'metadata_only',
                      'message': 'RString plain text retained; malformed '
                                 'rich-run tail omitted from formatting evidence.'})
    if runs:
        reader.issue({'code': 'rstring_rich_format_not_applied',
                      'sheet': state.bound['name'], 'cell': address,
                      'record_offset': record[0],
                      'cell_value_impact': 'metadata_only',
                      'message': 'RString plain text retained; rich run '
                                 'formatting is evidence only and is not '
                                 'applied to the saved value.'})


def _handle_boolerr(reader, state, record, payload, row, col, xf):
    name = state.bound['name']
    flag = payload[7]
    code = payload[6]
    if flag:
        if code in ERRORS:
            state.put(row, col, xf, 'error', ERRORS[code], record,
                      error_code=code)
            return
        state.put(row, col, xf, 'error', '#UNKNOWN_ERROR', record,
                  error_code=code, error_code_known=False,
                  boolerr_raw_hex=payload.hex())
        reader.issue({'code': 'unknown_boolerr_code', 'sheet': name,
                      'record_offset': record[0], 'error_code_raw': code,
                      'message': 'Unknown BIFF8 error code retained as an '
                                 'unknown error value; saved cell value '
                                 'preserved.'})
    elif code in (0, 1):
        state.put(row, col, xf, 'boolean', bool(code), record)
    else:
        state.put(row, col, xf, 'error', '#UNKNOWN_BOOLEAN', record,
                  boolean_raw=code, boolerr_raw_hex=payload.hex())
        reader.issue({'code': 'unknown_boolerr_boolean', 'sheet': name,
                      'record_offset': record[0], 'boolean_raw': code,
                      'message': 'Unknown BoolErr boolean encoding retained '
                                 'as an unknown error value.'})


def _formula_cache(payload):
    """Return ``(value, cell_type, cache_status, error_code, special_type)``."""
    raw = payload[6:14]
    if raw[6:8] != b'\xff\xff':
        return number(raw), 'number', 'unverified', None, None
    cache_type = raw[0]
    if cache_type == 0:
        return None, 'blank', 'pending_string', None, 0
    if cache_type == 1:
        if raw[2] in (0, 1):
            return bool(raw[2]), 'boolean', 'unverified', None, 1
        return '#UNKNOWN_BOOLEAN', 'error', 'unknown_cache_encoding', None, 1
    if cache_type == 2:
        if raw[2] in ERRORS:
            return ERRORS[raw[2]], 'error', 'error_unverified', raw[2], 2
        return '#UNKNOWN_ERROR', 'error', 'unknown_cache_encoding', None, 2
    if cache_type == 3:
        return '', 'text', 'unverified_empty_string', None, 3
    return ('#UNKNOWN_FORMULA_CACHE', 'error', 'unknown_special_cache', None,
            cache_type)


def _handle_formula(reader, state, records, index, record, payload, row, col,
                    xf):
    """Parse one Formula record; unknown token/cache variants degrade."""
    name = state.bound['name']
    if len(payload) < 23:
        state.omit(record, 'cell_value_record_omitted',
                   'Truncated Formula record; record skipped')
        return index
    try:
        value, cell_type, cache_status, error_code, special = _formula_cache(
            payload)
    except ValueError:
        state.omit(record, 'nonfinite_number_omitted',
                   'Non-finite Formula cache skipped; record is not a usable '
                   'saved value')
        return index
    flags = struct.unpack_from('<H', payload, 14)[0]
    count = struct.unpack_from('<H', payload, 20)[0]
    available = len(payload) - 22
    if count > available:
        reader.issue({'code': 'formula_token_length_invalid', 'sheet': name,
                      'record_offset': record[0],
                      'declared_token_bytes': count,
                      'available_token_bytes': available,
                      'message': 'Formula token length exceeds the record; '
                                 'available token bytes retained and the saved '
                                 'cache kept.'})
        tokens = payload[22:]
    elif count == 0:
        reader.issue({'code': 'formula_token_length_invalid', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'Formula declares zero token bytes; saved '
                                 'cache kept.'})
        tokens = payload[22:]
    else:
        tokens = payload[22:22 + count]
    ancillary = payload[22 + len(tokens):]
    anchor = None
    if len(tokens) == 5 and tokens[0] & 0x7F == 1:
        anchor_row, anchor_col = struct.unpack_from('<HH', tokens, 1)
        if anchor_col < 256:
            anchor = (anchor_row, anchor_col)
        else:
            reader.issue({'code': 'formula_token_anomaly', 'sheet': name,
                          'record_offset': record[0],
                          'message': 'Invalid shared-formula anchor column; '
                                     'cell cache kept without group binding.'})
    elif flags & 8:
        reader.issue({'code': 'formula_token_anomaly', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'Shared formula flag without a PtgExp '
                                 'anchor; cache kept without group binding.'})
    formula = {'encoding': 'opaque_biff8_ptgs', 'tokens_hex': tokens.hex(),
               'ancillary_hex': ancillary.hex(), 'flags_raw': flags,
               'needs_recalculation_flag': bool(flags & 1),
               'cache_raw_hex': payload[6:14].hex(),
               'cache_may_be_stale': True,
               **formula_text_fields(tokens, ancillary)}
    if special is not None and isinstance(cache_status, str) and \
            cache_status.startswith('unknown'):
        reader.issue({'code': 'unknown_formula_value_type', 'sheet': name,
                      'record_offset': record[0],
                      'special_cache_type_raw': special,
                      'message': 'Unknown special FormulaValue cache type '
                                 'retained as an unknown error value.'})
    address = state.put(row, col, xf, cell_type, value, record,
                        formula=formula, error_code=error_code)
    if address is None:
        return index
    state.cells[address]['cache_status'] = cache_status
    if cache_status == 'pending_string':
        state.pending = address
    state.last_formula = {'row': row, 'column': col, 'address': address,
                          'anchor': anchor, 'shared': bool(flags & 8),
                          'record_index': index - 1}
    if anchor is not None:
        state.formula_refs.append((row, col, anchor, bool(flags & 8),
                                   address))
    return index


def _handle_formula_group(reader, state, records, index, record, kind, payload):
    """Parse a shared/array group definition tolerantly."""
    name = state.bound['name']
    last = state.last_formula
    if last.get('record_index') != index - 2:
        reader.issue({'code': 'formula_group_definition_orphan', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'Shared/array definition does not immediately '
                                 'follow a Formula record; retained raw.'})
        salvage_metadata(reader, record, name, code='formula_group_definition_orphan')
        return index
    row, col = last['row'], last['column']
    address, anchor = last['address'], last['anchor']
    shared = last['shared']
    group_kind = 'shared' if kind == 0x04BC else 'array'
    if anchor != (row, col) or shared != (group_kind == 'shared'):
        reader.issue({'code': 'formula_group_definition_invalid',
                      'sheet': name, 'record_offset': record[0],
                      'message': 'Definition anchor/flag does not match its '
                                 'Formula record; retained raw.'})
        salvage_metadata(reader, record, name,
                         code='formula_group_definition_invalid')
        return index
    prefix = 8 if group_kind == 'shared' else 12
    if len(payload) < prefix + 3:
        reader.issue({'code': 'formula_group_definition_invalid',
                      'sheet': name, 'record_offset': record[0],
                      'message': 'Truncated shared/array definition; retained '
                                 'raw.'})
        salvage_metadata(reader, record, name,
                         code='formula_group_definition_invalid')
        return index
    r1, r2, c1, c2 = struct.unpack_from('<HHBB', payload)
    if not (r1 <= r2 and c1 <= c2 and r1 <= row <= r2 and c1 <= col <= c2):
        reader.issue({'code': 'formula_group_definition_invalid',
                      'sheet': name, 'record_offset': record[0],
                      'message': 'Invalid shared/array range; retained raw.'})
        salvage_metadata(reader, record, name,
                         code='formula_group_definition_invalid')
        return index
    if group_kind == 'array' and (row, col) != (r1, c1):
        reader.issue({'code': 'formula_group_definition_invalid',
                      'sheet': name, 'record_offset': record[0],
                      'message': 'Array anchor is not the top-left cell; '
                                 'retained raw.'})
        salvage_metadata(reader, record, name,
                         code='formula_group_definition_invalid')
        return index
    area = (r2 - r1 + 1) * (c2 - c1 + 1)
    if area > reader.limits['max_cells'] or \
            len(state.formula_groups) >= reader.limits['max_formula_groups']:
        reader.issue({'code': 'formula_group_budget_exceeded', 'sheet': name,
                      'record_offset': record[0],
                      'message': 'Shared/array range or group budget exceeded; '
                                 'definition retained raw.'})
        salvage_metadata(reader, record, name, code='formula_group_budget_exceeded')
        return index
    expected = payload[7] if group_kind == 'shared' else area
    use_count_unspecified = group_kind == 'shared' and expected == 0
    count = struct.unpack_from('<H', payload, prefix)[0]
    if ((not 0 < expected <= area and not use_count_unspecified)
            or not 0 < count <= len(payload) - prefix - 2):
        reader.issue({'code': 'formula_group_definition_invalid',
                      'sheet': name, 'record_offset': record[0],
                      'message': 'Invalid shared cUse or token length; '
                                 'retained raw.'})
        salvage_metadata(reader, record, name,
                         code='formula_group_definition_invalid')
        return index
    for other in state.formula_groups.values():
        a, b, c, d = other['bounds_zero_based']
        if not (r2 < a or b < r1 or c2 < c or d < c1):
            reader.issue({'code': 'formula_group_overlap', 'sheet': name,
                          'record_offset': record[0],
                          'message': 'Overlapping shared/array bounding '
                                     'ranges; later definition retained raw.'})
            salvage_metadata(reader, record, name, code='formula_group_overlap')
            return index
    if len(payload) > reader.limits['max_metadata_bytes'] - \
            reader.metadata_bytes:
        reader.metadata_truncated_count += 1
        reader.metadata_budget_issue(name)
    else:
        reader.metadata_bytes += len(payload)
    state.formula_groups[anchor] = {
        'kind': group_kind, 'anchor': address,
        'bounds_zero_based': [r1, r2, c1, c2],
        'expected_members': expected,
        'use_count_unspecified': use_count_unspecified,
        'record_offset': record[0],
        'record_id': hex(kind),
        'tokens_hex': payload[prefix + 2:prefix + 2 + count].hex(),
        'ancillary_hex': payload[prefix + 2 + count:].hex(),
        'header_raw_hex': payload[:prefix].hex(),
        'expression_decoded': False}
    reader.issue({'sheet': name, 'cell': address, 'record_offset': record[0],
                  'code': 'formula_group_expression_not_decoded',
                  'message': 'Saved cell caches and group pointers retained; '
                             'expression/ancillary semantics and cache '
                             'freshness are not verified. No recalculation.'})
    return index


def _handle_multi_cell(reader, state, index, record, kind, payload):
    if len(payload) < 6:
        state.omit(record, 'cell_value_record_omitted',
                   'Truncated multi-cell record; record skipped')
        return index
    row, first = struct.unpack_from('<HH', payload)
    last = struct.unpack_from('<H', payload, len(payload) - 2)[0]
    width = 6 if kind == 0x00BD else 2
    if not (first < last < 256
            and len(payload) == 6 + (last - first + 1) * width):
        state.omit(record, 'cell_value_record_omitted',
                   'Malformed MulRk/MulBlank bounds or length; record skipped')
        return index
    for col in range(first, last + 1):
        start = 4 + (col - first) * width
        xf = struct.unpack_from('<H', payload, start)[0]
        raw = payload[start + 2:start + 6] if kind == 0x00BD else None
        if raw is not None:
            try:
                value = rk_value(raw)
            except ValueError:
                state.omit(record, 'nonfinite_number_omitted',
                           'Non-finite MulRk value skipped; record is not a '
                           'usable saved value')
                continue
        else:
            value = None
        state.put(row, col, xf, 'number' if raw is not None else 'blank',
                  value, record,
                  raw_numeric_hex=None if raw is None else raw.hex())
    return index
