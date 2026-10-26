"""Synthetic regressions for records encountered in real WPS ETT templates."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_biff8 import workbook, rec
from wps_biff8 import parse_stream

MODE = 'legacy_excel_clsid'


def template_globals(marker=True, link=b'\x01\x00\x01\x04',
                     refs=b'\x01\x00' + bytes(6), area=None):
    if area is None:
        area = (b'\x20\x00\x00\x01\x0b\x00\x00\x00' +
                b'\x01\x00' + bytes(5) + b'\x06\x3b' +
                struct.pack('<5H', 0, 0, 10, 0, 2))
    return ((rec(0x60) if marker else b'') + rec(0x1AE, link) +
            rec(0x17, refs) + rec(0x18, area))


class TemplateMetadataTests(unittest.TestCase):
    def test_template_internal_print_area_retained_without_execution(self):
        data = workbook(sheet_records=[b''], names=['Sheet1'], extra_global=template_globals())
        result = parse_stream(data, compatibility=MODE)
        self.assertEqual([x['name'] for x in result['uninterpreted_metadata']],
                         ['Template', 'SupBookSelf', 'ExternSheetInternal', 'PrintAreaStatic'])
        self.assertFalse(result['formulas_executed'])
        self.assertFalse(result['external_links_followed'])
        self.assertFalse(result['native_et_ett_parsed'])
        with self.assertRaises(ValueError):
            parse_stream(data)

    def test_external_addin_and_malformed_internal_links_rejected(self):
        for link in (b'\x01\x00\x01\x3a', b'\x01\x00\x03\x00abc', b''):
            with self.subTest(link=link), self.assertRaises(ValueError):
                parse_stream(workbook(extra_global=template_globals(link=link)), compatibility=MODE)
        for refs in (b'\x01\x00' + struct.pack('<Hhh', 1, 0, 0),
                     b'\x01\x00' + struct.pack('<Hhh', 0, -1, -1),
                     b'\x01\x00' + struct.pack('<Hhh', 0, 0, 5), b'\x02\x00' + bytes(6)):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                parse_stream(workbook(extra_global=template_globals(refs=refs)), compatibility=MODE)

    def test_macro_names_relative_areas_bad_scope_and_duplicates_rejected(self):
        base = (b'\x20\x00\x00\x01\x0b\x00\x00\x00' +
                b'\x01\x00' + bytes(5) + b'\x06\x3b' +
                struct.pack('<5H', 0, 0, 10, 0, 2))
        for offset, value in ((0, 0x28), (15, 1), (8, 0), (17, 1), (26, 0xC0)):
            area = bytearray(base)
            area[offset] = value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                parse_stream(workbook(extra_global=template_globals(area=bytes(area))), compatibility=MODE)
        for extra in (rec(0x60), rec(0x60, b'x'), rec(0x1AE, b'\x01\x00\x01\x04'),
                      rec(0x17, b'\x01\x00' + bytes(6)), rec(0x18, base)):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                parse_stream(workbook(extra_global=template_globals() + extra), compatibility=MODE)

    def test_embedded_chart_records_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported global record 0xeb'):
            parse_stream(workbook(extra_global=rec(0xEB, b'chart')), compatibility=MODE)

    def test_offline_verifier_rejects_missing_or_changed_originals(self):
        from verify_et_samples import verify
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.assertTrue(all(not report['passed'] for report in verify(directory)))
            sample = directory / 'newfile.et'
            sample.write_bytes(b'not the original sample')
            reports = verify(directory)
            self.assertEqual(len(reports), 4)
            self.assertTrue(all(not report['passed'] for report in reports))
            self.assertIn('SHA-256', reports[0]['message'])
            self.assertEqual(sample.read_bytes(), b'not the original sample')


if __name__ == '__main__':
    unittest.main()
