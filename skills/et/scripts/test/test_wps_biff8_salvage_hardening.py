"""Adversarial, bounded synthetic fixtures for the tolerant BIFF8 reader.

Unknown records are always retained raw with uncertain coverage; a malformed
record never discards cells parsed before or after it. Hard safety boundaries
(bounded framing, encryption, FAT allocation) are unchanged. Maintenance-only
Python stdlib; no Office application or real producer evidence.
"""
import hashlib
import random
import struct
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_biff8 import workbook, rec, cell, formula, special
from test_wps_biff8_salvage import values, remove_global_date_marker
from wps_biff8 import parse_stream, ResourceLimitError, UnsupportedError


UNKNOWN_IDS = (0x7777, 0x6abc, 0x6bad, 0x7ffe, 0x7fff)
SEED = 20261101


def issue_codes(result):
    return [item.get('code') for item in result.get('issues', ())]


class ToleranceHardeningTests(unittest.TestCase):
    def test_deterministic_unknown_record_sizes_and_positions(self):
        randomizer = random.Random(SEED)
        baseline = values(parse_stream(workbook()))
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
                out = parse_stream(data)
                self.assertEqual(values(out), baseline)
                self.assertEqual(out['status'], 'partial')
                self.assertTrue(out['omissions_affect_cell_values'])
                self.assertEqual(out['salvage_unknown_record_count'], 1)
                entry = next(m for m in out['uninterpreted_metadata']
                             if m['record_id'] == hex(record_id))
                self.assertEqual(entry['payload_hex'], payload.hex())
                self.assertEqual(entry['scope'], 'globals')
                self.assertTrue(entry['may_affect_cell_values'])
                self.assertIn('unknown_biff_record_salvaged', issue_codes(out))

    def test_mixed_unknown_records_in_two_sheets_do_not_erase_known_caches(self):
        first = (rec(0x7777, b'\x00\x01') +
                 rec(0x0203, cell(0, 0) + struct.pack('<d', 10.5)) +
                 formula(0, 1, struct.pack('<d', 99.5)))
        second = (rec(0x0203, cell(4, 3) + struct.pack('<d', -7)) +
                  rec(0x6abc, b'\xff' * 9))
        data = workbook(sheet_records=[first, second], strings=[],
                        names=['first', 'second'])
        out = parse_stream(data)
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
        out = parse_stream(raw)
        entry = out['sheets'][0]['cells']['A1']
        self.assertEqual(entry['cache_status'], 'missing')
        self.assertIsNone(entry['value'])
        self.assertIn('missing_formula_string_cache', issue_codes(out))
        self.assertEqual(out['status'], 'partial')
        self.assertTrue(out['omissions_affect_cell_values'])

    def test_corrupted_unknown_record_header_degrades_not_crash(self):
        raw = bytearray(workbook(extra_global=rec(0x7777, b'ok')))
        offset = raw.index(rec(0x7777, b'ok'))
        for mutation in (b'\xff\xff', b'\xff\x7f'):
            changed = bytearray(raw)
            changed[offset+2:offset+4] = mutation
            with self.subTest(size=mutation.hex()), self.assertRaises(ValueError):
                # A corrupted declared payload above 8224 bytes is a hard
                # bounded-framing refusal, never a silent misparse.
                parse_stream(bytes(changed))

    def test_unknown_record_metadata_quota_and_diagnostic_quota(self):
        raw = workbook(extra_global=rec(0x7777, b'a'*128) * 40)
        with patch('wps_biff8.MAX_METADATA_BYTES', 400):
            result = parse_stream(raw)
            self.assertEqual(result['status'], 'partial')
            self.assertIn('metadata_budget_truncated', issue_codes(result))
            self.assertGreater(result['metadata_truncated_count'], 0)
            self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
        with patch('wps_biff8.MAX_RECORDS', 10), self.assertRaises(ResourceLimitError):
            parse_stream(raw)

    def test_diagnostic_cap_truncates_instead_of_raising(self):
        unknown = b''.join(rec(0x7000 + index, bytes((index,))) for index in range(20))
        with patch('wps_biff8.MAX_ISSUES', 3):
            result = parse_stream(workbook(extra_global=unknown))
        self.assertEqual(len(result['issues']), 3)
        self.assertTrue(result['issues_truncated'])
        self.assertGreater(result['truncated_issue_count'], 0)
        self.assertEqual(result['status'], 'partial')

    def test_known_sst_reference_outside_bounds_skips_cell_and_keeps_others(self):
        wrong_index = rec(0x00FD, cell(0, 0) + struct.pack('<I', 99))
        good = rec(0x0203, cell(0, 1) + struct.pack('<d', 3.5))
        raw = workbook(sheet_records=[rec(0x7777, b'x') + wrong_index + good],
                       strings=[])
        out = parse_stream(raw)
        self.assertEqual(out['sheets'][0]['cells']['B1']['value'], 3.5)
        self.assertNotIn('A1', out['sheets'][0]['cells'])
        self.assertIn('sst_index_out_of_range_omitted', issue_codes(out))
        self.assertTrue(out['omissions_affect_cell_values'])

    def test_duplicate_known_cells_skip_later_record_without_aborting(self):
        cell_bytes = rec(0x0203, cell(0, 0) + struct.pack('<d', 12.5))
        raw = workbook(sheet_records=[
            cell_bytes + rec(0x7777, b'x') + cell_bytes], strings=[])
        out = parse_stream(raw)
        self.assertEqual(out['sheets'][0]['cells']['A1']['value'], 12.5)
        self.assertIn('duplicate_cell_omitted', issue_codes(out))
        self.assertEqual(out['cell_count'], 1)

    def test_non_static_defined_name_is_retained_without_execution(self):
        name_record = rec(0x0018, bytes(27))
        raw = workbook(extra_global=name_record)
        for salvage in (False, True):
            with self.subTest(salvage=salvage):
                out = parse_stream(raw, salvage_unknown_metadata=salvage)
                self.assertEqual(out['status'], 'partial')
                self.assertIn('defined_name_retained_opaque', issue_codes(out))
                retained = [m for m in out['uninterpreted_metadata']
                            if m['name'] == 'DefinedNameOpaque']
                self.assertEqual(bytes.fromhex(retained[0]['payload_hex']),
                                 bytes(27))
                self.assertEqual(out['sheets'][0]['cells']['A1']['value'],
                                 '部门')
                self.assertFalse(out['external_links_followed'])

    def test_unknown_codepage_conflict_with_missing_date_degrades(self):
        raw = workbook(extra_global=rec(0x0042, struct.pack('<H', 65001)) +
                       rec(0x0042, struct.pack('<H', 1200)))
        raw = remove_global_date_marker(raw)
        out = parse_stream(raw)
        self.assertEqual(out['status'], 'partial')
        self.assertIn('conflicting_codepage_declaration', issue_codes(out))
        self.assertIn('date_system_missing', issue_codes(out))
        self.assertEqual(out['date_system'], 'unknown')
        self.assertEqual(out['sheets'][0]['cells']['A1']['value'], '部门')

    def test_unbound_trailing_records_are_retained_raw(self):
        raw = workbook() + rec(0x7777, b'trailing')
        out = parse_stream(raw)
        self.assertEqual(out['status'], 'partial')
        self.assertIn('unbound_trailing_records_retained', issue_codes(out))
        retained = [item for item in out['uninterpreted_metadata']
                    if item['scope'] == 'trailing']
        self.assertEqual(bytes.fromhex(retained[0]['payload_hex']), b'trailing')
        self.assertEqual(len(out['sheets'][0]['cells']), 18)

    def test_zero_padding_after_final_eof_is_ignored_with_issue(self):
        out = parse_stream(workbook() + bytes(64))
        self.assertEqual(out['status'], 'partial')
        self.assertIn('zero_trailing_bytes_ignored', issue_codes(out))
        self.assertEqual(len(out['sheets'][0]['cells']), 18)

    def test_input_byte_identity_after_all_bounded_variant_reads(self):
        original = workbook(extra_global=rec(0x7777, b'\x00\xff'))
        before = hashlib.sha256(original).hexdigest()
        for _ in range(4):
            out = parse_stream(original)
            self.assertEqual(out['salvage_unknown_record_count'], 1)
        self.assertEqual(hashlib.sha256(original).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
