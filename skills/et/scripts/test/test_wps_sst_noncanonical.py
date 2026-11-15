#!/usr/bin/env python3
"""Synthetic MS-XLS byte fixtures; not real Excel/WPS acceptance evidence."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_wps_biff8 import bof, cell, rec, string
from wps_biff8 import parse_stream
from wps_biff8_errors import ResourceLimitError
from wps_biff8_values import ContinuedStrings


def workbook(sst, *, total=2, unique=2, continues=b''):
    """Declare three indexed cells and one independent number."""
    globals_part = (bof() + rec(0x0022, b'\0\0') + rec(0x00E0, bytes(20))
                    + rec(0x00FC, struct.pack('<ii', total, unique) + sst)
                    + continues)
    bound = rec(0x0085, struct.pack('<IBB', 0, 0, 0) + string('S', True))
    sheet_offset = len(globals_part) + len(bound) + 4
    bound = rec(0x0085, struct.pack('<IBB', sheet_offset, 0, 0) + string('S', True))
    cells = b''.join(rec(0x00FD, cell(0, col) + struct.pack('<I', col))
                     for col in range(3))
    cells += rec(0x0203, cell(0, 3) + struct.pack('<d', 42))
    return globals_part + bound + rec(10) + bof(16) + rec(0x0081, bytes(2)) + cells + rec(10)


class NoncanonicalSstTests(unittest.TestCase):
    def test_reserved_bits_preserve_compressed_unicode_and_next_index(self):
        for reserved in (2, 16, 32, 64, 128, 0xF2):
            with self.subTest(reserved=reserved):
                data = struct.pack('<HB', 2, reserved) + b'A\xe9' + string('中文')
                result = parse_stream(workbook(data))
                cells = result['sheets'][0]['cells']
                self.assertEqual(cells['A1']['value'], 'Aé')
                self.assertEqual(cells['B1']['value'], '中文')
                self.assertNotIn('C1', cells)
                self.assertEqual(cells['D1']['value'], 42)
                self.assertEqual(result['status'], 'partial')
                issue = next(i for i in result['issues'] if i['code'] == 'sst_reserved_flags_ignored')
                self.assertEqual(issue['flags_raw'], reserved)

    def test_next_header_in_continue_reports_its_actual_location(self):
        result = parse_stream(workbook(string('first'), continues=rec(
            0x003C, struct.pack('<HB', 1, 0x80) + b'X')))
        self.assertEqual(result['sheets'][0]['cells']['B1']['value'], 'X')
        issue = next(i for i in result['issues'] if i['code'] == 'sst_reserved_flags_ignored')
        self.assertEqual(issue['payload_offset'], 0)
        data = workbook(string('first'), continues=rec(0x003C, struct.pack('<HB', 1, 0x80) + b'X'))
        self.assertEqual(struct.unpack_from('<H', data, issue['record_offset'])[0], 0x003C)

    def test_zero_runs_preserve_ext_length_and_following_string(self):
        data = (struct.pack('<HBHi', 1, 0x0C, 0, 3) + b'X' + b'abc'
                + string('next'))
        result = parse_stream(workbook(data))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 'X')
        self.assertEqual(result['sheets'][0]['cells']['B1']['value'], 'next')
        self.assertIn('sst_zero_format_runs', [i['code'] for i in result['issues']])

    def test_underreported_total_does_not_discard_declared_unique_strings(self):
        for total in (0, 1, -1):
            result = parse_stream(workbook(string('first') + string('second'), total=total))
            self.assertEqual(result['sheets'][0]['cells']['B1']['value'], 'second')
            self.assertIn('sst_counts_invalid', [i['code'] for i in result['issues']])

    def test_negative_unique_does_not_scan_or_guess_strings(self):
        result = parse_stream(workbook(string('not inferred'), unique=-1))
        self.assertEqual(set(result['sheets'][0]['cells']), {'D1'})
        self.assertTrue(result['omissions_affect_cell_values'])

    def test_invalid_total_cannot_bypass_unique_budget(self):
        with self.assertRaises(ResourceLimitError):
            parse_stream(workbook(b'', total=0, unique=200001))

    def test_reserved_rich_and_ext_flags_keep_all_length_fields(self):
        data = (struct.pack('<HBHi', 1, 0xFE, 1, 3) + b'X'
                + struct.pack('<HH', 0, 0) + b'abc' + string('next'))
        result = parse_stream(workbook(data))
        self.assertEqual(result['sheets'][0]['cells']['B1']['value'], 'next')

    def test_reserved_flag_recovery_does_not_relax_continue_encoding(self):
        data = struct.pack('<HB', 2, 2) + b'A'
        result = parse_stream(workbook(data, unique=1, continues=rec(0x003C, b'\x02B')))
        self.assertEqual(set(result['sheets'][0]['cells']), {'D1'})

    def test_complete_prefix_retained_but_no_resynchronization_after_bad_header(self):
        data = string('safe') + struct.pack('<HB', 2, 0xF7) + struct.pack('<i', -1) + string('not guessed')
        result = parse_stream(workbook(data, unique=3, total=3))
        self.assertEqual(set(result['sheets'][0]['cells']), {'A1', 'D1'})
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 'safe')

    def test_plain_string_caller_does_not_acquire_sst_recovery(self):
        parser = ContinuedStrings([(0, 0x0207, struct.pack('<HB', 1, 2) + b'X')], 0)
        with self.assertRaises(ValueError):
            parser.string()

    def test_biff5_and_biff7_bof_use_separate_schema(self):
        legacy = rec(0x0809, struct.pack('<4H', 0x0500, 5, 0, 1995)) + rec(10)
        result = parse_stream(legacy)
        self.assertEqual(result['schema_version'], 'biff5_readonly_v1')
        self.assertTrue(result['omissions_affect_cell_values'])

    def test_all_flags_and_run_lengths_preserve_next_index(self):
        for flag in range(256):
            for runs in (0, 1):
                header = struct.pack('<HB', 1, flag)
                if flag & 8:
                    header += struct.pack('<H', runs)
                if flag & 4:
                    header += struct.pack('<i', 2)
                body = 'é'.encode('utf-16le') if flag & 1 else b'\xe9'
                if flag & 8:
                    body += struct.pack('<HH', 0, 0) * runs
                if flag & 4:
                    body += b'zz'
                parser = ContinuedStrings([(0, 0xFC, header + body + string('next'))], 0)
                with self.subTest(flag=flag, runs=runs):
                    self.assertEqual(parser.string(allow_rich=True, allow_phonetic=True,
                                                   sst_compatibility=True), 'é')
                    self.assertEqual(parser.string(allow_rich=True, allow_phonetic=True,
                                                   sst_compatibility=True), 'next')
                    self.assertEqual(parser.finish(), 1)

    def test_rich_ext_fixed_header_splits_remain_rejected(self):
        header = struct.pack('<HBHi', 1, 0xFF, 1, 2)
        body = b'X\0' + struct.pack('<HH', 0, 0) + b'zz'
        for cut in range(1, len(header)):
            parser = ContinuedStrings([(0, 0xFC, header[:cut]), (10, 0x3C, header[cut:] + body)], 0)
            with self.subTest(cut=cut), self.assertRaises(ValueError):
                parser.string(allow_rich=True, allow_phonetic=True, sst_compatibility=True)

    def test_reserved_flags_survive_metadata_budget_plain_text_retry(self):
        from unittest.mock import patch
        payload = struct.pack('<HBH', 1, 0xFA, 1) + b'X' + struct.pack('<HH', 0, 0) + string('next')
        with patch('wps_biff8.MAX_METADATA_BYTES', 0):
            result = parse_stream(workbook(payload))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 'X')
        self.assertEqual(result['sheets'][0]['cells']['B1']['value'], 'next')
        codes = [item['code'] for item in result['issues']]
        self.assertIn('rich_phonetic_metadata_budget_truncated', codes)
        self.assertIn('sst_reserved_flags_ignored', codes)


if __name__ == '__main__':
    unittest.main()
