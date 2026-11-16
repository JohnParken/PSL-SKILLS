"""Read linked native chart titles and stored caches using WPS's own guards."""
import re
import wps_ooxml as core
from wps_ooxml_objects import stored_objects
from wps_ooxml_related import relationship_name

C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
CHART_TYPE = 'application/vnd.openxmlformats-officedocument.drawingml.chart+xml'
MAX_CHART_REFERENCES = 128
MAX_CHART_POINTS = 50000
MAX_CHART_RECORDS = 10000
MAX_CHART_TEXT_CHARS = 2 * 1024 * 1024


def _tag(name):
    return '{' + C + '}' + name


class ChartCollector:
    """Only follow chart references, never discover orphan parts or load workbooks."""

    def __init__(self, package, budget, revisions, issues):
        self.package = package
        self.budget = budget
        self.revisions = revisions
        self.issues = issues
        self.charts = []
        self.point_count = 0
        self.record_count = 0
        self.text_chars = 0
        self.relationships = {}
        self.declarations = None

    def _record(self):
        self.record_count += 1
        if self.record_count > MAX_CHART_RECORDS:
            raise ValueError("Chart record count limit exceeded")

    def _text(self, value):
        self.text_chars += len(value or '')
        if self.text_chars > MAX_CHART_TEXT_CHARS:
            raise ValueError('Chart text limit exceeded')
        return value

    def _relations(self, part):
        if part not in self.relationships:
            name = relationship_name(part)
            relations = {}
            if name in self.package.namelist():
                root = core._word_part(self.package, name, self.budget)
                if root.tag != '{' + core.RELATIONSHIPS + '}Relationships':
                    raise ValueError('Invalid chart relationships root')
                for item in root:
                    identity = item.get('Id')
                    if item.tag != '{' + core.RELATIONSHIPS + '}Relationship' or not identity or identity in relations:
                        raise ValueError('Invalid/duplicate chart relationship ID')
                    relations[identity] = item
            self.relationships[part] = relations
        return self.relationships[part]

    def _types(self):
        if self.declarations is None:
            self.declarations = {}
            root = core.fromstring(core.read_part(self.package, '[Content_Types].xml'), budget=self.budget)
            for item in root:
                if item.tag == '{' + core.CONTENT_TYPES + '}Override':
                    name = item.get('PartName')
                    if name in self.declarations:
                        raise ValueError('Duplicate part content-type declaration')
                    self.declarations[name] = item.get('ContentType')
        return self.declarations

    def _cache(self, parent, issues):
        if parent is None:
            return None
        caches = [node for node in parent.iter() if node.tag in {
            _tag('strCache'), _tag('numCache'), _tag('strLit'), _tag('numLit')}]
        output = []
        for cache in caches:
            self._record()
            points = []
            indices = set()
            for point in cache.findall(_tag('pt')):
                self.point_count += 1
                if self.point_count > MAX_CHART_POINTS:
                    raise ValueError('Chart point count limit exceeded')
                index = point.get('idx')
                values = point.findall(_tag('v'))
                valid_index = index is not None and re.fullmatch(r'[0-9]{1,10}', index) is not None
                if not valid_index or int(index) in indices or len(values) != 1:
                    issues.append('invalid_or_duplicate_cached_point')
                if len(values) != 1 or values[0].text is None:
                    issues.append('cached_value_missing')
                if valid_index:
                    indices.add(int(index))
                points.append({'index': index, 'raw_value': self._text(values[0].text) if len(values) == 1 else None})
            counts = cache.findall(_tag('ptCount'))
            declared = counts[0].get('val') if len(counts) == 1 else None
            complete = (declared is not None and re.fullmatch(r'[0-9]{1,10}', declared) is not None
                        and len(points) == len(indices) == int(declared)
                        and (not indices or (min(indices) == 0 and max(indices) == len(indices) - 1)))
            if not complete:
                issues.append('cached_point_coverage_incomplete_or_unknown')
            missing_ranges = []
            if declared is not None and re.fullmatch(r'[0-9]{1,10}', declared):
                cursor = 0
                for index in sorted(value for value in indices if value < int(declared)):
                    if index > cursor:
                        missing_ranges.append({'start': cursor, 'end': index - 1})
                    cursor = index + 1
                if cursor < int(declared):
                    missing_ranges.append({'start': cursor, 'end': int(declared) - 1})
            format_node = cache.find(_tag('formatCode'))
            output.append({'kind': cache.tag.rsplit('}', 1)[-1], 'declared_count': declared,
                           'format_code': self._text(format_node.text) if format_node is not None else None,
                           'points': points, 'coverage_complete': complete, 'missing_index_ranges': missing_ranges})
        if not output and not any(node.tag in {_tag('v'), '{' + A + '}t'} for node in parent.iter()):
            issues.append('stored_cache_missing_or_unsupported')
        if any(node.tag == _tag('multiLvlStrRef') for node in parent.iter()):
            issues.append('multilevel_categories_not_decoded')
        return {'caches': output, 'literal_values': [self._text(node.text) for node in parent.findall(_tag('v'))],
                'rich_text': [self._text(node.text) for node in parent.iter('{' + A + '}t')],
                'formulas': [self._text(node.text) for node in parent.iter(_tag('f'))]}

    def _decode(self, root, record):
        issues = record['limitations']
        charts = root.findall(_tag('chart'))
        if len(charts) != 1:
            issues.append('missing_or_ambiguous_chart_element')
            return
        chart = charts[0]
        if chart is None:
            issues.append('chart_element_missing')
            return
        titles = chart.findall(_tag('title'))
        if len(titles) > 1:
            issues.append('duplicate_chart_title_only_first_returned')
        title = titles[0] if titles else None
        record['title'] = self._cache(title, issues)
        record['series'] = []
        for series in chart.iter(_tag('ser')):
            self._record()
            if any(len(series.findall(_tag(name))) > 1 for name in ('tx', 'cat', 'val', 'xVal', 'yVal', 'bubbleSize')):
                issues.append('duplicate_series_field_only_first_returned')
            if series.find(_tag('val')) is None and series.find(_tag('yVal')) is None:
                issues.append('series_values_missing_or_unsupported')
            record['series'].append({
                name: self._cache(series.find(_tag(tag)), issues)
                for name, tag in (('name', 'tx'), ('categories', 'cat'), ('values', 'val'),
                                  ('x_values', 'xVal'), ('y_values', 'yVal'), ('bubble_sizes', 'bubbleSize'))})
        if not record['series']:
            issues.append('series_missing_or_unsupported')
        if any(node.tag == _tag('externalData') for node in root.iter()):
            issues.append('external_or_embedded_workbook_not_read')
        issues.extend(('stored_values_not_refreshed', 'visual_formatting_and_layout_not_verified'))

    def collect(self, root, source_part):
        """Return stored native chart data; bad local XML can be partial, unsafe XML cannot."""
        for index, (reference, branches) in enumerate(stored_objects(root, _tag('chart'), self.revisions)):
            if len(self.charts) >= MAX_CHART_REFERENCES:
                raise ValueError('Chart reference count limit exceeded')
            identity = reference.get('{' + core.R + '}id')
            relation = self._relations(source_part).get(identity)
            record = {'source': {'part': source_part, 'element_index': index, 'relationship_id': identity},
                      'status': 'partial', 'title': None, 'series': [], 'limitations': [],
                      'value_source': 'stored_chart_xml_only', 'current_values_verified': False}
            if branches:
                record['source']['alternate_content'] = branches
                record['limitations'].append('alternate_content_branch_unresolved')
            if relation is None or relation.get('Type') != core.R + '/chart':
                record['limitations'].append('missing_or_wrong_chart_relationship')
            else:
                if relation.get('TargetMode', 'Internal') != 'Internal':
                    raise ValueError('unsupported external chart relationship')
                part = core.resolve_internal_target(relation.get('Target'), source_part)
                record['source']['chart_part'] = part
                if part not in self.package.namelist() or self._types().get('/' + part) != CHART_TYPE:
                    record['limitations'].append('missing_chart_part_or_content_type')
                else:
                    try:
                        chart_root = core.fromstring(core.read_part(self.package, part), budget=self.budget)
                        if chart_root.tag != _tag('chartSpace'):
                            record['limitations'].append('unsupported_chart_root_or_dialect')
                        else:
                            self._decode(chart_root, record)
                    except ValueError as exc:
                        if not str(exc).startswith('Invalid XML:'):
                            raise
                        record['limitations'].append('invalid_chart_xml')
            record['limitations'] = sorted(set(record['limitations']))
            self.charts.append(record)
            self.issues.append({'stream': source_part, 'location': identity, 'offset': None,
                                'code': 'chart_stored_content_partial',
                                'message': 'Native chart stored content returned separately; inspect chart limitations'})
