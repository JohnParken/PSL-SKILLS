"""Default model-facing content keeps values without exposing parser scaffolding."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from docx.oxml import parse_xml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tl_docx import arguments, read_result, encode
from tl_read_content import content_records, content_coverage
from tl_read_charts import read_chart
from test_tl_read_content import record
from test_tl_read import report_fixture


class VisibleContentTests(unittest.TestCase):
    """Compare independent visible expectations, omissions and explicit details."""
    def test_default_metadata_is_actionable_not_an_option_dump(self):
        """Hide checksums/default options but retain explicit content selectors."""
        package = SimpleNamespace(main='word/document.xml', sha256='first-source')
        raw = ([record('paragraph', '/p', text='Visible text')], [], {'status': 'ok'})
        plain = read_result(package, arguments(['read', 'sample.docx']), raw_report=raw)
        self.assertNotIn('sha256', plain)
        self.assertNotIn('options', plain)
        self.assertNotIn('cache', plain)
        self.assertEqual(1, plain['api_version'])
        detailed = read_result(package, arguments(['read', 'sample.docx', '--details']), raw_report=raw)
        self.assertEqual('first-source', detailed['sha256'])
        self.assertIn('format', detailed['options'])
        selected = read_result(package, arguments(['read', 'sample.docx', '--body-only', '--revisions', 'original']), raw_report=raw)
        self.assertEqual({'include_related': False, 'revisions': 'original'}, selected['options'])

    def test_hidden_checksum_does_not_weaken_cursor_binding(self):
        """Changing source identity still rejects the otherwise identical cursor."""
        package = SimpleNamespace(main='word/document.xml', sha256='first-source')
        raw = ([record('paragraph', '/p[' + str(i) + ']', text='Text ' * 30) for i in range(25)], [], {'status': 'ok'})
        args = arguments(['read', 'sample.docx', '--max-output-bytes', '3000'])
        first = read_result(package, args, raw_report=raw)
        self.assertTrue(first['has_more'])
        self.assertNotIn('sha256', first)
        args.cursor = first['next_cursor']
        package.sha256 = 'different-source'
        with self.assertRaisesRegex(ValueError, 'another document or view'):
            read_result(package, args, raw_report=raw)

    def test_internal_fields_and_visible_field_result(self):
        """Keep visible results and unknown text without exposing field commands."""
        records = [record('paragraph', '/p', text='Result 42',
                          fields=[{'instruction': 'DDE secret command', 'text': '42', 'path': '/p/f'}],
                          numbering={'num_id': '8', 'text': '%1.', 'label': '2.', 'level': '0'}),
                   record('metadata', '/meta', values={'creator': 'not visible'}),
                   record('chart_point', '/point', value='9007199254740993', category='001',
                          value_cell='B2', display={'value': {'text': '9007199254740993', 'status': 'raw'}}),
                   record('image', '/image', target='word/media/image1.png', description='Alt text'),
                   record('future_object', '/future', text='Unknown visible content')]
        result = content_records(records)
        self.assertNotIn('fields', result[0])
        self.assertEqual({'label': '2.', 'level': '0'}, result[0]['numbering'])
        self.assertEqual('9007199254740993', result[1]['value'])
        self.assertEqual('001', result[1]['category'])
        self.assertNotIn('value_cell', result[1])
        self.assertNotIn('target', result[2])
        self.assertEqual('Alt text', result[2]['description'])
        self.assertEqual('Unknown visible content', result[-1]['text'])
        self.assertIn('DDE secret command', str(content_records(records, details=True)))
        self.assertNotIn('DDE secret command', str(result))
        self.assertNotIn('not visible', str(result))

    def test_table_header_certainty_merge_and_prose(self):
        """Retain table semantics while omitting duplicate label maps."""
        rows = [record('table', '/tbl', table_index=1,
                       header={'status': 'candidate', 'rows': [1], 'columns': [{'labels': ['Heading']}],
                               'spans': [{'text': 'Heading'}], 'diagnostics': ['header_candidate_unconfirmed']}),
                record('table_row', '/tbl/tr', table_index=1, row_index=1,
                       cells=[{'text': 'Heading', 'column': 1, 'colspan': 2, 'row_span': 2,
                               'origin': {'row': 1, 'column': 1}, 'path': '/tbl/tr/tc'}])]
        result = content_records(rows)
        self.assertEqual('candidate', result[0]['header']['status'])
        self.assertNotIn('columns', result[0]['header'])
        self.assertEqual('Heading', result[1]['cells'][0]['text'])
        self.assertEqual(2, result[1]['cells'][0]['colspan'])
        self.assertEqual({'row': 1, 'column': 1}, result[1]['cells'][0]['origin'])
        self.assertNotIn('path', result[1]['cells'][0])

    def test_full_source_and_content_chart_points(self):
        """Default and full views agree on actual stored point strings."""
        package = report_fixture()
        package.sha256 = 'synthetic-chart-fixture'
        default = read_result(package, arguments(['read', 'sample.docx']))
        detailed = read_result(package, arguments(['read', 'sample.docx', '--details']))
        full = read_result(package, arguments(['read', 'sample.docx', '--full']))
        points = [item for item in default['records'] if item['type'] == 'chart_point']
        expected = [point for item in full['records'] if item['type'] == 'chart_series' for point in item['points']]
        self.assertEqual([(p['index'], p['category'], p['value']) for p in expected],
                         [(p['index'], p['category'], p['value']) for p in points])
        self.assertTrue(points)
        self.assertLess(len(encode(default)), len(encode(detailed)))
        self.assertNotIn('main_part', default['coverage'])
        self.assertIn('main_part', detailed['coverage'])
        self.assertEqual(default['status'], detailed['status'])
        self.assertEqual(sum(issue['count'] for issue in default['issues']), len(detailed['issues']))

    def test_axis_and_custom_labels_are_stored_not_invented(self):
        """Read explicit rich/cache labels, excluding explicitly deleted labels."""
        xml = b'''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
          xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><c:chart><c:plotArea>
          <c:barChart><c:ser><c:idx val="0"/><c:dLbls><c:dLbl><c:idx val="2"/>
          <c:tx><c:rich><a:p><a:r><a:t>Custom</a:t></a:r><a:r><a:t> label</a:t></a:r></a:p>
          <a:p><a:r><a:t>second line</a:t></a:r></a:p></c:rich></c:tx></c:dLbl>
          <c:dLbl><c:idx val="3"/><c:delete val="1"/><c:tx><c:v>Hidden</c:v></c:tx></c:dLbl>
          </c:dLbls></c:ser></c:barChart>
          <c:valAx><c:title><c:tx><c:strRef><c:f>Sheet1!A1</c:f><c:strCache><c:pt idx="0">
          <c:v>Revenue</c:v></c:pt></c:strCache></c:strRef></c:tx></c:title></c:valAx>
          </c:plotArea></c:chart></c:chartSpace>'''
        result = read_chart(parse_xml(xml))
        self.assertEqual([{'kind': 'data_label', 'series_index': 1, 'point_index': 2,
                           'text': 'Custom label\nsecond line'},
                          {'kind': 'axis_title', 'axis_type': 'valAx', 'text': 'Revenue'}], result['annotations'])
        self.assertIn('value_source_missing', result['series'][0]['issues'])
        self.assertEqual([], result['series'][0]['points'])

    def test_coverage_keeps_missing_scope_and_unknown_fields(self):
        """Coverage retains unavailable stories, partial scope and new fields."""
        source = {'main_part': 'word/document.xml', 'records_by_type': {'paragraph': 1},
                  'status': 'partial', 'scope': 'section', 'document_complete': False,
                  'availability': {'main': 'available', 'headers': 'unreadable', 'styles': 'available'},
                  'future_coverage': {'unknown': True}}
        result = content_coverage(source)
        self.assertEqual('unreadable', result['availability']['headers'])
        self.assertFalse(result['document_complete'])
        self.assertEqual({'unknown': True}, result['future_coverage'])


if __name__ == '__main__':
    unittest.main()
