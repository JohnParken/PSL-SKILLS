"""Stored-value recovery only; synthetic inputs, not native WPS conformance."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import read_bytes, read, ParseError
from wps_biff8 import parse_stream, read_file
from test_wps_biff8 import workbook, rec, cell, formula, special
from test_wps_cfb import synthetic_cfb
from test_wps_legacy_compat import legacy_container
from test_wps_embedded_charts import chart_workbook, CHART, DRAWING


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

    def test_explicit_strict_keeps_old_metadata_rejection(self):
        data = synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'x'))})
        with self.assertRaises(ParseError):
            read_bytes(data, compatibility='strict')
        with self.assertRaises(ValueError):
            parse_stream(workbook(extra_global=rec(0xE1, b'x')))

    def test_only_evidenced_legacy_excel_guid_is_recognized(self):
        data = legacy_container({'Workbook': workbook()})
        result = read_bytes(data)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['source']['container_compatibility'], 'legacy_excel_clsid')
        with self.assertRaises(ParseError):
            read_bytes(data, compatibility='strict')
        unknown = bytearray(data); unknown[8:24] = b'?' * 16
        with self.assertRaises(ParseError):
            read_bytes(bytes(unknown))
        with self.assertRaises(ParseError):
            read_bytes(legacy_container({'Workbook': workbook()}, major=4))

    def test_default_chart_keeps_following_cells_and_uninterpreted_bytes(self):
        result = read_bytes(synthetic_cfb({'Workbook': chart_workbook()}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        self.assertEqual(result['cell_count'], 1)
        self.assertEqual(result['embedded_charts'][0]['sha256'], hashlib.sha256(CHART).hexdigest())
        self.assertFalse(result['charts_decoded'])
        with self.assertRaises(ParseError):
            read_bytes(synthetic_cfb({'Workbook': chart_workbook()}), chart_policy='reject')

    def test_unknown_records_never_turn_into_empty_or_partial_success(self):
        for stream in [workbook(extra_global=rec(0x7777, b'x')),
                       workbook([rec(0x7777, b'x')]),
                       chart_workbook(chart=CHART.replace(rec(0x1001, bytes(2)), rec(0x7777)))]:
            with self.subTest(stream=stream[:10]), self.assertRaises(ParseError):
                read_bytes(synthetic_cfb({'Workbook': stream}))

    def test_encryption_truncation_and_bad_cell_remain_failures(self):
        for stream in [workbook(extra_global=rec(0x2F, b'x')),
                       workbook() + b'x', workbook([rec(0x203, cell(0, 0) + bytes(7))]),
                       workbook([rec(0x203, cell(0, 0) + struct.pack('<d', float('nan')))])]:
            with self.subTest(length=len(stream)), self.assertRaises(ParseError):
                read_bytes(synthetic_cfb({'Workbook': stream}))

    def test_bad_chart_framing_is_not_swallowed(self):
        with self.assertRaises(ParseError):
            read_bytes(synthetic_cfb({'Workbook': chart_workbook(drawing=DRAWING[:-1])}))

    def test_external_link_and_macro_storage_still_fail(self):
        for streams in [{'Workbook': workbook(extra_global=rec(0x1AE, b'external'))},
                        {'Workbook': workbook(), 'VBA': b'not executed'}]:
            with self.assertRaises(ParseError):
                read_bytes(synthetic_cfb(streams))

    def test_duplicate_cells_and_resource_budget_still_fail(self):
        duplicate = rec(0x203, cell(0, 0) + struct.pack('<d', 2)) * 2
        with self.assertRaises(ParseError):
            read_bytes(synthetic_cfb({'Workbook': workbook([duplicate], strings=[])}))
        with patch('wps_biff8.MAX_METADATA_BYTES', 1), self.assertRaises(ParseError) as caught:
            read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'xx'))}))
        self.assertEqual(caught.exception.status, 'resource_limit')

    def test_low_and_unified_file_apis_and_cli_agree_on_partial(self):
        data = legacy_container({'Workbook': workbook(extra_global=rec(0xE1, b'x'))})
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'input.et'; path.write_bytes(data)
            for result in [read_file(path), read(path)]:
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
            for name in ['read_wps.py', 'wps_biff8.py']:
                command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name(name)), str(path)]
                process = subprocess.run(command, capture_output=True, text=True, encoding='utf8')
                self.assertEqual(process.returncode, 3, process.stderr)
                self.assertEqual(json.loads(process.stdout)['status'], 'partial')
                process = subprocess.run(command + ['--compatibility', 'strict'], capture_output=True, text=True, encoding='utf8')
                self.assertEqual(process.returncode, 1)
                self.assertNotIn('sheets', json.loads(process.stdout))
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
