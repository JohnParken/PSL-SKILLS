"""Synthetic chart presentation checks; no private document fixtures."""
import copy
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wps_binary_chart_cache import ChartCacheBudget, ChartCacheLimit
from wps_chart_presentation import add_presentation, display_number


def channel(points, status='complete', **extra):
    return {'points': [{'index': index, 'value': value} for index, value in points],
            'status': status, 'missing_ranges': [], **extra}


def chart(categories, values):
    return {'status': 'partial', 'workbook_status': 'external_not_followed',
            'series': [{'categories': categories, 'values': values, 'index': 0,
                        'order': 0, 'name': None, 'chart_type': 'pieChart'}]}


class ChartPresentationTests(unittest.TestCase):
    def test_percent_precision_and_raw_retained(self):
        value = '0.12345678901234567890123456789'
        data = chart(channel([(0, 'Category')]), channel([(0, value)], format_code='0.0%'))
        before = copy.deepcopy(data)
        add_presentation(data, ChartCacheBudget())
        row = data['presentation']['series'][0]['rows'][0]
        self.assertEqual(row['raw_value'], value)
        self.assertEqual(row['display_value'], '12.3%')
        self.assertEqual(data['series'], before['series'])
        self.assertEqual(data['status'], 'partial')

    def test_missing_values_do_not_hide_categories(self):
        data = chart(channel([(0, 'A'), (1, 'B')]), channel([], 'unavailable'))
        add_presentation(data, ChartCacheBudget())
        view = data['presentation']['series'][0]
        self.assertEqual(view['missing_fields'], ['values'])
        self.assertEqual([r['category'] for r in view['rows']], ['A', 'B'])
        self.assertTrue(all(r['display_value'] is None for r in view['rows']))

    def test_sparse_index_alignment_and_duplicates(self):
        data = chart(channel([(2, 'B'), (0, 'A')]), channel([(2, '4'), (1, '9'), (1, '8')]))
        add_presentation(data, ChartCacheBudget())
        rows = data['presentation']['series'][0]['rows']
        self.assertEqual([(r['index'], r['raw_value']) for r in rows], [(0, None), (1, None), (2, '4')])
        self.assertEqual(rows[2]['category'], 'B')

    def test_no_expansion_of_missing_ranges(self):
        values = channel([], 'partial', declared_count=4294967295)
        values['missing_ranges'] = [[0, 4294967295]]
        data = chart(channel([]), values)
        add_presentation(data, ChartCacheBudget())
        view = data['presentation']['series'][0]
        self.assertEqual(view['rows'], [])
        self.assertEqual(view['missing_value_ranges'], [[0, 4294967295]])

    def test_bounded_format_subset(self):
        self.assertEqual(display_number('0.125', '0.0%'), ('12.5%', 'simple_number_format'))
        self.assertEqual(display_number('-1.25', '0.0'), ('-1.3', 'simple_number_format'))
        for raw, fmt in [('1e99999999', '0'), ('NaN', '0'), ('2', '#,##0'), ('2', '[Red]0')]:
            self.assertEqual(display_number(raw, fmt), (raw, 'format_not_rendered'))
        self.assertEqual(display_number('1.234', 'General'), ('1.234', 'raw'))

    def test_shared_output_budget(self):
        budget = ChartCacheBudget()
        budget.MAX_TEXT_BYTES = 1
        with self.assertRaises(ChartCacheLimit):
            add_presentation(chart(channel([(0, 'long')]), channel([(0, '1')])), budget)

    def test_source_not_executed_or_formula_exposed(self):
        data = chart(channel([(0, 'A')]), channel([(0, '1')], formula='file:///private.xlsx'))
        add_presentation(data, ChartCacheBudget())
        self.assertNotIn('private.xlsx', str(data['presentation']))
        self.assertFalse(data['presentation']['external_source_followed'])
        self.assertFalse(data['presentation']['series'][0]['values_refreshed'])


if __name__ == '__main__':
    unittest.main()
