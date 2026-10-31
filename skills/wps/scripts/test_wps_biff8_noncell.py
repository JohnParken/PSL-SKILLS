"""Strictly scoped BIFF8 non-cell compatibility regressions.

Constructed records are synthetic MS-XLS fixtures, never WPS producer evidence.
"""
import struct
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_biff8 import workbook, rec, cell, formula
from wps_biff8 import parse_stream, UnsupportedError
from wps_biff8_noncell import inspect_noncell


def prepend_global_record(book, extra):
    # The first 20 bytes are global BOF. Update each BoundSheet8 pointer
    # before its first EOF; no unrelated offset or record is rewritten.
    stream = bytearray(book[:20] + extra + book[20:])
    at = 20 + len(extra)
    while at < len(stream):
        kind, length = struct.unpack_from('<HH', stream, at)
        if kind == 0x0085:
            original = struct.unpack_from('<I', stream, at + 4)[0]
            struct.pack_into('<I', stream, at + 4, original + len(extra))
        if kind == 0x000a:
            break
        at += 4 + length
    return bytes(stream)


def insert_after_date1904(book, extra):
    # Global Date1904 is the first ordinary record after BOF in this fixture.
    at = 20
    _, length = struct.unpack_from('<HH', book, at)
    at += 4 + length
    stream = bytearray(book[:at] + extra + book[at:])
    pos = at + len(extra)
    while pos < len(stream):
        kind, size = struct.unpack_from('<HH', stream, pos)
        if kind == 0x0085:
            original = struct.unpack_from('<I', stream, pos + 4)[0]
            struct.pack_into('<I', stream, pos + 4, original + len(extra))
        if kind == 0x000a:
            break
        pos += 4 + size
    return bytes(stream)


def prepend_sheet_record(book, extra):
    stream = bytearray(book)
    at = 20
    while at < len(stream):
        kind, size = struct.unpack_from('<HH', stream, at)
        if kind == 0x0085:
            start = struct.unpack_from('<I', stream, at + 4)[0]
            position = start + 20   # directly after Worksheet BOF
            return bytes(stream[:position] + extra + stream[position:])
        if kind == 0x000a:
            break
        at += 4 + size
    raise AssertionError('Fixture has no worksheet')


def cell_semantics(result):
    # Adding a record necessarily changes later physical BIFF source offsets.
    # Exclude ONLY those expected physical locations, not typed cells/caches.
    return [{address: {key: value for key, value in cell.items()
                       if key not in ('record_offset', 'cache_record_offset')}
             for address, cell in sheet['cells'].items()}
            for sheet in result['sheets']]


def merge_semantics(result):
    return [[{key: val for key, val in item.items() if key != 'record_offset'}
             for item in sheet['merges']] for sheet in result['sheets']]


class NonCellRecordsTests(unittest.TestCase):
    def setUp(self):
        self.original = workbook()
        # Single-sheet fixture lets the inline worksheet record be inserted
        # without rebuilding unrelated subsequent BoundSheet8 offsets.
        self.single = workbook(sheet_records=[
            formula(3, 1, struct.pack('<d', 42))], strings=[])

    def test_first_writeprotect_retained_without_changing_saved_values(self):
        incoming = prepend_global_record(self.original, rec(0x0086))
        with self.assertRaises(UnsupportedError):
            parse_stream(incoming, compatibility='strict')
        before = parse_stream(self.original, compatibility='data')
        after = parse_stream(incoming, compatibility='data')
        self.assertEqual(cell_semantics(before), cell_semantics(after))
        self.assertEqual(merge_semantics(before), merge_semantics(after))
        self.assertEqual(after['sheets'][0]['cells']['A1']['record_offset']
                         - before['sheets'][0]['cells']['A1']['record_offset'],
                         len(rec(0x0086)))
        self.assertEqual(before['cell_count'], after['cell_count'])
        self.assertEqual(after['sheets'][0]['offset'] - before['sheets'][0]['offset'],
                         len(rec(0x0086)))
        self.assertEqual(after['status'], 'partial')
        saved = [m for m in after['uninterpreted_metadata'] if m['record_id'] == '0x86']
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0]['structure']['write_protected_raw'])
        self.assertEqual(saved[0]['payload_hex'], '')
        self.assertTrue(any(x['code'] == 'non_cell_metadata_not_decoded' for x in after['issues']))

    def test_writeprotect_wrong_length_late_or_duplicate_rejected(self):
        for item in (prepend_global_record(self.original, rec(0x0086, b'\0')),
                     insert_after_date1904(self.original, rec(0x0086)),
                     prepend_global_record(self.original, rec(0x0086) + rec(0x0086))):
            with self.subTest(length=len(item)):
                with self.assertRaises(ValueError):
                    parse_stream(item, compatibility='data')

    def test_uncalced_readable_without_recomputing_formula(self):
        incoming = prepend_sheet_record(self.single, rec(0x005e, b'\0\0'))
        before = parse_stream(self.single, compatibility='data')
        after = parse_stream(incoming, compatibility='data')
        self.assertEqual(cell_semantics(before), cell_semantics(after))
        self.assertEqual(merge_semantics(before), merge_semantics(after))
        self.assertEqual(after['sheets'][0]['cells']['B4']['record_offset']
                         - before['sheets'][0]['cells']['B4']['record_offset'],
                         len(rec(0x005e, b'\0\0')))
        self.assertEqual(after['status'], 'partial')
        self.assertTrue(any(x['code'] == 'saved_formula_recalculation_pending'
                            for x in after['issues']))
        record = next(m for m in after['uninterpreted_metadata']
                      if m['record_id'] == '0x5e')
        self.assertTrue(record['structure']['saved_formula_recalculation_pending'])
        self.assertEqual(record['structure']['cache_freshness'], 'unverified')
        value = after['sheets'][0]['cells']['B4']
        self.assertEqual(value['cache_status'], 'unverified')
        self.assertEqual(value['value'], 42)

    def test_uncalced_invalid_reserved_length_order_or_duplicate_rejected(self):
        base = self.single
        valid = rec(0x005e, b'\0\0')
        malformed = [rec(0x005e), rec(0x005e, b'\x01\0'),
                     rec(0x005e, b'\0\0\0'), valid + valid]
        for item in malformed:
            with self.subTest(payload=item):
                with self.assertRaises(ValueError):
                    parse_stream(prepend_sheet_record(base, item), compatibility='data')
        # After worksheet WsBool is not valid for the first-position Uncalced.
        sheet_begin = base.find(rec(0x0081, b'\0\0'))
        self.assertGreaterEqual(sheet_begin, 0)
        late = base[:sheet_begin+6] + valid + base[sheet_begin+6:]
        with self.assertRaises(ValueError):
            parse_stream(late, compatibility='data')
        with self.assertRaises(UnsupportedError):
            parse_stream(prepend_sheet_record(base, valid), compatibility='strict')

    def test_unknown_and_safety_records_still_fail_closed(self):
        for kind in (0x7777, 0x01ae, 0x003c):
            with self.subTest(record=hex(kind)):
                with self.assertRaises(ValueError):
                    parse_stream(prepend_global_record(self.original, rec(kind)),
                                 compatibility='data')
        # Encrypted workbook must never become readable via a non-cell rule.
        with self.assertRaises(ValueError):
            parse_stream(prepend_global_record(self.original, rec(0x002f, b'\0\0')),
                         compatibility='data')
        with self.assertRaises(ValueError):
            parse_stream(prepend_sheet_record(self.original, rec(0x0203, b'\0\0')),
                         compatibility='data')

    def test_noncell_policy_rejects_wrong_scope_and_all_unknown_ids(self):
        for scope, kind in (('globals', 0x005e), ('worksheet', 0x0086),
                            ('globals', 0x9999), ('worksheet', 0x9999)):
            with self.assertRaises(ValueError):
                inspect_noncell(kind, b'', scope)


if __name__ == '__main__':
    unittest.main()
