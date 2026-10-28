"""Repository integration: WPS stored-value JSON into optional XLSX analysis."""
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills' / name / 'scripts') for name in ('wps', 'xlsx')]
from test_wps_biff8 import workbook, rec, formula, special
from test_wps_cfb import synthetic_cfb
from xlsx_analysis import region


class EtPipelineTests(unittest.TestCase):
    def extract(self, directory, stream):
        path = directory / 'input.et'
        path.write_bytes(synthetic_cfb({'Workbook': stream}))
        process = subprocess.run([sys.executable, '-I', '-S', '-B',
                                  str(ROOT / 'skills/wps/scripts/read_wps.py'), str(path)],
                                 capture_output=True, text=True, encoding='utf-8', check=False)
        self.assertIn(process.returncode, (0, 3), process.stdout)
        result = json.loads(process.stdout)
        self.assertEqual(result['status'], 'ok' if process.returncode == 0 else 'partial')
        target = directory / 'result.json'
        target.write_text(process.stdout, encoding='utf-8')
        return target, result

    def test_partial_roundtrip_precision_identifier_and_cache(self):
        with tempfile.TemporaryDirectory(dir='.') as temp:
            target, result = self.extract(Path(temp), workbook(extra_global=rec(0xE1, b'raw')))
            _, _, rows, metadata = region(target, '中文表1', 'A2:B2', header=False)
            self.assertEqual(rows[0][1], [('text', '00012345678901234567890'),
                                          ('number', Decimal.from_float(.1))])
            self.assertEqual(metadata['source_status'], 'partial')
            self.assertEqual(metadata['source_issues'], result['issues'])
            self.assertEqual(metadata['source_cell_value_coverage'], result['cell_value_coverage'])
            self.assertEqual(metadata['source_omissions_affect_cell_values'], result['omissions_affect_cell_values'])
            self.assertEqual(metadata['source_reading_assessment'], result['reading_assessment'])
            with self.assertRaisesRegex(ValueError, 'Formula'):
                region(target, '中文表1', 'B4:B4', header=False)
            _, _, rows, metadata = region(target, '中文表1', 'B4:B4', header=False, formulas='cached')
            self.assertEqual(rows[0][1], [('number', Decimal(42))])
            self.assertEqual(metadata['unverified_formula_caches'], 1)

    def test_missing_cache_and_wrong_schema_never_become_empty_analysis(self):
        with tempfile.TemporaryDirectory(dir='.') as temp:
            target, result = self.extract(Path(temp), workbook([formula(0, 0, special(0))], strings=[]))
            self.assertEqual(result['cell_value_coverage'], 'missing_formula_caches')
            with self.assertRaisesRegex(ValueError, 'Missing'):
                region(target, '中文表1', 'A1:A1', header=False, formulas='cached')
            for update in ({'schema_version': 'word'}, {'status': 'unsupported'}):
                invalid = {**result, **update}
                target.write_text(json.dumps(invalid), encoding='utf-8')
                with self.assertRaises(ValueError):
                    region(target, '中文表1', 'A1:A1', header=False, formulas='cached')


if __name__ == '__main__':
    unittest.main()
