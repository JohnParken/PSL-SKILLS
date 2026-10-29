"""Stored values survive standard DXF metadata; no real WPS sample claim."""
from pathlib import Path
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import read_bytes, ParseError
from test_wps_biff8 import workbook, rec
from test_wps_cfb import synthetic_cfb
from wps_biff8 import parse_stream, ResourceLimitError


# Independently transcribed MS-XLS: FrtHeader rt=088D, flags=0,
# 8 ignored bytes, DXF flags=0; XFProps reserved=0, cprops=0.
EMPTY_DXF = bytes.fromhex('8d0800000000000000000000000000000000')


class DxfIntegrationTests(unittest.TestCase):
    def test_stored_values_and_caches_unchanged(self):
        baseline = parse_stream(workbook(), compatibility='data')
        added_record = rec(0x088D, EMPTY_DXF)
        stream = workbook(extra_global=added_record)
        result = read_bytes(synthetic_cfb({'Workbook': stream}))
        self.assertEqual(result['status'], 'partial')
        expected_sheets = copy.deepcopy(baseline['sheets'])
        # Inserting a framed global record moves every worksheet and source
        # cell/cache record by its exact 22-byte length. All other fields stay.
        for sheet in expected_sheets:
            sheet['offset'] += len(added_record)
            for cell in sheet['cells'].values():
                for key in ('record_offset', 'cache_record_offset'):
                    if key in cell:
                        cell[key] += len(added_record)
            for merge in sheet['merges']:
                merge['record_offset'] += len(added_record)
        self.assertEqual(result['sheets'], expected_sheets)
        self.assertEqual(result['styles'], baseline['styles'])
        self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')
        self.assertFalse(result['omissions_affect_cell_values'])
        item = next(item for item in result['uninterpreted_metadata']
                    if item['record_id'] == '0x88d')
        self.assertEqual(item['payload_hex'], EMPTY_DXF.hex())
        self.assertEqual(item['name'], 'DXF')
        self.assertEqual(item['structure']['property_count'], 0)
        self.assertTrue(any(issue.get('record_id') == '0x88d'
                            for issue in result['issues']))
        with self.assertRaisesRegex(ValueError, 'Unsupported global record'):
            parse_stream(stream, compatibility='strict')
        legacy = parse_stream(stream, compatibility='legacy_excel_clsid')
        self.assertEqual(legacy['sheets'], expected_sheets)

    def test_cli_keeps_partial_json_and_original_file(self):
        data = synthetic_cfb({'Workbook': workbook(extra_global=rec(0x088D, EMPTY_DXF))})
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'with_formats.et'
            path.write_bytes(data)
            for script in ('read_wps.py', 'wps_biff8.py'):
                process = subprocess.run(
                    [sys.executable, '-I', '-S', '-B',
                     str(Path(__file__).with_name(script)), str(path)],
                    capture_output=True, text=True, timeout=5, env={'PATH': ''})
                self.assertEqual(process.returncode, 3, process.stderr)
                result = json.loads(process.stdout)
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
                self.assertEqual(result['sheets'][0]['cells']['C4']['value'], '缓存中文')
            self.assertEqual(path.read_bytes(), data)

    def test_malformed_or_wrong_scope_record_not_swallowed(self):
        for payload in (EMPTY_DXF[:-1], b'private bytes', EMPTY_DXF + b'\x00'):
            with self.subTest(payload=payload):
                with self.assertRaises(ParseError) as caught:
                    read_bytes(synthetic_cfb({'Workbook': workbook(
                        extra_global=rec(0x088D, payload))}))
                self.assertNotIn(caught.exception.status, ('ok', 'partial'))
                self.assertEqual(caught.exception.location['record_id'], '0x88d')
        with self.assertRaisesRegex(ValueError, 'Unsupported worksheet record'):
            parse_stream(workbook([rec(0x088D, EMPTY_DXF)]), compatibility='data')
        with patch('wps_biff8.MAX_METADATA_BYTES', 1):
            with self.assertRaises(ResourceLimitError):
                parse_stream(workbook(extra_global=rec(0x088D, EMPTY_DXF)),
                             compatibility='data')

    def test_aggregate_property_budget_is_enforced(self):
        # One valid type 0x14 property (wrap flag) after the count=1 header.
        one_property = EMPTY_DXF[:16] + b'\x01\x00' + bytes.fromhex('1400050001')
        stream = workbook(extra_global=rec(0x088D, one_property) * 2)
        result = read_bytes(synthetic_cfb({'Workbook': stream}))
        structure = next(item['structure'] for item in result['uninterpreted_metadata']
                         if item['record_id'] == '0x88d')
        self.assertEqual(json.loads(json.dumps(structure)), structure)
        self.assertEqual(structure['properties'][0]['payload_hex'], '01')
        with patch('wps_biff8.MAX_DXF_PROPERTIES', 1):
            with self.assertRaisesRegex(ResourceLimitError, 'aggregate property'):
                parse_stream(stream, compatibility='data')


if __name__ == '__main__':
    unittest.main()
