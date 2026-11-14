"""Read internal XLSX stored chart values without formula execution or external I/O.

Only unambiguous single-sheet A1 vectors supplement absent cache points. Original
cache points/status/issues remain untouched; enrichment is explicitly separate.
"""
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re
import zipfile
import zlib

from wps_binary_chart_cache import CHART_NS, ChartCacheLimit
from wps_ooxml import (CONTENT_TYPES, R, RELATIONSHIPS, XmlBudgetExceeded,
                       check_archive, preflight_archive, read_part, resolve_internal_target)

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
MAIN = XLSX + '.main+xml'
SHEET = 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'
STRINGS = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml'
RELS = 'application/vnd.openxmlformats-package.relationships+xml'
MAX_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_EXPANDED = 8 * 1024 * 1024
MAX_TOTAL_EXPANDED = 32 * 1024 * 1024
MAX_MEMBERS = 128
MAX_CELLS = 100000
MAX_SHEETS = 128
MAX_STRINGS = 25000
MAX_VECTOR = 25000
CELL = re.compile(r'\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})\Z')
NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
SAFE_ERRORS = (ValueError, KeyError, zipfile.BadZipFile, NotImplementedError, OSError, zlib.error)


def tag(local, namespace=S):
    return '{' + namespace + '}' + local


def one(root, name, required=True):
    items = root.findall(name)
    if len(items) != 1:
        if not items and not required:
            return None
        raise ValueError('ambiguous_or_missing_element')
    return items[0]


def charge(budget, field, amount, limit):
    value = getattr(budget, field, 0) + amount
    if value > limit:
        raise ChartCacheLimit('Embedded workbook resource budget exceeded')
    setattr(budget, field, value)


def content_types(root):
    if root.tag != tag('Types', CONTENT_TYPES):
        raise ValueError('invalid_content_types')
    defaults, overrides = {}, {}
    for node in root:
        if node.tag == tag('Override', CONTENT_TYPES):
            raw = node.get('PartName', '')
            if not raw.startswith('/'):
                raise ValueError('invalid_part_name')
            key = resolve_internal_target(raw, None)
            if raw != '/' + key:
                raise ValueError('noncanonical_part_name')
            values = overrides
        elif node.tag == tag('Default', CONTENT_TYPES):
            key = node.get('Extension', '').lower()
            values = defaults
        else:
            raise ValueError('unknown_content_type_element')
        value = node.get('ContentType')
        if not key or not value or key in values:
            raise ValueError('ambiguous_content_type')
        values[key] = value
    return defaults, overrides


def type_of(part, types):
    defaults, overrides = types
    return overrides.get(part, defaults.get(part.rsplit('.', 1)[-1].lower()))


def typed_xml(package, part, expected, types, budget):
    if type_of(part, types) != expected:
        raise ValueError('invalid_part_type')
    return budget.load_xml(package, part)


def relationships(package, source, types, budget):
    directory, _, name = (source or '').rpartition('/')
    part = (directory + '/' if directory else '') + '_rels/' + name + '.rels'
    root = typed_xml(package, part, RELS, types, budget)
    if root.tag != tag('Relationships', RELATIONSHIPS):
        raise ValueError('invalid_relationships')
    result = {}
    for node in root:
        identity = node.get('Id')
        if node.tag != tag('Relationship', RELATIONSHIPS) or not identity or identity in result:
            raise ValueError('ambiguous_relationship')
        result[identity] = node
    return result


def internal(relation, source, kind):
    if relation is None or relation.get('Type') != R + '/' + kind:
        raise ValueError('wrong_relationship')
    if relation.get('TargetMode', 'Internal') != 'Internal':
        raise ValueError('noninternal_relationship')
    target = relation.get('Target', '')
    if len(target.encode('utf-8')) > 1024:
        raise ChartCacheLimit('Embedded workbook target budget exceeded')
    return resolve_internal_target(target, source)


def coordinate(value):
    match = CELL.fullmatch(value)
    if match is None:
        raise ValueError('unsupported_coordinate')
    column = 0
    for char in match[1]:
        column = column * 26 + ord(char) - 64
    row = int(match[2])
    if column > 16384 or row > 1048576:
        raise ValueError('coordinate_outside_sheet')
    return column, row, match[1] + match[2]


def vector(formula):
    """Parse a chart range only, never Excel expression evaluation."""
    if not isinstance(formula, str) or len(formula) > 1024 or formula.startswith('='):
        raise ValueError('unsupported_reference')
    match = re.fullmatch(r"(?:'((?:[^']|'')+)'|([^'!\s]+))!([^!]+)", formula)
    if match is None:
        raise ValueError('unsupported_reference')
    sheet = match[1].replace("''", "'") if match[1] is not None else match[2]
    if any(char in sheet for char in '[]:/\\?*'):
        raise ValueError('nonlocal_reference')
    refs = match[3].split(':')
    if len(refs) not in (1, 2):
        raise ValueError('unsupported_reference')
    a, b = coordinate(refs[0]), coordinate(refs[-1])
    if a[0] > b[0] or a[1] > b[1] or a[0] != b[0] and a[1] != b[1]:
        raise ValueError('reference_not_vector')
    count = max(b[0] - a[0], b[1] - a[1]) + 1
    if count > MAX_VECTOR:
        raise ChartCacheLimit('Embedded workbook vector budget exceeded')
    return sheet, [(a[0] + (i if a[1] == b[1] else 0),
                    a[1] + (i if a[0] == b[0] else 0)) for i in range(count)]


class Workbook:
    """Read only referenced worksheets inside shared document budgets."""

    def __init__(self, package, budget):
        self.package, self.budget = package, budget
        self.types = content_types(budget.load_xml(package, '[Content_Types].xml'))
        rels = relationships(package, None, self.types, budget)
        main = [r for r in rels.values() if r.get('Type') == R + '/officeDocument']
        if len(main) != 1:
            raise ValueError('ambiguous_workbook')
        self.part = internal(main[0], None, 'officeDocument')
        root = typed_xml(package, self.part, MAIN, self.types, budget)
        if root.tag != tag('workbook'):
            raise ValueError('invalid_workbook')
        self.rels = relationships(package, self.part, self.types, budget)
        self.sheets, self.loaded, self.shared = {}, {}, None
        names, targets = set(), set()
        for item in one(root, tag('sheets')):
            name = item.get('name')
            if item.tag != tag('sheet') or not name or len(name) > 31 or name.casefold() in names:
                raise ValueError('ambiguous_sheet')
            if len(self.sheets) >= MAX_SHEETS:
                raise ChartCacheLimit('Embedded workbook sheet budget exceeded')
            target = internal(self.rels.get(item.get(tag('id', R))), self.part, 'worksheet')
            if target in targets:
                raise ValueError('aliased_worksheet')
            names.add(name.casefold())
            targets.add(target)
            self.sheets[name] = target

    def strings(self):
        if self.shared is not None:
            return self.shared
        relations = [r for r in self.rels.values() if r.get('Type') == R + '/sharedStrings']
        if len(relations) != 1:
            raise ValueError('ambiguous_shared_strings')
        part = internal(relations[0], self.part, 'sharedStrings')
        root = typed_xml(self.package, part, STRINGS, self.types, self.budget)
        if root.tag != tag('sst'):
            raise ValueError('invalid_shared_strings')
        result = []
        for item in root:
            if item.tag != tag('si'):
                raise ValueError('invalid_shared_string')
            charge(self.budget, 'workbook_strings', 1, MAX_STRINGS)
            result.append(self.rich_text(item))
        self.shared = result
        return result

    def rich_text(self, root):
        pieces = []
        for node in root:
            if node.tag == tag('t') and not len(node):
                pieces.append(node.text or '')
            elif node.tag == tag('r'):
                value = one(node, tag('t'))
                if len(value):
                    raise ValueError('invalid_rich_text')
                pieces.append(value.text or '')
            elif node.tag not in (tag('rPh'), tag('phoneticPr')):
                raise ValueError('unsupported_string_markup')
        text = ''.join(pieces)
        if len(text.encode('utf-8')) > self.budget.caches.MAX_VALUE_BYTES:
            raise ChartCacheLimit('Embedded workbook value budget exceeded')
        return text

    def cells(self, sheet):
        if sheet in self.loaded:
            return self.loaded[sheet]
        if sheet not in self.sheets:
            raise ValueError('unknown_sheet')
        root = typed_xml(self.package, self.sheets[sheet], SHEET, self.types, self.budget)
        if root.tag != tag('worksheet'):
            raise ValueError('invalid_worksheet')
        cells = {}
        for row in one(root, tag('sheetData')):
            if row.tag != tag('row'):
                raise ValueError('unsupported_sheet_data')
            for cell in row:
                if cell.tag != tag('c'):
                    raise ValueError('unsupported_cell_markup')
                charge(self.budget, 'workbook_cells', 1, MAX_CELLS)
                col, line, name = coordinate(cell.get('r', ''))
                if cell.get('r') != name or (col, line) in cells:
                    raise ValueError('ambiguous_cell')
                cells[(col, line)] = cell
        self.loaded[sheet] = cells
        return cells

    def value(self, cell, numeric):
        if cell is None:
            raise ValueError('cell_absent')
        formulas = cell.findall(tag('f'))
        if len(formulas) > 1:
            raise ValueError('ambiguous_cell_formula')
        kind = cell.get('t', 'n')
        value = one(cell, tag('v'), required=False)
        if value is not None and len(value):
            raise ValueError('invalid_cell_value')
        text = value.text if value is not None else None
        if kind == 'inlineStr':
            if value is not None or formulas:
                raise ValueError('ambiguous_inline_string')
            text = self.rich_text(one(cell, tag('is')))
        elif kind == 's':
            if text is None or not re.fullmatch(r'[0-9]{1,8}', text):
                raise ValueError('invalid_string_index')
            strings = self.strings()
            if int(text) >= len(strings):
                raise ValueError('invalid_string_index')
            text = strings[int(text)]
        elif kind == 'n':
            if text is None or len(text) > 4096 or not NUMBER.fullmatch(text):
                raise ValueError('missing_or_invalid_number')
            try:
                if not Decimal(text).is_finite():
                    raise ValueError('nonfinite_number')
            except InvalidOperation as exc:
                raise ValueError('invalid_number') from exc
        elif kind == 'b':
            if text not in ('0', '1'):
                raise ValueError('invalid_boolean')
        elif kind == 'str':
            if text is None:
                raise ValueError('string_value_absent')
        else:
            raise ValueError('error_or_unsupported_cell_type')
        if numeric and kind != 'n':
            raise ValueError('nonnumeric_cell')
        if len(text.encode('utf-8')) > self.budget.caches.MAX_VALUE_BYTES:
            raise ChartCacheLimit('Embedded workbook value budget exceeded')
        return text, {'value_kind': kind, 'formula_cached': bool(formulas)}


def fill_channel(channel, node, book, budget):
    """Keep original cache fields; report effective supplementary coverage separately."""
    if channel.get('status') == 'complete':
        return 0
    choices = [n for n in node if n.tag in (tag('numRef', CHART_NS), tag('strRef', CHART_NS))]
    if len(node) != 1 or len(choices) != 1:
        raise ValueError('ambiguous_or_unsupported_chart_source')
    reference = choices[0]
    formula = one(reference, tag('f', CHART_NS))
    if len(formula):
        raise ValueError('invalid_chart_formula')
    sheet, coordinates = vector(formula.text)
    declared = channel.get('declared_count')
    if declared is not None and declared != len(coordinates):
        raise ValueError('cache_range_count_mismatch')
    cache_issues = set(channel.get('issues', []))
    if not cache_issues.issubset({'cache_missing', 'pt_count_missing', 'point_value_invalid'}):
        raise ValueError('ambiguous_cache_not_supplemented')
    points = channel.get('points', [])
    existing = {point['index'] for point in points}
    if len(existing) != len(points) or any(type(i) is not int or not 0 <= i < len(coordinates) for i in existing):
        raise ValueError('ambiguous_cache_indices')
    cells = book.cells(sheet)
    added, missing, missing_cells = [], [], []
    numeric = reference.tag == tag('numRef', CHART_NS)
    for index, position in enumerate(coordinates):
        if index in existing:
            continue
        budget.caches.charge_points()
        try:
            cell = cells.get(position)
            value, evidence = book.value(cell, numeric)
        except ChartCacheLimit:
            raise
        except ValueError as exc:
            missing.append(index)
            missing_cells.append({'index': index, 'reason': str(exc)})
            continue
        budget.caches.charge_text(len(value.encode('utf-8')))
        added.append({'index': index, 'value': value, 'source': {
            'kind': 'embedded_workbook', 'sheet': sheet, 'cell': cell.get('r'),
            'stored_value_only': True, **evidence}})
    # Commit only after the entire channel passes shared resource budgets.
    channel['points'] = sorted(points + added, key=lambda point: point['index'])
    if added:
        channel['provenance'] = 'mixed_chart_cache_and_embedded_workbook' if points else 'embedded_workbook'
    channel['effective_missing_indices'] = missing
    channel['workbook_enrichment'] = {
        'status': 'partial' if missing else 'stored_values', 'filled_points': len(added),
        'reference_count': len(coordinates), 'missing_indices': missing, 'missing_cells': missing_cells,
        'cache_fields_preserved': True, 'formulas_evaluated': False,
        'number_formats_applied': False, 'freshness_verified': False,
    }
    return len(added)


def fill(parsed, root, book, budget, state):
    chart = one(root, tag('chart', CHART_NS))
    plot = one(chart, tag('plotArea', CHART_NS))
    elements = [series for group in plot for series in group if series.tag == tag('ser', CHART_NS)]
    if len(elements) != len(parsed.get('series', [])):
        raise ValueError('series_mapping_ambiguous')
    for element, series in zip(elements, parsed['series']):
        for local, key in (('cat', 'categories'), ('val', 'values')):
            try:
                wrapper = one(element, tag(local, CHART_NS))
                state['filled_points'] += fill_channel(series[key], wrapper, book, budget)
                details = series[key].get('workbook_enrichment')
                if details and details['status'] == 'partial':
                    state['issues'].append('referenced_cells_missing_or_unsupported')
            except (ChartCacheLimit, XmlBudgetExceeded):
                raise
            except SAFE_ERRORS:
                state['issues'].append('channel_reference_unresolved')


def enrich_embedded(package, root, chart_part, types, budget, parsed):
    """Return safe local state; never let optional workbook failure erase caches."""
    state = {'status': 'no_reference', 'filled_points': 0, 'issues': [],
             'formulas_evaluated': False, 'scope': 'embedded_xlsx_stored_values'}
    sources = root.findall(tag('externalData', CHART_NS))
    if not sources:
        return state
    try:
        if len(sources) != 1:
            raise ValueError('ambiguous_external_data')
        rels = relationships(package, chart_part, types, budget)
        relation = rels.get(sources[0].get(tag('id', R)))
        if relation is not None and relation.get('TargetMode') == 'External':
            state['status'] = 'external_not_followed'
            return state
        part = internal(relation, chart_part, 'package')
        if type_of(part, types) != XLSX:
            state['status'] = 'embedded_format_unsupported'
            return state
        info = package.getinfo(part)
        if info.file_size > MAX_BYTES:
            raise ChartCacheLimit('Embedded workbook byte budget exceeded')
        charge(budget, 'workbook_bytes', info.file_size, MAX_TOTAL_BYTES)
        payload = read_part(package, part)
        stream = BytesIO(payload)
        preflight_archive(stream, len(payload))
        with zipfile.ZipFile(stream) as workbook:
            entries = workbook.infolist()
            if len(entries) > MAX_MEMBERS:
                raise ChartCacheLimit('Embedded workbook member budget exceeded')
            expanded = sum(entry.file_size for entry in entries)
            if expanded > MAX_EXPANDED:
                raise ChartCacheLimit('Embedded workbook expanded budget exceeded')
            charge(budget, 'workbook_expanded', expanded, MAX_TOTAL_EXPANDED)
            check_archive(workbook)
            book = Workbook(workbook, budget)
            fill(parsed, root, book, budget, state)
        state['status'] = 'partial' if state['issues'] else 'stored_values'
    except (ChartCacheLimit, XmlBudgetExceeded):
        raise
    except SAFE_ERRORS:
        state['status'] = 'unresolved'
        state['issues'].append('embedded_workbook_unresolved')
    state['issues'] = list(dict.fromkeys(state['issues']))
    return state
