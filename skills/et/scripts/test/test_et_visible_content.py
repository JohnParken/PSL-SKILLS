"""Synthetic semantic assertions for the model-facing content projection."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from test_wps_ooxml_sheet import package, P, R
from read_et import read_bytes
from et_content import project_content
from et_ooxml_charts import CHART_NS, DRAW_NS, TEXT_NS

C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
D = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
CACHE = '<c:strRef><c:f>Data!$A$2:$A$4</c:f><c:strCache><c:ptCount val="3"/><c:pt idx="0"><c:v>第一季</c:v></c:pt><c:pt idx="2"><c:v>第三季</c:v></c:pt></c:strCache></c:strRef>'
CHART = (f'<c:chartSpace xmlns:c="{C}" xmlns:a="{A}"><c:chart><c:title><c:tx><c:rich><a:p><a:r><a:t>收入</a:t></a:r></a:p></c:rich></c:tx></c:title>'
         f'<c:plotArea><c:barChart><c:ser><c:tx><c:v>销售额</c:v></c:tx><c:cat>{CACHE}</c:cat>'
         '<c:val><c:numRef><c:f>Data!$B$2:$B$4</c:f><c:numCache><c:ptCount val="3"/><c:pt idx="0"><c:v>1.2500</c:v></c:pt>'
         '<c:pt idx="1"><c:v>2</c:v></c:pt><c:pt idx="2"><c:v>3</c:v></c:pt></c:numCache></c:numRef></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>')


def chart_package(chart=CHART, hidden=False, external=False, referenced=True):
    external_mode = 'TargetMode="External"' if external else ''
    return package(sheet_extra='<drawing r:id="d1"/>' if referenced else '', extra_parts={
        'xl/worksheets/_rels/sheet1.xml.rels': f'<Relationships xmlns="{P}"><Relationship Id="d1" Type="{R}/drawing" Target="../drawings/drawing1.xml"/></Relationships>',
        'xl/drawings/drawing1.xml': (f'<x:wsDr xmlns:x="{D}" xmlns:c="{C}" xmlns:r="{R}"><x:twoCellAnchor><x:from><x:col>3</x:col><x:row>4</x:row></x:from>'
            f'<x:graphicFrame><x:nvGraphicFramePr><x:cNvPr id="1" name="Chart 1" hidden="{int(hidden)}"/></x:nvGraphicFramePr>'
            '<c:chart r:id="c1"/></x:graphicFrame></x:twoCellAnchor></x:wsDr>'),
        'xl/drawings/_rels/drawing1.xml.rels': (f'<Relationships xmlns="{P}"><Relationship Id="c1" Type="{R}/chart" '
            f'Target="{("https://example.invalid/chart.xml" if external else "../charts/chart1.xml")}" {external_mode}/></Relationships>'),
        'xl/charts/chart1.xml': chart,
        'xl/charts/orphan.xml': CHART.replace('收入', '不应读取孤立图表'),
    })


class VisibleContentTests(unittest.TestCase):
    def test_default_chart_saved_text_sparse_indexes_precision(self):
        chart = CHART.replace('</c:barChart>', '</c:barChart><c:catAx><c:title><c:tx><c:rich><a:p><a:r><a:t>季度</a:t></a:r></a:p></c:rich></c:tx></c:title></c:catAx>')
        value = read_bytes(chart_package(chart), profile='content')
        self.assertIn(value['status'], ('ok', 'partial'))
        charts = value['sheets'][0]['charts']
        self.assertEqual(len(charts), 1)
        self.assertEqual(charts[0]['title']['text'], '收入')
        self.assertEqual(charts[0]['anchor_cell'], 'D5')
        self.assertEqual(charts[0]['axis_titles'][0]['text'], '季度')
        self.assertEqual(charts[0]['series'][0]['name']['text'], '销售额')
        self.assertEqual(charts[0]['series'][0]['cat']['cache_status'], 'incomplete')
        self.assertEqual([x['index'] for x in charts[0]['series'][0]['cat']['points']], [0, 2])
        self.assertEqual(charts[0]['series'][0]['val']['points'][0]['value'], '1.2500')
        self.assertEqual(value['sheets'][0]['hidden_state'], 1)
        self.assertNotIn('不应读取孤立图表', json.dumps(value, ensure_ascii=False, default=str))

    def test_absent_cache_is_not_filled_from_cells(self):
        chart = CHART.replace('<c:numCache>', '<c:removed>').replace('</c:numCache>', '</c:removed>')
        value = read_bytes(chart_package(chart), profile='content')
        data = value['sheets'][0]['charts'][0]['series'][0]['val']
        self.assertEqual(data['cache_status'], 'missing')
        self.assertNotIn('points', data)
        self.assertIn('chart_cache_missing', [x['code'] for x in value['issues']])

    def test_hidden_external_and_unreferenced_are_not_read(self):
        for options in ({'hidden': True}, {'external': True}, {'referenced': False}):
            value = read_bytes(chart_package(**options), profile='content')
            self.assertEqual(value['sheets'][0]['charts'], [])
            self.assertNotIn('收入', json.dumps(value, ensure_ascii=False, default=str))

    def test_chart_dtd_and_resource_budget_remain_hard_refusals(self):
        raw = chart_package('<!DOCTYPE x [<!ENTITY e "bad">]><x>&e;</x>')
        self.assertNotIn(read_bytes(raw, profile='content')['status'], ('ok', 'partial'))
        with patch('et_ooxml_charts.MAX_POINTS', 1):
            self.assertEqual(read_bytes(chart_package(), profile='content')['status'], 'resource_limit')

    def test_broken_chart_keeps_cells_and_explicit_gap(self):
        result = read_bytes(chart_package('<bad'), profile='content')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(str(result['sheets'][0]['cells']['A1']['value']), '12.5')
        self.assertEqual(result['sheets'][0]['charts'][0]['status'], 'not_read')

    def test_invalid_number_stays_literal_and_is_not_valid_data(self):
        raw = chart_package(CHART.replace('1.2500', 'NaN'))
        value = read_bytes(raw, profile='content')
        point = value['sheets'][0]['charts'][0]['series'][0]['val']['points'][0]
        self.assertEqual(point['value'], 'NaN')
        self.assertEqual(point['numeric_status'], 'invalid_saved_number')

    def test_empty_content_records_have_package_wide_budget(self):
        raw = chart_package(CHART.replace('<c:ser>', '<c:ser/><c:ser/><c:ser>', 1))
        with patch('et_ooxml_charts.MAX_RECORDS', 2):
            self.assertEqual(read_bytes(raw, profile='content')['status'], 'resource_limit')

    def test_duplicate_content_fields_report_omissions(self):
        chart = CHART.replace('<c:val>', '<c:val/><c:val>', 1)
        value = read_bytes(chart_package(chart), profile='content')
        self.assertEqual(value['status'], 'partial')
        self.assertIn('chart_duplicate_content_field', [x['code'] for x in value['issues']])
        self.assertEqual(value['sheets'][0]['charts'][0]['series'][0]['val']['cache_status'], 'missing')

    def test_full_legacy_contract_is_unchanged(self):
        result = read_bytes(chart_package())
        self.assertEqual(result['schema_version'], 'ooxml_spreadsheet_readonly_v1')
        self.assertNotIn('charts', result['sheets'][0])
        self.assertIn('styles', result)

    def test_explicit_blanks_are_lossless_ranges_not_inferred_holes(self):
        cells = {ref: {'type': 'blank', 'value': None, 'cache_status': 'not_applicable'} for ref in
                 ('A1', 'B1', 'A2', 'B2', 'A4', 'C4')}
        cells['B4'] = {'type': 'text', 'value': ''}
        cells['A6'] = {'type': 'blank', 'value': None, 'coordinate_verified': False}
        cells['A7'] = {'type': 'blank', 'value': None, 'error': 'unreadable'}
        cells['A5'] = {'type': 'blank', 'value': None, 'formula': {}, 'cache_status': 'missing'}
        output = project_content({'status': 'partial', 'sheets': [{'name': 'x', 'cells': cells,
                                 'merges': [{'range': 'A1:B2'}]}]})['sheets'][0]
        self.assertEqual(output['stored_blank_cells'], {'count': 6, 'ranges': ['A1:B2', 'A4', 'C4']})
        self.assertEqual(set(output['cells']), {'B4', 'A5', 'A6', 'A7'})
        self.assertFalse(output['cells']['A6']['coordinate_verified'])
        self.assertEqual(output['cells']['B4']['value'], '')
        self.assertEqual(output['cells']['A5']['cache_status'], 'missing')
        self.assertEqual(output['merges'], [{'range': 'A1:B2'}])

    def test_no_parser_structure_or_repeated_assessment(self):
        raw = package(cells='<c r="A1"><f>1+1</f><v>2</v></c>')
        result = read_bytes(raw, profile='content')
        encoded = json.dumps(result, default=str)
        for token in ('reading_assessment', 'xf_index', 'sheet_id', 'source_record', 'attributes_raw', 'number_format_source', 'omitted_sheet_fields'):
            self.assertNotIn(token, encoded)
        cell = result['sheets'][0]['cells']['A1']
        self.assertEqual(cell['formula']['text'], '1+1')
        self.assertEqual(cell['cache_status'], 'unverified')
        self.assertEqual(cell['numeric_interpretation']['interpreted_value'], '2')

    def test_repeated_diagnostics_aggregate_without_losing_code_count(self):
        result = project_content({'status': 'partial', 'issues': [
            {'code': 'missing_cache', 'sheet': 'Data', 'cell': f'A{i}', 'record_offset': i}
            for i in range(100)], 'sheets': []})
        issue = result['issues'][0]
        self.assertEqual(issue['count'], 100)
        self.assertEqual(len(issue['locations']), 2)
        self.assertNotIn('record_offset', json.dumps(issue))

    def test_invalid_visual_coordinates_and_fictional_date_survive(self):
        result = read_bytes(b'<table><tr><td colspan="2">001</td></tr><tr><td>x</td><td>y</td></tr></table>', profile='content')
        self.assertFalse(result['coordinates_verified'])
        self.assertEqual(result['coordinate_basis'], 'source_order_only_not_visual_grid')
        self.assertFalse(result['sheets'][0]['cells']['A1']['coordinate_verified'])
        value = project_content({'status': 'ok', 'sheets': [{'name': 'x', 'cells': {'A1': {
            'type': 'number', 'value': '60', 'numeric_interpretation': {'kind': 'fictional_date',
            'interpreted_value': '1900-02-29', 'valid_gregorian_date': False}}}}]})
        self.assertFalse(value['sheets'][0]['cells']['A1']['numeric_interpretation']['valid_gregorian_date'])


if __name__ == '__main__':
    unittest.main()
