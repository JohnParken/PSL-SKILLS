"""WPS family inputs are routed away from native spreadsheet commands."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stdlib_xlsx import Workbook
class FamilyRoutingTests(unittest.TestCase):
    def test_family_and_disguised_cfb_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for suffix in ('.et', '.ET', '.ett', '.ETT', '.wps', '.WPS', '.xlsx'):
                path = root / ('input'+suffix)
                path.write_bytes(bytes.fromhex('d0cf11e0a1b11ae1'))
                result = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('stdlib_xlsx.py')), 'read', str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                report = json.loads(result.stdout)
                self.assertEqual(report['status'], 'unsupported')
                self.assertNotIn('sheets', report)
                self.assertIn('wps skill', report['message'])
    def test_identify_and_analysis_explicit_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            for suffix in ('.WPS', '.ET', '.ETT'):
                path = Path(tmp) / ('input' + suffix)
                path.write_bytes(b'')
                for script, args in (('xlsx_identify.py', [str(path)]),
                                     ('xlsx_analysis.py', ['profile', str(path), '--sheet', 'Data'])):
                    run = subprocess.run([sys.executable, '-I', '-S', '-B',
                                          str(Path(__file__).with_name(script))] + args,
                                         capture_output=True, text=True)
                    self.assertEqual(run.returncode, 1, run.stderr)
                    report = json.loads(run.stdout)
                    self.assertEqual(report['status'], 'unsupported')
                    self.assertIn('不支持读取 WPS 格式', report['message'])
                    self.assertIn('wps skill', report['message'])

    def test_unknown_native_is_not_wps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'unknown.xlsx'
            path.write_bytes(b'unknown input')
            from xlsx_identify import identify
            result = identify(path)
            self.assertEqual(result['status'], 'unrecognized')
            self.assertNotIn('recommended_skill', result)
            with self.assertRaises(zipfile.BadZipFile) as caught:
                Workbook.load(path)
            self.assertNotIn('wps skill', str(caught.exception))

    def test_ooxml_under_family_suffix_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            native = Path(tmp)/'native.xlsx'
            Workbook.create({'sheets':[{'name':'Data','cells':{}}]}).save(native)
            path = native.with_suffix('.et'); path.write_bytes(native.read_bytes())
            with self.assertRaisesRegex(ValueError, 'wps skill'): Workbook.load(path)
if __name__ == '__main__': unittest.main()
