"""Bounded, opaque BIFF8 formatting/validation rules; never a rule evaluator.

Only the group, range and formula-length framing is inspected. DXFN and
formula bodies remain unvalidated bytes, without formatting or execution.
"""
import struct


def _require(condition, message, record):
    if not condition:
        error = ValueError(message)
        error.location = {'record_offset': record[0], 'record_id': hex(record[1])}
        raise error


def _range(payload, offset, record):
    bounds = struct.unpack_from('<4H', payload, offset)
    first_row, last_row, first_col, last_col = bounds
    _require(first_row <= last_row and first_col <= last_col < 256,
             'Invalid CondFmt range bounds', record)
    return bounds


def conditional_group(records, index):
    """Return the exact CondFmt + 1..3 adjacent CF group and source metadata."""
    record = records[index]
    payload = record[2]
    _require(record[1] == 0x01B0 and len(payload) >= 14, 'Truncated CondFmt header', record)
    count, flags = struct.unpack_from('<2H', payload)
    ranges = struct.unpack_from('<H', payload, 12)[0]
    _require(1 <= count <= 3, 'CondFmt requires 1..3 CF records', record)
    _require(1 <= ranges <= 1026 and len(payload) == 14 + 8 * ranges,
             'Malformed/oversized CondFmt range list', record)
    bounds = _range(payload, 4, record)
    for position in range(14, len(payload), 8):
        region = _range(payload, position, record)
        _require(bounds[0] <= region[0] <= region[1] <= bounds[1] and
                 bounds[2] <= region[2] <= region[3] <= bounds[3],
                 'CondFmt range lies outside its bounding rectangle', record)
    evidence = [{
        'rule_count': count, 'range_count': ranges, 'bounds_zero_based': list(bounds),
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
        _require(condition in (1, 2), 'Unsupported CF condition type', rule)
        _require(condition != 1 or 1 <= comparison <= 8, 'Invalid CF comparison operator', rule)
        _require(first_length <= 16409 and second_length <= 16409 and
                 first_length + second_length <= len(raw) - 12, 'CF formula lengths exceed record boundary', rule)
        _require(second_length == 0 or (condition == 1 and comparison in (1, 2)),
                 'Unexpected CF second formula', rule)
        evidence.append({
            'condition_type_raw': condition, 'comparison_raw': comparison,
            'first_formula_bytes': first_length, 'second_formula_bytes': second_length,
            'differential_format_bytes': len(raw) - 6 - first_length - second_length,
            'inner_payload_validated': False, 'formulas_executed': False, 'formatting_applied': False,
        })
    return stop, evidence


def validation_group(records, index, record_budget):
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
    for rule in records[index + 1:stop]:
        raw = rule[2]
        _require(rule[1] == 0x1BE and len(raw) >= 4, 'Expected adjacent Dv record', rule)
        position = 4
        for limit in (32, 32, 255, 225):
            _require(position + 3 <= len(raw), 'Truncated Dv string header', rule)
            units, flag = struct.unpack_from('<HB', raw, position)
            _require(units <= limit and flag in (0, 1), 'Unsupported Dv string length/flags', rule)
            end = position + 3 + units * (2 if flag else 1)
            _require(end <= len(raw), 'Dv string crosses record boundary', rule)
            try:
                raw[position + 3:end].decode('utf-16le' if flag else 'latin1')
            except UnicodeDecodeError:
                _require(False, 'Invalid Dv Unicode string', rule)
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
            _require(first_row <= last_row and first_col <= last_col < 256, 'Invalid Dv range bounds', rule)
        evidence.append({'range_count': ranges, 'formula_bytes': lengths, 'validation_applied': False,
                         'formulas_executed': False, 'formula_semantics_validated': False,
                         'prompt_and_error_text_exposed': False})
    return stop, evidence
