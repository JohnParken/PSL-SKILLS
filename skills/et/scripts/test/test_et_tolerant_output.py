#!/usr/bin/env python3
"""Tolerant output options: bounded JSON, compact delivery and explicit file output."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
from test_wps_biff8 import workbook
from test_wps_cfb import synthetic_cfb
from wps_result import (MAX_JSON_OUTPUT_BYTES, compact_result, encode_result,
                        write_result_file)
from wps_access import ParseError


class TolerantOutputTests(unittest.TestCase):
    def sample_result(self):
        cell = {'type': 'number', 'value': Decimal('1.5'), 'raw_numeric_hex': '3ff8000000000000',
                'record_offset': 12, 'record_id': '0x203', 'xf_index': 0,
                'number_format_id_raw': 0, 'custom_format_raw': None,
                'numeric_interpretation': {'status': 'interpreted', 'kind': 'number',
                                           'interpreted_value': '1.5', 'format_id': 0,
                                           'format_code': 'General', 'date_system': '1900',
                                           'basis': 'stored_number_and_cell_xf_fAtrNum',
                                           'confidence': 'limited_format_subset',
                                           'source_value_unchanged': True,
                                           'not_excel_rendering': True},
                'cache_status': 'not_applicable'}
        return {'status': 'partial', 'schema_version': 'biff8_readonly_v1',
                'sheets': [{'name': 'Data', 'cells': {'A' + str(index + 1): dict(cell) for index in range(30)}}],
                'uninterpreted_metadata': [
                    {'scope': 'globals', 'offset': 10, 'record_id': '0x60', 'name': 'Template',
                     'payload_hex': '00' * 20},
                    {'scope': 'globals', 'offset': 40, 'record_id': '0x60', 'name': 'Template',
                     'payload_hex': '00' * 20}],
                'issues': [{'code': 'non_cell_metadata_not_decoded', 'records': 2}]}

    def test_compact_keeps_values_and_reduces_provenance(self):
        source = self.sample_result()
        original = deepcopy(source)
        compacted = compact_result(source)
        cell = compacted['sheets'][0]['cells']['A1']
        self.assertEqual(cell['value'], Decimal('1.5'))
        self.assertNotIn('raw_numeric_hex', cell)
        self.assertNotIn('record_offset', cell)
        self.assertEqual(cell['numeric_interpretation']['interpreted_value'], '1.5')
        self.assertEqual(compacted['uninterpreted_metadata'],
                         [{'scope': 'globals', 'record_id': '0x60', 'name': 'Template', 'count': 2}])
        self.assertTrue(compacted['uninterpreted_metadata_compacted'])
        self.assertLess(len(encode_result(compacted)), len(encode_result(original)))

    def test_encode_result_never_returns_partial_json(self):
        result = self.sample_result()
        with self.assertRaises(ParseError) as caught:
            encode_result(result, limit=64)
        self.assertEqual(caught.exception.status, 'resource_limit')
        encoded = encode_result(result, limit=MAX_JSON_OUTPUT_BYTES)
        self.assertEqual(json.loads(encoded)['sheets'][0]['cells']['A1']['value'], '1.5')

    def test_write_result_file_is_exclusive_and_bare_name_only(self):
        result = self.sample_result()
        with tempfile.TemporaryDirectory() as task:
            old = Path.cwd()
            try:
                import os
                os.chdir(task)
                manifest = write_result_file('full.json', result)
                written = json.loads(Path(task, 'full.json').read_text(encoding='utf-8'))
                self.assertEqual(written['status'], 'partial')
                self.assertEqual(manifest['bytes'], (Path(task, 'full.json')).stat().st_size)
                with self.assertRaises(ParseError):
                    write_result_file('full.json', result)
                for name in ('../escape.json', 'dir/full.json', '', '.', '..'):
                    with self.subTest(name=name), self.assertRaises(ParseError):
                        write_result_file(name, result)
            finally:
                os.chdir(old)

    def test_cli_output_manifest_and_compact_delivery(self):
        raw = synthetic_cfb({'Workbook': workbook()})
        with tempfile.TemporaryDirectory() as task:
            path = Path(task) / 'source.et'
            path.write_bytes(raw)
            script = str(SCRIPTS / 'read_et.py')
            full = subprocess.run([sys.executable, '-I', '-S', '-B', script, 'source.et'],
                                  cwd=task, capture_output=True, text=True, timeout=20)
            self.assertIn(full.returncode, (0, 3), full.stderr)
            out = subprocess.run([sys.executable, '-I', '-S', '-B', script, 'source.et',
                                  '--compact', '--output', 'extraction.json'],
                                 cwd=task, capture_output=True, text=True, timeout=20)
            self.assertIn(out.returncode, (0, 3), out.stderr)
            manifest = json.loads(out.stdout)
            self.assertEqual(manifest['schema_version'], 'et_output_manifest_v1')
            self.assertEqual(manifest['output']['file'], 'extraction.json')
            payload = json.loads((Path(task) / 'extraction.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['status'], manifest['status'])
            self.assertIn('sheets', payload)
            self.assertTrue(manifest['compacted'])

    def test_bad_json_budget_is_a_usage_error(self):
        script = str(SCRIPTS / 'read_et.py')
        proc = subprocess.run([sys.executable, '-I', '-S', '-B', script, 'input.et', '--max-json-bytes', '1'],
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('max-json-bytes', proc.stderr)


if __name__ == '__main__':
    unittest.main()
