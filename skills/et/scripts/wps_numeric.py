"""Conservative stored-number interpretation; never changes source values."""
from datetime import date, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
from fractions import Fraction
import re
from wps_format_rules import format_directives

BUILTIN_FORMATS = {
    0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00', 9: '0%', 10: '0.00%', 11: '0.00E+00', 12: '# ?/?', 13: '# ??/??',
    14: 'm/d/yy', 20: 'h:mm', 21: 'h:mm:ss', 22: 'm/d/yy h:mm', 37: '#,##0_);(#,##0)', 38: '#,##0_);[Red](#,##0)',
    39: '#,##0.00_);(#,##0.00)', 40: '#,##0.00_);[Red](#,##0.00)', 45: 'mm:ss', 46: '[h]:mm:ss', 49: '@',
}
DATE_FORMATS = {'yyyy-mm-dd','yyyy/mm/dd','m/d/yy','yyyy-mm-dd hh:mm:ss','m/d/yy h:mm'}
TIME_FORMATS = {'h:mm','hh:mm','h:mm:ss','hh:mm:ss','mm:ss','[h]:mm:ss'}
COLOR = re.compile(r'\[(Black|Blue|Cyan|Green|Magenta|Red|White|Yellow|Color ?(?:[1-9]|[1-4][0-9]|5[0-6]))\]', re.ASCII | re.IGNORECASE)
SCIENTIFIC = re.compile(r'0(?:\.(0{1,12}))?E([+-])(0{1,4})')
FRACTION_LIMITS = {'# ?/?': 9, '# ??/??': 99}
CJK_DATE = re.compile(r'yyyy(?:"年"|年)(m{1,2})(?:"月"|月)(d{1,2})(?:"日"|日)')
SLASH_DATE = re.compile(r'(m{1,2})\\?/(d{1,2})\\?/yyyy')
FIXED = re.compile(
    r'(?P<prefix>[$¥￥€£]|"[$¥￥€£]"|\\[$¥￥€£])?(?P<core>0+|#,##0)(?P<dec>\.0{0,12}#{0,12})?(?P<scale>,{0,3})(?P<percent>%?)'
    r'(?P<suffix>"[^";\x00-\x1f\x7f]{1,32}"|\\[^;\x00-\x1f\x7f])?'
)


def _number_result(format_id, custom_format, date_system):
    return {'status':'unknown','kind':'unknown','format_id':format_id,
            'format_code':custom_format if custom_format is not None else BUILTIN_FORMATS.get(format_id),
            'date_system':date_system,'basis':'stored_number_and_direct_xf_format',
            'confidence':'limited_format_subset','source_value_unchanged':True,'not_excel_rendering':True}


def _color_prefix(code):
    match = COLOR.match(code)
    return (code[match.end():], match[1]) if match else (code, None)


def _optional_decimals(text, placeholders):
    optional = placeholders.count('#')
    if not optional:
        return text
    return text[:-optional] + text[-optional:].rstrip('0') if '0' in placeholders else text.rstrip('0').removesuffix('.')


def _explicit_date_display(code, year, month, day):
    cjk = CJK_DATE.fullmatch(code)
    spec = cjk or SLASH_DATE.fullmatch(code)
    if spec is None:
        return {}
    month_text = str(month).zfill(len(spec[1]))
    day_text = str(day).zfill(len(spec[2]))
    text = f'{year:04d}年{month_text}月{day_text}日' if cjk else f'{month_text}/{day_text}/{year:04d}'
    return {'display_text': text}


def _scientific(value, spec):
    places = len(spec[1] or '')
    exponent = value.adjusted() if value else 0
    with localcontext() as context:
        context.prec = 1100
        mantissa = value.scaleb(-exponent).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        if mantissa.copy_abs() == 10:
            mantissa = mantissa.scaleb(-1)
            exponent += 1
    exponent_sign = '-' if exponent < 0 else '+' if spec[2] == '+' else ''
    exponent_digits = str(abs(exponent)).zfill(len(spec[3]))
    return {'status':'interpreted', 'kind':'scientific', 'interpreted_value':str(value),
            'display_text':f'{mantissa:.{places}f}E{exponent_sign}{exponent_digits}', 'decimal_places':places, 'rounding':'half_up'}


def _fraction(value, max_denominator):
    nearest = Fraction(value.copy_abs()).limit_denominator(max_denominator)
    whole, numerator = divmod(nearest.numerator, nearest.denominator)
    if numerator:
        text = (f'{whole} ' if whole else '') + f'{numerator}/{nearest.denominator}'
    else:
        text = str(whole)
    return {'status':'interpreted', 'kind':'fraction', 'interpreted_value':str(value),
            'display_text':('-' if value < 0 and nearest else '') + text, 'max_denominator':max_denominator,
            'alignment_omitted':True, 'display_width_verified':False}


def interpret_number(value, format_id, custom_format, date_system):
    """Return additive interpretation, with unknown for every unimplemented grammar."""
    result = _number_result(format_id, custom_format, date_system)
    fmt = result['format_code']
    def unknown(reason):
        return dict(result, reason=reason)
    if not isinstance(value, Decimal) or not value.is_finite():
        return unknown('finite_decimal_required')
    if len(value.as_tuple().digits)>1024 or abs(value.adjusted())>1024:
        return unknown('interpretation_numeric_budget')
    if fmt is None or len(fmt)>255:
        return unknown('format_not_in_supported_subset')
    if ';' not in fmt:
        fmt, color = _color_prefix(fmt)
        if color is not None:
            result['format_color_raw'] = color
    directives = format_directives(value, fmt, lambda number, code: interpret_number(number, format_id, code, date_system))
    if directives is not None:
        directives.update(format_id=format_id, format_code=result['format_code'], date_system=date_system,
                          source_value_unchanged=True, not_excel_rendering=True)
        return dict(result, **directives)
    if fmt == 'General':
        return dict(result,status='interpreted',kind='number',interpreted_value=str(value))
    if fmt == '@':
        return unknown('numeric_cell_with_text_format_not_coerced')
    scientific = SCIENTIFIC.fullmatch(fmt)
    if scientific:
        return dict(result, **_scientific(value, scientific))
    if fmt in FRACTION_LIMITS:
        return dict(result, **_fraction(value, FRACTION_LIMITS[fmt]))
    lower=fmt.lower()
    if lower in DATE_FORMATS or lower in TIME_FORMATS or CJK_DATE.fullmatch(lower) or SLASH_DATE.fullmatch(lower):
        if date_system not in ('1900','1904'):
            return unknown('unknown_date_system')
        if value<0:
            return unknown('negative_date_or_time_not_interpreted')
        day=int(value)
        with localcontext() as ctx:
            ctx.prec=1100
            seconds=int(((value-Decimal(day))*86400).quantize(Decimal(1),rounding=ROUND_HALF_UP))
        if seconds==86400 and (lower in TIME_FORMATS or 'h' in lower):
            day+=1
            seconds=0
        hour,remain=divmod(seconds,3600)
        minute,second=divmod(remain,60)
        if lower in TIME_FORMATS:
            if lower=='[h]:mm:ss':
                hour+=day*24
                kind='duration'
            else:
                kind='time_of_day'
            text=(f'{minute:02d}:{second:02d}' if lower=='mm:ss' else f'{hour:02d}:{minute:02d}'+(f':{second:02d}' if 'ss' in lower else ''))
            return dict(result,status='interpreted',kind=kind,interpreted_value=text,whole_days=day,rounding='half_up_to_second',time_wraps_days=kind=='time_of_day')
        if date_system=='1900' and day==60:
            return dict(result,status='interpreted',kind='fictional_date',interpreted_value='1900-02-29',valid_gregorian_date=False,time_seconds=seconds,
                        **_explicit_date_display(lower, 1900, 2, 29))
        try:
            actual=date(1904,1,1)+timedelta(days=day) if date_system=='1904' else date(1899,12,31)+timedelta(days=day-(day>60))
        except (OverflowError,ValueError):
            return unknown('date_out_of_range')
        text=actual.isoformat()
        if 'h' in lower:
            text+=f'T{hour:02d}:{minute:02d}'+(f':{second:02d}' if 'ss' in lower else '')
        return dict(result,status='interpreted',kind='date_time' if 'h' in lower else 'date',interpreted_value=text,
                    valid_gregorian_date=True,rounding='half_up_to_second',display_locale_not_applied=True,
                    **_explicit_date_display(lower, actual.year, actual.month, actual.day))
    if ';' in fmt:
        accounting = _accounting(value, fmt)
        if accounting is None:
            return unknown('complex_or_unsupported_accounting_format')
        return dict(result, **accounting)
    match = FIXED.fullmatch(fmt)
    if not match or (match['dec'] and not 2 <= len(match['dec']) <= 13):
        return unknown('complex_or_unsupported_format')
    placeholders = (match['dec'] or '')[1:]
    places = len(placeholders)
    with localcontext() as ctx:
        ctx.prec=1100
        multiplier=Decimal(100) if match['percent'] else Decimal(1)
        divisor=Decimal(1000)**len(match['scale'])
        interpreted=value*multiplier/divisor
        rounded=interpreted.quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP)
        text=format(rounded,(',' if ',' in match['core'] else '')+f'.{places}f')
    if len(match['core'])>1 and set(match['core'])=={'0'}:
        sign='-' if text.startswith('-') else ''
        text=sign+text.lstrip('-').zfill(len(match['core'])+(places+1 if places else 0))
    currency_symbol = (match['prefix'] or '').strip('"\\')
    suffix = match['suffix'] or ''
    if suffix:
        suffix = suffix[1:-1] if suffix[0] == '"' else suffix[1:]
    kind='percentage' if match['percent'] else 'currency' if currency_symbol else 'number'
    return dict(result,status='interpreted',kind=kind,interpreted_value=str(interpreted),
                display_text=currency_symbol+_optional_decimals(text, placeholders)+match['percent']+suffix,currency_symbol=currency_symbol or None,
                multiplier=str(multiplier),scale_divisor=str(divisor),decimal_places=places,rounding='half_up',currency_code=None)


def interpret_cell_number(value, format_id, custom_format, date_system, xf_raw_hex, *, styles=()):
    """Interpret the CellXF format, checking the stored copy against its StyleXF when linked."""
    raw = bytes.fromhex(xf_raw_hex)
    source = {}
    if len(raw) != 20 or raw[4] & 4:
        return dict(_number_result(format_id, custom_format, date_system), reason='style_inheritance_unresolved')
    basis = 'stored_number_and_cell_xf_fAtrNum'
    if not raw[9] & 4:
        parent_index = int.from_bytes(raw[4:6], 'little') >> 4
        source = {'style_xf_index': parent_index}
        parent = styles[parent_index] if parent_index != 0xfff and parent_index < len(styles) else None
        parent_raw = bytes.fromhex(parent['raw_hex']) if parent is not None else b''
        # StyleXF sets fStyle, clears f123Prefix and has no parent (0xFFF).
        if len(parent_raw) != 20 or int.from_bytes(parent_raw[4:6], 'little') & 0xfffc != 0xfff4:
            return dict(_number_result(format_id, custom_format, date_system), reason='style_inheritance_unresolved', number_format_source=source)
        source['parent_num_fmt_id'] = parent['number_format_id_raw']
        if format_id != source['parent_num_fmt_id']:
            return dict(_number_result(format_id, custom_format, date_system), reason='cell_parent_number_format_conflict', number_format_source=source)
        basis = 'stored_number_and_matching_biff_style_xf'
    result = interpret_number(value, format_id, custom_format, date_system)
    result['basis'] = basis
    if source:
        result['number_format_source'] = source
    if result['status'] == 'interpreted' and result['format_code'] == 'General':
        binary_value = float(value)
        # RK scaling can produce an exact Decimal that is not binary64.
        if Decimal.from_float(binary_value) == value:
            result['display_text'] = repr(binary_value).removesuffix('.0')
    return result


def _accounting(value, code):
    """Bounded positional accounting sections; alignment is explicitly omitted."""
    sections = ['']
    cursor = 0
    alignment = False
    while cursor < len(code):
        char = code[cursor]
        cursor += 1
        if char == ';':
            sections.append('')
        elif char == '"':
            end = code.find('"', cursor)
            if end < 0:
                return None
            literal = code[cursor:end]
            if not literal or any(c not in '$¥￥€£- ' for c in literal):
                return None
            sections[-1] += literal
            cursor = end + 1
        elif char == chr(92):
            if cursor == len(code) or code[cursor] not in '$¥￥€£()- ':
                return None
            sections[-1] += code[cursor]
            cursor += 1
        elif char in '_*':
            if cursor == len(code) or (char == '*' and code[cursor] != ' ') or (char == '_' and code[cursor] not in '() '):
                return None
            alignment = True
            cursor += 1
        else:
            sections[-1] += char
        if len(sections) > 4:
            return None
    if len(sections) not in (2, 3, 4):
        return None
    sections = [section.strip() for section in sections]
    if len(sections) == 4 and sections[3] != '@':
        return None
    parsed = []
    pattern = (r'(?P<minus>-?)(?P<currency>[$¥￥€£]?)(?P<open>\()? *(?P<inner_currency>[$¥￥€£]?)'
               r'(?P<core>#,##0|0)(?P<dec>\.0{0,12}#{0,12})?(?P<close>\))?')
    for index, section in enumerate(sections[:3]):
        section, color = _color_prefix(section)
        color_fields = {'format_color_raw': color} if color is not None else {}
        if index == 2 and re.fullmatch(r'[$¥￥€£]? *- *\?{0,12}', section):
            parsed.append(('zero_literal', section.rstrip('?').strip(), color_fields))
            continue
        match = re.fullmatch(pattern, section)
        if not match or bool(match['open']) != bool(match['close']) or (match['dec'] and not 2 <= len(match['dec']) <= 13):
            return None
        # Positive and zero sections cannot claim parenthesized negative display.
        if (index != 1 and (match['open'] or match['minus'])) or (match['open'] and match['minus']):
            return None
        if match['inner_currency'] and (not match['open'] or match['currency']):
            return None
        parsed.append(('number', match, color_fields))
    selected = 2 if value == 0 and len(parsed) == 3 else 1 if value < 0 else 0
    kind, spec, color_fields = parsed[selected]
    if kind == 'zero_literal':
        return {'status':'interpreted', 'kind':'accounting', 'interpreted_value':str(value),
                'display_text':spec, 'selected_section':selected, 'rounding':'half_up',
                'alignment_omitted':alignment, 'display_width_verified':False, **color_fields}
    placeholders = (spec['dec'] or '')[1:]
    places = len(placeholders)
    with localcontext() as ctx:
        ctx.prec = 1100
        magnitude = value.copy_abs()
        rounded = magnitude.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        display = format(rounded, (',' if ',' in spec['core'] else '')+f'.{places}f')
    display = _optional_decimals(display, placeholders)
    if selected == 1:
        display = '('+spec['inner_currency']+display+')' if spec['open'] else display
    return {'status':'interpreted', 'kind':'accounting', 'interpreted_value':str(value),
            'display_text':spec['minus']+spec['currency']+display, 'selected_section':selected,
            'currency_symbol':spec['currency'] or spec['inner_currency'] or None, 'currency_code':None,
            'decimal_places':places, 'rounding':'half_up', 'alignment_omitted':alignment,
            'display_width_verified':False, **color_fields}
