"""Synthetic BIFF8 SST ExtRst retention; not producer compatibility evidence."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_biff8 import bof, cell, formula, rec, special
from test_wps_cfb import synthetic_cfb
from wps_biff8 import (ContinuedStrings, ResourceLimitError, UnsupportedError,
                       parse_stream, read_file)


def encoded_string(text, flags, runs=b'', ext=b''):
    raw = text.encode('utf-16le' if flags & 1 else 'latin1')
    units = len(raw) // 2 if flags & 1 else len(raw)
    output = bytearray(struct.pack('<HB', units, flags))
    if flags & 8:
        output.extend(struct.pack('<H', len(runs) // 4))
    if flags & 4:
        output.extend(struct.pack('<i', len(ext)))
    output.extend(raw)
    output.extend(runs)
    output.extend(ext)
    return bytes(output)


def workbook(raw_sst=b'', continuations=(), *, total=1, unique=1,
             sheet_records=None, font_count=4):
    globals_part = bof() + rec(0x22, b'\0\0') + rec(0xE0, bytes(20))
    globals_part += b''.join(rec(0x31, bytes(14) + b'\1\0A')
                             for _ in range(font_count))
    globals_part += rec(0xFC, struct.pack('<II', total, unique) + raw_sst)
    globals_part += b''.join(rec(0x3C, part) for part in continuations)
    if sheet_records is None:
        sheet_records = rec(0xFD, cell(0, 0) + struct.pack('<I', 0))
    bound_size = len(rec(0x85, struct.pack('<IBB', 0, 0, 0) + b'\1\0S'))
    eof = rec(0x0A)
    sheet_offset = len(globals_part) + bound_size + len(eof)
    bound = rec(0x85, struct.pack('<IBB', sheet_offset, 0, 0) + b'\1\0S')
    sheet = bof(0x10) + rec(0x81, b'\0\0') + sheet_records + eof
    return globals_part + bound + eof + sheet


def metadata_prefix(font_count=4):
    return (bof() + rec(0x22, b'\0\0') + rec(0xE0, bytes(20))
            + b''.join(rec(0x31, bytes(14) + b'\1\0A')
                       for _ in range(font_count)))


class PhoneticStringTests(unittest.TestCase):
    def test_all_supported_flag_combinations(self):
        for flags in (0, 1, 4, 5, 8, 9, 12, 13):
            with self.subTest(flags=flags):
                text = '中' if flags & 1 else 'A'
                runs = struct.pack('<HH', 0, 0) if flags & 8 else b''
                ext = b'\x00\xff' if flags & 4 else b''
                cursor = ContinuedStrings([(0, 0xFC,
                    encoded_string(text, flags, runs, ext))], 0)
                self.assertEqual(cursor.string(allow_rich=True,
                                              allow_phonetic=True), text)
                self.assertEqual(cursor.last_rich is not None, bool(flags & 8))
                self.assertEqual(cursor.last_phonetic is not None, bool(flags & 4))
                self.assertEqual(cursor.finish(), 1)

    def test_plain_and_rich_phonetic_strings_keep_text_and_raw_metadata(self):
        ext = bytes.fromhex('001122ff')
        result = parse_stream(workbook(encoded_string('Ab', 4, ext=ext)))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 'Ab')
        self.assertEqual(result['shared_string_phonetics'], [{
            'record_offset': len(metadata_prefix()), 'payload_offset': 8,
            'utf16_units': 2, 'sst_index': 0, 'raw_ext_hex': ext.hex()}])
        self.assertEqual([item['code'] for item in result['issues']],
                         ['phonetic_string_data_not_decoded'])

        runs = struct.pack('<HH', 0, 0)
        rich_result = parse_stream(workbook(
            encoded_string('Ab', 12, runs=runs, ext=ext)))
        self.assertEqual(rich_result['sheets'][0]['cells']['A1']['value'], 'Ab')
        self.assertEqual(rich_result['shared_string_formatting'][0]['runs_raw_hex'],
                         runs.hex())
        self.assertEqual(rich_result['shared_string_phonetics'][0]['raw_ext_hex'],
                         ext.hex())
        self.assertEqual(sum(item['code'] == 'phonetic_string_data_not_decoded'
                             for item in rich_result['issues']), 1)

    def test_every_raw_extension_split_keeps_following_string_header(self):
        ext = bytes.fromhex('1020304050')
        next_string = struct.pack('<HB', 1, 0) + b'Z'
        for split in range(len(ext) + 1):
            with self.subTest(split=split):
                first = struct.pack('<HB', 1, 4) + struct.pack('<i', len(ext))
                first += b'X' + ext[:split]
                second = ext[split:] + next_string
                cursor = ContinuedStrings([(100, 0xFC, first),
                                           (200, 0x3C, second)], 0)
                self.assertEqual(cursor.string(allow_phonetic=True), 'X')
                self.assertEqual(cursor.last_phonetic, {
                    'record_offset': 100, 'payload_offset': 0,
                    'utf16_units': 1, 'ext_bytes': ext})
                self.assertEqual(cursor.string(allow_phonetic=True), 'Z')
                self.assertIsNone(cursor.last_phonetic)
                self.assertEqual(cursor.finish(), 2)

    def test_character_encoding_switch_surrogate_and_raw_extension_continuations(self):
        ext = bytes.fromhex('deadbeef01')
        first = struct.pack('<HB', 5, 4) + struct.pack('<i', len(ext)) + b'A'
        first_continue = b'\1' + '中'.encode('utf-16le') + b'\x3d\xd8'
        second_continue = b'\1\x00\xde' + 'B'.encode('utf-16le') + ext[:2]
        next_string = struct.pack('<HB', 1, 0) + b'Z'
        cursor = ContinuedStrings([
            (10, 0xFC, first), (20, 0x3C, first_continue),
            (30, 0x3C, second_continue),
            (40, 0x3C, ext[2:] + next_string)], 0)
        self.assertEqual(cursor.string(allow_phonetic=True), 'A中😀B')
        self.assertEqual(cursor.last_phonetic['ext_bytes'], ext)
        self.assertEqual(cursor.last_phonetic['utf16_units'], 5)
        self.assertEqual(cursor.string(allow_phonetic=True), 'Z')
        self.assertEqual(cursor.finish(), 4)

    def test_combined_run_and_extension_budget_fails_before_body_read(self):
        runs = struct.pack('<HH', 0, 0)
        header = struct.pack('<HBHi', 1, 12, 1, 4)
        cursor = ContinuedStrings([(0, 0xFC,
            header + b'A' + runs + b'1234')], 0)
        with self.assertRaises(ResourceLimitError):
            cursor.string(allow_rich=True, metadata_budget=7,
                          allow_phonetic=True)
        self.assertEqual(cursor.position, len(header))
        self.assertIsNone(cursor.last_rich)
        self.assertIsNone(cursor.last_phonetic)

        header = struct.pack('<HBHi', 1, 12, 1, 1)
        cursor = ContinuedStrings([(0, 0xFC, header)], 0)
        with self.assertRaises(ResourceLimitError):
            cursor.string(allow_rich=True, allow_phonetic=True,
                          metadata_entries_remaining=1)
        self.assertEqual(cursor.position, len(header))

    def test_fixed_header_length_sign_and_extension_truncation(self):
        fixed = struct.pack('<HB', 1, 4) + struct.pack('<i', 2)
        for split in range(3, len(fixed)):
            cursor = ContinuedStrings([(0, 0xFC, fixed[:split]),
                                       (10, 0x3C, fixed[split:] + b'A12')], 0)
            with self.subTest(split=split), self.assertRaisesRegex(
                    ValueError, 'fixed header crosses record boundary'):
                cursor.string(allow_phonetic=True)
            self.assertEqual(cursor.index, 0)

        negative = struct.pack('<HB', 1, 4) + struct.pack('<i', -1) + b'A'
        with self.assertRaisesRegex(ValueError, 'Negative phonetic'):
            ContinuedStrings([(0, 0xFC, negative)], 0).string(
                allow_phonetic=True)

        truncated = struct.pack('<HB', 1, 4) + struct.pack('<i', 4) + b'A12'
        with self.assertRaisesRegex(ValueError, 'Missing Continue'):
            ContinuedStrings([(0, 0xFC, truncated)], 0).string(
                allow_phonetic=True)

    def test_reserved_flags_and_formula_string_default_remain_rejected(self):
        for flags in (2, 3, 6, 7, 10, 11, 14, 15, 16, 0x84):
            cursor = ContinuedStrings([(0, 0xFC,
                struct.pack('<HB', 0, flags))], 0)
            with self.subTest(flags=flags), self.assertRaises(UnsupportedError):
                cursor.string(allow_rich=True, allow_phonetic=True)

        formula_string = struct.pack('<HB', 2, 4) + struct.pack('<i', 0) + b'AB'
        sheet = formula(0, 0, special(0)) + rec(0x0207, formula_string)
        with self.assertRaises(UnsupportedError):
            parse_stream(workbook(b'', total=0, unique=0,
                                  sheet_records=sheet))

        cursor = ContinuedStrings([
            (100, 0xFC, struct.pack('<HB', 2, 0) + b'A'),
            (200, 0x3C, b'\2x')], 0)
        with self.assertRaisesRegex(ValueError, 'Invalid Continue encoding flag') as caught:
            cursor.string()
        self.assertEqual(caught.exception.location, {
            'record_offset': 200, 'payload_offset': 0, 'record_id': '0x3c'})

    def test_public_cfb_reader_returns_partial_with_additive_phonetics(self):
        ext = bytes.fromhex('0100fe7f')
        data = workbook(encoded_string('AB', 4, ext=ext))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'phonetic.cfb'
            path.write_bytes(synthetic_cfb({'Workbook': data}))
            result = read_file(path)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 'AB')
        self.assertEqual(result['shared_string_phonetics'][0]['raw_ext_hex'],
                         ext.hex())
        self.assertNotIn('ext_bytes', result['shared_string_phonetics'][0])
        self.assertEqual(sum(item['code'] == 'phonetic_string_data_not_decoded'
                             for item in result['issues']), 1)

    def test_shared_rich_and_phonetic_entry_count_is_bounded(self):
        entry = encoded_string('', 12, runs=struct.pack('<HH', 0, 0), ext=b'')

        def parts(count):
            payload = entry * count
            return [payload[start:start + 7800]
                    for start in range(0, len(payload), 7800)]

        accepted = parts(2048)
        result = parse_stream(workbook(accepted[0], accepted[1:],
            total=2048, unique=2048, sheet_records=b''))
        self.assertEqual(len(result['shared_string_formatting']), 2048)
        self.assertEqual(len(result['shared_string_phonetics']), 2048)

        rejected = parts(2049)
        with self.assertRaises(ResourceLimitError):
            parse_stream(workbook(rejected[0], rejected[1:],
                total=2049, unique=2049, sheet_records=b''))


if __name__ == '__main__':
    unittest.main()
