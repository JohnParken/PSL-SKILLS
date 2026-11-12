"""Synthetic regressions for records encountered in real WPS ETT templates."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
        for mode in (None, MODE):
            with self.subTest(mode=mode):
                result = parse_stream(data, compatibility=mode)
                names = [x['name'] for x in result['uninterpreted_metadata']]
                for expected in ('Template', 'SupBookSelf',
                                 'ExternSheetInternal', 'PrintAreaStatic'):
                    self.assertIn(expected, names)
                self.assertFalse(result['formulas_executed'])
                self.assertFalse(result['external_links_followed'])
                self.assertFalse(result['native_et_ett_parsed'])
                self.assertEqual(result['status'], 'partial')

    def test_external_addin_and_malformed_internal_links_degrade(self):
        for link in (b'\x01\x00\x01\x3a', b'\x01\x00\x03\x00abc', b''):
            with self.subTest(link=link):
                result = parse_stream(workbook(extra_global=template_globals(link=link)),
                                      compatibility=MODE)
                self.assertEqual(result['status'], 'partial')
                self.assertFalse(result['external_links_followed'])
                self.assertTrue([item for item in result['issues']
                                 if item['code'] in ('link_metadata_invalid',
                                                     'defined_name_retained_opaque')])
        for refs in (b'\x01\x00' + struct.pack('<Hhh', 1, 0, 0),
                     b'\x01\x00' + struct.pack('<Hhh', 0, -1, -1),
                     b'\x01\x00' + struct.pack('<Hhh', 0, 0, 5), b'\x02\x00' + bytes(6)):
            with self.subTest(refs=refs):
                result = parse_stream(workbook(extra_global=template_globals(refs=refs)),
                                      compatibility=MODE)
                self.assertEqual(result['status'], 'partial')

    def test_macro_names_and_duplicate_markers_are_retained_with_diagnostics(self):
        base = (b'\x20\x00\x00\x01\x0b\x00\x00\x00' +
                b'\x01\x00' + bytes(5) + b'\x06\x3b' +
                struct.pack('<5H', 0, 0, 10, 0, 2))
        for offset, value in ((0, 0x28), (15, 1), (8, 0), (17, 1), (26, 0xC0)):
            area = bytearray(base)
            area[offset] = value
            with self.subTest(offset=offset):
                result = parse_stream(workbook(extra_global=template_globals(area=bytes(area))),
                                      compatibility=MODE)
                self.assertEqual(result['status'], 'partial')
                self.assertTrue([item for item in result['issues']
                                 if item['code'] in ('defined_name_retained_opaque',
                                                     'print_area_invalid',
                                                     'name_scope_out_of_range')])
        for extra, expected in ((rec(0x60), 'template_record_invalid'),
                                (rec(0x60, b'x'), 'template_record_invalid'),
                                (rec(0x1AE, b'\x01\x00\x01\x04'), 'link_metadata_invalid'),
                                (rec(0x17, b'\x01\x00' + bytes(6)), 'link_metadata_invalid'),
                                (rec(0x18, base), 'defined_name_duplicate')):
            with self.subTest(extra=extra[:4]):
                result = parse_stream(workbook(extra_global=template_globals() + extra),
                                      compatibility=MODE)
                self.assertEqual(result['status'], 'partial')
                self.assertFalse(result['external_links_followed'])

    def test_invalid_drawing_group_is_retained_with_diagnostic(self):
        result = parse_stream(workbook(extra_global=rec(0xEB, b'chart')), compatibility=MODE)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('drawing_group_invalid',
                      [item['code'] for item in result['issues']])
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')

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
