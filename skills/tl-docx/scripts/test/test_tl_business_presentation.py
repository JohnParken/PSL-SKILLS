"""Synthetic-only opt-in table confidence and chart display regressions."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_read_tables import header_metadata
from tl_report_objects import describe_series, display_value, locate_series


class BusinessPresentationTests(unittest.TestCase):
    """Check additive metadata without rewriting source values."""

    def setUp(self):
        self.table = {'table_index': 1, 'column_count': 1, 'repeat_header_rows': []}
        self.rows = [{'row_index': index, 'cells': [{'column': 1, 'colspan': 1, 'text': text}]}
                     for index, text in ((1, 'Label\nSecond line'), (2, 'Value'))]

    def test_candidate_is_not_confirmation(self):
        """First row remains candidate and its text is not flattened."""
        header = header_metadata(self.table, self.rows, None, True)
        self.assertEqual(header['status'], 'candidate')
        self.assertIsNone(header['confirmation_basis'])
        self.assertIn('header_candidate_unconfirmed', header['diagnostics'])
        self.assertEqual(header['columns'][0]['labels'], ['Label\nSecond line'])

    def test_explicit_zero_and_document_marker(self):
        """Explicit no-header remains a confirmed choice."""
        header = header_metadata(self.table, self.rows, 0, True)
        self.assertEqual((header['status'], header['confirmation_basis'], header['rows']), ('confirmed', 'explicit', []))
        self.table['repeat_header_rows'] = [1]
        header = header_metadata(self.table, self.rows, None, True)
        self.assertEqual(header['confirmation_basis'], 'document_repeat_marker')
        header = header_metadata(self.table, self.rows, None, False)
        self.assertEqual(header['mapping'], 'unavailable')
        self.assertEqual(header['status'], 'confirmed')
        self.assertIn('header_geometry_unavailable', header['diagnostics'])

    def test_percent_exact_and_raw_retained(self):
        """Decimal formatting preserves original precision."""
        item = {'chart_type': 'barChart', 'value_source': 'cache', 'format_code': '0.00%', 'issues': []}
        points = [{'index': 0, 'category': 'A', 'value': '0.12345'}]
        original = deepcopy(points)
        describe_series(item, points)
        self.assertEqual(points[0]['value'], original[0]['value'])
        self.assertEqual(points[0]['display']['value']['text'], '12.35%')
        self.assertEqual(item['cache_summary']['status'], 'available')
        self.assertEqual(display_value('-0.12345', '0.00%')['text'], '-12.35%')

    def test_complex_formats_and_hostile_numbers(self):
        """Unsupported formats and extreme exponents remain raw."""
        for code in ('yyyy-mm-dd', '#,##0.00', '[Red]0%', '0.00%;-0.00%', '0.0000000%'):
            with self.subTest(code=code):
                self.assertEqual(display_value('0.125', code)['status'], 'unsupported_format')
                self.assertEqual(display_value('0.125', code)['text'], '0.125')
        self.assertEqual(display_value('1e9999', '0%')['status'], 'format_limit')
        self.assertEqual(display_value('NaN', '0%')['status'], 'invalid_numeric')
        self.assertEqual(display_value(None, '0%')['status'], 'missing')
        self.assertEqual(display_value('0001.2000', None)['text'], '0001.2000')

    def test_missing_dimension_reports_partial(self):
        """Bubble series does not appear complete because x exists."""
        item = {'chart_type': 'bubbleChart', 'x_source': 'literal', 'y_source': 'cache',
                'bubble_size_source': 'unavailable', 'issues': ['bubble_size_cache_missing']}
        points = [{'index': 0, 'x': '1', 'y': '0.25', 'bubble_size': None}]
        describe_series(item, points)
        self.assertEqual(item['cache_summary']['status'], 'partial')
        self.assertEqual(item['cache_summary']['dimensions']['bubble_size']['missing_in_observed_points'], 1)
        self.assertEqual(points[0]['display']['bubble_size']['status'], 'missing')

    def test_empty_series_is_unavailable(self):
        """No rows or zero values are invented for absent caches."""
        item = {'chart_type': 'barChart', 'issues': ['value_cache_missing']}
        describe_series(item, [])
        self.assertEqual(item['cache_summary']['status'], 'unavailable')

    def test_existing_locate_entry(self):
        """Existing enhanced report entry receives additive metadata."""
        item = {'chart_type': 'barChart', 'format_code': '0%', 'value_source': 'cache', 'value_formula': 'Sheet1!B1', 'issues': []}
        points = [{'index': 0, 'category': 'A', 'value': '0.25'}]
        locate_series(item, points)
        self.assertEqual(points[0]['value_cell'], 'B1')
        self.assertEqual(points[0]['display']['value']['text'], '25%')
        self.assertEqual(item['cache_summary']['point_scope'], 'observed_indices_only')


if __name__ == '__main__':
    unittest.main()
