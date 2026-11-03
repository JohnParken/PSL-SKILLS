"""Conservative stored-number interpretation; never changes source values."""
from datetime import date, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
import re
from wps_format_rules import format_directives

BUILTIN_FORMATS = {0:'General',1:'0',2:'0.00',3:'#,##0',4:'#,##0.00',9:'0%',10:'0.00%',14:'m/d/yy',20:'h:mm',21:'h:mm:ss',22:'m/d/yy h:mm',45:'mm:ss',46:'[h]:mm:ss',49:'@'}
DATE_FORMATS = {'yyyy-mm-dd','yyyy/mm/dd','m/d/yy','yyyy-mm-dd hh:mm:ss','m/d/yy h:mm'}
TIME_FORMATS = {'h:mm','hh:mm','h:mm:ss','hh:mm:ss','mm:ss','[h]:mm:ss'}

def interpret_number(value, format_id, custom_format, date_system):
    """Return additive interpretation, with unknown for every unimplemented grammar."""
    fmt = custom_format if custom_format is not None else BUILTIN_FORMATS.get(format_id)
    result = {'status':'unknown','kind':'unknown','format_id':format_id,'format_code':fmt,
              'date_system':date_system,'basis':'stored_number_and_direct_xf_format',
              'confidence':'limited_format_subset','source_value_unchanged':True,
              'not_excel_rendering':True}
    def unknown(reason):
        return dict(result, reason=reason)
    if not isinstance(value, Decimal) or not value.is_finite():
        return unknown('finite_decimal_required')
    if len(value.as_tuple().digits)>1024 or abs(value.adjusted())>1024:
        return unknown('interpretation_numeric_budget')
    if fmt is None or len(fmt)>255: return unknown('format_not_in_supported_subset')
    directives = format_directives(value, fmt, lambda number, code: interpret_number(number, format_id, code, date_system))
    if directives is not None:
        directives.update(format_id=format_id, format_code=fmt, date_system=date_system,
                          source_value_unchanged=True, not_excel_rendering=True)
        return dict(result, **directives)
    if fmt == 'General': return dict(result,status='interpreted',kind='number',interpreted_value=str(value))
    if fmt == '@': return unknown('numeric_cell_with_text_format_not_coerced')
    lower=fmt.lower()
    if lower in DATE_FORMATS or lower in TIME_FORMATS:
        if date_system not in ('1900','1904'): return unknown('unknown_date_system')
        if value<0: return unknown('negative_date_or_time_not_interpreted')
        day=int(value)
        with localcontext() as ctx:
            ctx.prec=1100
            seconds=int(((value-Decimal(day))*86400).quantize(Decimal(1),rounding=ROUND_HALF_UP))
        if seconds==86400 and (lower in TIME_FORMATS or 'h' in lower): day+=1; seconds=0
        hour,remain=divmod(seconds,3600); minute,second=divmod(remain,60)
        if lower in TIME_FORMATS:
            if lower=='[h]:mm:ss': hour+=day*24; kind='duration'
            else: kind='time_of_day'
            text=(f'{minute:02d}:{second:02d}' if lower=='mm:ss' else f'{hour:02d}:{minute:02d}'+(f':{second:02d}' if 'ss' in lower else ''))
            return dict(result,status='interpreted',kind=kind,interpreted_value=text,whole_days=day,rounding='half_up_to_second',time_wraps_days=kind=='time_of_day')
        if date_system=='1900' and day==60:
            return dict(result,status='interpreted',kind='fictional_date',interpreted_value='1900-02-29',valid_gregorian_date=False,time_seconds=seconds)
        try:
            actual=date(1904,1,1)+timedelta(days=day) if date_system=='1904' else date(1899,12,31)+timedelta(days=day-(day>60))
        except (OverflowError,ValueError): return unknown('date_out_of_range')
        text=actual.isoformat()
        if 'h' in lower: text+=f'T{hour:02d}:{minute:02d}'+(f':{second:02d}' if 'ss' in lower else '')
        return dict(result,status='interpreted',kind='date_time' if 'h' in lower else 'date',interpreted_value=text,valid_gregorian_date=True,rounding='half_up_to_second',display_locale_not_applied=True)
    if ';' in fmt:
        accounting = _accounting(value, fmt)
        if accounting is None:
            return unknown('complex_or_unsupported_accounting_format')
        return dict(result, **accounting)
    # One numeric section only. No conditions, locale directives, escapes, or expressions.
    match=re.fullmatch(r'(?P<prefix>[$¥￥€£]?)(?P<core>0+|#,##0)(?P<dec>\.0{1,12})?(?P<scale>,{0,3})(?P<percent>%?)',fmt)
    if not match: return unknown('complex_or_unsupported_format')
    places=len(match['dec'] or '')-1 if match['dec'] else 0
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
    kind='percentage' if match['percent'] else 'currency' if match['prefix'] else 'number'
    return dict(result,status='interpreted',kind=kind,interpreted_value=str(interpreted),
                display_text=match['prefix']+text+match['percent'],currency_symbol=match['prefix'] or None,
                multiplier=str(multiplier),scale_divisor=str(divisor),decimal_places=places,rounding='half_up',currency_code=None)


def interpret_cell_number(value, format_id, custom_format, date_system, xf_raw_hex):
    result = interpret_number(value, format_id, custom_format, date_system)
    raw = bytes.fromhex(xf_raw_hex)
    # XF: fStyle is bit2 at byte4; CellXF fAtrNum is bit2 at byte9.
    # Parent style resolution is deliberately not implemented in this subset.
    if len(raw) != 20 or raw[4] & 4 or not raw[9] & 4:
        result.update(status='unknown', kind='unknown', reason='style_inheritance_unresolved')
        for key in ('interpreted_value','display_text','valid_gregorian_date','time_seconds','whole_days','time_wraps_days','currency_symbol','multiplier','scale_divisor','decimal_places','rounding','currency_code','display_locale_not_applied','selected_section','alignment_omitted','display_width_verified'):
            result.pop(key, None)
    else:
        result['basis'] = 'stored_number_and_cell_xf_fAtrNum'
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
    pattern = r'(?P<currency>[$¥￥€£]?)(?P<open>\()? *(?P<core>#,##0|0)(?P<dec>\.0{1,12})?(?P<close>\))?'
    for index, section in enumerate(sections[:3]):
        if index == 2 and re.fullmatch(r'[$¥￥€£]? *- *\?{0,12}', section):
            parsed.append(('zero_literal', section.rstrip('?').strip()))
            continue
        match = re.fullmatch(pattern, section)
        if not match or bool(match['open']) != bool(match['close']):
            return None
        # Positive and zero sections cannot claim parenthesized negative display.
        if index != 1 and match['open']:
            return None
        parsed.append(('number', match))
    selected = 2 if value == 0 and len(parsed) == 3 else 1 if value < 0 else 0
    kind, spec = parsed[selected]
    if kind == 'zero_literal':
        return {'status':'interpreted', 'kind':'accounting', 'interpreted_value':str(value),
                'display_text':spec, 'selected_section':selected, 'rounding':'half_up',
                'alignment_omitted':alignment, 'display_width_verified':False}
    places = len(spec['dec'])-1 if spec['dec'] else 0
    with localcontext() as ctx:
        ctx.prec = 1100
        magnitude = value.copy_abs()
        rounded = magnitude.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        display = format(rounded, (',' if ',' in spec['core'] else '')+f'.{places}f')
    if selected == 1:
        display = '('+display+')' if spec['open'] else display
    return {'status':'interpreted', 'kind':'accounting', 'interpreted_value':str(value),
            'display_text':spec['currency']+display, 'selected_section':selected,
            'currency_symbol':spec['currency'] or None, 'currency_code':None,
            'decimal_places':places, 'rounding':'half_up', 'alignment_omitted':alignment,
            'display_width_verified':False}
