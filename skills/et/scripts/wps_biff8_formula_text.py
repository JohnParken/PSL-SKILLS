"""Bounded BIFF8 expression transcription. No evaluation or link resolution."""
from decimal import Decimal
import math
import struct

from wps_biff8_headers import column_name


BINARY = dict(zip(range(0x03, 0x0F), ('+', '-', '*', '/', '^', '&', '<', '<=', '=', '>=', '>', '<>')))
FIXED_FUNCTIONS = {24: ('ABS', 1), 27: ('ROUND', 2), 34: ('TRUE', 0), 35: ('FALSE', 0)}
VARIABLE_FUNCTIONS = {0: 'COUNT', 4: 'SUM', 5: 'AVERAGE', 6: 'MIN', 7: 'MAX', 169: 'COUNTA'}


def numeric_constant(tokens):
    """Return an exact Decimal for one literal, or an explicitly signed literal."""
    if len(tokens) == 3 and tokens[0] == 0x1E:
        return Decimal(struct.unpack_from('<H', tokens, 1)[0])
    if len(tokens) == 9 and tokens[0] == 0x1F:
        value = struct.unpack_from('<d', tokens, 1)[0]
        return Decimal.from_float(value) if math.isfinite(value) else None
    if len(tokens) in (4, 10) and tokens[-1] in (0x12, 0x13):
        value = numeric_constant(tokens[:-1])
        if value is not None:
            return value.copy_negate() if tokens[-1] == 0x13 else value
    return None


def _cell(row, column):
    if column & 0x3F00:
        raise ValueError('Reserved BIFF8 column bits')
    return (('' if column & 0x4000 else '$') + column_name((column & 0xFF) + 1)
            + ('' if column & 0x8000 else '$') + str(row + 1))


def _string_literal(tokens, index):
    """Decode one PtgStr ShortXLUnicodeString operand and quote it safely.

    Formula text uses Excel string-literal syntax: surrounding double quotes and
    inner quotes doubled. No evaluation or formula injection is introduced.
    """
    if index + 2 > len(tokens):
        raise ValueError('Truncated PtgStr header')
    count = tokens[index]
    flags = tokens[index + 1]
    index += 2
    high_byte = bool(flags & 1)
    byte_count = count * (2 if high_byte else 1)
    if index + byte_count > len(tokens):
        raise ValueError('Truncated PtgStr characters')
    raw = tokens[index:index + byte_count]
    if high_byte:
        text = raw.decode('utf-16le')
    else:
        text = ''.join(chr(value) for value in raw)
    return '"' + text.replace('"', '""') + '"', index + byte_count


def _call(stack, name, count):
    if count > len(stack):
        raise ValueError('Not enough formula operands')
    args = stack[-count:] if count else []
    if count:
        del stack[-count:]
    return f'{name}({",".join(args)})'


def _expression(tokens):
    stack = []
    index = 0
    while index < len(tokens):
        code = tokens[index]
        index += 1
        if 0x20 <= code < 0x80:
            code = 0x20 | (code & 0x1F)
        if code in BINARY:
            right = stack.pop()
            value = f'({stack.pop()}{BINARY[code]}{right})'
        elif code in (0x12, 0x13, 0x14, 0x15):
            item = stack.pop()
            value = {0x12: f'(+{item})', 0x13: f'(-{item})', 0x14: f'({item}%)', 0x15: f'({item})'}[code]
        elif code == 0x17:
            value, index = _string_literal(tokens, index)
        elif code == 0x19 and tokens[index:index + 1] == b'\x10' and index + 3 <= len(tokens):
            value = _call(stack, 'SUM', 1)
            index += 3
        elif code == 0x1D and tokens[index:index + 1] in (b'\0', b'\1'):
            value = 'TRUE' if tokens[index] else 'FALSE'
            index += 1
        elif code == 0x1E:
            value = str(struct.unpack_from('<H', tokens, index)[0])
            index += 2
        elif code == 0x1F:
            number = struct.unpack_from('<d', tokens, index)[0]
            if not math.isfinite(number):
                return None
            value = repr(number).removesuffix('.0')
            if number < 0:
                value = '(' + value + ')'
            index += 8
        elif code == 0x21:
            value = _call(stack, *FIXED_FUNCTIONS[struct.unpack_from('<H', tokens, index)[0]])
            index += 2
        elif code == 0x22:
            count, function = struct.unpack_from('<BH', tokens, index)
            if not 1 <= count <= 30 and not (count == 0 and function in (0, 169)):
                return None
            value = _call(stack, VARIABLE_FUNCTIONS[function], count)
            index += 3
        elif code == 0x24:
            value = _cell(*struct.unpack_from('<2H', tokens, index))
            index += 4
        elif code == 0x25:
            first, last, left, right = struct.unpack_from('<4H', tokens, index)
            if first > last or (left & 0xFF) > (right & 0xFF):
                return None
            value = _cell(first, left) + ':' + _cell(last, right)
            index += 8
        else:
            return None
        if len(value) > 1024:
            return None
        stack.append(value)
    return '=' + stack[0] if len(stack) == 1 else None


def formula_text_fields(tokens, ancillary):
    """Return display-only text for fully transcribed standalone formulas."""
    if ancillary or len(tokens) > 512:
        return {}
    try:
        text = _expression(tokens)
    except (IndexError, KeyError, ValueError, struct.error):
        return {}
    return {'formula_text': text} if text is not None else {}
