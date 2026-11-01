"""MS-XLS Print_Titles real-producer PtgMemFunc regression.

A bounded 47-byte Lbl test vector is independently published in ExaDev
 documents.js print-names.test.ts as bytes from a LibreOffice-produced BIFF8
.xls. This is not a WPS-origin .et/.ett sample.
No third-party parser or Office executable is imported or executed.
"""
import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_biff8 import workbook, rec
from test_wps_biff8_builtin_names import area, built_in_name, built_in_workbook
from test_wps_biff8_names import name_payload
from wps_biff8 import parse_stream
from wps_biff8_names import classify_static_builtin_name, classify_static_name

# Source: https://github.com/ExaDev/documents.js/blob/main/packages/xls-codec/src/workbook/print-names.test.ts
# Literal record source bytes (BIFF8 framing + Lbl) from a real independent
# LibreOffice .xls authoring a repeated column A and rows 1-2.
LIBREOFFICE_LBL_PRINT_TITLES = bytes.fromhex(
    '18 00 2b 00 20 00 00 01 1b 00 00 00 01 00 00 00 00 00 00 07 '
    '29 17 00 3b 00 00 00 00 ff ff 00 00 00 00 '
    '3b 00 00 00 00 01 00 00 00 ff 00 10 15'
)


def decoded_source(payload):
    return classify_static_builtin_name(payload, 1, [(0, 0)])


class RealSourceMemFuncTests(unittest.TestCase):
    def test_real_libreoffice_record_is_literal_biff8_lbl_with_memfunc(self):
        identifier, size = struct.unpack_from('<HH', LIBREOFFICE_LBL_PRINT_TITLES)
        self.assertEqual((identifier, size), (0x18, 43))
        self.assertEqual(len(LIBREOFFICE_LBL_PRINT_TITLES), 47)
        record = LIBREOFFICE_LBL_PRINT_TITLES[4:]
        obj = decoded_source(record)
        self.assertEqual(obj['classification'], 'built_in_print_titles_static_area_only')
        self.assertEqual(obj['builtin_name_id_raw'], 0x07)
        self.assertEqual(obj['regions_zero_based'],
                         [[0, 65535, 0, 0], [0, 1, 0, 255]])
        self.assertEqual(obj['regions_kind'], ['full_column', 'full_row'])
        self.assertTrue(obj['ptg_union_raw_present'])
        self.assertTrue(obj['ptg_memfunc_wrapper_present'])
        self.assertTrue(obj['ptg_paren_display_token_present'])
        self.assertFalse(obj['print_settings_applied'])
        self.assertFalse(obj['formula_executed'])
        self.assertFalse(obj['external_link_followed'])

    def test_full_biff8_reader_stores_literal_source_and_no_applied_style(self):
        # Workbook is synthetic, *record* is from a separate real producer.
        book = built_in_workbook(LIBREOFFICE_LBL_PRINT_TITLES[4:])
        result = parse_stream(book, compatibility='data')
        self.assertEqual(result['status'], 'partial')
        found = [m for m in result['uninterpreted_metadata']
                 if m['name'] == 'PrintTitlesStatic']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['payload_hex'],
                         LIBREOFFICE_LBL_PRINT_TITLES[4:].hex())
        self.assertEqual(found[0]['structure']['regions_kind'],
                         ['full_column', 'full_row'])
        self.assertFalse(found[0]['structure']['print_settings_applied'])

    def test_real_layout_without_optional_parentheses_also_accepted(self):
        sample = bytearray(LIBREOFFICE_LBL_PRINT_TITLES[4:])
        sample[4:6] = struct.pack('<H', 26)
        # Drop trailing PtgParen; the PtgMemFunc.cce is still exactly 23.
        sample.pop()
        obj = decoded_source(bytes(sample))
        self.assertTrue(obj['ptg_memfunc_wrapper_present'])
        self.assertFalse(obj['ptg_paren_display_token_present'])
        self.assertEqual(len(obj['regions_zero_based']), 2)

    def test_reference_value_array_memfunc_classes_have_identical_fixed_width(self):
        for opcode in (0x29, 0x49, 0x69):
            sample = bytearray(LIBREOFFICE_LBL_PRINT_TITLES[4:])
            sample[16] = opcode
            with self.subTest(opcode=hex(opcode)):
                result = decoded_source(bytes(sample))
                self.assertEqual(result['builtin_name_id_raw'], 7)
                self.assertTrue(result['ptg_memfunc_wrapper_present'])

    def test_invalid_memfunc_cce_and_trailers_fail_closed(self):
        good = LIBREOFFICE_LBL_PRINT_TITLES[4:]
        mutations = []
        for bad_cce in (0, 11, 22, 24, 0xFFFF):
            payload = bytearray(good)
            struct.pack_into('<H', payload, 17, bad_cce)
            mutations.append(bytes(payload))
        for trailing in (b'\x10', b'\x15\x15', b'\x00', b'\x10\x15'):
            payload = bytearray(good)
            payload.extend(trailing)
            struct.pack_into('<H', payload, 4, len(payload) - 16)
            mutations.append(bytes(payload))
        mutations.extend((good[:-1], good[:-3], good[:16]))
        for i, bad in enumerate(mutations):
            with self.subTest(i=i), self.assertRaises(ValueError):
                decoded_source(bad)

    def test_wrong_inner_union_or_nested_token_refused(self):
        good = bytearray(LIBREOFFICE_LBL_PRINT_TITLES[4:])
        # Within rgce: first PtgArea3d starts at payload offset 19.
        for offset, new_byte in ((19, 0x29), (30, 0x39), (41, 0x11)):
            bad = bytearray(good)
            bad[offset] = new_byte
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                decoded_source(bytes(bad))

    def test_generic_names_and_filter_do_not_gain_memfunc_fallback(self):
        good = LIBREOFFICE_LBL_PRINT_TITLES[4:]
        with self.assertRaises(ValueError):
            decoded_source(good[:15] + bytes([0x0D]) + good[16:])
        # An ordinary Name's formula containing PtgMemFunc remains opaque.
        union_expr = good[16:]
        with self.assertRaises(ValueError):
            classify_static_name(
                name_payload('OnlyStatic', formula=union_expr), 1, [(0, 0)])

    def test_nonwrapped_trailing_parentheses_not_silently_ignored(self):
        basic = built_in_name(0x07, formula=area((0, 2), (0, 255)))
        mutated = bytearray(basic)
        mutated.extend(b'\x15')
        struct.pack_into('<H', mutated, 4, 12)
        with self.assertRaises(ValueError):
            decoded_source(bytes(mutated))


if __name__ == '__main__':
    unittest.main()
