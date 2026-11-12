"""Stored-value recovery only; synthetic inputs, not native WPS conformance.

One tolerant policy: known/unknown metadata is retained raw, cell values and
cache status are preserved, and hard refusals are limited to encryption, unsafe
framing/allocation and resource budgets that protect output size.
"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_wps import read_bytes, read
from wps_biff8 import parse_stream, read_file
from test_wps_biff8 import workbook, rec, cell, formula, special
from test_wps_cfb import synthetic_cfb
from test_wps_legacy_compat import legacy_container
from test_wps_embedded_charts import chart_workbook, CHART, DRAWING


def issue_codes(result):
    return [item.get('code') for item in result.get('issues', ())]


class DataPolicyTests(unittest.TestCase):
    def test_default_known_metadata_keeps_exact_values_and_locations(self):
        extra = rec(0xE1, b'raw') + rec(0xE2)
        stream = workbook(extra_global=extra)
        result = read_bytes(synthetic_cfb({'Workbook': stream}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['compatibility_mode'], 'data')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
        self.assertEqual(result['sheets'][0]['cells']['B2']['value'], Decimal.from_float(.1))
        self.assertEqual(result['sheets'][0]['cells']['C4']['value'], '缓存中文')
        self.assertEqual(result['sheets'][0]['cells']['C4']['cache_status'], 'unverified')
        self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')
        self.assertIs(result['omissions_affect_cell_values'], False)
        event = next(x for x in result['issues'] if x.get('record_id') == '0xe1')
        self.assertEqual(event['offset'], stream.index(rec(0xE1, b'raw')))
        self.assertEqual(result['reading_assessment']['outcome'], 'partial')
        self.assertFalse(result['formulas_executed'])
        self.assertFalse(result['external_links_followed'])

    def test_partial_metadata_reports_every_affected_sheet(self):
        payload = rec(0x0084, bytes(2)) + rec(0x0203, cell(0, 0) + struct.pack('<d', 42))
        result = read_bytes(synthetic_cfb({'Workbook': workbook([payload, payload], names=['One', 'Two'])}))
        event = next(item for item in result['issues'] if item.get('record_id') == '0x84')
        self.assertEqual(event['scopes'], ['One', 'Two'])
        self.assertEqual(event['records'], 2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([sheet['cells']['A1']['value'] for sheet in result['sheets']], [42, 42])

    def test_explicit_strict_is_tolerated_with_notice(self):
        data = synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'x'))})
        result = read_bytes(data, compatibility='strict')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('compatibility_option_ignored', issue_codes(result))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
        result = parse_stream(workbook(extra_global=rec(0xE1, b'x')))
        self.assertEqual(result['status'], 'partial')
        self.assertIn('non_cell_metadata_not_decoded', issue_codes(result))

    def test_legacy_excel_guid_is_a_diagnostic_not_a_strict_mode(self):
        data = legacy_container({'Workbook': workbook()})
        result = read_bytes(data)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['source']['container_compatibility'], 'data')
        strict = read_bytes(data, compatibility='strict')
        self.assertEqual(strict['status'], 'partial')
        self.assertIn('compatibility_option_ignored', issue_codes(strict))
        unknown = bytearray(data)
        unknown[8:24] = b'?' * 16
        result = read_bytes(bytes(unknown))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
        result = read_bytes(legacy_container({'Workbook': workbook()}, major=4))
        self.assertEqual(result['status'], 'partial')

    def test_default_chart_keeps_following_cells_and_uninterpreted_bytes(self):
        result = read_bytes(synthetic_cfb({'Workbook': chart_workbook()}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        self.assertEqual(result['cell_count'], 1)
        self.assertEqual(result['embedded_charts'][0]['sha256'], hashlib.sha256(CHART).hexdigest())
        self.assertFalse(result['charts_decoded'])
        rejected = parse_stream(chart_workbook(), chart_policy='reject')
        self.assertEqual(rejected['status'], 'partial')
        self.assertIn('chart_policy_option_ignored', issue_codes(rejected))
        self.assertEqual(len(rejected['embedded_charts']), 1)

    def test_unknown_records_preserve_values_and_uncertain_coverage(self):
        known_cell = rec(0x0203, cell(0, 0) + struct.pack('<d', 42))
        unknown = rec(0x7777, b'x')
        for scope, stream in (
            ('globals', workbook([known_cell], extra_global=unknown)),
            ('中文表1', workbook([unknown + known_cell])),
        ):
            with self.subTest(scope=scope):
                raw = synthetic_cfb({'Workbook': stream})
                result = read_bytes(raw)
                self.assertEqual((result['status'], result['cell_count']), ('partial', 1))
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 42)
                self.assertEqual(result['cell_value_coverage'], 'unverified_due_to_best_effort_salvage')
                self.assertTrue(result['omissions_affect_cell_values'])
                issue = next(item for item in result['issues'] if item['code'] == 'unknown_biff_record_salvaged')
                self.assertEqual((issue['scope'], issue['offset']), (scope, stream.index(unknown)))
        malformed_chart = chart_workbook(chart=CHART.replace(rec(0x1001, bytes(2)), unknown))
        result = read_bytes(synthetic_cfb({'Workbook': malformed_chart}))
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        self.assertTrue(any(code.startswith('embedded_chart') for code in issue_codes(result)))

    def test_encryption_is_hard_while_value_problems_degrade(self):
        with self.assertRaises(ValueError):
            read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0x2F, b'x'))}))
        result = read_bytes(synthetic_cfb({'Workbook': workbook() + b'x'}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
        result = read_bytes(synthetic_cfb({'Workbook': workbook([rec(0x203, cell(0, 0) + bytes(7))])}))
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['omissions_affect_cell_values'])
        result = read_bytes(synthetic_cfb({'Workbook': workbook([
            rec(0x203, cell(0, 0) + struct.pack('<d', float('nan')))
            + rec(0x203, cell(0, 1) + struct.pack('<d', 3))])}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['B1']['value'], 3)
        self.assertIn('nonfinite_number_omitted', issue_codes(result))

    def test_bad_chart_framing_keeps_later_cells(self):
        result = read_bytes(synthetic_cfb({'Workbook': chart_workbook(drawing=DRAWING[:-1])}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        self.assertIn('drawing_fragments_incomplete', issue_codes(result))

    def test_external_link_and_macro_storage_are_reported_not_followed(self):
        for streams, expected in (({'Workbook': workbook(extra_global=rec(0x1AE, b'external'))},
                                   'link_metadata_invalid'),
                                  ({'Workbook': workbook(), 'VBA': b'not executed'},
                                   'macro_storage_present_unloaded')):
            with self.subTest(expected=expected):
                result = read_bytes(synthetic_cfb(streams))
                self.assertEqual(result['status'], 'partial')
                self.assertIn(expected, issue_codes(result))
                self.assertFalse(result['external_links_followed'])
                self.assertFalse(result['formulas_executed'])
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')

    def test_duplicate_cells_degrade_and_resource_budgets_still_raise(self):
        duplicate = rec(0x203, cell(0, 0) + struct.pack('<d', 2)) * 2
        result = read_bytes(synthetic_cfb({'Workbook': workbook([duplicate], strings=[])}))
        self.assertEqual(result['status'], 'partial')
        self.assertIn('duplicate_cell_omitted', issue_codes(result))
        with patch('wps_biff8.MAX_RECORDS', 2), self.assertRaises(ValueError):
            read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'xx'))}))
        with patch('wps_biff8.MAX_METADATA_BYTES', 1):
            result = read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'xx'))}))
        self.assertEqual(result['status'], 'partial')
        self.assertIn('metadata_budget_truncated', issue_codes(result))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')

    def test_low_and_unified_file_apis_and_cli_agree_on_partial(self):
        data = legacy_container({'Workbook': workbook(extra_global=rec(0xE1, b'x'))})
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'input.et'
            path.write_bytes(data)
            for result in [read_file(path), read(path)]:
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
            for name in ['read_wps.py', 'wps_biff8.py']:
                command = [sys.executable, '-I', '-S', '-B', str((Path(__file__).resolve().parents[1] / name)), str(path)]
                process = subprocess.run(command, capture_output=True, text=True, encoding='utf8')
                self.assertEqual(process.returncode, 3, process.stderr)
                self.assertEqual(json.loads(process.stdout)['status'], 'partial')
                process = subprocess.run(command + ['--compatibility', 'strict'], capture_output=True, text=True, encoding='utf8')
                self.assertEqual(process.returncode, 3, process.stderr)
                strict = json.loads(process.stdout)
                self.assertEqual(strict['status'], 'partial')
                self.assertIn('compatibility_option_ignored', issue_codes(strict))
                self.assertEqual(strict['sheets'][0]['cells']['A1']['value'], '部门')
            self.assertEqual(path.read_bytes(), data)

    def test_missing_formula_cache_never_claims_complete_values(self):
        result = read_bytes(synthetic_cfb({'Workbook': workbook([formula(0, 0, special(0))], strings=[])}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['cell_value_coverage'], 'missing_formula_caches')
        self.assertTrue(result['omissions_affect_cell_values'])
        self.assertEqual(result['reading_assessment']['cell_values']['coverage'], 'missing_formula_caches')
        self.assertEqual(result['sheets'][0]['cells']['A1']['cache_status'], 'missing')

    def test_other_container_stream_is_reported_without_guessing_its_semantics(self):
        result = read_bytes(synthetic_cfb({'Workbook': workbook([b''], strings=[]), 'ETExtra': b'private'}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['other_container_streams_not_decoded'][0]['path'], ['ETExtra'])
        self.assertIn('other_container_streams_not_decoded', issue_codes(result))
        self.assertFalse(result['native_et_ett_parsed'])

    def test_basic_workbook_without_omissions_is_ok(self):
        result = read_bytes(synthetic_cfb({'Workbook': workbook([b''], strings=[])}))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['sheets'][0]['cells'], {})

    def test_repeated_passive_metadata_aggregates_without_new_diagnostic_limit(self):
        result = read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'x') * 1001)}))
        event = next(x for x in result['issues'] if x.get('record_id') == '0xe1')
        self.assertEqual(event['records'], 1001)
        self.assertLess(len(result['issues']), 10)
        self.assertEqual(len(result['uninterpreted_metadata']), 1001)


if __name__ == '__main__':
    unittest.main()
