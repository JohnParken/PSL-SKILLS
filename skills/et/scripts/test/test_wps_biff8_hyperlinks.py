#!/usr/bin/env python3
"""Synthetic BIFF8 HLink/HLinkTooltip extraction; no targets are followed."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from read_et import read_bytes
from test_wps_biff8 import cell, rec, workbook
from test_wps_cfb import synthetic_cfb
from wps_biff8 import parse_stream
from wps_biff8_hyperlinks import (HLINK, HLINK_TOOLTIP, parse_hlink,
                                  parse_hlink_tooltip)


def hyperlink_string(value):
    raw = value.encode('utf-16le')
    return struct.pack('<I', len(value) + 1) + raw + b'\x00\x00'


def hlink(flags, body=b'', ref=(0, 0, 0, 0), clsid=bytes(16), version=2):
    return (struct.pack('<4H', *ref) + clsid
            + struct.pack('<II', version, flags) + body)


def tooltip(ref, text, rt=HLINK_TOOLTIP):
    return (struct.pack('<H', rt) + struct.pack('<4H', *ref)
            + text.encode('utf-16le') + b'\x00\x00')


def read(sheet_records, *, sheet_extra=b''):
    stream = workbook(sheet_records=[b''.join(sheet_records) + sheet_extra])
    return parse_stream(stream)


def codes(result):
    return [item.get('code') for item in result['issues']]


class HLinkTests(unittest.TestCase):
    def test_internal_location_only_link_is_exposed_not_followed(self):
        payload = hlink(0x0008, hyperlink_string('Sheet1!B2'))
        result = read([rec(HLINK, payload),
                       rec(0x0203, cell(0, 0) + struct.pack('<d', 7))])
        sheet = result['sheets'][0]
        self.assertEqual(sheet['cells']['A1']['value'], 7)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('hyperlink_targets_not_resolved', codes(result))
        link = sheet['hyperlinks'][0]
        self.assertEqual((link['cell'], link['target_kind']), ('A1', 'internal'))
        self.assertEqual(link['location'], 'Sheet1!B2')
        self.assertEqual(link['target'], 'Sheet1!B2')
        self.assertFalse(link['target_resolved'])
        self.assertFalse(link['followed'])
        self.assertFalse(link['link_executed'])
        self.assertEqual(link['source']['record_id'], hex(HLINK))
        self.assertNotIn('range', link)

    def test_external_string_moniker_display_frame_and_range(self):
        flags = 0x0001 | 0x0100 | 0x0010 | 0x0080
        body = (hyperlink_string('显示名') + hyperlink_string('_blank')
                + hyperlink_string('https://example.invalid/report'))
        result = read([rec(HLINK, hlink(flags, body, ref=(1, 2, 0, 1)),
                           ), rec(0x0203, cell(4, 0) + struct.pack('<d', 9))])
        link = result['sheets'][0]['hyperlinks'][0]
        self.assertEqual(link['cell'], 'A2')
        self.assertEqual(link['range'], 'A2:B3')
        self.assertEqual(link['display'], '显示名')
        self.assertEqual(link['target_frame'], '_blank')
        self.assertEqual(link['target_kind'], 'external_string')
        self.assertEqual(link['target'], 'https://example.invalid/report')
        self.assertEqual(link['moniker'], 'https://example.invalid/report')
        self.assertFalse(link['target_resolved'])
        self.assertFalse(link['followed'])
        self.assertFalse(link['ole_moniker_opaque'])

    def test_tooltip_matches_hlink_ref_and_orphan_is_raw_evidence(self):
        ref = (3, 3, 1, 1)
        result = read([rec(HLINK, hlink(0x0008, hyperlink_string('Sheet1!A1'),
                                        ref=ref)),
                       rec(HLINK_TOOLTIP, tooltip(ref, '提示')),
                       rec(0x0203, cell(0, 0) + struct.pack('<d', 1))])
        link = result['sheets'][0]['hyperlinks'][0]
        self.assertEqual(link['tooltip'], '提示')
        self.assertEqual(link['tooltip_source']['record_id'],
                         hex(HLINK_TOOLTIP))
        result = read([rec(HLINK_TOOLTIP, tooltip((0, 0, 0, 0), '孤立')),
                       rec(0x0203, cell(0, 0) + struct.pack('<d', 1))])
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 1)
        self.assertIn('hlink_tooltip_orphan', codes(result))

    def test_opaque_ole_moniker_does_not_abort_or_claim_target(self):
        result = read([rec(HLINK, hlink(0x0001, b'opaque-ole-moniker'),
                           ), rec(0x0203, cell(0, 0) + struct.pack('<d', 3))])
        link = result['sheets'][0]['hyperlinks'][0]
        self.assertTrue(link['ole_moniker_opaque'])
        self.assertIsNone(link['target'])
        self.assertEqual(link['target_kind'], 'ole_moniker_opaque')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 3)
        self.assertFalse(link['followed'])

    def test_malformed_links_are_raw_and_later_cells_are_kept(self):
        cases = (
            (rec(HLINK, b'\x00' * 10), 'hlink_invalid'),
            (rec(HLINK, hlink(0x0008, hyperlink_string('x'))[:-1]),
             'hlink_invalid'),
            (rec(HLINK, hlink(0x0010)), 'hlink_invalid'),
            (rec(HLINK, hlink(0x0008, hyperlink_string('x'),
                             ref=(5, 1, 0, 0))), 'hlink_invalid'),
        )
        for records, code in cases:
            with self.subTest(code=code):
                result = read([records,
                               rec(0x0203, cell(0, 0) + struct.pack('<d', 5))])
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 5)
                self.assertIn(code, codes(result))

    def test_full_reader_returns_links_end_to_end(self):
        records = [rec(HLINK, hlink(0x0008, hyperlink_string('Sheet1!B2'))),
                   rec(0x0203, cell(0, 0) + struct.pack('<d', 7))]
        data = synthetic_cfb({'Workbook': workbook(sheet_records=[b''.join(records)])})
        result = read_bytes(data)
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 7)
        self.assertEqual(result['sheets'][0]['hyperlinks'][0]['location'],
                         'Sheet1!B2')
        self.assertFalse(result['sheets'][0]['hyperlinks'][0]['followed'])

    def test_helper_parsers_return_bounded_fields_and_validate_rt(self):
        payload = hlink(0x0008, hyperlink_string('Sheet1!A1'))
        parsed = parse_hlink(payload, (9, HLINK, payload))
        self.assertEqual(parsed['location'], 'Sheet1!A1')
        with self.assertRaises(ValueError):
            parse_hlink_tooltip(struct.pack('<H', 0x0801) + bytes(8) + b'\x00' * 2,
                                (9, HLINK_TOOLTIP, b''))
        with self.assertRaises(ValueError):
            parse_hlink(hlink(0x0008, hyperlink_string('x'), version=3),
                        (9, HLINK, b''))


if __name__ == '__main__':
    unittest.main()
