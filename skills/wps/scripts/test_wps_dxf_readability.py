"""Stored data survives bounded, nonessential DXF formatting problems."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import read_bytes, ParseError
from test_wps_biff8 import workbook, rec
from test_wps_cfb import synthetic_cfb
from test_wps_dxf_semantics import dxf
from wps_dxf import validate_dxf

CASES = {
    'invalid_wrap_enum': dxf([(0x14, b'\xcc')]),
    'wrong_scalar_length': dxf([(0x14, b'\x01\x00')]),
    'invalid_font_length': dxf([(0x18, b'\x02\x00A\x00')]),
    'missing_alignment_dependency': dxf([(0x15, b'\x01')]),
    'unknown_property_type': dxf([(0x7777, b'opaque')]),
    'conflicting_fill_properties': dxf([(0x00, b''), (0x03, b'')]),
    'internal_border_flag_clear': dxf([(0x0B, b'')]),
}


class DxfReadabilityTests(unittest.TestCase):
    def test_data_and_legacy_recover_all_cells_and_caches_without_applying_formatting(self):
        baseline = read_bytes(synthetic_cfb({'Workbook': workbook()}))
        for name, payload in CASES.items():
            added = rec(0x088D, payload)
            expected = copy.deepcopy(baseline['sheets'])
            for sheet in expected:
                sheet['offset'] += len(added)
                for cell in sheet['cells'].values():
                    for key in ('record_offset', 'cache_record_offset'):
                        if key in cell:
                            cell[key] += len(added)
                for merge in sheet['merges']:
                    merge['record_offset'] += len(added)
            data = synthetic_cfb({'Workbook': workbook(extra_global=added)})
            for mode in ('data', 'legacy_excel_clsid'):
                with self.subTest(case=name, mode=mode):
                    result = read_bytes(data, compatibility=mode)
                    self.assertEqual(result['status'], 'partial')
                    self.assertEqual(result['sheets'], expected)
                    self.assertEqual(result['styles'], baseline['styles'])
                    self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')
                    self.assertFalse(result['omissions_affect_cell_values'])
                    item = next(item for item in result['uninterpreted_metadata'] if item['record_id'] == '0x88d')
                    self.assertEqual(item['payload_hex'], payload.hex())
                    self.assertTrue(item['structure']['formatting_issues'])
                    self.assertEqual(item['structure']['effective_formatting'], 'unknown')
                    self.assertTrue(any(issue['code'] == 'dxf_formatting_issues_retained' for issue in result['issues']))
            with self.assertRaises(ParseError):
                read_bytes(data, compatibility='strict')
            # A direct strict validator remains available; default readers no longer use it.
            with self.assertRaises(ValueError):
                validate_dxf(payload)

    def test_framing_damage_resource_limits_and_unknown_data_still_refused(self):
        valid = dxf([(0x14, b'\x01')])
        overrun = bytearray(valid)
        overrun[20:22] = b'\xff\xff'
        for payload in (valid[:-1], bytes(overrun), valid + b'\x00'):
            with self.subTest(payload=payload.hex()), self.assertRaises(ParseError):
                read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0x088D, payload))}))
        with patch('wps_biff8.MAX_METADATA_BYTES', 0), self.assertRaises(ParseError):
            read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0x088D, CASES['invalid_wrap_enum']))}))
        with self.assertRaises(ParseError):
            read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0x7777, b'could contain data'))}))

    def test_unsupported_scope_flags_do_not_invent_property_interpretation(self):
        payload = bytearray(dxf([(0x14, b'\x01')]))
        payload[2:4] = b'\x01\x00'
        result = validate_dxf(payload, formatting_policy='preserve')
        self.assertEqual(result['property_interpretations'], [])
        self.assertEqual(result['formatting_status'], 'uninterpreted_due_to_issues')
        self.assertEqual(result['properties'][0]['payload_hex'], '01')

    def test_cli_returns_partial_json_source_record_and_unchanged_original(self):
        payload = CASES['invalid_wrap_enum']
        data = synthetic_cfb({'Workbook': workbook(extra_global=rec(0x088D, payload))})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'input.et'
            path.write_bytes(data)
            for script in ('read_wps.py', 'wps_biff8.py'):
                child = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name(script)), './input.et'],
                                       cwd=temporary, capture_output=True, text=True)
                self.assertEqual(child.returncode, 3)
                result = json.loads(child.stdout)
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
                issue = next(item for item in result['issues'] if item['code'] == 'dxf_formatting_issues_retained')
                self.assertEqual(issue['record_id'], '0x88d')
                self.assertIsInstance(issue['record_offset'], int)
                self.assertEqual(result['uninterpreted_metadata'][issue['metadata_index']]['payload_hex'], payload.hex())
            self.assertEqual(path.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
