"""Independent synthetic PLC fixtures; not real Office producer acceptance."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parents[1])]

import struct
import unittest

from read_wps import ParseError, Region, require
from wps_binary_textboxes import MAX_TEXTBOX_RECORDS, textbox_references


def plc_fixture(cps, records):
    """Encode literal CP and 22-byte FTXBXS arrays from the MS-DOC layout."""
    record_count = len(records)
    assert len(cps) == record_count + 1
    data = bytearray(4 + 26 * record_count)
    struct.pack_into('<' + 'I' * len(cps), data, 0, *cps)
    ftxbxs_start = 4 * len(cps)
    for index, record in enumerate(records):
        offset = ftxbxs_start + index * 22
        reusable, shape_id, destination, undo = record
        struct.pack_into('<H', data, offset + 8, reusable)
        struct.pack_into('<I', data, offset + 10, destination)
        struct.pack_into('<I', data, offset + 14, shape_id)
        struct.pack_into('<I', data, offset + 18, undo)
    return bytes(data)


def baseline_fixture():
    cps = (2, 5, 6, 9, 10)
    records = (
        (0, 700, 0x12345678, 0x87654321),
        (5, 0, 0xFFFFFFFF, 0xFFFFFFFF),
        (0, 800, 0xABCDEF01, 0x10203040),
        (2, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF),
    )
    units = [0x0061] * 12
    units[4] = 0x000D
    units[8] = 0x000D
    return plc_fixture(cps, records), units


class BinaryTextboxesTests(unittest.TestCase):
    def parse(self, data, units, anchors=None, story_start=100,
              story_count=12, base=500):
        if anchors is None:
            anchors = {}
        plc = Region(data, '1Table', base=base)
        return textbox_references(
            plc, story_start, story_count,
            lambda cp: units[cp - story_start], anchors, require)

    def assert_corrupt(self, data, units, **options):
        with self.assertRaises(ParseError) as caught:
            self.parse(data, units, **options)
        self.assertEqual(caught.exception.status, 'corrupt')
        return caught.exception

    def test_real_ranges_skip_spare_and_ignore_final_record_semantics(self):
        data, units = baseline_fixture()
        result = self.parse(data, units, {
            17: {'shape_id': 700, 'shape_index': 3},
            21: {'shape_id': 800, 'shape_index': 4},
        })
        self.assertEqual(result, [
            {
                'textbox_index': 1,
                'shape_id': 700,
                'text_range': {
                    'stream': 'WordDocument', 'cp_start': 102, 'cp_end': 105,
                },
                'source': {'stream': '1Table', 'offset': 520},
                'anchor_status': 'matched_spa_shape_id',
                'anchor_cp': 17,
            },
            {
                'textbox_index': 3,
                'shape_id': 800,
                'text_range': {
                    'stream': 'WordDocument', 'cp_start': 106, 'cp_end': 109,
                },
                'source': {'stream': '1Table', 'offset': 564},
                'anchor_status': 'matched_spa_shape_id',
                'anchor_cp': 21,
            },
        ])

    def test_missing_or_ambiguous_shape_matches_keep_ranges_unknown(self):
        data, units = baseline_fixture()
        no_anchors = self.parse(data, units)
        self.assertEqual([item['anchor_status'] for item in no_anchors],
                         ['unknown', 'unknown'])
        self.assertTrue(all('anchor_cp' not in item for item in no_anchors))

        duplicate_anchor = self.parse(data, units, {
            31: {'shape_id': 700}, 32: {'shape_id': 700},
            40: {'shape_id': 800},
        })
        self.assertEqual(duplicate_anchor[0]['anchor_status'], 'unknown')
        self.assertNotIn('anchor_cp', duplicate_anchor[0])
        self.assertEqual(duplicate_anchor[1]['anchor_status'],
                         'matched_spa_shape_id')
        self.assertEqual(duplicate_anchor[1]['anchor_cp'], 40)

    def test_duplicate_textbox_shape_ids_remain_unmatched(self):
        cps = (2, 5, 6, 9, 10)
        records = (
            (0, 700, 0, 0), (1, 0, 0, 0),
            (0, 700, 0, 0), (0, 0xFFFFFFFF, 0, 0),
        )
        _, units = baseline_fixture()
        units[4] = units[8] = 0x000D
        result = self.parse(plc_fixture(cps, records), units,
                            {31: {'shape_id': 700}})
        self.assertEqual([item['text_range'] for item in result], [
            {'stream': 'WordDocument', 'cp_start': 102, 'cp_end': 105},
            {'stream': 'WordDocument', 'cp_start': 106, 'cp_end': 109},
        ])
        self.assertEqual([item['anchor_status'] for item in result],
                         ['unknown', 'unknown'])
        self.assertTrue(all('anchor_cp' not in item for item in result))

    def test_rejects_bad_layout_and_zero_record_plc(self):
        for data in (bytes(29), bytes(4)):
            with self.subTest(size=len(data)):
                self.assert_corrupt(data, [0] * 12)

    def test_rejects_cp_order_and_story_bounds(self):
        records = ((0, 700, 0, 0), (1, 0, 0, 0),
                   (0, 800, 0, 0), (0, 0xFFFFFFFF, 0, 0))
        for cps in ((2, 5, 6, 9, 8), (2, 5, 6, 13, 14)):
            with self.subTest(cps=cps):
                self.assert_corrupt(plc_fixture(cps, records), [0x0061] * 12)

    def test_final_guard_terminal_can_exceed_story_without_being_read(self):
        data, units = baseline_fixture()
        guard = bytearray(data)
        struct.pack_into('<I', guard, 16, 0xFFFFFFFF)
        self.assertEqual(self.parse(guard, units), self.parse(data, units))

    def test_real_textbox_requires_terminal_cr_and_nonzero_shape_id(self):
        data, units = baseline_fixture()
        units[4] = 0x0062
        self.assert_corrupt(data, units)

        records = ((0, 0, 0, 0), (5, 0, 0, 0),
                   (0, 800, 0, 0), (2, 0xFFFFFFFF, 0, 0))
        self.assert_corrupt(plc_fixture((2, 5, 6, 9, 10), records),
                            [0x0061, 0x0061, 0x0061, 0x0061,
                             0x000D, 0x0061, 0x0061, 0x0061,
                             0x000D, 0x0061, 0x0061, 0x0061])

    def test_real_range_may_not_split_surrogate_pair_at_start(self):
        records = ((1, 0, 0, 0), (0, 700, 0, 0), (2, 0xFFFFFFFF, 0, 0))
        data = plc_fixture((1, 2, 5, 6), records)
        units = [0x0061] * 10
        units[1] = 0xD83D
        units[2] = 0xDE00
        units[4] = 0x000D
        error = self.assert_corrupt(data, units, story_count=10)
        self.assertIn('starts inside', str(error))

    def test_real_range_may_not_split_surrogate_pair_at_end(self):
        data, units = baseline_fixture()
        units[4] = 0xD83D
        units[5] = 0xDE00
        error = self.assert_corrupt(data, units)
        self.assertIn('ends inside', str(error))

    def test_anchor_shape_id_is_a_required_field(self):
        data, units = baseline_fixture()
        self.assert_corrupt(data, units, anchors={31: {}})

    def test_record_budget_fails_before_cp_expansion_or_text_reads(self):
        count = MAX_TEXTBOX_RECORDS + 1
        data = bytes(4 + 26 * count)
        calls = []
        plc = Region(data, '1Table', base=500)
        with self.assertRaises(ParseError) as caught:
            textbox_references(
                plc, 100, 1, lambda cp: calls.append(cp) or 0,
                {}, require)
        self.assertEqual(caught.exception.status, 'resource_limit')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
