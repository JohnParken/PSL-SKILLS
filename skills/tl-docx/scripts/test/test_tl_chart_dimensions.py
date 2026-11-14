"""Saved chart dimensions, sparse hierarchies and report projection regressions."""

from pathlib import Path
import sys
import unittest
from xml.sax.saxutils import escape

from docx.oxml import parse_xml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_read_charts import read_chart
from tl_report_objects import locate_series


NAMESPACE = 'http://schemas.openxmlformats.org/drawingml/2006/chart'


def source(role, points, *, formula=None, count=None):
    """Build an independently expected numeric cache with precise saved strings."""
    values = ''.join(f'<c:pt idx="{index}"><c:v>{escape(value)}</c:v></c:pt>' for index, value in points)
    count = len(points) if count is None else count
    cache = f'<c:formatCode>0.000</c:formatCode><c:ptCount val="{count}"/>{values}'
    content = f'<c:numLit>{cache}</c:numLit>' if formula is None else f'<c:numRef><c:f>{escape(formula)}</c:f><c:numCache>{cache}</c:numCache></c:numRef>'
    return f'<c:{role}>{content}</c:{role}>'


def chart(kind, content):
    """Create only chart XML; no workbook or network source is available."""
    xml = f'<c:chartSpace xmlns:c="{NAMESPACE}"><c:chart><c:plotArea><c:{kind}><c:ser><c:idx val="0"/>{content}</c:ser></c:{kind}></c:plotArea></c:chart></c:chartSpace>'
    return read_chart(parse_xml(xml.encode()))


class ChartDimensionsTests(unittest.TestCase):
    """Check fidelity of newly supported dimensions and malformed partial data."""

    def test_scatter_preserves_x_y_and_compatibility(self):
        result = chart('scatterChart', source('xVal', [(0, '001.20'), (1, '2e-9')], formula='Sheet1!A1:A2')
                       + source('yVal', [(0, '-0.000'), (1, '9007199254740993')], formula='Sheet1!B1:B2'))
        series = result['series'][0]
        self.assertEqual(series['issues'], [])
        self.assertEqual(series['x_formula'], 'Sheet1!A1:A2')
        self.assertEqual(series['points'][0], {'index': 0, 'category': '001.20', 'value': '-0.000', 'x': '001.20', 'y': '-0.000'})
        self.assertEqual(series['points'][1]['y'], '9007199254740993')
        self.assertEqual(series['y_source'], 'cache')

    def test_bubble_keeps_size_only_indices_and_missing_y(self):
        series = chart('bubbleChart', source('xVal', [(0, '1'), (1, '2')])
                       + source('yVal', [(0, '3')]) + source('bubbleSize', [(0, '0.10'), (2, '5')], count=3))['series'][0]
        self.assertEqual([point['index'] for point in series['points']], [0, 1, 2])
        self.assertEqual(series['points'][2]['bubble_size'], '5')
        self.assertIsNone(series['points'][2]['y'])
        self.assertIn('chart_bubble_value_indices_differ', series['issues'])
        self.assertIn('chart_category_value_indices_differ', series['issues'])

    def test_multilevel_preserves_sparse_levels_without_fill(self):
        categories = ('<c:cat><c:multiLvlStrRef><c:f>Sheet1!A1:B2</c:f><c:multiLvlStrCache><c:ptCount val="2"/>'
                      '<c:lvl><c:pt idx="0"><c:v>Jan</c:v></c:pt><c:pt idx="1"><c:v>Feb</c:v></c:pt></c:lvl>'
                      '<c:lvl><c:pt idx="0"><c:v>2026</c:v></c:pt></c:lvl>'
                      '</c:multiLvlStrCache></c:multiLvlStrRef></c:cat>')
        series = chart('barChart', categories + source('val', [(0, '1'), (1, '2')]))['series'][0]
        self.assertEqual(series['issues'], [])
        self.assertEqual(series['points'][1]['category'], 'Feb')
        self.assertEqual(series['points'][1]['category_levels'], ['Feb', None])
        self.assertEqual(series['category_levels'][1], {'level_index': 1, 'points': [{'index': 0, 'value': '2026'}]})

    def test_invalid_indices_do_not_erase_other_values(self):
        series = chart('scatterChart', source('xVal', [(-1, 'bad'), (0, 'good'), (0, 'duplicate'), (4294967296, 'bad')], count=1)
                       + source('yVal', [(0, '3')]))['series'][0]
        self.assertEqual(series['points'][0]['x'], 'good')
        self.assertEqual(len(series['points']), 1)
        self.assertIn('category_point_index_invalid', series['issues'])
        self.assertIn('category_point_index_duplicate', series['issues'])

    def test_missing_cache_is_partial_not_evaluated(self):
        series = chart('scatterChart', '<c:xVal><c:numRef><c:f>https://example.test/file</c:f></c:numRef></c:xVal>'
                       + source('yVal', [(0, '2')]))['series'][0]
        self.assertEqual(series['x_formula'], 'https://example.test/file')
        self.assertIn('category_cache_missing', series['issues'])
        self.assertEqual(series['points'][0]['y'], '2')
        self.assertIsNone(series['points'][0]['x'])

    def test_report_locates_all_three_dimensions(self):
        series = chart('bubbleChart', source('xVal', [(0, '1')], formula='Data!A1')
                       + source('yVal', [(0, '2')], formula='Data!B1')
                       + source('bubbleSize', [(0, '3')], formula='Data!C1'))['series'][0]
        locate_series(series, series['points'])
        self.assertEqual(series['points'][0]['x_cell'], 'A1')
        self.assertEqual(series['points'][0]['y_cell'], 'B1')
        self.assertEqual(series['points'][0]['bubble_size_cell'], 'C1')
        self.assertNotIn('bubble_size_formula', series)
        self.assertEqual(series['bubble_size_reference']['sheet'], 'Data')

    def test_declared_count_never_allocates_missing_points(self):
        series = chart('scatterChart', source('xVal', [(0, '1')], count=4294967295)
                       + source('yVal', [(0, '2')]))['series'][0]
        self.assertEqual(len(series['points']), 1)
        self.assertIn('category_point_count_mismatch', series['issues'])


if __name__ == '__main__':
    unittest.main()
