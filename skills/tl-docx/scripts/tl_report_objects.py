"""Stored drawing offsets and chart cell locations for enhanced reports."""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from tl_read_tables import resolve_path

WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
VML = '{urn:schemas-microsoft-com:vml}'
SHEET_RANGE = re.compile(r"(?:(?P<sheet>'(?:[^']|'')+'|(?:\[[^\[\]!]+\])?[^!\[\]':]+)!)?\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>[1-9][0-9]{0,6})"
                         r"(?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>[1-9][0-9]{0,6}))?", re.ASCII | re.IGNORECASE)


def drawing_anchor(package, record):
    """Get the actual source drawing container and its stored relative position."""
    node = resolve_path(package.xml(record['part']), record['path'])
    while node is not None and node.tag not in (WP + 'anchor', WP + 'inline', VML + 'shape', VML + 'rect'):
        node = node.getparent()
    if node is None:
        return None
    if node.tag.startswith(VML):
        fields = {'position', 'left', 'top', 'margin-left', 'margin-top',
                  'mso-position-horizontal-relative', 'mso-position-vertical-relative'}
        positions = {}
        for declaration in node.get('style', '').split(';'):
            key, separator, value = declaration.partition(':')
            key = key.strip().lower()
            if separator and key in fields:
                positions[key] = value.strip()
        return {'kind': 'vml', 'stored_style': positions}
    anchor = {'kind': 'floating' if node.tag == WP + 'anchor' else 'inline'}
    for key, tag in (('horizontal', 'positionH'), ('vertical', 'positionV')):
        position = node.find(WP + tag)
        if position is not None:
            values = {'relative_from': position.get('relativeFrom')}
            offset, alignment = (position.find(WP + name) for name in ('posOffset', 'align'))
            if offset is not None and re.fullmatch(r'[+-]?[0-9]{1,14}', offset.text or ''):
                values['offset_emu'] = int(offset.text)
            if alignment is not None:
                values['alignment'] = alignment.text
            anchor[key] = values
    return anchor


def column_number(letters):
    """Convert Excel column letters to a one-based number."""
    number = 0
    for letter in letters.upper():
        number = number * 26 + ord(letter) - ord('A') + 1
    return number


def column_letters(number):
    """Convert a valid one-based Excel column number to letters."""
    result = ''
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(ord('A') + digit) + result
    return result


def chart_range(formula):
    """Recognize one-dimensional stored A1 ranges without resolving external data."""
    if not formula:
        return {'status': 'absent'}, None
    match = SHEET_RANGE.fullmatch(formula)
    if not match:
        return {'status': 'unsupported'}, None
    raw_sheet = match['sheet']
    sheet = raw_sheet[1:-1].replace("''", "'") if raw_sheet and raw_sheet.startswith("'") else raw_sheet
    external = False
    if sheet and sheet.startswith('['):
        workbook = re.fullmatch(r'\[[^\[\]\x00-\x1f]+\]([^\[\]\x00-\x1f]+)', sheet)
        if workbook is None or any(char in sheet[1:sheet.index(']')] for char in ':\x7f/\\?*!'):
            return {'status': 'unsupported'}, None
        sheet = workbook[1]
        external = True
    if sheet and (len(sheet) > 31 or any(char in sheet for char in '[]:*?/\\') or any(ord(char) < 32 or ord(char) == 127 for char in sheet)):
        return {'status': 'unsupported'}, None
    left, right = column_number(match['c1']), column_number(match['c2'] or match['c1'])
    top, bottom = int(match['r1']), int(match['r2'] or match['r1'])
    if not (left <= right <= 16384 and top <= bottom <= 1048576) or (left != right and top != bottom):
        return {'status': 'unsupported'}, None
    reference = {'status': 'a1_range', 'sheet': sheet, 'start': f'{column_letters(left)}{top}', 'end': f'{column_letters(right)}{bottom}'}
    if external:
        reference['external_workbook'] = True
    return reference, (left, right, top, bottom)


def locate_series(item, points):
    """Associate saved point indices with compatible stored formula ranges."""
    describe_series(item, points)
    roles = ['category', 'value'] + [role for role in ('x', 'y', 'bubble_size') if role + '_source' in item]
    for role in roles:
        location, coordinates = chart_range(item.get(role + '_formula'))
        item[role + '_reference'] = location
        if coordinates is None or not points:
            continue
        left, right, top, bottom = coordinates
        length = max(right - left, bottom - top) + 1
        indexes = [point['index'] for point in points]
        if indexes != list(range(len(points))) or length != len(points):
            continue
        for point in points:
            index = point['index']
            column = left + index if left != right else left
            row = top + index if top != bottom else top
            point[role + '_cell'] = f'{column_letters(column)}{row}'
    for key in ['name_formula'] + [role + '_formula' for role in roles]:
        item.pop(key, None)


def display_value(raw, format_code):
    """Offer a limited display without replacing the original saved string."""
    result = {'text': raw, 'status': 'raw', 'format_code': format_code}
    if raw is None:
        return dict(result, status='missing')
    if not format_code or format_code == 'General':
        return result
    match = re.fullmatch(r'0(?:\.(0{1,6}))?%', format_code)
    if match is None:
        return dict(result, status='unsupported_format')
    if not isinstance(raw, str) or len(raw) > 256:
        return dict(result, status='format_limit')
    if re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]{1,4})?', raw) is None:
        return dict(result, status='invalid_numeric')
    try:
        number = Decimal(raw)
        if not number.is_finite() or abs(number.adjusted()) > 100:
            return dict(result, status='format_limit')
        digits = len(match[1] or '')
        with localcontext() as context:
            context.prec = 300
            percentage = (number * 100).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
            rendered = format(percentage, f'.{digits}f') + '%'
    except InvalidOperation:
        return dict(result, status='invalid_numeric')
    return dict(result, text=rendered, status='formatted_percent')


def describe_series(item, points):
    """Add saved-data availability and display values to an opt-in report only."""
    roles = ['x', 'y'] if item.get('chart_type') in ('scatterChart', 'bubbleChart') else ['value']
    if item.get('chart_type') == 'bubbleChart':
        roles.append('bubble_size')
    dimensions = {}
    for role in roles:
        available = sum(point.get(role) is not None for point in points)
        dimensions[role] = {'available_points': available, 'missing_in_observed_points': len(points) - available,
                            'source': item.get(role + '_source', item.get('value_source') if role == 'y' else None)}
        for point in points:
            code = item.get('format_code') if role == 'value' else item.get(role + '_format_code')
            point.setdefault('display', {})[role] = display_value(point.get(role), code)
    issues = list(item.get('issues', []))
    available_count = sum(value['available_points'] for value in dimensions.values())
    missing_count = sum(value['missing_in_observed_points'] for value in dimensions.values())
    status = 'unavailable' if not available_count else 'partial' if issues or missing_count else 'available'
    descriptions = {'available': '可读取此系列已保存的数据；不代表数据源已更新。',
                    'partial': '此系列仅有部分已保存的数据可用，缺失项未补算。',
                    'unavailable': '此系列没有可用的已保存数值，未从外部数据源补取。'}
    item['cache_summary'] = {'status': status, 'description': descriptions[status],
                             'dimensions': dimensions, 'diagnostics': issues,
                             'point_scope': 'observed_indices_only',
                             'display_scope': 'simple_percent_only; not rendered labels or formula calculation'}
