#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic regressions for BIFF8 Label and tolerant legacy metadata reads.

All compatibility values select the same tolerant policy. Malformed Label or
legacy metadata records degrade to bounded diagnostics; stored cells already
framed are kept. Palette/Pane/page-break bytes are retained without being
applied, and read-only safety is unchanged.
"""
import struct
import sys
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_wps import read_bytes
from test_wps_biff8 import cell, formula, rec, workbook
from test_wps_cfb import synthetic_cfb
from wps_biff8 import EncryptedError, Reader, ResourceLimitError, parse_stream


LEGACY_MODE = 'legacy_excel_clsid'
MODE = 'data'


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
    data[8:24] = bytes.fromhex('2008020000000000c000000000000046')
    return bytes(data)


def issue_codes(result):
    return [item.get('code') for item in result.get('issues', ())]


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

        for compatibility in (None, MODE, 'strict', LEGACY_MODE):
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

    def test_label_malformed_records_are_omitted_with_omission_diagnostic(self):
        malformed = [
            raw_label(0, 0, b'\0\0'),
            raw_label(0, 0, struct.pack('<HB', 1, 2) + b'A'),
            raw_label(0, 0, struct.pack('<HB', 1, 9) + b'A'),
            raw_label(0, 0, struct.pack('<HB', 0, 4)),
            raw_label(0, 0, struct.pack('<HB', 0, 5)),
            raw_label(0, 0, label_string('A') + b'tail'),
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')),
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')) + rec(0x003C, b'\x02BC'),
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')) + rec(0x003C, b''),
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')) + rec(0x003C, b'\x01'),
            raw_label(0, 0, struct.pack('<HB', 1, 1) + b'\x00\xdc'),
            raw_label(0, 0, struct.pack('<HB', 2, 1) + 'A'.encode('utf-16le')) + rec(0x003C, b'\x01A'),
        ]
        for records in malformed:
            with self.subTest(records=records):
                result = parse_stream(workbook(sheet_records=[records], strings=[]))
                self.assertEqual(result['status'], 'partial')
                # Reserved XLUnicodeString flag bits are ignored and reported;
                # genuinely undecodable records skip only that cell.
                cell_entry = result['sheets'][0]['cells'].get('A1')
                if cell_entry is not None:
                    self.assertIn('plain_string_reserved_flags_ignored',
                                  issue_codes(result))
                else:
                    self.assertTrue(result['omissions_affect_cell_values'])
        header_in_continue = rec(0x0204, cell(0, 0)) + rec(0x003C, label_string('A'))
        result = parse_stream(workbook(sheet_records=[header_in_continue], strings=[]))
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('A1', result['sheets'][0]['cells'])
        self.assertTrue(any(code in ('cell_value_record_omitted',
                                     'worksheet_record_validation_failed')
                            for code in issue_codes(result)))

    def test_label_uses_shared_cell_validation_and_output_text_budget(self):
        duplicate = parse_stream(workbook(sheet_records=[label(0, 0, 'A') * 2], strings=[]))
        self.assertEqual(duplicate['status'], 'partial')
        self.assertIn('duplicate_cell_omitted', issue_codes(duplicate))
        fallback = parse_stream(workbook(sheet_records=[label(0, 0, 'A', xf=2)], strings=[]))
        self.assertEqual(fallback['status'], 'partial')
        self.assertEqual(fallback['sheets'][0]['cells']['A1']['value'], 'A')
        self.assertIn('xf_index_out_of_range_fallback', issue_codes(fallback))

        reader = Reader(workbook(sheet_records=[label(0, 0, 'A')], strings=[]))
        with patch('wps_biff8.MAX_OUTPUT_TEXT_UNITS', 0):
            with self.assertRaises(ResourceLimitError):
                reader.parse()
        self.assertEqual(reader.cell_count, 0)
        with patch('wps_biff8.MAX_CELLS', 0):
            with self.assertRaises(ResourceLimitError):
                parse_stream(workbook(sheet_records=[label(0, 0, 'A')], strings=[]))

    def test_legacy_structured_records_retain_raw_bytes_and_saved_cells(self):
        color_entries = bytes(range(224))
        palette_payload = palette(entries=color_entries)
        pane_payload = pane(x=65535, y=65535, top_row=65535, col_left=255,
                            active=3, reserved=255)
        horizontal_payload = page_breaks([(2, 0, 3), (2, 4, 8), (7, 3, 3)])
        vertical_payload = page_breaks([(1, 0, 5), (1, 7, 65535), (8, 0, 2)])
        sheet = (rec(0x0041, pane_payload) + rec(0x001B, horizontal_payload)
                 + rec(0x001A, vertical_payload)
                 + rec(0x0203, cell(0, 0) + struct.pack('<d', 8)))
        data = workbook(sheet_records=[sheet], extra_global=rec(0x0092, palette_payload), strings=[])
        for mode in (None, MODE, 'strict', LEGACY_MODE):
            with self.subTest(mode=mode):
                result = parse_stream(data, compatibility=mode)
                by_name = {item['name']: item for item in result['uninterpreted_metadata']}
                self.assertEqual(by_name['Palette']['payload_hex'], palette_payload.hex())
                self.assertEqual(by_name['Palette']['scope'], 'globals')
                self.assertEqual(by_name['Pane']['payload_hex'], pane_payload.hex())
                self.assertEqual(by_name['HorizontalPageBreaks']['payload_hex'], horizontal_payload.hex())
                self.assertEqual(by_name['VerticalPageBreaks']['payload_hex'], vertical_payload.hex())
                self.assertEqual(by_name['Pane']['scope'], '中文表1')
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 8)
                self.assertEqual(result['status'], 'partial')

    def test_palette_malformed_is_retained_raw(self):
        bad_payloads = [
            palette(count=56, entries=bytes(223)),
            palette(count=55, entries=bytes(224)),
            palette(count=-1, entries=bytes(224)),
            struct.pack('<h', 0),
        ]
        for payload in bad_payloads:
            with self.subTest(length=len(payload)):
                result = parse_stream(workbook(extra_global=rec(0x0092, payload), strings=[]),
                                      compatibility=LEGACY_MODE)
                self.assertEqual(result['status'], 'partial')
                self.assertIn('palette_invalid', issue_codes(result))
                retained = [item for item in result['uninterpreted_metadata']
                            if item['name'] == 'PaletteOpaque']
                self.assertEqual(bytes.fromhex(retained[0]['payload_hex']), payload)

    def test_pane_malformed_is_retained_and_cells_kept(self):
        malformed = [bytes(9), bytes(11), pane(col_left=256), pane(active=4)]
        for payload in malformed:
            with self.subTest(payload=payload):
                result = parse_stream(workbook(sheet_records=[
                    rec(0x0041, payload) + rec(0x0203, cell(0, 0) + struct.pack('<d', 4))],
                    strings=[]), compatibility=LEGACY_MODE)
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 4)
                self.assertIn('pane_metadata_invalid', issue_codes(result))
        accepted = pane(x=65535, y=65535, top_row=65535, col_left=255, active=3, reserved=255)
        retained = parse_stream(workbook(sheet_records=[rec(0x0041, accepted)], strings=[]),
                                compatibility=LEGACY_MODE)['uninterpreted_metadata']
        pane_entry = next(item for item in retained if item['name'] == 'Pane')
        self.assertEqual(pane_entry['payload_hex'], accepted.hex())

    def test_pagebreak_ranges_order_and_overlap_are_diagnostics(self):
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
            with self.subTest(record_id=hex(kind), length=len(payload)):
                result = parse_stream(workbook(sheet_records=[
                    rec(kind, payload) + rec(0x0203, cell(0, 0) + struct.pack('<d', 5))],
                    strings=[]), compatibility=LEGACY_MODE)
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 5)
                self.assertTrue(any(code in ('page_break_metadata_invalid',
                                             'page_break_layout_not_applied')
                                    for code in issue_codes(result)))
        for second in (page_breaks([(2, 3, 4)]), page_breaks([(1, 4, 5)])):
            sheet = rec(0x001B, page_breaks([(2, 0, 3)])) + rec(0x001B, second)
            result = parse_stream(workbook(sheet_records=[sheet], strings=[]),
                                  compatibility=LEGACY_MODE)
            self.assertEqual(result['status'], 'partial')
            self.assertIn('page_break_layout_not_applied', issue_codes(result))
        empty = page_breaks([])
        result = parse_stream(workbook(sheet_records=[rec(0x001B, empty)], strings=[]),
                              compatibility=LEGACY_MODE)
        entry = next(item for item in result['uninterpreted_metadata']
                     if item['name'] == 'HorizontalPageBreaks')
        self.assertEqual(entry['payload_hex'], empty.hex())

    def test_metadata_budget_truncates_instead_of_aborting(self):
        valid_palette = rec(0x0092, palette())
        with patch('wps_biff8.MAX_METADATA_BYTES', 100):
            result = parse_stream(workbook(extra_global=valid_palette, strings=[]),
                                  compatibility=LEGACY_MODE)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('metadata_budget_truncated', issue_codes(result))
        repeated_panes = rec(0x0041, pane()) * 20
        with patch('wps_biff8.MAX_METADATA_ENTRIES', 5):
            result = parse_stream(workbook(sheet_records=[repeated_panes], strings=[]),
                                  compatibility=LEGACY_MODE)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('metadata_budget_truncated', issue_codes(result))

    def test_hard_rejections_and_unified_byte_reader_route(self):
        with self.assertRaises(EncryptedError):
            parse_stream(workbook(extra_global=rec(0x002F), strings=[]),
                         compatibility=LEGACY_MODE)
        result = parse_stream(workbook(extra_global=rec(0x0042, struct.pack('<H', 1252)),
                                       strings=[]), compatibility=LEGACY_MODE)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('codepage_metadata_not_applied', issue_codes(result))
        unknown = parse_stream(workbook(extra_global=rec(0x7777, b'x'), strings=[]),
                               compatibility=LEGACY_MODE)
        self.assertEqual(unknown['status'], 'partial')
        self.assertIn('unknown_biff_record_salvaged', issue_codes(unknown))
        drawing = parse_stream(workbook(extra_global=rec(0x00EB, b''), strings=[]),
                               compatibility=LEGACY_MODE)
        self.assertEqual(drawing['status'], 'partial')
        self.assertIn('drawing_group_invalid', issue_codes(drawing))
        no_date = bytearray(workbook(sheet_records=[b''], strings=[]))
        offset = 0
        while struct.unpack_from('<H', no_date, offset)[0] != 0x0022:
            offset += 4 + struct.unpack_from('<H', no_date, offset + 2)[0]
        struct.pack_into('<H', no_date, offset, 0x0031)
        result = parse_stream(bytes(no_date), compatibility=LEGACY_MODE)
        self.assertEqual(result['date_system'], 'unknown')
        self.assertIn('date_system_missing', issue_codes(result))

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
        self.assertEqual(result['reader_status'], 'partial')
        self.assertEqual(cells['A1']['value'], '入口')
        self.assertEqual(cells['B1']['value'], 12.5)
        self.assertEqual(cells['C1']['value'], 42)
        self.assertEqual(cells['C1']['cache_status'], 'unverified')
        self.assertFalse(result['formulas_executed'])
        self.assertEqual({item['name'] for item in result['uninterpreted_metadata']},
                         {'Palette', 'Pane', 'HorizontalPageBreaks', 'VerticalPageBreaks'})
        self.assertFalse(result['producer_verified'])


if __name__ == '__main__':
    unittest.main()
