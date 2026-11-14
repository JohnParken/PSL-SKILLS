"""Keep independently framed formula caches when expression tokens are absent."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_biff8 import cell, formula, rec, special, string, workbook
from wps_biff8 import parse_stream


class FormulaCacheRecoveryTests(unittest.TestCase):
    """Missing expression bytes do not invalidate a complete saved value."""

    def test_fixed_header_retains_numeric_boolean_error_and_empty_cache(self):
        cases = [(struct.pack('<d', 123.5), 'number', 123.5),
                 (special(1, 1), 'boolean', True),
                 (special(2, 7), 'error', '#DIV/0!'),
                 (special(3), 'text', '')]
        for cache, kind, expected in cases:
            with self.subTest(kind=kind):
                result = parse_stream(workbook([formula(0, 0, cache, tokens=b'')]))
                found = result['sheets'][0]['cells']['A1']
                self.assertEqual(found['value'], expected)
                self.assertEqual(found['type'], kind)
                self.assertTrue(found['formula']['cache_may_be_stale'])
                self.assertEqual(result['status'], 'partial')
                self.assertFalse(result['formulas_executed'])
                self.assertIn('formula_token_length_invalid',
                              [item['code'] for item in result['issues']])

    def test_missing_declared_tokens_keeps_cache_and_following_cell(self):
        payload = cell(0, 0) + struct.pack('<d', 42) + struct.pack('<HIH', 0, 0, 3)
        following = rec(0x0203, cell(1, 0) + struct.pack('<d', 99))
        result = parse_stream(workbook([rec(6, payload) + following]))
        cells = result['sheets'][0]['cells']
        self.assertEqual(cells['A1']['value'], 42)
        self.assertEqual(cells['A2']['value'], 99)
        self.assertEqual(cells['A1']['formula']['tokens_hex'], '')

    def test_string_cache_remains_bound_to_its_formula(self):
        raw = formula(0, 0, special(0), tokens=b'') + rec(0x207, string('已保存文本'))
        result = parse_stream(workbook([raw]))
        found = result['sheets'][0]['cells']['A1']
        self.assertEqual(found['value'], '已保存文本')
        self.assertEqual(found['cache_status'], 'unverified')

    def test_truncated_fixed_header_still_omits_only_damaged_cell(self):
        payload = cell(0, 0) + struct.pack('<d', 42) + bytes(7)
        following = rec(0x0203, cell(1, 0) + struct.pack('<d', 99))
        result = parse_stream(workbook([rec(6, payload) + following]))
        self.assertNotIn('A1', result['sheets'][0]['cells'])
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 99)
        self.assertTrue(result['omissions_affect_cell_values'])


if __name__ == '__main__':
    unittest.main()
