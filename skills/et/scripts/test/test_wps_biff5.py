#!/usr/bin/env python3
"""Synthetic old-BIFF fixtures, independent of BIFF8 string/style builders."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_cfb import synthetic_cfb
from wps_biff8 import parse_stream
from wps_biff8_errors import EncryptedError
from read_et import read_bytes
from et_cli import metadata
from et_table_data import bounds


def rec(kind, payload=b''):
    return struct.pack('<HH', kind, len(payload)) + payload


def bof(kind=5, version=0x0500):
    return rec(0x0809, struct.pack('<4H', version, kind, 0, 1995))


def cell(row=0, col=0, xf=0):
    return struct.pack('<HHH', row, col, xf)


def text(raw):
    return struct.pack('<H', len(raw)) + raw


def label(raw=b'hello', row=0, col=0):
    return rec(0x204, cell(row, col) + text(raw))


def number(value=42, row=0, col=1):
    return rec(0x203, cell(row, col) + struct.pack('<d', value))


def formula(cache, row=0, col=0):
    return rec(6, cell(row, col) + cache + struct.pack('<HIH', 0, 0, 3) + b'\x1e\x01\x00')


def workbook(cells=b'', codepages=(1252,), extra=b'', name=b'S', tail=b''):
    glob = bof() + b''.join(rec(0x42, struct.pack('<H', cp)) for cp in codepages)
    glob += rec(0xE0, bytes(16)) + rec(0x22, b'\0\0') + extra
    sheet_name = bytes([len(name)]) + name
    offset = len(glob) + 4 + 6 + len(sheet_name) + 4
    bound = rec(0x85, struct.pack('<IBB', offset, 0, 0) + sheet_name)
    return glob + bound + rec(10) + bof(16) + cells + rec(10) + tail


class Biff5CoreTests(unittest.TestCase):
    def test_real_cfb_entry_and_metadata_use_distinct_schema(self):
        result = read_bytes(synthetic_cfb({'Book': workbook(label(b'000123') + number())}))
        self.assertEqual(result['schema_version'], 'biff5_readonly_v1')
        self.assertEqual(result['format'], 'biff5_subset')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['formulas_executed'])
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '000123')
        self.assertEqual(metadata(result)['sheet_names'], ['S'])

    def test_explicit_single_and_multibyte_codepages(self):
        for cp, codec, value in ((1252, 'cp1252', 'é€'), (936, 'gbk', '中文'),
                                 (932, 'cp932', '日本'), (1251, 'cp1251', 'Привет')):
            with self.subTest(cp=cp):
                result = parse_stream(workbook(label(value.encode(codec)), codepages=(cp,)))
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], value)
                self.assertEqual(result['text_encoding'], codec)

    def test_missing_unknown_conflicting_encoding_never_guessed(self):
        for cp in ((), (9999,), (1200,), (1252, 936)):
            result = parse_stream(workbook(label(b'ASCII') + number(), codepages=cp))
            self.assertEqual(set(result['sheets'][0]['cells']), {'B1'})
            self.assertTrue(result['sheets'][0]['name_unavailable'])
            self.assertTrue(result['omissions_affect_cell_values'])

    def test_repeated_same_codepage_is_not_conflict(self):
        result = parse_stream(workbook(label(b'\xe9'), codepages=(1252, 1252)))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 'é')

    def test_invalid_byte_is_not_replaced(self):
        result = parse_stream(workbook(label(b'\x81') + number()))
        self.assertEqual(set(result['sheets'][0]['cells']), {'B1'})

    def test_biff8_style_size_is_not_applied_to_legacy_xf(self):
        result = parse_stream(workbook(number(), extra=rec(0xE0, bytes(20))))
        self.assertFalse(result['styles'][1]['valid'])
        self.assertNotIn('numeric_interpretation', result['sheets'][0]['cells']['B1'])

    def test_legacy_row_limit_applies_to_values_and_analysis(self):
        result = parse_stream(workbook(number(row=16384) + number(row=16383)))
        self.assertEqual(set(result['sheets'][0]['cells']), {'B16384'})
        with self.assertRaises(ValueError):
            bounds('A16385', 'biff5_readonly_v1')

    def test_truncated_string_does_not_consume_next_record(self):
        bad = rec(0x204, cell() + struct.pack('<H', 5) + b'X')
        result = parse_stream(workbook(bad + rec(0x3C, b'ABCD') + number()))
        self.assertEqual(set(result['sheets'][0]['cells']), {'B1'})

    def test_rk_multi_and_bool_blank_values(self):
        records = rec(0xBD, struct.pack('<HHHIHIH', 0, 0, 0, 6, 0, 10, 1))
        records += rec(0xBE, struct.pack('<HHHHH', 1, 0, 0, 0, 1))
        records += rec(0x205, cell(2, 0) + b'\1\0')
        records += rec(0x205, cell(2, 1) + b'\x07\1')
        result = parse_stream(workbook(records))['sheets'][0]['cells']
        self.assertEqual([result[k]['value'] for k in ('A1', 'B1', 'A2', 'B2', 'A3', 'B3')],
                         [1, 2, None, None, True, '#DIV/0!'])

    def test_numeric_boolean_error_and_text_caches_are_saved_only(self):
        cases = ((struct.pack('<d', 3.5), b'', 3.5),
                 (b'\1\0\1\0\0\0\xff\xff', b'', True),
                 (b'\2\0\7\0\0\0\xff\xff', b'', '#DIV/0!'),
                 (b'\0\0\0\0\0\0\xff\xff', rec(0x207, text(b'caf\xe9')), 'café'),
                 (b'\0\0\0\0\0\0\xff\xff', rec(0x207, text(b'')), ''))
        for cache, following, expected in cases:
            result = parse_stream(workbook(formula(cache) + following))
            actual = result['sheets'][0]['cells']['A1']
            self.assertEqual(actual['value'], expected)
            self.assertIn(actual['cache_status'], ('unverified', 'error_unverified'))
            self.assertNotIn('formula_text', actual['formula'])
            self.assertFalse(result['formulas_executed'])

    def test_biff8_empty_marker_or_nonadjacent_string_is_not_guessed(self):
        for records in (formula(b'\3\0\0\0\0\0\xff\xff'),
                        formula(b'\0\0\0\0\0\0\xff\xff') + rec(0x221) + rec(0x207, text(b'x'))):
            result = parse_stream(workbook(records + number()))
            self.assertEqual(set(result['sheets'][0]['cells']), {'B1'})

    def test_nested_substream_cannot_contribute_sheet_values(self):
        result = parse_stream(workbook(number() + bof(32) + number(999, col=2)))
        self.assertEqual(set(result['sheets'][0]['cells']), {'B1'})
        self.assertTrue(result['omissions_affect_cell_values'])

    def test_invalid_bound_offset_cannot_scan_for_cells(self):
        data = bytearray(workbook(number()))
        index = data.index(b'\x85\0') + 4
        struct.pack_into('<I', data, index, 1)
        result = parse_stream(bytes(data))
        self.assertEqual(result['sheets'][0]['cells'], {})
        self.assertEqual(result['sheets'][0]['read_status'], 'not_read')

    def test_duplicate_cell_keeps_first_value(self):
        result = parse_stream(workbook(number(1) + number(2)))
        self.assertEqual(result['sheets'][0]['cells']['B1']['value'], 1)
        self.assertTrue(result['omissions_affect_cell_values'])

    def test_encryption_and_old_payload_limit_remain_hard_refusals(self):
        with self.assertRaises(EncryptedError):
            parse_stream(workbook(extra=rec(0x2F, b'xx')))
        with self.assertRaisesRegex(ValueError, '2080'):
            parse_stream(workbook(extra=rec(0x7777, bytes(2081))))

    def test_display_and_analysis_keep_source_partial(self):
        from et_table_analysis import analyze
        from et_table_preview import preview
        result = parse_stream(workbook(label(b'id') + number()))
        query = {'sheet': 'S', 'range': 'A1:B1'}
        self.assertEqual(preview(result, query)['status'], 'partial')
        self.assertEqual(analyze(result, query)['status'], 'partial')

    def test_truncated_tokens_keep_verified_numeric_and_text_caches(self):
        for cache, following, value in ((struct.pack('<d', 42), b'', 42),
                                        (b'\0\0\0\0\0\0\xff\xff', rec(0x207, text(b'known')), 'known')):
            fixed = cell() + cache + struct.pack('<HIH', 0, 0, 99)
            result = parse_stream(workbook(rec(6, fixed) + following))
            actual = result['sheets'][0]['cells']['A1']
            self.assertEqual(actual['value'], value)
            self.assertFalse(actual['formula']['tokens_complete'])
            self.assertEqual(actual['cache_status'], 'unverified')
            self.assertIn('biff5_formula_tokens_truncated', [i['code'] for i in result['issues']])
            self.assertEqual(result['status'], 'partial')

    def test_short_formula_header_still_cannot_supply_cache(self):
        for size in (13, 14, 20, 21):
            result = parse_stream(workbook(rec(6, (cell() + struct.pack('<d', 42) + bytes(8))[:size]) + number()))
            self.assertEqual(set(result['sheets'][0]['cells']), {'B1'})

    def test_biff8_header_not_relabelled_legacy(self):
        data = rec(0x0809, struct.pack('<4H2I', 0x0500, 5, 0, 1995, 0, 0)) + rec(10)
        with self.assertRaisesRegex(ValueError, '8-byte BIFF5/7 BOF'):
            parse_stream(data)


if __name__ == '__main__':
    unittest.main()
