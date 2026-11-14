"""Bound optional package inspection without discarding verified document text."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from read_wps import ParseError, read_bytes
import wps_binary_charts as charts
from test_wps_binary_charts import chart_fixture, chart_package, _relationships


def links(count, payload=b'opaque'):
    """Supply unique, already-validated source positions for unit-level expansion."""
    return [({'anchor_cp': index}, (payload, {'stream': 'Data', 'offset': index * 10}))
            for index in range(count)]


class ChartScanLimitTests(unittest.TestCase):
    def test_noncharts_are_counted_and_uninspected_reported(self):
        result = {'text': 'kept', 'issues': []}
        with patch.object(charts, '_read_blob', return_value=None) as reader:
            charts.add_binary_charts(result, links(66))
        self.assertEqual(reader.call_count, 64)
        self.assertEqual(result['text'], 'kept')
        self.assertEqual(result['status'], 'partial')
        summary = result['binary_charts']
        self.assertEqual(summary['chart_count'], 0)
        self.assertEqual(summary['inspection']['uninspected_candidate_count'], 2)
        self.assertEqual(summary['inspection']['uninspected_source_bounds'], [
            {'stream': 'Data', 'min_offset': 640, 'max_offset': 650, 'candidate_count': 2}])
        self.assertFalse(summary['inspection']['all_candidates_inspected'])
        self.assertFalse(summary['inspection']['source_bounds_are_contiguous'])

    def test_exact_limit_no_new_warning_or_false_chart(self):
        result = {'text': 'kept', 'issues': []}
        with patch.object(charts, '_read_blob', return_value=None) as reader:
            charts.add_binary_charts(result, links(64))
        self.assertEqual(reader.call_count, 64)
        self.assertNotIn('binary_charts', result)
        self.assertEqual(result['issues'], [])

    def test_dedup_preserves_references_and_counts_unique_skips(self):
        items = links(3)
        items += [({'anchor_cp': 4}, items[0][1]), ({'anchor_cp': 5}, items[2][1])]
        result = {'issues': []}
        with patch.object(charts, 'MAX_BLOBS', 2), patch.object(
                charts, '_read_blob', side_effect=lambda *args: {'status': 'stored_data'}) as reader:
            charts.add_binary_charts(result, items)
        self.assertEqual(reader.call_count, 2)
        summary = result['binary_charts']
        self.assertEqual(len(summary['charts'][0]['references']), 2)
        self.assertEqual(summary['chart_count'], 2)
        self.assertEqual(summary['inspection']['uninspected_candidate_count'], 1)
        self.assertEqual(summary['inspection']['uninspected_reference_count'], 2)

    def test_source_collisions_rejected_even_after_cap(self):
        for offset in (0, 20):
            items = links(3)
            items.append(({'anchor_cp': 9}, (b'changed', {'stream': 'Data', 'offset': offset})))
            with self.subTest(offset=offset), patch.object(charts, 'MAX_BLOBS', 2), patch.object(
                    charts, '_read_blob', return_value=None), self.assertRaises(ParseError) as failure:
                charts.add_binary_charts({'issues': []}, items)
            self.assertEqual(failure.exception.status, 'corrupt')

    def test_resource_errors_are_not_swallowed(self):
        result = {'issues': []}
        with patch.object(charts, 'MAX_BLOB_BYTES', 1), self.assertRaises(ParseError) as failure:
            charts.add_binary_charts(result, links(1))
        self.assertEqual(failure.exception.status, 'resource_limit')

    def test_public_read_retains_text_and_first_chart(self):
        data = chart_fixture(floating=chart_package(), inline=chart_package())
        with patch.object(charts, 'MAX_BLOBS', 1):
            result = read_bytes(data, reading_mode='maximum')
        self.assertIn('end', result['text'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['binary_charts']['chart_count'], 1)
        self.assertEqual(result['binary_charts']['inspection']['uninspected_candidate_count'], 1)
        self.assertTrue(result['binary_charts']['charts'][0]['series'])

    def test_real_nonchart_package_consumes_one_scan(self):
        data = chart_fixture(floating=chart_package(replace_parts={'_rels/.rels': _relationships()}),
                             inline=chart_package())
        with patch.object(charts, 'MAX_BLOBS', 1):
            result = read_bytes(data, reading_mode='maximum')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['binary_charts']['chart_count'], 0)
        self.assertEqual(result['binary_charts']['inspection']['scanned_candidate_count'], 1)


if __name__ == '__main__':
    unittest.main()
