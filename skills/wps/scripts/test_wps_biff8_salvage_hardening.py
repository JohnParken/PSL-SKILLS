"""Adversarial, bounded synthetic fixtures for explicit BIFF8 salvage.

Maintenance-only Python stdlib; no Office application or real producer evidence.
Unknown BIFF records are never assumed to be non-cell data.
"""
import hashlib
import random
import struct
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_biff8 import workbook, rec, cell, formula, special
from test_wps_biff8_salvage import values, remove_global_date_marker
from wps_biff8 import parse_stream, ResourceLimitError, UnsupportedError


UNKNOWN_IDS = (0x7777, 0x6abc, 0x6bad, 0x7ffe, 0x7fff)
SEED = 20261101


class SalvageHardeningTests(unittest.TestCase):
    def test_deterministic_unknown_record_sizes_and_positions(self):
        randomizer = random.Random(SEED)
        baseline = values(parse_stream(workbook(), compatibility='data'))
        # Sample both empty and nonempty raw records; all remain within the
        # published BIFF8 record and metadata quotas. Never infer their meaning.
        for attempt in range(72):
            record_id = randomizer.choice(UNKNOWN_IDS)
            raw_length = randomizer.choice((0, 1, 2, 17, 63, 255, 512))
            payload = bytes(randomizer.randrange(256) for _ in range(raw_length))
            unknown = rec(record_id, payload)
            data = workbook(extra_global=unknown)
            with self.subTest(attempt=attempt, record_id=hex(record_id),
                              payload_length=raw_length):
                with self.assertRaises(UnsupportedError):
                    parse_stream(data, compatibility='data')
                out = parse_stream(data, compatibility='data',
                                   salvage_unknown_metadata=True)
                self.assertEqual(values(out), baseline)
                self.assertEqual(out['status'], 'partial')
                self.assertTrue(out['omissions_affect_cell_values'])
                self.assertFalse(out['saved_value_completeness_certified'])
                self.assertEqual(out['salvage_unknown_record_count'], 1)
                entry = next(m for m in out['uninterpreted_metadata']
                             if m['record_id'] == hex(record_id))
                self.assertEqual(entry['payload_hex'], payload.hex())
                self.assertEqual(entry['scope'], 'globals')
                self.assertTrue(entry['may_affect_cell_values'])

    def test_mixed_unknown_records_in_two_sheets_do_not_erase_known_caches(self):
        first = (rec(0x7777, b'\x00\x01') +
                 rec(0x0203, cell(0, 0) + struct.pack('<d', 10.5)) +
                 formula(0, 1, struct.pack('<d', 99.5)))
        second = (rec(0x0203, cell(4, 3) + struct.pack('<d', -7)) +
                  rec(0x6abc, b'\xff' * 9))
        data = workbook(sheet_records=[first, second], strings=[],
                        names=['first', 'second'])
        out = parse_stream(data, compatibility='data',
                           salvage_unknown_metadata=True)
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(out['salvage_unknown_record_count'], 2)
        self.assertEqual(out['sheets'][0]['cells']['A1']['value'], 10.5)
        self.assertEqual(out['sheets'][0]['cells']['B1']['value'], 99.5)
        self.assertEqual(out['sheets'][0]['cells']['B1']['cache_status'], 'unverified')
        self.assertEqual(out['sheets'][1]['cells']['D5']['value'], -7)
        self.assertEqual({m['scope'] for m in out['uninterpreted_metadata']
                          if m['name'] == 'UnknownBIFFSalvaged'},
                         {'first', 'second'})
        self.assertEqual(out['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')

    def test_unknown_record_between_formula_and_string_reports_missing_cache(self):
        formula_string = formula(0, 0, special(0))
        raw = workbook(sheet_records=[
            formula_string + rec(0x7777, b'unknown')], strings=[])
        out = parse_stream(raw, compatibility='data',
                           salvage_unknown_metadata=True)
        entry = out['sheets'][0]['cells']['A1']
        self.assertEqual(entry['cache_status'], 'missing')
        self.assertIsNone(entry['value'])
        self.assertTrue(any(issue['code'] == 'missing_formula_string_cache'
                            for issue in out['issues']))
        self.assertEqual(out['status'], 'partial')
        self.assertTrue(out['omissions_affect_cell_values'])

    def test_corrupted_unknown_record_header_never_salvaged(self):
        raw = bytearray(workbook(extra_global=rec(0x7777, b'ok')))
        offset = raw.index(rec(0x7777, b'ok'))
        for mutation in (b'\xff\xff', b'\xff\x7f'):
            changed = bytearray(raw)
            changed[offset+2:offset+4] = mutation
            with self.subTest(size=mutation.hex()), self.assertRaises(ValueError):
                parse_stream(bytes(changed), compatibility='data',
                             salvage_unknown_metadata=True)

    def test_unknown_record_metadata_quota_and_diagnostic_quota(self):
        raw = workbook(extra_global=rec(0x7777, b'a'*128) * 40)
        with patch('wps_biff8.MAX_METADATA_BYTES', 400):
            with self.assertRaises(ResourceLimitError):
                parse_stream(raw, compatibility='data',
                             salvage_unknown_metadata=True)
        with patch('wps_biff8.MAX_RECORDS', 10):
            with self.assertRaises(ResourceLimitError):
                parse_stream(raw, compatibility='data',
                             salvage_unknown_metadata=True)

    def test_known_sst_reference_outside_bounds_still_rejected(self):
        wrong_index = rec(0x00FD, cell(0, 0) + struct.pack('<I', 99))
        raw = workbook(sheet_records=[
            rec(0x7777, b'x') + wrong_index], strings=[])
        with self.assertRaisesRegex(ValueError, 'SST index'):
            parse_stream(raw, compatibility='data',
                         salvage_unknown_metadata=True)

    def test_duplicate_known_cells_still_rejected(self):
        cell_bytes = rec(0x0203, cell(0, 0) + struct.pack('<d', 12.5))
        raw = workbook(sheet_records=[
            cell_bytes + rec(0x7777, b'x') + cell_bytes], strings=[])
        with self.assertRaisesRegex(ValueError, 'Duplicate cell'):
            parse_stream(raw, compatibility='data',
                         salvage_unknown_metadata=True)

    def test_unknown_codepage_conflict_with_missing_date_stays_rejected(self):
        raw = workbook(extra_global=rec(0x0042, struct.pack('<H', 65001)) +
                       rec(0x0042, struct.pack('<H', 1200)))
        raw = remove_global_date_marker(raw)
        with self.assertRaisesRegex(ValueError, 'Conflicting CodePage'):
            parse_stream(raw, compatibility='data',
                         salvage_unknown_metadata=True)

    def test_unbound_trailing_record_never_ignored(self):
        # Even a syntactically valid unknown record cannot live outside
        # the bounded globals and worksheet substreams.
        raw = workbook() + rec(0x7777, b'trailing')
        with self.assertRaises(ValueError):
            parse_stream(raw, compatibility='data',
                         salvage_unknown_metadata=True)

    def test_input_byte_identity_after_all_bounded_variant_reads(self):
        original = workbook(extra_global=rec(0x7777, b'\x00\xff'))
        before = hashlib.sha256(original).hexdigest()
        for _ in range(4):
            out = parse_stream(original, compatibility='data',
                               salvage_unknown_metadata=True)
            self.assertEqual(out['salvage_unknown_record_count'], 1)
        self.assertEqual(hashlib.sha256(original).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
