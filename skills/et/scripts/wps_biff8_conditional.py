"""Bounded BIFF8 formatting/validation metadata, without evaluation or application."""
import struct

from wps_biff8_formula_text import numeric_constant


COMPARISONS = ('between', 'notBetween', 'equal', 'notEqual', 'greaterThan', 'lessThan', 'greaterThanOrEqual', 'lessThanOrEqual')


def _comparison(kind, operator, first, second):
    if kind != 1 or not 1 <= operator <= len(COMPARISONS) or bool(second) != (operator <= 2):
        return {}
    values = [numeric_constant(tokens) for tokens in (first, second) if tokens]
    if len(values) != (2 if operator <= 2 else 1) or any(value is None for value in values):
        return {}
    return {'cell_value_comparison': {'operator': COMPARISONS[operator - 1], 'operands': [str(value) for value in values]}}


def _fill_only(data):
    if len(data) != 10:
        return {}
    flags, options, pattern_word, colors = struct.unpack('<I3H', data)
    # DXFN with exactly one optional structure, DXFPat. Ninch marks fields to ignore.
    if (flags >> 25) & 0x3F != 0x10 or flags & (7 << 22) or options & 0x7FF8:
        return {}
    pattern = pattern_word >> 10
    if pattern > 18:
        return {}
    fields = {'pattern_id': pattern, 'foreground_color_index': colors & 0x7F, 'background_color_index': (colors >> 7) & 0x7F}
    return {'differential_fill': {key: value for bit, (key, value) in enumerate(fields.items(), 16)
                                  if not (flags & (1 << bit))}}


def _require(condition, message, record):
    if not condition:
        error = ValueError(message)
        error.location = {'record_offset': record[0], 'record_id': hex(record[1])}
        raise error


def _semantic(condition, message, record, issues):
    if not condition:
        if issues is None:
            _require(False, message, record)
        else:
            issues.setdefault(record[0], message)


def _range(payload, offset, record, issues):
    bounds = struct.unpack_from('<4H', payload, offset)
    first_row, last_row, first_col, last_col = bounds
    _semantic(first_row <= last_row and first_col <= last_col < 256,
              'Invalid CondFmt range bounds', record, issues)
    return bounds


def conditional_group(records, index, *, tolerate_semantics=False):
    """Return the exact CondFmt + 1..3 adjacent CF group and source metadata."""
    record = records[index]
    payload = record[2]
    _require(record[1] == 0x01B0 and len(payload) >= 14, 'Truncated CondFmt header', record)
    count, flags = struct.unpack_from('<2H', payload)
    ranges = struct.unpack_from('<H', payload, 12)[0]
    _require(1 <= count <= 3, 'CondFmt requires 1..3 CF records', record)
    _require(1 <= ranges <= 1026 and len(payload) == 14 + 8 * ranges,
             'Malformed/oversized CondFmt range list', record)
    issues = {} if tolerate_semantics else None
    bounds = _range(payload, 4, record, issues)
    region_bounds = []
    for position in range(14, len(payload), 8):
        region = _range(payload, position, record, issues)
        region_bounds.append(list(region))
        _semantic(bounds[0] <= region[0] <= region[1] <= bounds[1] and
                  bounds[2] <= region[2] <= region[3] <= bounds[3],
                  'CondFmt range lies outside its bounding rectangle', record, issues)
    evidence = [{
        'rule_count': count, 'range_count': ranges, 'bounds_zero_based': list(bounds),
        **({'ranges_zero_based': region_bounds} if not issues else {}),
        'identifier_raw': flags >> 1, 'tough_recalculation_raw': bool(flags & 1),
        'rules_evaluated': False, 'formatting_applied': False,
    }]
    stop = index + 1 + count
    _require(stop <= len(records), 'Missing CF records after CondFmt', record)
    for rule in records[index + 1:stop]:
        raw = rule[2]
        _require(rule[1] == 0x01B1, 'CondFmt must be followed by its exact adjacent CF records', rule)
        _require(len(raw) >= 12, 'Truncated CF header/DXFN prefix', rule)
        condition, comparison, first_length, second_length = struct.unpack_from('<BBHH', raw)
        _semantic(condition in (1, 2), 'Unsupported CF condition type', rule, issues)
        _semantic(condition != 1 or 1 <= comparison <= 8, 'Invalid CF comparison operator', rule, issues)
        _require(first_length <= 16409 and second_length <= 16409 and
                 first_length + second_length <= len(raw) - 12, 'CF formula lengths exceed record boundary', rule)
        _semantic(second_length == 0 or (condition == 1 and comparison in (1, 2)),
                  'Unexpected CF second formula', rule, issues)
        first_start = len(raw) - first_length - second_length
        second_start = len(raw) - second_length
        evidence.append({
            'condition_type_raw': condition, 'comparison_raw': comparison,
            'first_formula_bytes': first_length, 'second_formula_bytes': second_length,
            'differential_format_bytes': len(raw) - 6 - first_length - second_length,
            'inner_payload_validated': False, 'formulas_executed': False, 'formatting_applied': False,
            **_comparison(condition, comparison, raw[first_start:second_start], raw[second_start:]),
            **_fill_only(raw[6:first_start]),
        })
    _attach_issues(records[index:stop], evidence, issues)
    return stop, evidence


def validation_group(records, index, record_budget, *, tolerate_semantics=False):
    """Bound DVal/Dv text/formula/range partitions; do not enforce saved rules."""
    record = records[index]
    payload = record[2]
    _require(record[1] == 0x1B2 and len(payload) == 18, 'DVal must be 18 bytes', record)
    count = struct.unpack_from('<I', payload, 14)[0]
    _require(count <= 65534, 'Invalid DVal rule count', record)
    if count + 1 > record_budget:
        error = ValueError('DVal exceeds remaining metadata record budget')
        error.status = 'resource_limit'
        raise error
    stop = index + 1 + count
    _require(stop <= len(records), 'Missing Dv records after DVal', record)
    evidence = [{'rule_count': count, 'validation_applied': False, 'display_applied': False}]
    issues = {} if tolerate_semantics else None
    for rule in records[index + 1:stop]:
        raw = rule[2]
        _require(rule[1] == 0x1BE and len(raw) >= 4, 'Expected adjacent Dv record', rule)
        position = 4
        for limit in (32, 32, 255, 225):
            _require(position + 3 <= len(raw), 'Truncated Dv string header', rule)
            units, flag = struct.unpack_from('<HB', raw, position)
            _require(flag in (0, 1), 'Unsupported Dv string length/flags', rule)
            _semantic(units <= limit, 'Dv string exceeds its field limit', rule, issues)
            end = position + 3 + units * (2 if flag else 1)
            _require(end <= len(raw), 'Dv string crosses record boundary', rule)
            try:
                raw[position + 3:end].decode('utf-16le' if flag else 'latin1')
            except UnicodeDecodeError:
                _semantic(False, 'Invalid Dv Unicode string', rule, issues)
            position = end
        lengths = []
        for _ in range(2):
            _require(position + 4 <= len(raw), 'Truncated Dv formula length', rule)
            length = struct.unpack_from('<H', raw, position)[0]
            position += 4 + length
            _require(length <= 16384 and position <= len(raw), 'Dv formula crosses record boundary', rule)
            lengths.append(length)
        _require(position + 2 <= len(raw), 'Truncated Dv range count', rule)
        ranges = struct.unpack_from('<H', raw, position)[0]
        position += 2
        _require(1 <= ranges <= 432 and len(raw) == position + ranges * 8, 'Malformed Dv range list', rule)
        for start in range(position, len(raw), 8):
            first_row, last_row, first_col, last_col = struct.unpack_from('<4H', raw, start)
            _semantic(first_row <= last_row and first_col <= last_col < 256,
                      'Invalid Dv range bounds', rule, issues)
        evidence.append({'range_count': ranges, 'formula_bytes': lengths, 'validation_applied': False,
                         'formulas_executed': False, 'formula_semantics_validated': False,
                         'prompt_and_error_text_exposed': False})
    _attach_issues(records[index:stop], evidence, issues)
    return stop, evidence


def _attach_issues(records, evidence, issues):
    if issues:
        for record, structure in zip(records, evidence):
            if record[0] in issues:
                structure['semantic_issue'] = issues[record[0]]
