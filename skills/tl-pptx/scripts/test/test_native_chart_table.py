"""Native chart/table regressions independent of upstream source checkout."""
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from svg_to_pptx.drawingml.context import ConvertContext
from svg_to_pptx.native_objects.chart_data import _chart_data
from svg_to_pptx.native_objects.chart_xml import _chart_xml
from svg_to_pptx.native_objects.table import _build_native_table

CHART_NS = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
DRAWING_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
ELEMENT = '<g xmlns="http://www.w3.org/2000/svg"/>'


class NativeChartTableTests(unittest.TestCase):
    """Exercise actual converters and failure boundaries after source splitting."""

    def test_combo_merges_adjacent_matching_plot_series(self):
        """Adjacent series with the same plot signature share one chart group."""
        payload = {'type': 'combo', 'categories': ['A', 'B'], 'series': [
            {'name': 'one', 'values': [1, 2], 'type': 'column'},
            {'name': 'two', 'values': [3, 4], 'type': 'column'},
        ]}
        data = _chart_data(payload)
        self.assertEqual(len(data['plots']), 1)
        self.assertEqual([item['name'] for item in data['plots'][0]['series']], ['one', 'two'])

    def test_column_chart_preserves_cached_values_and_labels(self):
        """The emitted native XML contains the independent input data."""
        payload = {'type': 'column', 'categories': ['North', 'South'],
                   'series': [{'name': 'Revenue', 'values': [10, 22]}]}
        data = _chart_data(payload)
        result = _chart_xml(ET.fromstring(ELEMENT), payload, chart_rels_id='rId7', chart_data=data,
                            chart_bounds=(0, 0, 5715000, 2857500))
        root = ET.fromstring(result)
        self.assertIsNotNone(root.find(f'.//{{{CHART_NS}}}barChart'))
        values = [node.text for node in root.findall(f'.//{{{CHART_NS}}}v')]
        for value in ('North', 'South', 'Revenue', '10', '22'):
            self.assertIn(value, values)
        external = root.find(f'{{{CHART_NS}}}externalData')
        self.assertEqual(external.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'), 'rId7')

    def build_table(self, rows):
        """Build a native table with explicit canonical metadata."""
        payload = {'schema': 'ppt-master.semantic-table.v2', 'rows': rows,
                   'x': 10, 'y': 20, 'width': 600, 'height': 300}
        return _build_native_table(ET.fromstring(ELEMENT), ConvertContext(), payload)

    def test_merged_table_keeps_text_and_span(self):
        """Merged-cell text and gridSpan survive native table conversion."""
        result = self.build_table([[{'text': 'Merged heading', 'col_span': 2}, ''], ['Left', 'Right']])
        wrapper = f'<root xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="{DRAWING_NS}">{result.xml}</root>'
        root = ET.fromstring(wrapper)
        cells = root.findall(f'.//{{{DRAWING_NS}}}tc')
        self.assertEqual(cells[0].get('gridSpan'), '2')
        self.assertEqual([node.text for node in root.findall(f'.//{{{DRAWING_NS}}}t') if node.text], ['Merged heading', 'Left', 'Right'])

    def test_rich_table_runs_keep_bold_and_italic(self):
        """Native text run properties are emitted, not rasterized."""
        result = self.build_table([[{'paragraphs': [{'runs': [{'text': 'Bold', 'bold': True}, {'text': 'Italic', 'italic': True}]}]}, 'Cell']])
        wrapper = f'<root xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="{DRAWING_NS}">{result.xml}</root>'
        root = ET.fromstring(wrapper)
        properties = root.findall(f'.//{{{DRAWING_NS}}}rPr')
        self.assertTrue(any(item.get('b') == '1' for item in properties))
        self.assertTrue(any(item.get('i') == '1' for item in properties))

    def test_out_of_grid_merge_is_rejected(self):
        """Invalid metadata does not silently truncate a spanning cell."""
        with self.assertRaisesRegex(RuntimeError, 'exceeds'):
            self.build_table([[{'text': 'too wide', 'col_span': 3}, ''], ['A', 'B']])


if __name__ == '__main__':
    unittest.main()
