"""Straight-span merge controls, retained physical text, and refusal boundaries."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import unittest
from wps_rtf_builder import build_document_from_spec
from read_wps import read_bytes


def build(rows, **options):
    return build_document_from_spec({'page_numbering': False, 'blocks': [dict(type='table', rows=rows, **options)]}).render()


class MergeTests(unittest.TestCase):
    def test_horizontal_merge_and_raw_cell_roundtrip(self):
        text = build([['合并标题', '', '第三列'], ['甲', '乙', '丙']], merges=[[0, 0, 0, 1]], col_widths=[72, 72, 72])
        self.assertEqual(text.count(r'\clmgf'), 1)
        self.assertEqual(text.count(r'\clmrg'), 1)
        self.assertIn(r'\cellx1440', text)
        self.assertIn(r'\cellx2880', text)
        parsed = read_bytes(text.encode('ascii'))
        self.assertEqual(parsed['status'], 'ok')
        self.assertEqual([c['text'] for c in parsed['tables'][0]['rows'][0]], ['合并标题', '', '第三列'])
        self.assertEqual(parsed['tables'][0]['merges'], [[0, 0, 0, 1]])

    def test_vertical_merge_has_same_column_boundaries(self):
        text = build([['标题', '值'], ['分组', '甲'], ['', '乙']], merges=[[1, 0, 2, 0]], col_widths=[72, 144])
        self.assertEqual(text.count(r'\clvmgf'), 1)
        self.assertEqual(text.count(r'\clvmrg'), 1)
        self.assertEqual(text.count(r'\cellx1440'), 3)
        self.assertEqual(text.count(r'\cellx4320'), 3)
        self.assertEqual(read_bytes(text.encode('ascii'))['tables'][0]['rows'][2][1]['text'], '乙')

    def test_overlap_bounds_rectangular_and_content_protection(self):
        rows = [['标题', ''], ['', '']]
        for merges in ([[0, 0, 0, 1], [0, 1, 1, 1]], [[0, 0, 2, 0]], [[0, 0, 0, 0]], [[True, 0, 0, 1]]):
            with self.subTest(merges=merges), self.assertRaises(ValueError):
                build(rows, merges=merges)
        with self.assertRaises(ValueError):
            build([['甲', '不可丢弃']], merges=[[0, 0, 0, 1]])
        with self.assertRaises(ValueError):
            build(rows, merges=[[0, 0, 1, 0]])
        self.assertIn(r'\clvmgf', build(rows, merges=[[0, 0, 1, 0]], repeat_header=False))

    def test_explicit_border_modes_and_width(self):
        text = build([['甲', '乙']], borders='outer', border_width_pt=1)
        self.assertEqual(text.count(r'\brdrs\brdrw20'), 6)
        self.assertEqual(text.count(r'\brdrnil'), 2)
        text = build([['甲']], borders='none')
        self.assertEqual(text.count(r'\brdrnil'), 4)
        self.assertNotIn(r'\brdrs', text)
        self.assertEqual(build([['甲']], borders=False), text)
        for options in ({'borders': 'double'}, {'border_width_pt': float('nan')}, {'border_width_pt': 0}, {'col_widths': [0.01]}):
            with self.assertRaises(ValueError):
                build([['甲']], **options)


if __name__ == '__main__':
    unittest.main()
