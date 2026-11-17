"""Bounded native table and saved chart-cache content from validated PPTX XML.

No workbook evaluation, link following, number coercion or rendering occurs here.
Semantic traversal callbacks keep the package reader's selected MCE branch policy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
C_NS = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
CX_NS = 'http://schemas.microsoft.com/office/drawing/2014/chartex'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MC_NS = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
M_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
MAX_CONTENT_ITEMS = 100000
MAX_CONTENT_CHARACTERS = 4 * 1024 * 1024
MAX_CONTENT_DIAGNOSTICS = 128
CHART_TYPES = frozenset({
    'areaChart', 'area3DChart', 'barChart', 'bar3DChart', 'bubbleChart', 'doughnutChart',
    'lineChart', 'line3DChart', 'ofPieChart', 'pieChart', 'pie3DChart', 'radarChart',
    'scatterChart', 'stockChart', 'surfaceChart', 'surface3DChart',
})
DATA_ROLES = {'tx': 'name', 'cat': 'categories', 'val': 'values', 'xVal': 'x_values',
              'yVal': 'y_values', 'bubbleSize': 'bubble_sizes'}


def _tag(namespace, name):
    return '{' + namespace + '}' + name


def _unsigned(raw):
    """Parse bounded indexes without allocating from attacker-controlled counts."""
    if raw is not None and re.fullmatch(r'[0-9]{1,10}', raw):
        value = int(raw)
        if value <= 4294967295:
            return value
    return None


class _ContentLimit(Exception):
    """The shared additive content budget is exhausted."""


@dataclass
class _ContentBudget:
    items: int = 0
    characters: int = 0

    def consume(self, text=''):
        """Charge all emitted records and stored text before returning them."""
        if self.items >= MAX_CONTENT_ITEMS or self.characters + len(text) > MAX_CONTENT_CHARACTERS:
            raise _ContentLimit
        self.items += 1
        self.characters += len(text)
        return text


class ContentReader:
    """Extract content without changing the structural reader's acceptance rules."""

    def __init__(self, package, semantic_elements, alternate_branch):
        self.package = package
        self.semantic_elements = semantic_elements
        self.alternate_branch = alternate_branch
        self.budget = _ContentBudget()
        self.diagnostics = []
        self.diagnostics_omitted = 0
        self.scope = ''
        self.order = 0

    def _elements(self, root):
        for element in self.semantic_elements(root, self.package.understood_choices):
            self.budget.consume()
            yield element

    def _children(self, root):
        for child in root:
            self.budget.consume()
            if child.tag == _tag(MC_NS, 'AlternateContent'):
                branch = self.alternate_branch(child, self.package.understood_choices, 'PPTX')
                if branch is not None:
                    yield from self._children(branch)
            else:
                yield child

    def _named(self, root, namespace, name):
        return [child for child in self._children(root) if child.tag == _tag(namespace, name)]

    def _first(self, root, namespace, name):
        return next((child for child in self._children(root) if child.tag == _tag(namespace, name)), None)

    def _diagnose(self, code, **location):
        if len(self.diagnostics) < MAX_CONTENT_DIAGNOSTICS:
            self.diagnostics.append({'code': code, 'scope': self.scope, 'order': self.order, **location})
        else:
            self.diagnostics_omitted += 1

    def _text(self, root):
        paragraphs = []
        for paragraph in self._elements(root):
            if paragraph.tag != _tag(A_NS, 'p'):
                continue
            pieces = []
            for item in self._elements(paragraph):
                if item.tag in (_tag(A_NS, 't'), _tag(M_NS, 't')):
                    pieces.append(self.budget.consume(item.text or ''))
                elif item.tag == _tag(A_NS, 'br'):
                    pieces.append(self.budget.consume('\n'))
                if item.tag.startswith('{' + M_NS + '}'):
                    self._diagnose('math_text_flattened')
            self.budget.consume('\n')
            paragraphs.append(''.join(pieces))
        return '\n'.join(paragraphs)

    def extract(self, root, owner, scope):
        """Return per-scope lists and diagnostics, using one deck-wide budget."""
        self.scope, self.order = scope, 0
        self.diagnostics, self.diagnostics_omitted = [], 0
        result = {'tables': [], 'charts': []}
        try:
            for element in self._elements(root):
                if element.tag not in (_tag(A_NS, 'tbl'), _tag(C_NS, 'chart'), _tag(CX_NS, 'chart')):
                    continue
                self.order += 1
                self.budget.consume()
                item = {'order': self.order, 'source': {'part': owner}}
                if element.tag == _tag(A_NS, 'tbl'):
                    result['tables'].append(item)
                    self._table(element, item)
                else:
                    result['charts'].append(item)
                    self._chart(element, owner, item)
        except _ContentLimit:
            self.diagnostics.append({'code': 'content_limit_exceeded', 'scope': scope, 'order': self.order,
                                     'limits': {'items': MAX_CONTENT_ITEMS, 'characters': MAX_CONTENT_CHARACTERS}})
        if self.diagnostics_omitted:
            self.diagnostics.append({'code': 'content_diagnostics_truncated', 'scope': scope,
                                     'omitted': self.diagnostics_omitted})
        return result, self.diagnostics

    def _table(self, root, result):
        grids = self._named(root, A_NS, 'tblGrid')
        rows = self._named(root, A_NS, 'tr')
        columns = len(self._named(grids[0], A_NS, 'gridCol')) if grids else 0
        result.update({'row_count': len(rows), 'column_count': columns, 'rows': []})
        if len(grids) != 1 or not columns or not rows:
            self._diagnose('table_grid_missing_or_invalid')
        for row_number, row in enumerate(rows, 1):
            self.budget.consume()
            cells = self._named(row, A_NS, 'tc')
            record = {'row': row_number, 'cells': []}
            result['rows'].append(record)
            if len(cells) != columns:
                self._diagnose('table_row_width_mismatch', row=row_number, cells=len(cells), columns=columns)
            for column, cell in enumerate(cells, 1):
                self.budget.consume()
                entry = {'column': column, 'text': self._text(cell)}
                record['cells'].append(entry)
                spans = {}
                for attribute, field in (('rowSpan', 'row_span'), ('gridSpan', 'column_span')):
                    value = _unsigned(cell.get(attribute, '1'))
                    spans[field] = value
                    entry[field] = value
                    if value is None or value < 1:
                        self._diagnose('table_merge_invalid', row=row_number, column=column, attribute=attribute)
                for attribute, field in (('hMerge', 'h_merge'), ('vMerge', 'v_merge')):
                    raw = cell.get(attribute, '0')
                    entry[field] = raw in ('1', 'true')
                    if raw not in ('0', '1', 'true', 'false'):
                        self._diagnose('table_merge_invalid', row=row_number, column=column, attribute=attribute)
                if ((spans['row_span'] or 1) + row_number - 1 > len(rows)
                        or (spans['column_span'] or 1) + column - 1 > columns):
                    self._diagnose('table_merge_out_of_grid', row=row_number, column=column)
        self._check_merges(result)

    def _check_merges(self, table):
        """Check continuation topology in O(stored cells), never O(declared spans)."""
        vertical_ends = {}
        for row in table['rows']:
            horizontal_end, horizontal_row_end = 0, 0
            for cell in row['cells']:
                self.budget.consume()
                number, column = row['row'], cell['column']
                horizontal_expected = column <= horizontal_end
                vertical_expected = number <= vertical_ends.get(column, 0)
                if cell['h_merge'] != horizontal_expected or cell['v_merge'] != vertical_expected:
                    self._diagnose('table_merge_topology_inconsistent', row=number, column=column)
                if not cell['h_merge']:
                    horizontal_end = column + (cell['column_span'] or 1) - 1
                    horizontal_row_end = (vertical_ends.get(column, number) if cell['v_merge']
                                          else number + (cell['row_span'] or 1) - 1)
                if not cell['v_merge']:
                    vertical_ends[column] = (horizontal_row_end if cell['h_merge'] and horizontal_expected
                                             else number + (cell['row_span'] or 1) - 1)

    def _chart(self, reference, owner, result):
        relation = self.package.relationships.get(owner, {}).get(reference.get(_tag(R_NS, 'id'), ''))
        result.update({'data_policy': 'saved_xml_only_not_recalculated', 'series': []})
        if relation is None:
            self._diagnose('chart_relationship_missing')
            return
        expected = (R_NS + '/chart' if reference.tag == _tag(C_NS, 'chart') else
                    'http://schemas.microsoft.com/office/2014/relationships/chartEx')
        if relation.external or relation.kind != expected:
            self._diagnose('chart_relationship_unsupported')
            return
        result['source']['chart_part'] = relation.target
        root = self.package.xml.get(relation.target)
        if root is None:
            self._diagnose('chart_part_unreadable')
            return
        if reference.tag == _tag(CX_NS, 'chart'):
            self._diagnose('chart_format_unsupported', format='chartEx')
            return
        chart = self._first(root, C_NS, 'chart')
        if chart is None:
            self._diagnose('chart_content_missing')
            return
        title = self._first(chart, C_NS, 'title')
        if title is not None:
            result['title'] = self._title(title)
        plot = self._first(chart, C_NS, 'plotArea')
        if plot is None:
            self._diagnose('chart_plot_missing')
            return
        result['chart_types'] = []
        for chart_group in self._children(plot):
            local = chart_group.tag.rsplit('}', 1)[-1]
            if chart_group.tag.startswith('{' + C_NS + '}') and local in CHART_TYPES:
                self.budget.consume()
                result['chart_types'].append(local)
                self._series(chart_group, local, result['series'])
            elif local.endswith('Chart') or not chart_group.tag.startswith('{' + C_NS + '}'):
                self._diagnose('chart_type_unsupported')
        if not result['chart_types']:
            self._diagnose('chart_type_missing_or_unsupported')
        if not result['series']:
            self._diagnose('chart_series_missing')
        axes = []
        for axis in self._children(plot):
            if axis.tag not in {_tag(C_NS, kind) for kind in ('catAx', 'valAx', 'dateAx', 'serAx')}:
                continue
            title = self._first(axis, C_NS, 'title')
            units = self._first(axis, C_NS, 'dispUnits')
            number_format = self._first(axis, C_NS, 'numFmt')
            if title is not None or units is not None or number_format is not None:
                self.budget.consume()
                record = {'type': axis.tag.rsplit('}', 1)[-1]}
                if title is not None:
                    record['title'] = self._title(title)
                if number_format is not None:
                    record['number_format'] = self.budget.consume(number_format.get('formatCode', ''))
                if units is not None:
                    record['display_units'] = self._units(units)
                axes.append(record)
        if axes:
            result['axes'] = axes
        if any(item.tag == _tag(C_NS, 'extLst') and len(item) for item in self._elements(root)):
            self._diagnose('chart_extensions_not_interpreted')

    def _units(self, root):
        result = {}
        for name in ('builtInUnit', 'custUnit'):
            element = self._first(root, C_NS, name)
            if element is not None:
                result[name] = self.budget.consume(element.get('val', ''))
        return result

    def _title(self, root):
        text = self._first(root, C_NS, 'tx')
        if text is None:
            self._diagnose('chart_title_text_missing')
            return {'source': 'missing'}
        rich = self._first(text, C_NS, 'rich')
        if rich is not None:
            return {'source': 'stored_rich_text', 'text': self._text(rich)}
        return self._dataset(text, 'title')

    def _series(self, group, chart_type, result):
        for series in self._named(group, C_NS, 'ser'):
            self.budget.consume()
            record = {'order': len(result) + 1, 'chart_type': chart_type}
            result.append(record)
            for child in self._children(series):
                local = child.tag.rsplit('}', 1)[-1]
                if not child.tag.startswith('{' + C_NS + '}') or local not in DATA_ROLES:
                    continue
                role = DATA_ROLES[local]
                if role in record:
                    self._diagnose('chart_series_field_duplicate', series=record['order'], field=role)
                    continue
                record[role] = self._dataset(child, role)
            required = ('x_values', 'y_values') if chart_type in ('scatterChart', 'bubbleChart') else ('values',)
            if chart_type == 'bubbleChart':
                required += ('bubble_sizes',)
            for role in required:
                if role not in record:
                    self._diagnose('chart_series_data_missing', series=record['order'], field=role)
            if chart_type not in ('scatterChart', 'bubbleChart') and 'categories' not in record:
                self._diagnose('chart_categories_missing', series=record['order'])

    def _dataset(self, root, role):
        supported = {'strRef', 'numRef', 'strLit', 'numLit', 'multiLvlStrRef', 'v'}
        choices = [node for node in self._children(root)
                   if node.tag.startswith('{' + C_NS + '}') and node.tag.rsplit('}', 1)[-1] in supported]
        if len(choices) != 1:
            self._diagnose('chart_data_missing_or_unsupported', field=role)
            return {'source': 'missing_or_unsupported'}
        node = choices[0]
        kind = node.tag.rsplit('}', 1)[-1]
        if kind == 'v':
            return {'source': 'stored_literal', 'value': self.budget.consume(node.text or '')}
        result = {'source': 'saved_cache' if kind.endswith('Ref') else 'stored_literal',
                  'value_type': 'number' if kind.startswith('num') else 'string'}
        if kind.endswith('Ref'):
            formula = self._first(node, C_NS, 'f')
            if formula is not None:
                result['formula'] = self.budget.consume(formula.text or '')
            cache_name = {'strRef': 'strCache', 'numRef': 'numCache', 'multiLvlStrRef': 'multiLvlStrCache'}[kind]
            caches = self._named(node, C_NS, cache_name)
            if len(caches) != 1:
                result['status'] = 'missing' if not caches else 'ambiguous'
                self._diagnose('chart_cache_missing_or_ambiguous', field=role)
                return result
            node = caches[0]
        result['status'] = 'available'
        count = self._first(node, C_NS, 'ptCount')
        point_count = _unsigned(count.get('val')) if count is not None else None
        result['declared_count'] = point_count
        if point_count is None:
            self._diagnose('chart_point_count_missing_or_invalid', field=role)
        number_format = self._first(node, C_NS, 'formatCode')
        if number_format is not None:
            result['number_format'] = self.budget.consume(number_format.text or '')
        if kind == 'multiLvlStrRef':
            result['levels'] = []
            for level in self._named(node, C_NS, 'lvl'):
                self.budget.consume()
                result['levels'].append(self._points(level, point_count, role))
            if not result['levels']:
                self._diagnose('chart_cache_levels_missing', field=role)
        else:
            result['points'] = self._points(node, point_count, role)
        return result

    def _points(self, root, count, role):
        points, seen = [], set()
        for point in self._named(root, C_NS, 'pt'):
            self.budget.consume()
            index = _unsigned(point.get('idx'))
            values = self._named(point, C_NS, 'v')
            value = self.budget.consume(values[0].text or '') if len(values) == 1 else None
            points.append({'index': index, 'value': value})
            if index is None or index in seen or (count is not None and index >= count):
                self._diagnose('chart_point_index_invalid_or_duplicate', field=role)
            if len(values) != 1:
                self._diagnose('chart_point_value_missing_or_ambiguous', field=role)
            seen.add(index)
        if count is not None and len(seen - {None}) < count:
            self._diagnose('chart_cache_sparse', field=role, declared_count=count, stored_points=len(points))
        return points
