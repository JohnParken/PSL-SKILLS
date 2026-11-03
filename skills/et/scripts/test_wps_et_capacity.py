"""ET BIFF8 capacity and corruption checks; synthetic, not producer evidence."""
from pathlib import Path
import random
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_biff8_merges import validate_merge_ranges
from wps_biff8 import parse_stream, ResourceLimitError, reconstruct_headers
from test_wps_biff8 import workbook, rec, cell


def merge_records(ranges):
    # Each record remains within BIFF8's 8224-byte payload limit.
    chunks = []
    for start in range(0, len(ranges), 1000):
        group = ranges[start:start + 1000]
        chunks.append(rec(0xE5, struct.pack('<H', len(group))
                          + b''.join(struct.pack('<4H', *bounds) for bounds in group)))
    return b''.join(chunks)


class MergeSweepTests(unittest.TestCase):
    def test_disjoint_adjacency_and_full_height_without_expansion(self):
        validate_merge_ranges([(0, 65535, 0, 127), (0, 65535, 128, 255)])
        validate_merge_ranges([(0, 3, 0, 1), (4, 8, 0, 1), (0, 3, 2, 3)])

    def test_overlap_duplicate_and_boundary_cell_are_rejected(self):
        for ranges in ([(0, 4, 0, 4), (4, 8, 4, 8)],
                       [(0, 1, 0, 1), (0, 1, 0, 1)],
                       [(0, 65535, 0, 255), (100, 101, 5, 6)]):
            with self.subTest(ranges=ranges), self.assertRaisesRegex(ValueError, 'Overlapping'):
                validate_merge_ranges(ranges)

    def test_random_small_sets_match_independent_cell_enumeration(self):
        rng = random.Random(271027)
        for _ in range(200):
            ranges = []
            occupied = set()
            overlaps = False
            for _ in range(rng.randrange(1, 20)):
                first, last = sorted(rng.sample(range(8), 2))
                left, right = sorted(rng.sample(range(8), 2))
                ranges.append((first, last, left, right))
                area = {(row, col) for row in range(first, last + 1)
                        for col in range(left, right + 1)}
                overlaps |= bool(occupied & area)
                occupied |= area
            if overlaps:
                with self.assertRaises(ValueError):
                    validate_merge_ranges(ranges)
            else:
                validate_merge_ranges(ranges)

    def test_invalid_bounds_are_rejected(self):
        for bounds in ((0, 0, 0, 0), (-1, 1, 0, 1), (0, 65536, 0, 1),
                       (0, 1, 0, 256), (True, 1, 0, 1)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                validate_merge_ranges([bounds])


class CapacityIntegrationTests(unittest.TestCase):
    def test_many_merges_preserve_ranges_and_nonanchor_data(self):
        ranges = [(row, row, 0, 1) for row in range(2000)]
        payload = (rec(0x203, cell(0, 0) + struct.pack('<d', 10))
                   + rec(0x203, cell(0, 1) + struct.pack('<d', 20)) + merge_records(ranges))
        result = parse_stream(workbook([payload], strings=[]))
        sheet = result['sheets'][0]
        self.assertEqual(len(sheet['merges']), 2000)
        self.assertEqual(sheet['merges'][-1]['range'], 'A2000:B2000')
        self.assertEqual(sheet['cells']['A1']['value'], 10)
        self.assertEqual(sheet['cells']['B1']['value'], 20)

    def test_overlap_across_separate_records_is_rejected(self):
        payload = merge_records([(0, 2, 0, 2)]) + merge_records([(2, 3, 2, 4)])
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            parse_stream(workbook([payload], strings=[]))

    def test_merge_budget_has_resource_limit_status(self):
        with patch('wps_biff8.MAX_MERGES', 1), self.assertRaises(ResourceLimitError):
            parse_stream(workbook([merge_records([(0, 0, 0, 1), (1, 1, 0, 1)])], strings=[]))

    def test_full_two_hundred_thousand_distinct_shared_strings(self):
        strings = ['编号' + str(index) for index in range(200000)]
        # Build SST records explicitly with continuation only between strings.
        globals_part = (rec(0x809, struct.pack('<4H2I', 0x600, 5, 0x2013, 1997, 1, 6))
                        + rec(0x22, bytes(2)) + rec(0xE0, bytes(20)))
        chunks = [bytearray(struct.pack('<ii', len(strings), len(strings)))]
        for value in strings:
            raw = value.encode('utf-16le')
            encoded = struct.pack('<HB', len(value), 1) + raw
            if len(chunks[-1]) + len(encoded) > 8000:
                chunks.append(bytearray())
            chunks[-1].extend(encoded)
        globals_part += b''.join(rec(0xFC if index == 0 else 0x3C, bytes(chunk))
                                 for index, chunk in enumerate(chunks))
        bound = rec(0x85, struct.pack('<IBB', len(globals_part) + 17, 0, 0) + b'\1\0S')
        sheet = (rec(0x809, struct.pack('<4H2I', 0x600, 0x10, 0x2013, 1997, 1, 6))
                 + rec(0x81, bytes(2)) + rec(0xFD, cell(0, 0) + struct.pack('<I', 199999)) + rec(0xA))
        result = parse_stream(globals_part + bound + rec(0xA) + sheet)
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '编号199999')

    def test_full_merge_capacity_is_bounded_without_cell_expansion(self):
        ranges = [(row, row, col, col + 1) for row in range(512) for col in range(0, 256, 2)]
        result = parse_stream(workbook([merge_records(ranges)], strings=[]))
        self.assertEqual(len(result['sheets'][0]['merges']), 65536)
        self.assertEqual(result['cell_count'], 0)
        from read_wps import encode_result
        self.assertIsInstance(encode_result(result), str)
        ranges.append((512, 512, 0, 1))
        with self.assertRaises(ResourceLimitError):
            parse_stream(workbook([merge_records(ranges)], strings=[]))

    def test_oversized_sst_count_fails_before_string_allocation(self):
        from wps_biff8 import records
        data = bytearray(workbook([b''], strings=[]))
        offset = next(offset for offset, kind, _ in records(bytes(data)) if kind == 0xFC)
        struct.pack_into('<ii', data, offset + 4, 200001, 200001)
        with self.assertRaises(ResourceLimitError):
            parse_stream(bytes(data))

    def test_small_header_not_blocked_by_merges_elsewhere(self):
        ranges = [(row, row, 0, 1) for row in range(11000)]
        result = parse_stream(workbook([merge_records(ranges)], strings=[]))
        schema = reconstruct_headers(result, '中文表1', 'A1:B1', '1')
        self.assertEqual(len(schema['columns']), 2)
