"""Conservative MS-XLS BIFF8 CodePage declaration compatibility.

Data mode accepts only enumerated Microsoft CODEPG identifiers as *metadata*.
No CodePage-selected decoding, formula execution, Date1904 fallback, or Word edits.
Synthetic record fixtures are not native WPS producer evidence.
"""
import copy
import struct
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_biff8 import workbook, rec, cell
from test_wps_biff_reading import label
from wps_biff8 import parse_stream


KNOWN_ADDITIONAL_CODES = (874, 949, 950, 1250, 1251)


def _literal_cells(result):
    # Record insertion changes physical offsets, not saved cell semantics.
    return [{coordinate: {
        key: value for key, value in entry.items()
        if key not in ('record_offset', 'cache_record_offset')}
        for coordinate, entry in sheet['cells'].items()}
        for sheet in result['sheets']]


class BiffCodePageTests(unittest.TestCase):
    def test_known_declarations_are_metadata_only_and_values_unchanged(self):
        baseline = parse_stream(workbook(), compatibility='data')
        original = copy.deepcopy(_literal_cells(baseline))
        for codepage in KNOWN_ADDITIONAL_CODES:
            with self.subTest(codepage=codepage):
                result = parse_stream(
                    workbook(extra_global=rec(0x0042, struct.pack('<H', codepage))),
                    compatibility='data')
                self.assertEqual(_literal_cells(result), original)
                self.assertEqual(result['date_system'], '1900')
                self.assertEqual(result['code_page_raw'], codepage)
                self.assertEqual(result['status'], 'partial')
                matching = [x for x in result['issues']
                            if x['code'] == 'codepage_metadata_not_applied']
                self.assertEqual(len(matching), 1)
                self.assertEqual(matching[0]['code_page'], codepage)
                self.assertEqual(result['sheets'][0]['cells']['C4']['cache_status'],
                                 'unverified')
                metadata = [item for item in result['uninterpreted_metadata']
                            if item['record_id'] == '0x42']
                self.assertEqual(len(metadata), 1)
                self.assertEqual(metadata[0]['payload_hex'],
                                 struct.pack('<H', codepage).hex())

    def test_codepage_must_not_select_legacy_byte_encoding(self):
        # In BIFF8 compressed XLUnicodeString, 0x80 is U+0080; it is NOT
        # decoded through CP1251, CP1250, Big5, Korean or Thai code pages.
        expected = '\x80é中'
        sheet_records = [label(0, 0, '\x80é', compressed=True) +
                         label(0, 1, '中')]
        for codepage in KNOWN_ADDITIONAL_CODES:
            with self.subTest(codepage=codepage):
                parsed = parse_stream(workbook(
                    sheet_records=sheet_records, strings=[],
                    extra_global=rec(0x0042, struct.pack('<H', codepage))),
                    compatibility='data')
                self.assertEqual(
                    parsed['sheets'][0]['cells']['A1']['value'] +
                    parsed['sheets'][0]['cells']['B1']['value'], expected)

    def test_strict_and_legacy_explicit_modes_remain_1200_only(self):
        for mode in ('strict', 'legacy_excel_clsid'):
            for codepage in KNOWN_ADDITIONAL_CODES:
                with self.subTest(mode=mode, codepage=codepage):
                    with self.assertRaisesRegex(ValueError, 'Unicode CodePage 1200'):
                        parse_stream(workbook(
                            extra_global=rec(0x0042, struct.pack('<H', codepage))),
                            compatibility=mode)
        self.assertEqual(parse_stream(workbook(
            extra_global=rec(0x0042, struct.pack('<H', 1200))),
            compatibility='strict')['code_page_raw'], 1200)

    def test_unknown_and_bad_payload_still_rejected(self):
        for raw in (b'', b'\x01', b'\x00\x00', b'\xff\xff',
                    struct.pack('<H', 65001), struct.pack('<H', 1201),
                    struct.pack('<H', 1251) + b'\x00'):
            with self.subTest(payload=raw.hex()):
                with self.assertRaises(ValueError):
                    parse_stream(workbook(extra_global=rec(0x0042, raw)),
                                 compatibility='data')

    def test_conflicting_declarations_still_rejected(self):
        extra = rec(0x0042, struct.pack('<H', 950))
        extra += rec(0x0042, struct.pack('<H', 1251))
        with self.assertRaisesRegex(ValueError, 'Conflicting CodePage'):
            parse_stream(workbook(extra_global=extra), compatibility='data')

    def test_missing_Date1904_is_not_inferred_from_known_codepage(self):
        input_file = workbook(extra_global=rec(0x0042, struct.pack('<H', 950)))
        at = 20
        self.assertEqual(input_file[at:at+2], b'\x22\x00')
        without_date = input_file[:at] + input_file[at+6:]
        # BoundSheet8 points 6 bytes after removed Date1904; update actual
        # substream position ONLY, not the contents or formula cache.
        data = bytearray(without_date)
        i = at
        while i < len(data):
            kind, size = struct.unpack_from('<HH', data, i)
            if kind == 0x0085:
                current = struct.unpack_from('<I', data, i+4)[0]
                struct.pack_into('<I', data, i+4, current - 6)
            if kind == 0x000A:
                break
            i += 4 + size
        with self.assertRaisesRegex(ValueError, 'Date1904'):
            parse_stream(bytes(data), compatibility='data')

    def test_wrong_biff_version_and_cell_bounds_not_hidden_by_codepage(self):
        data = bytearray(workbook(extra_global=rec(0x0042, struct.pack('<H', 950))))
        struct.pack_into('<H', data, 4, 0x0500)
        with self.assertRaisesRegex(ValueError, 'BIFF version'):
            parse_stream(bytes(data), compatibility='data')
        bad_cell = rec(0x0203, cell(65535, 256) + struct.pack('<d', 1))
        with self.assertRaises(ValueError):
            parse_stream(workbook(sheet_records=[bad_cell],
                                  extra_global=rec(0x0042, struct.pack('<H', 950))),
                         compatibility='data')


if __name__ == '__main__':
    unittest.main()
