"""ET/ETT CLI routing with synthetic BIFF8 fixtures; not WPS acceptance."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_xlsx_biff8 import workbook, rec
from test_xlsx_cfb import synthetic_cfb
from test_xlsx_legacy_compat import legacy_container


class EtEntrypointTests(unittest.TestCase):
    def run_read(self, data, suffix, *options):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / ('input' + suffix)
            source.write_bytes(data)
            result = subprocess.run(
                [sys.executable, '-I', '-S', '-B',
                 str(Path(__file__).with_name('stdlib_xlsx.py')), 'read',
                 source.name, *options], cwd=tmp, text=True, capture_output=True)
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], [source.name])
            self.assertEqual(result.stderr, '')
            return result.returncode, json.loads(result.stdout)

    def test_both_suffixes_read_typed_subset_without_native_claim(self):
        data = synthetic_cfb({'Workbook': workbook()})
        for suffix in ('.et', '.ett', '.ET', '.ETT'):
            with self.subTest(suffix=suffix):
                code, result = self.run_read(data, suffix)
                self.assertEqual(code, 0)
                self.assertTrue(result['biff_parsed'])
                self.assertFalse(result['native_et_ett_parsed'])
                self.assertFalse(result['wps_compatibility_verified'])
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
                self.assertEqual(result['source']['extension'], suffix.lower())

    def test_known_legacy_header_requires_explicit_opt_in(self):
        data = legacy_container({'Workbook': workbook()})
        self.assertEqual(self.run_read(data, '.et')[0], 1)
        code, result = self.run_read(data, '.ett', '--compatibility', 'legacy_excel_clsid')
        self.assertEqual(code, 0)
        self.assertEqual(result['compatibility_mode'], 'legacy_excel_clsid')
        self.assertTrue(result['source']['container_issues'])

    def test_unknown_records_and_invalid_content_fail_without_partial_cells(self):
        for data in (b'not a spreadsheet',
                     synthetic_cfb({'Workbook': workbook(extra_global=rec(0xFFFF))})):
            code, result = self.run_read(data, '.et')
            self.assertEqual(code, 1)
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('sheets', result)
            self.assertFalse(result['output_written'])

    def test_compatibility_cannot_silently_apply_to_other_formats(self):
        code, result = self.run_read(b'one,two\n', '.csv', '--compatibility', 'legacy_excel_clsid')
        self.assertEqual(code, 1)
        self.assertIn('only to ET/ETT', result['message'])


if __name__ == '__main__':
    unittest.main()
