"""Precise ET/BIFF failure locations; synthetic byte sequences only."""
from pathlib import Path
import struct
import json
import io
from contextlib import redirect_stdout
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_biff8 import parse_stream, ResourceLimitError
from read_wps import read_bytes, ParseError
from test_wps_biff8 import workbook, rec, cell
from test_wps_cfb import synthetic_cfb


class EtErrorLocationTests(unittest.TestCase):
    def test_unknown_sheet_record_preserves_status_and_exact_location(self):
        payload = rec(0x7777, b'not interpreted')
        data = workbook([payload], strings=[])
        expected_offset = data.index(payload)
        with self.assertRaises(ParseError) as error:
            read_bytes(synthetic_cfb({'Workbook': data}))
        self.assertEqual(error.exception.status, 'unsupported')
        self.assertEqual(error.exception.location['sheet'], '中文表1')
        self.assertEqual(error.exception.location['record_offset'], expected_offset)
        self.assertEqual(error.exception.location['record_id'], '0x7777')
        self.assertNotIn('not interpreted', str(error.exception.location))

    def test_malformed_cell_record_keeps_source_location(self):
        payload = rec(0x203, cell(1, 2) + bytes(7))
        data = workbook([payload], strings=[])
        with self.assertRaises(ValueError) as error:
            parse_stream(data)
        self.assertEqual(error.exception.location['sheet'], '中文表1')
        self.assertEqual(error.exception.location['record_offset'], data.index(payload))

    def test_truncated_record_header_and_payload_report_framing_offset(self):
        base = workbook([b''], strings=[])
        for suffix in (b'\x01', struct.pack('<HH', 0x7777, 9) + b'x'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError) as error:
                parse_stream(base + suffix)
            self.assertEqual(error.exception.location['record_offset'], len(base))

    def test_merge_overlap_location_points_to_merge_record_not_eof(self):
        first = rec(0xE5, struct.pack('<5H', 1, 0, 2, 0, 2))
        second = rec(0xE5, struct.pack('<5H', 1, 2, 3, 2, 4))
        data = workbook([first + second], strings=[])
        with self.assertRaises(ValueError) as error:
            parse_stream(data)
        self.assertEqual(error.exception.location['record_id'], '0xe5')
        self.assertEqual(error.exception.location['record_offset'], data.index(second))
        self.assertEqual(error.exception.location['merge_index'], 1)

    def test_phonetic_unified_cli_is_partial_and_input_stays_unchanged(self):
        from test_wps_phonetic import workbook as phonetic_workbook, encoded_string
        payload = synthetic_cfb({'Workbook': phonetic_workbook(encoded_string('中文', 5, ext=b'raw'))})
        result = read_bytes(payload)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '中文')
        self.assertFalse(result['reading_assessment']['full_format_verified'])
        self.assertFalse(result['formulas_executed'])
        with tempfile.TemporaryDirectory(prefix='et-cli-', dir='.') as directory:
            sample = Path(directory) / 'synthetic.et'
            sample.write_bytes(payload)
            script = Path(__file__).resolve().parent / 'read_wps.py'
            process = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), str(sample)],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 3, process.stderr)
            self.assertEqual(json.loads(process.stdout)['shared_string_phonetics'][0]['raw_ext_hex'], '726177')
            self.assertEqual(sample.read_bytes(), payload)

    def test_low_level_cli_preserves_resource_failure_and_location(self):
        import wps_biff8
        failure = ResourceLimitError('metadata budget')
        failure.location = {'record_offset': 99, 'sheet': 'Data'}
        stream = io.StringIO()
        with patch('sys.argv', ['wps_biff8.py', 'sample.et']), patch('wps_biff8.read_file', side_effect=failure):
            with redirect_stdout(stream):
                exit_code = wps_biff8.main()
        result = json.loads(stream.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(result['status'], 'resource_limit')
        self.assertEqual(result['location'], failure.location)

    def test_metadata_budget_is_resource_limit_not_generic_corruption(self):
        data = workbook([b''], strings=[], extra_global=rec(0x3D, b'window'))
        with patch('wps_biff8.MAX_METADATA_BYTES', 1), self.assertRaises(ResourceLimitError):
            parse_stream(data)

    def test_failed_shared_string_reports_actual_continuation_record(self):
        # Exercise cursor's more precise location instead of replacing it with SST.
        from wps_biff8 import ContinuedStrings
        cursor = ContinuedStrings([(100, 0xFC, b'\x02\0\1A\0'), (200, 0x3C, b'\2B\0')], 0)
        with self.assertRaises(ValueError) as error:
            cursor.string()
        self.assertEqual(error.exception.location['record_offset'], 200)
        self.assertEqual(error.exception.location['payload_offset'], 0)
