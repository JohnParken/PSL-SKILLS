"""Read referenced worksheet chart content, never formulas or external sources.

This is a saved-content subset, not a renderer. Orphan parts are not charts on a
sheet; only drawing relationships referenced by the worksheet are traversed.
"""
from decimal import Decimal, InvalidOperation

from wps_ooxml import (fromstring, read_part, relationships, rels_path, split_tag,
                       is_security_error)

CHART_NS = {'http://schemas.openxmlformats.org/drawingml/2006/chart',
            'http://purl.oclc.org/ooxml/drawingml/chart'}
DRAW_NS = {'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
           'http://purl.oclc.org/ooxml/drawingml/spreadsheetDrawing'}
TEXT_NS = {'http://schemas.openxmlformats.org/drawingml/2006/main',
           'http://purl.oclc.org/ooxml/drawingml/main'}
REL_NS = {'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
          'http://purl.oclc.org/ooxml/officeDocument/relationships'}
MAX_CHARTS, MAX_POINTS, MAX_TEXT = 256, 100000, 2 * 1024 * 1024
MAX_RECORDS = 100000


def _is(node, name, namespaces=CHART_NS):
    namespace, local = split_tag(node.tag)
    return local == name and namespace in namespaces


def _children(node, name):
    return [child for child in node if _is(child, name)]


def _rel_id(node):
    return next((node.get('{' + ns + '}id') for ns in REL_NS
                 if node.get('{' + ns + '}id') is not None), None)


class ChartContent:
    """One package-wide budget shared across every referenced worksheet chart."""

    def __init__(self, package, budget, report, used_parts):
        self.package, self.budget, self.report = package, budget, report
        self.used_parts = used_parts
        self.charts = self.points = self.text = self.records = 0

    def allocate(self):
        self.records += 1
        if self.records > MAX_RECORDS:
            raise ValueError('Chart content record limit exceeded')

    def check_duplicates(self, items, field, sheet):
        if len(items) > 1:
            self.report.add('chart_duplicate_content_field', sheet=sheet, field=field,
                            message='Repeated chart content field: first retained, later fields omitted')
        return items

    def value(self, value):
        value = value or ''
        self.text += len(value)
        if self.text > MAX_TEXT:
            raise ValueError('Chart text limit exceeded')
        return value

    def load(self, path, sheet):
        self.used_parts.add(path)
        try:
            return fromstring(read_part(self.package, path), budget=self.budget)
        except (ValueError, KeyError) as exc:
            if is_security_error(exc) or 'limit' in str(exc).lower():
                raise
            self.report.add('chart_content_not_read', sheet=sheet, reason=str(exc)[:120])
            return None

    def relation(self, relation, kind, sheet):
        if (not relation or relation.get('kind') != kind or relation.get('external')
                or not relation.get('resolved') or not relation.get('path')):
            self.report.add('chart_relationship_not_followed', sheet=sheet,
                            message='Missing, unsupported or external chart/drawing target')
            return None
        return relation['path']

    def cached(self, node, sheet):
        """Keep explicit point indexes: a missing point is not zero or a blank."""
        self.allocate()
        output = {}
        refs = [item for item in node.iter() if _is(item, 'f')]
        self.check_duplicates(refs, 'source_reference', sheet)
        if refs:
            output['source_reference'] = self.value(refs[0].text)
        caches = [item for item in node.iter() if split_tag(item.tag)[0] in CHART_NS
                  and split_tag(item.tag)[1] in ('strCache', 'numCache', 'strLit', 'numLit')]
        if not caches:
            output['cache_status'] = 'missing'
            self.report.add('chart_cache_missing', sheet=sheet,
                            message='Chart source is not evaluated; saved points unavailable')
            return output
        if len(caches) != 1:
            output['cache_status'] = 'unsupported'
            self.report.add('chart_cache_structure_unsupported', sheet=sheet)
            return output
        cache = caches[0]
        output['type'] = 'number' if split_tag(cache.tag)[1].startswith('num') else 'text'
        output['cache_status'] = 'unverified_saved_cache' if refs else 'saved_literal'
        output['points'] = []
        formats = self.check_duplicates(_children(cache, 'formatCode'), 'formatCode', sheet)
        if formats:
            output['format_code'] = self.value(formats[0].text)
        count_nodes = self.check_duplicates(_children(cache, 'ptCount'), 'ptCount', sheet)
        count = count_nodes[0].get('val') if count_nodes else None
        declared = int(count) if count and count.isascii() and count.isdigit() and len(count) <= 8 else None
        if declared is not None:
            output['declared_count'] = declared
        seen = set()
        for point in _children(cache, 'pt'):
            raw = point.get('idx', '')
            values = _children(point, 'v')
            if (not raw.isascii() or not raw.isdigit() or len(raw) > 8
                    or int(raw) in seen or len(values) != 1):
                output['cache_status'] = 'incomplete'
                self.report.add('chart_point_not_read', sheet=sheet)
                continue
            index = int(raw)
            seen.add(index)
            self.points += 1
            if self.points > MAX_POINTS:
                raise ValueError('Chart point limit exceeded')
            value = self.value(values[0].text)
            point_result = {'index': index, 'value': value}
            if output['type'] == 'number':
                try:
                    valid_number = len(value) <= 1024 and Decimal(value).is_finite()
                except InvalidOperation:
                    valid_number = False
                if not valid_number:
                    point_result['numeric_status'] = 'invalid_saved_number'
                    output['cache_status'] = 'incomplete'
                    self.report.add('chart_numeric_point_invalid', sheet=sheet)
            output['points'].append(point_result)
        if declared is not None and (len(seen) != declared or any(i >= declared for i in seen)):
            output['cache_status'] = 'incomplete'
            self.report.add('chart_cache_incomplete', sheet=sheet,
                            message='Saved chart points are sparse or differ from declared count')
        return output

    def label(self, node, sheet):
        self.allocate()
        runs = [item for item in node.iter() if _is(item, 't', TEXT_NS)]
        if runs:
            return {'text': self.value(''.join(item.text or '' for item in runs))}
        direct = self.check_duplicates(_children(node, 'v'), 'label_value', sheet)
        if direct:
            return {'text': self.value(direct[0].text)}
        if any(_is(item, 'strRef') for item in node.iter()):
            return self.cached(node, sheet)
        return {}

    def chart(self, path, sheet):
        self.charts += 1
        if self.charts > MAX_CHARTS:
            raise ValueError('Chart count limit exceeded')
        root = self.load(path, sheet)
        if root is None:
            return {'status': 'not_read'}
        if not _is(root, 'chartSpace'):
            self.report.add('chart_format_not_supported', sheet=sheet)
            return {'status': 'unsupported'}
        result = {'status': 'saved_content_subset', 'series': [], 'cache_freshness': 'unverified'}
        chart_nodes = _children(root, 'chart')
        if len(chart_nodes) != 1:
            self.report.add('chart_structure_not_read', sheet=sheet)
            return {'status': 'not_read'}
        chart = chart_nodes[0]
        titles = self.check_duplicates(_children(chart, 'title'), 'title', sheet)
        if titles:
            result['title'] = self.label(titles[0], sheet)
        result['types'] = []
        result['axis_titles'] = []
        for plot in _children(chart, 'plotArea'):
            for group in plot:
                self.allocate()
                ns, kind = split_tag(group.tag)
                if ns not in CHART_NS:
                    self.report.add('chart_extension_not_read', sheet=sheet)
                    continue
                if kind.endswith('Ax'):
                    for title in _children(group, 'title'):
                        result['axis_titles'].append(self.label(title, sheet))
                if not kind.endswith('Chart'):
                    continue
                result['types'].append(kind)
                for series in _children(group, 'ser'):
                    self.allocate()
                    item = {}
                    names = self.check_duplicates(_children(series, 'tx'), 'series_name', sheet)
                    if names:
                        item['name'] = self.label(names[0], sheet)
                    for key in ('cat', 'val', 'xVal', 'yVal', 'bubbleSize'):
                        data = self.check_duplicates(_children(series, key), key, sheet)
                        if data:
                            item[key] = self.cached(data[0], sheet)
                    result['series'].append(item)
        if not result['types']:
            self.report.add('chart_type_not_read', sheet=sheet)
        return result

    def worksheet(self, root, relations, sheet):
        result = []
        for node in root:
            if split_tag(node.tag)[1] != 'drawing' or split_tag(node.tag)[0] not in {
                    'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
                    'http://purl.oclc.org/ooxml/spreadsheetml/main'}:
                continue
            path = self.relation(relations.get(_rel_id(node)), 'drawing', sheet)
            if not path:
                continue
            drawing = self.load(path, sheet)
            if drawing is None:
                continue
            if not _is(drawing, 'wsDr', DRAW_NS):
                self.report.add('chart_drawing_not_read', sheet=sheet)
                continue
            self.used_parts.add(rels_path(path))
            drawing_rels = relationships(self.package, path, self.budget, report=self.report, sheet=sheet)
            for anchor in drawing:
                if split_tag(anchor.tag)[0] not in DRAW_NS or split_tag(anchor.tag)[1] not in (
                        'twoCellAnchor', 'oneCellAnchor', 'absoluteAnchor'):
                    self.report.add('chart_anchor_not_read', sheet=sheet)
                    continue
                hidden = any(_is(item, 'cNvPr', DRAW_NS) and item.get('hidden') in ('1', 'true')
                             for item in anchor.iter())
                for ref in anchor.iter():
                    if not _is(ref, 'chart'):
                        continue
                    if hidden:
                        self.report.add('hidden_chart_omitted', sheet=sheet)
                        continue
                    chart_path = self.relation(drawing_rels.get(_rel_id(ref)), 'chart', sheet)
                    if chart_path:
                        item = self.chart(chart_path, sheet)
                        item['position'] = len(result) + 1
                        starts = [n for n in anchor if _is(n, 'from', DRAW_NS)]
                        if starts:
                            coords = {split_tag(n.tag)[1]: n.text or '' for n in starts[0]
                                      if split_tag(n.tag)[0] in DRAW_NS}
                            row, col = coords.get('row', ''), coords.get('col', '')
                            if (row.isascii() and col.isascii() and row.isdigit() and col.isdigit()
                                    and len(row) <= 7 and len(col) <= 5
                                    and int(row) < 1048576 and int(col) < 16384):
                                from wps_biff8_headers import column_name
                                item['anchor_cell'] = column_name(int(col) + 1) + str(int(row) + 1)
                        result.append(item)
        return result
