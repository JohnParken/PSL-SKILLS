"""Plain BIFF8 Unicode fields and code-page declarations; synthetic only."""
from decimal import Decimal
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import read_bytes, ParseError
from wps_biff8 import parse_stream
from test_wps_biff8 import workbook, rec, cell, formula, special, string
from test_wps_cfb import synthetic_cfb
from test_wps_formula_groups import member, group


def plain(value, flags=1):
    encoded = value.encode('utf-16le' if flags & 1 else 'latin1')
    count = len(value.encode('utf-16le')) // 2
    return struct.pack('<HB', count, flags) + encoded


def label(value, flags=1, row=0, col=0):
    return rec(0x204, cell(row, col) + plain(value, flags))


class PureTableTests(unittest.TestCase):
    def data(self, payload=b'', extra=b'', strings=None):
        return read_bytes(synthetic_cfb({'Workbook': workbook([payload], strings=[] if strings is None else strings, extra_global=extra)}))

    def test_known_codepages_preserve_unicode_text_and_exact_numbers(self):
        body = label('中文') + label('é\x80', 0, row=1) + rec(0x203, cell(2, 0) + struct.pack('<d', .1))
        for codepage in [932, 936, 1252]:
            with self.subTest(codepage=codepage):
                result = self.data(body, rec(0x42, struct.pack('<H', codepage)))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['code_page_raw'], codepage)
                cells = result['sheets'][0]['cells']
                self.assertEqual(cells['A1']['value'], '中文')
                self.assertEqual(cells['A2']['value'], 'é\x80')
                self.assertEqual(cells['A3']['value'], Decimal.from_float(.1))
                self.assertIn('codepage_metadata_not_applied', [i['code'] for i in result['issues']])

    def test_explicit_strict_still_refuses_non_unicode_codepage(self):
        for codepage in [932, 936, 1252]:
            with self.assertRaises(ParseError):
                read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0x42, struct.pack('<H', codepage)))}), compatibility='strict')

    def test_invalid_lengths_unknown_or_conflicting_codepages_fail(self):
        for extra in [rec(0x42, b'x'), rec(0x42, bytes(3)), rec(0x42, b'\xff\xff'),
                      rec(0x42, struct.pack('<H', 936)) + rec(0x42, struct.pack('<H', 1252))]:
            with self.assertRaises(ParseError):
                self.data(extra=extra)

    def test_plain_label_reserved_bits_preserve_text_raw_flags_and_location(self):
        for flags, text in [(0x80, 'é'), (0x81, '中文'), (0xFF, '长编号000123')]:
            result = self.data(label(text, flags))
            value = result['sheets'][0]['cells']['A1']
            self.assertEqual(value['value'], text)
            self.assertEqual(value['string_header_flags_raw'], flags)
            self.assertEqual(result['status'], 'partial')
            event = next(i for i in result['issues'] if i['code'] == 'plain_string_reserved_flags_ignored')
            self.assertEqual(event['offset'], value['record_offset'])
            self.assertEqual(event['cell'], 'A1')

    def test_formula_string_cache_reserved_bits_stays_unverified(self):
        result = self.data(formula(0, 0, special(0)) + rec(0x207, plain('缓存中文', 0x81)))
        value = result['sheets'][0]['cells']['A1']
        self.assertEqual(value['value'], '缓存中文')
        self.assertEqual(value['cache_status'], 'unverified')
        self.assertEqual(value['cache_string_header_flags_raw'], 0x81)
        self.assertFalse(result['formulas_executed'])
        self.assertFalse(result['omissions_affect_cell_values'])

    def test_shared_and_array_cached_text_with_reserved_bits(self):
        for shared in [True, False]:
            body = member(0, shared=shared, cache=special(0)) + group('shared' if shared else 'array')
            body += rec(0x207, plain('甲', 0x81)) + member(1, shared=shared, cache=special(0))
            body += rec(0x207, plain('乙', 0x81))
            result = self.data(body)
            cells = result['sheets'][0]['cells']
            self.assertEqual([cells[x]['value'] for x in ['A1', 'A2']], ['甲', '乙'])
            self.assertEqual(cells['A2']['formula']['group_anchor'], 'A1')
            self.assertEqual(cells['A2']['cache_status'], 'unverified')

    def test_format_reserved_bits_preserve_format_and_raw_value(self):
        # Existing fixture owns format 164; use another unambiguous format ID.
        result = self.data(rec(0x203, cell(0, 0) + struct.pack('<d', 123.5)),
                           rec(0x41E, struct.pack('<H', 165) + plain('0.000', 0x80)))
        self.assertEqual(result['custom_number_formats']['165'], '0.000')
        self.assertEqual(result['format_string_header_flags']['165']['flags_raw'], 0x80)
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], Decimal('123.5'))
        self.assertEqual(result['status'], 'partial')

    def test_strict_rejects_reserved_headers_in_plain_fields(self):
        for body, extra in [(label('中', 0x81), b''),
                            (formula(0, 0, special(0)) + rec(0x207, plain('中', 0x81)), b''),
                            (b'', rec(0x41E, struct.pack('<H', 165) + plain('0.00', 0x80)))]:
            with self.assertRaises(ValueError):
                parse_stream(workbook([body], strings=[], extra_global=extra))

    def test_plain_reserved_flag_does_not_create_or_consume_rich_metadata(self):
        # XLUnicodeString's bit3 is reserved, not SST's fRichSt.
        result = self.data(label('中', 9))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '中')
        extra_tail = rec(0x204, cell(0, 0) + plain('中', 9) + bytes(4))
        with self.assertRaises(ParseError):
            self.data(extra_tail)
        bad_sst = bytearray(workbook([rec(0xFD, cell(0, 0) + bytes(4))], strings=['中']))
        marker = struct.pack('<ii', 1, 1)
        position = bad_sst.index(marker) + 8 + 2
        bad_sst[position] = 0x81
        with self.assertRaises(ParseError):
            read_bytes(synthetic_cfb({'Workbook': bytes(bad_sst)}))

    def test_continued_plain_label_and_cache_use_independent_character_flags(self):
        prefix = struct.pack('<HB', 3, 0x81) + '中'.encode('utf-16le')
        text_records = rec(0x204, cell(0, 0) + prefix) + rec(0x3C, b'\0AB')
        cache_records = formula(1, 0, special(0)) + rec(0x207, prefix) + rec(0x3C, b'\0AB')
        result = self.data(text_records + cache_records)
        self.assertEqual([result['sheets'][0]['cells'][a]['value'] for a in ['A1', 'A2']], ['中AB', '中AB'])
        with self.assertRaises(ParseError):
            self.data(rec(0x204, cell(0, 0) + prefix) + rec(0x3C, b'\x80AB'))

    def test_malformed_utf16_and_tail_or_unknown_record_still_fail(self):
        for body in [rec(0x204, cell(0, 0) + b'\1\0\x81\0\xd8'),
                     rec(0x204, cell(0, 0) + b'\2\0\x81A\0'),
                     label('中', 0x81) + rec(0x7777),
                     rec(0x204, cell(0, 0) + plain('中', 0x81) + b'x')]:
            with self.assertRaises(ParseError):
                self.data(body)

    def test_sparse_last_cell_long_identifier_and_budget_not_expanded(self):
        value = '000123456789012345678901234567890'
        result = self.data(label(value, 0x80, row=65535, col=255))
        cells = result['sheets'][0]['cells']
        self.assertEqual(list(cells), ['IV65536'])
        self.assertEqual(cells['IV65536']['value'], value)
        self.assertEqual(result['cell_count'], 1)
        with patch('wps_biff8.MAX_CELLS', 0), self.assertRaises(ParseError):
            self.data(label(value, 0x80))
        for payload in [label('超预算文字', 0x81), formula(0, 0, special(0)) + rec(0x207, plain('超预算缓存', 0x81))]:
            with patch('wps_biff8.MAX_OUTPUT_TEXT_UNITS', 1), self.assertRaises(ParseError) as error:
                self.data(payload)
            self.assertEqual(error.exception.status, 'resource_limit')

    def test_cli_default_partial_strict_failure_and_input_unchanged(self):
        data = synthetic_cfb({'Workbook': workbook([label('中文', 0x81)], strings=[], extra_global=rec(0x42, struct.pack('<H', 936)))})
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'table.et'; path.write_bytes(data)
            command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('read_wps.py')), str(path)]
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf8')
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(json.loads(result.stdout)['sheets'][0]['cells']['A1']['value'], '中文')
            result = subprocess.run(command + ['--compatibility', 'strict'], capture_output=True, text=True, encoding='utf8')
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('sheets', json.loads(result.stdout))
            self.assertEqual(path.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
