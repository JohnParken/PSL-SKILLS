"""Behavior checks for the retained upstream positional-tspan algorithm."""
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from svg_finalize.flatten_tspan import flatten_text_with_tspans

SVG = ('<svg xmlns="http://www.w3.org/2000/svg"><text x="10" y="20" font-size="20">'
       '<tspan x="10" dy="0">One</tspan><tspan x="10" dy="24">Two</tspan></text></svg>')


class TspanNormalizationTests(unittest.TestCase):
    """Protect split/reflow/preserve behavior through the shared core API."""

    def test_split_baselines(self):
        """Relative line movement must accumulate into absolute baselines."""
        tree = ET.ElementTree(ET.fromstring(SVG))
        self.assertTrue(flatten_text_with_tspans(tree))
        self.assertEqual([(node.text, node.get('x'), node.get('y')) for node in tree.getroot()],
                         [('One', '10', '20'), ('Two', '10', '44')])
        self.assertFalse(flatten_text_with_tspans(tree))

    def test_reflow_marks_soft_break(self):
        """Reflow keeps one frame while distinguishing soft continuation."""
        tree = ET.ElementTree(ET.fromstring(SVG))
        self.assertTrue(flatten_text_with_tspans(tree, merge_paragraphs=True))
        root = tree.getroot()
        self.assertEqual(len(root), 1)
        self.assertEqual(root[0].get('data-paragraph-line-height'), '24')
        self.assertEqual(root[0][1].get('data-paragraph-soft-break'), '1')
        self.assertEqual(''.join(root.itertext()), 'OneTwo')

    def test_preserve_marks_hard_break(self):
        """Preserving authored rows must not silently reflow the second row."""
        tree = ET.ElementTree(ET.fromstring(SVG))
        self.assertTrue(flatten_text_with_tspans(tree, merge_paragraphs=True, preserve_line_breaks=True))
        second = tree.getroot()[0][1]
        self.assertEqual(second.get('data-paragraph-line-break'), '1')
        self.assertIsNone(second.get('data-paragraph-soft-break'))

    def test_nested_position_rejected(self):
        """Nested independent baseline jumps are unsupported, not discarded."""
        source = ('<svg xmlns="http://www.w3.org/2000/svg"><text x="10" y="20">'
                  '<tspan x="10"><tspan y="30">Nested</tspan></tspan></text></svg>')
        tree = ET.ElementTree(ET.fromstring(source))
        with self.assertRaises(ValueError):
            flatten_text_with_tspans(tree)

    def test_plain_text_unchanged(self):
        """Plain text does not acquire a needless replacement frame."""
        tree = ET.ElementTree(ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg"><text>Plain</text></svg>'))
        before = ET.tostring(tree.getroot())
        self.assertFalse(flatten_text_with_tspans(tree))
        self.assertEqual(ET.tostring(tree.getroot()), before)


if __name__ == '__main__':
    unittest.main()
