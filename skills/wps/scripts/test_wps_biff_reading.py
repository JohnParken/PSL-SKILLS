#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic regressions for BIFF8 Label and bounded legacy metadata reads."""
import struct
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import read_bytes
from test_wps_biff8 import cell, formula, rec, workbook
from test_wps_cfb import synthetic_cfb
from wps_biff8 import EncryptedError, Reader, ResourceLimitError, parse_stream


LEGACY_MODE = 'legacy_excel_clsid'
LEGACY_CLSID = bytes.fromhex('2008020000000000c000000000000046')


def label_string(value, *, compressed=False):
    raw = value.encode('latin1') if compressed else value.encode('utf-16le')
    count = len(raw) if compressed else len(raw) // 2
    return struct.pack('<HB', count, 0 if compressed else 1) + raw


def label(row, col, value, *, compressed=False, xf=0):
    return rec(0x0204, cell(row, col, xf) + label_string(value, compressed=compressed))


def raw_label(row, col, data, *, xf=0):
    return rec(0x0204, cell(row, col, xf) + data)


def palette(count=56, entries=None):
    entries = bytes(4 * 56) if entries is None else entries
    return struct.pack('<h', count) + entries


def pane(*, x=0, y=0, top_row=0, col_left=0, active=0, reserved=0):
    return struct.pack('<HHHHBB', x, y, top_row, col_left, active, reserved)


def page_breaks(entries):
    return struct.pack('<H', len(entries)) + b''.join(struct.pack('<3H', *item) for item in entries)


def legacy_cfb(streams):
    data = bytearray(synthetic_cfb(streams))
    data[8:24] = LEGACY_CLSID
    return bytes(data)


class BiffReadingTests(unittest.TestCase):
    def test_label_text_continue_and_mixed_cells_in_both_modes(self):
        split_surrogate = raw_label(0, 3, struct.pack('<HB', 2, 1) + b'\x3d\xd8')
        payload = label(0, 0, '中文')
        payload += label(0, 1, '\x80éÿ', compressed=True)
        payload += label(0, 2, '')
        payload += split_surrogate + rec(0x003C, b'\x01\x00\xde')
        payload += rec(0x0203, cell(0, 4) + struct.pack('<d', 12.5))
        payload += formula(0, 5, struct.pack('<d', 42))
        payload += raw_label(1, 0, struct.pack('<HB', 2, 0) + b'\x80')
        payload += rec(0x003C, b'\x01' + '中'.encode('utf-16le'))
        payload += raw_label(1, 1, struct.pack('<HB', 2, 1) + '中'.encode('utf-16le'))
        payload += rec(0x003C, b'\x00\x80')
        payload += rec(0x0203, cell(1, 2) + struct.pack('<d', 9))
        payload += label(2, 0, '000123')
        data = workbook(sheet_records=[payload], strings=[])

        for compatibility in ('strict', LEGACY_MODE):
            with self.subTest(compatibility=compatibility):
                sheet = parse_stream(data, compatibility=compatibility)['sheets'][0]
                cells = sheet['cells']
                self.assertEqual(cells['A1']['value'], '中文')
                self.assertEqual(cells['B1']['value'], '\x80éÿ')
                self.assertEqual(cells['C1']['value'], '')
                self.assertEqual(cells['D1']['value'], '😀')
                self.assertEqual(cells['E1']['value'], 12.5)
                self.assertEqual(cells['F1']['value'], 42)
                self.assertEqual(cells['A2']['value'], '\x80中')
                self.assertEqual(cells['B2']['value'], '中\x80')
                self.assertEqual(cells['C2']['value'], 9)
                self.assertEqual(cells['A3']['type'], 'text')
                self.assertEqual(cells['A3']['value'], '000123')
                self.assertEqual(cells['A1']['record_id'], '0x204')
                self.assertEqual(cells['D1']['record_id'], '0x204')

    def test_label_requires_fixed_header_plain_flags_and_full_consumption(self):
        malformed = [
            raw_label(0, 0, b'\0\0'),  # fixed XLUnicodeString header must remain in Label
            raw_label(0, 0, struct.pack('<HB', 1, 2) + b'A'),
            raw_label(0, 0, struct.pack('<HB', 1, 9) + b'A'),  # no rich/phonetic guessing
            raw_label(0, 0, struct.pack('<HB', 0, 4)),
            raw_label(0, 0, struct.pack('<HB', 0, 5)),
            raw_label(0, 0, label_string('A') + b'tail'),
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')),
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')) + rec(0x003C, b'\x02BC'),
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')) + rec(0x003C, b''),
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')) + rec(0x003C, b'\x01'),
            raw_label(0, 0, struct.pack('<HB', 1, 1) + b'\x00\xdc'),  # isolated low surrogate
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')) + rec(0x003C, b'\x01A'),
        ]
        for records in malformed:
            with self.subTest(records=records), self.assertRaises(ValueError):
                parse_stream(workbook(sheet_records=[records], strings=[]))
        header_in_continue = rec(0x0204, cell(0, 0)) + rec(0x003C, label_string('A'))
        with self.assertRaisesRegex(ValueError, 'fixed header'):
            parse_stream(workbook(sheet_records=[header_in_continue], strings=[]))

    def test_label_uses_shared_cell_validation_and_output_text_budget(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate cell'):
            parse_stream(workbook(sheet_records=[label(0, 0, 'A') * 2], strings=[]))
        with self.assertRaisesRegex(ValueError, 'XF index'):
            parse_stream(workbook(sheet_records=[label(0, 0, 'A', xf=2)], strings=[]))

        reader = Reader(workbook(sheet_records=[label(0, 0, 'A')], strings=[]))
        with patch('wps_biff8.MAX_OUTPUT_TEXT_UNITS', 0):
            with self.assertRaises(ResourceLimitError):
                reader.parse()
        self.assertEqual(reader.cell_count, 0)

        with patch('wps_biff8.MAX_CELLS', 0):
            with self.assertRaises(ResourceLimitError):
                parse_stream(workbook(sheet_records=[label(0, 0, 'A')], strings=[]))

    def test_legacy_structured_records_retain_raw_bytes_and_strict_rejects(self):
        color_entries = bytes(range(224))
        palette_payload = palette(entries=color_entries)
        pane_payload = pane(x=65535, y=65535, top_row=65535, col_left=255,
                            active=3, reserved=255)
        horizontal_payload = page_breaks([(2, 0, 3), (2, 4, 8), (7, 3, 3)])
        vertical_payload = page_breaks([(1, 0, 5), (1, 7, 65535), (8, 0, 2)])
        sheet = (rec(0x0041, pane_payload) + rec(0x001B, horizontal_payload)
                 + rec(0x001A, vertical_payload))
        data = workbook(sheet_records=[sheet], extra_global=rec(0x0092, palette_payload), strings=[])
        result = parse_stream(data, compatibility=LEGACY_MODE)
        raw = result['uninterpreted_metadata']
        by_name = {item['name']: item for item in raw}
        self.assertEqual(by_name['Palette']['payload_hex'], palette_payload.hex())
        self.assertEqual(by_name['Palette']['scope'], 'globals')
        self.assertEqual(by_name['Pane']['payload_hex'], pane_payload.hex())
        self.assertEqual(by_name['HorizontalPageBreaks']['payload_hex'], horizontal_payload.hex())
        self.assertEqual(by_name['VerticalPageBreaks']['payload_hex'], vertical_payload.hex())
        self.assertEqual(by_name['Pane']['scope'], '中文表1')
        self.assertEqual(result['issues'][0]['code'], 'known_metadata_retained_uninterpreted')

        for kind, payload in ((0x0092, palette_payload), (0x0041, pane_payload),
                              (0x001B, horizontal_payload), (0x001A, vertical_payload)):
            with self.subTest(record_id=hex(kind)), self.assertRaisesRegex(ValueError, 'Unsupported'):
                if kind == 0x0092:
                    parse_stream(workbook(extra_global=rec(kind, payload), strings=[]))
                else:
                    parse_stream(workbook(sheet_records=[rec(kind, payload)], strings=[]))

    def test_palette_requires_signed_count_56_and_exact_payload(self):
        bad_payloads = [
            palette(count=56, entries=bytes(223)),
            palette(count=55, entries=bytes(224)),
            palette(count=-1, entries=bytes(224)),
            struct.pack('<h', 0),
        ]
        for payload in bad_payloads:
            with self.subTest(length=len(payload), count=struct.unpack_from('<h', payload)[0]), self.assertRaises(ValueError):
                parse_stream(workbook(extra_global=rec(0x0092, payload), strings=[]),
                             compatibility=LEGACY_MODE)

    def test_pane_requires_size_column_and_active_pane_but_keeps_reserved(self):
        malformed = [
            bytes(9),
            bytes(11),
            pane(col_left=256),
            pane(active=4),
        ]
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_stream(workbook(sheet_records=[rec(0x0041, payload)], strings=[]),
                             compatibility=LEGACY_MODE)
        accepted = pane(x=65535, y=65535, top_row=65535, col_left=255, active=3, reserved=255)
        retained = parse_stream(workbook(sheet_records=[rec(0x0041, accepted)], strings=[]),
                                compatibility=LEGACY_MODE)['uninterpreted_metadata']
        self.assertEqual(retained[0]['payload_hex'], accepted.hex())

    def test_pagebreak_counts_lengths_ranges_order_and_overlap(self):
        invalid = [
            (0x001B, struct.pack('<H', 1)),
            (0x001B, page_breaks([(2, 5, 4)])),
            (0x001B, page_breaks([(2, 0, 256)])),
            (0x001B, page_breaks([(2, 0, 2), (2, 2, 3)])),
            (0x001B, page_breaks([(2, 0, 2), (1, 4, 5)])),
            (0x001B, page_breaks([(2, 0, 2)]) + b'\0'),
            (0x001B, struct.pack('<H', 1027) + bytes(1027 * 6)),
            (0x001A, page_breaks([(256, 0, 5)])),
            (0x001A, page_breaks([(1, 5, 4)])),
            (0x001A, page_breaks([(1, 0, 5), (1, 5, 7)])),
            (0x001A, page_breaks([(2, 0, 5), (1, 7, 8)])),
            (0x001A, struct.pack('<H', 256) + bytes(256 * 6)),
        ]
        for kind, payload in invalid:
            with self.subTest(record_id=hex(kind), payload=payload[:20]), self.assertRaises(ValueError):
                parse_stream(workbook(sheet_records=[rec(kind, payload)], strings=[]),
                             compatibility=LEGACY_MODE)

        # Ordering and interval disjointness span repeated records of one kind.
        for second in (page_breaks([(2, 3, 4)]), page_breaks([(1, 4, 5)])):
            sheet = rec(0x001B, page_breaks([(2, 0, 3)])) + rec(0x001B, second)
            with self.subTest(second=second), self.assertRaises(ValueError):
                parse_stream(workbook(sheet_records=[sheet], strings=[]), compatibility=LEGACY_MODE)

        empty = page_breaks([])
        result = parse_stream(workbook(sheet_records=[rec(0x001B, empty)], strings=[]),
                              compatibility=LEGACY_MODE)
        self.assertEqual(result['uninterpreted_metadata'][0]['payload_hex'], empty.hex())

        adjacent = page_breaks([(5, 0, 2), (5, 3, 4)])
        max_horizontal = page_breaks([(row, 0, 0) for row in range(1026)])
        max_vertical = page_breaks([(col, 0, 65535) for col in range(255)])
        ordered_repeated = (rec(0x001B, page_breaks([(5, 0, 2)]))
                            + rec(0x001B, page_breaks([(5, 3, 4), (6, 0, 1)])))
        for records, expected_metadata in ((rec(0x001B, adjacent), 1),
                                           (rec(0x001B, max_horizontal), 1),
                                           (rec(0x001A, max_vertical), 1),
                                           (ordered_repeated, 2)):
            parsed = parse_stream(workbook(sheet_records=[records], strings=[]),
                                  compatibility=LEGACY_MODE)
            self.assertEqual(len(parsed['uninterpreted_metadata']), expected_metadata)

    def test_validated_metadata_uses_shared_metadata_byte_and_entry_budgets(self):
        valid_palette = rec(0x0092, palette())
        with patch('wps_biff8.MAX_METADATA_BYTES', 225):
            with self.assertRaises(ResourceLimitError):
                parse_stream(workbook(extra_global=valid_palette, strings=[]), compatibility=LEGACY_MODE)

        repeated_panes = rec(0x0041, pane()) * 4097
        with self.assertRaises(ResourceLimitError):
            parse_stream(workbook(sheet_records=[repeated_panes], strings=[]), compatibility=LEGACY_MODE)

    def test_existing_strict_defaults_and_unified_byte_reader_route(self):
        with self.assertRaises(EncryptedError):
            parse_stream(workbook(extra_global=rec(0x002F), strings=[]), compatibility=LEGACY_MODE)
        with self.assertRaisesRegex(ValueError, 'Unicode CodePage 1200'):
            parse_stream(workbook(extra_global=rec(0x0042, struct.pack('<H', 1252)), strings=[]),
                         compatibility=LEGACY_MODE)
        parse_stream(workbook(sheet_records=[b''], extra_global=rec(0x0042, struct.pack('<H', 1200)), strings=[]),
                     compatibility=LEGACY_MODE)
        with self.assertRaisesRegex(ValueError, 'Unsupported global record'):
            parse_stream(workbook(extra_global=rec(0x7777, b'x'), strings=[]), compatibility=LEGACY_MODE)
        with self.assertRaisesRegex(ValueError, 'Unsupported global record'):
            parse_stream(workbook(extra_global=rec(0x00EB, b''), strings=[]), compatibility=LEGACY_MODE)

        no_date = bytearray(workbook(sheet_records=[b''], strings=[]))
        offset = 0
        while struct.unpack_from('<H', no_date, offset)[0] != 0x0022:
            offset += 4 + struct.unpack_from('<H', no_date, offset + 2)[0]
        struct.pack_into('<H', no_date, offset, 0x0031)
        with self.assertRaisesRegex(ValueError, 'explicit Date1904'):
            parse_stream(bytes(no_date), compatibility=LEGACY_MODE)

        data = legacy_cfb({'Workbook': workbook(
            sheet_records=[label(0, 0, '入口')
                           + rec(0x0203, cell(0, 1) + struct.pack('<d', 12.5))
                           + formula(0, 2, struct.pack('<d', 42))
                           + rec(0x0041, pane(reserved=255))
                           + rec(0x001B, page_breaks([(2, 0, 3)]))
                           + rec(0x001A, page_breaks([(1, 0, 5)]))],
            extra_global=rec(0x0092, palette()), strings=[])})
        result = read_bytes(data, compatibility=LEGACY_MODE)
        cells = result['sheets'][0]['cells']
        self.assertEqual(result['format'], 'biff8_subset')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['reader_status'], 'read_biff8_subset_with_diagnostics')
        self.assertEqual(cells['A1']['value'], '入口')
        self.assertEqual(cells['B1']['value'], 12.5)
        self.assertEqual(cells['C1']['value'], 42)
        self.assertEqual(cells['C1']['cache_status'], 'unverified')
        self.assertFalse(result['formulas_executed'])
        self.assertEqual({item['name'] for item in result['uninterpreted_metadata']},
                         {'Palette', 'Pane', 'HorizontalPageBreaks', 'VerticalPageBreaks'})
        self.assertIn('known_metadata_retained_uninterpreted',
                      {issue['code'] for issue in result['issues']})
        self.assertFalse(result['producer_verified'])


if __name__ == '__main__':
    unittest.main()
