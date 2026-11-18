"""Retained geometry rejection and font-source isolation regression tests."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from svg_to_pptx.shape_boolean import render_boolean_svg_fragments
from svg_to_pptx import text_outline


class BooleanTests(unittest.TestCase):
    """Retain safety coverage independent of the removed optional geometry engine."""


    def test_explicit_fonts_do_not_scan_defaults(self):
        """An explicit empty font directory must never fall back to private roots."""
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(text_outline, '_system_font_roots', side_effect=AssertionError('implicit scan')):
                self.assertEqual(text_outline._font_files((directory,)), ())
        home = Path.home()
        self.assertTrue(all(not root.is_relative_to(home) for root in text_outline._system_font_roots()))


    def test_invalid_operands_and_css_rejected(self):
        """Input validation remains fail-closed for ambiguous and unsupported input."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'bad.svg'
            source.write_text('<svg><style>rect{fill:red}</style><rect id="a"/><rect id="b"/></svg>', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'embedded CSS'):
                render_boolean_svg_fragments(source, operation='union', source_ids=['a', 'b'], output_id='out')
            with self.assertRaises(ValueError):
                render_boolean_svg_fragments(source, operation='unsupported', source_ids=['a', 'b'], output_id='out')


if __name__ == '__main__':
    unittest.main()
