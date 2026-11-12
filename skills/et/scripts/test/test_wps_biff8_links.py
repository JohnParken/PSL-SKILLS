"""Synthetic BIFF8 supporting-link tests through the default file reader."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_et import read_bytes
from test_wps_biff8 import formula, rec, special, workbook
from test_wps_biff8_names import name_payload
from test_wps_cfb import synthetic_cfb


def external_book():
    path = b'Book1.xls'
    return rec(0x01AE, struct.pack('<HH', 1, len(path)) + b'\0' + path + b'\x06\0\0Sheet1')


def file_bytes(globals_records, worksheet=b''):
    return synthetic_cfb({'Workbook': workbook([worksheet], strings=[], extra_global=globals_records)})


class SupportingLinkTests(unittest.TestCase):
    def test_external_link_preserves_present_and_missing_cache_status(self):
        xti = rec(0x0017, struct.pack('<HHhh', 1, 0, 0, 0))
        present = formula(0, 0, struct.pack('<d', 7), tokens=b'\x3a' + bytes(6))
        missing = formula(1, 0, special(0), tokens=b'\x3a' + bytes(6))
        source = file_bytes(external_book() + xti, present + missing)
        result = read_bytes(source)
        cells = result['sheets'][0]['cells']
        self.assertEqual((cells['A1']['value'], cells['A1']['cache_status']), (7, 'unverified'))
        self.assertEqual((cells['A2']['value'], cells['A2']['cache_status']), (None, 'missing'))
        self.assertEqual(result['cell_value_coverage'], 'missing_formula_caches')
        self.assertTrue(result['omissions_affect_cell_values'])

    def test_mixed_positions_and_formula_cell_uses_only_workbook_cache(self):
        local_book = rec(0x01AE, struct.pack('<HH', 1, 0x0401))
        addin = rec(0x01AE, struct.pack('<HH', 1, 0x3A01)) + rec(0x0023, bytes(6))
        external_cache = rec(0x0059, struct.pack('<hH', 1, 0)) + rec(0x005A, bytes(4) + b'\x01' + struct.pack('<d', 99))
        xti_payload = struct.pack('<HHhhHhhHhh', 3, 0, 0, 0, 1, 0, 0, 2, -2, -2)
        xti = rec(0x0017, xti_payload[:8]) + rec(0x003C, xti_payload[8:])
        local = rec(0x0018, name_payload('Local', ref=1))
        external = rec(0x0018, name_payload('External', ref=0))
        source = file_bytes(external_book() + external_cache + local_book + addin + xti + local + external,
                            formula(0, 0, struct.pack('<d', 42), tokens=b'\x3a' + bytes(6)))
        result = read_bytes(source)
        self.assertEqual(result['status'], 'partial')
        cell = result['sheets'][0]['cells']['A1']
        self.assertEqual((cell['value'], cell['cache_status']), (42, 'unverified'))
        self.assertFalse(result['formulas_executed'])
        self.assertFalse(result['external_links_followed'])
        records = {item['name']: item for item in result['uninterpreted_metadata']}
        entries = records['ExternSheetOpaque']['structure']['xti']
        self.assertEqual([(e['xti_index'], e['supbook_index'], e['internal']) for e in entries],
                         [(0, 0, False), (1, 1, True), (2, 2, False)])
        name = records['OrdinaryStaticInternalAreaName']['structure']
        self.assertEqual((name['internal_xti_index_raw'], name['internal_sheet_index_zero_based']), (1, 0))
        self.assertEqual(records['DefinedNameOpaque']['payload_hex'], name_payload('External', ref=0).hex())
        self.assertIn('ExternalCellCacheOpaque', records)
        self.assertEqual(records['ExternSheetContinuation']['payload_hex'], xti_payload[8:].hex())
        for mode in ('strict', 'legacy_excel_clsid'):
            with self.subTest(mode=mode):
                result = read_bytes(source, compatibility=mode)
                self.assertEqual(result['status'], 'partial')
                self.assertIn('compatibility_option_ignored',
                              [item['code'] for item in result['issues']])
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 42)

    def test_invalid_link_boundaries_and_indices_fail(self):
        cases = (
            rec(0x01AE, b'\x01\0\x05\0\0x'),
            external_book() + rec(0x0017, struct.pack('<HHhh', 1, 1, 0, 0)),
            external_book() + rec(0x0017, struct.pack('<HHhh', 1, 0, 1, 1)),
            external_book() + rec(0x0017, b'\x01\0\0\0'),
            external_book() + rec(0x0017, b'\x01\0\0\0') + rec(0x003C, bytes(5)),
            external_book() + rec(0x0059, struct.pack('<hH', 1, 0)),
            external_book() + rec(0x7777) + rec(0x0023, bytes(6)),
            external_book() + rec(0x005A, bytes(4)),
        )
        for records in cases:
            with self.subTest(records=records.hex()):
                result = read_bytes(file_bytes(records))
                self.assertEqual(result['status'], 'partial')
                self.assertFalse(result['external_links_followed'])
                self.assertFalse(result['formulas_executed'])
                self.assertTrue([item for item in result['issues']
                                 if item['code'] in ('link_metadata_invalid',
                                                     'unknown_biff_record_salvaged',
                                                     'defined_name_retained_opaque')])


if __name__ == '__main__':
    unittest.main()
