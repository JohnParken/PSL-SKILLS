"""Bounded numeric format conditions and locale metadata; no formula evaluation."""
from decimal import Decimal
import re

MAX_CONDITION_DIGITS = 64
CONDITION = re.compile(r'\[(<=|>=|<>|=|<|>)([+-]?(?:[0-9]{1,64}(?:\.[0-9]{1,64})?|\.[0-9]{1,64}))\]')
LOCALE = re.compile(r'\[\$(?P<symbol>[^\[\];\x00-\x1f]{0,8})-(?P<lcid>[0-9a-fA-F]{1,8})\]')
SIMPLE_NUMBER = re.compile(r'[$¥￥€£]?(?:0+|#,##0)(?:\.0{1,12})?,{0,3}%?')


def format_directives(value, code, interpret):
    """Return None for ordinary grammar, otherwise a finite result/unknown.

    Conditions are constants compared with the stored Decimal, not conditional
    formatting formulas. Locale directives are metadata only: no OS locale or
    calendar lookup is performed and no locale-dependent display is fabricated.
    """
    locale = LOCALE.match(code)
    if locale:
        return {'status': 'unknown', 'kind': 'unknown',
                'reason': 'locale_display_not_interpreted',
                'locale_directive': {'raw': locale[0], 'currency_symbol_raw': locale['symbol'],
                                     'lcid_hex_raw': locale['lcid'], 'lcid_integer': int(locale['lcid'], 16),
                                     'resolved': False},
                'format_without_locale_prefix': code[locale.end():],
                'display_locale_not_applied': True}
    if not code.startswith('[') or not CONDITION.match(code):
        return None
    sections = code.split(';')
    if not 2 <= len(sections) <= 3:
        return {'status': 'unknown', 'kind': 'unknown', 'reason': 'unsupported_conditional_format_sections'}
    conditions = []
    for index, section in enumerate(sections):
        match = CONDITION.match(section)
        if match:
            if index == len(sections)-1:
                return {'status':'unknown','kind':'unknown','reason':'conditional_format_requires_final_fallback'}
            literal = match[2]
            if sum(c.isdigit() for c in literal) > MAX_CONDITION_DIGITS:
                return {'status':'unknown','kind':'unknown','reason':'condition_constant_budget'}
            number = Decimal(literal)
            body = section[match.end():]
            op = match[1]
            selected = {'<': value < number, '>': value > number, '<=': value <= number,
                        '>=': value >= number, '=': value == number, '<>': value != number}[op]
            conditions.append({'operator':op, 'constant':literal, 'matched':selected})
        else:
            if index != len(sections)-1:
                return {'status':'unknown','kind':'unknown','reason':'conditional_format_unconditioned_section_not_last'}
            body = section
            selected = True
        # Validate ALL sections, including unselected ones; do not imply complete
        # grammar interpretation merely because this value chose a simpler branch.
        if not SIMPLE_NUMBER.fullmatch(body):
            return {'status':'unknown','kind':'unknown','reason':'conditional_format_body_outside_subset'}
    choice = next((i for i,item in enumerate(conditions) if item['matched']),len(sections)-1)
    match = CONDITION.match(sections[choice])
    selected_code = sections[choice][match.end():] if match else sections[choice]
    result = interpret(value, selected_code)
    result.update(selected_section=choice, conditions=conditions,
                  condition_policy='stored_decimal_constant_comparisons_only',
                  selected_format_code=selected_code)
    return result
