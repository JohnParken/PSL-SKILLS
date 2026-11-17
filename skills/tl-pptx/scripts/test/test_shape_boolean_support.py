"""Boolean and glyph-outline synthetic tests; explicit licensed font fixture only."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pathops
from svg_to_pptx.shape_boolean import render_boolean_svg_fragments, _shape_commands
from svg_to_pptx.shape_boolean_paths import _commands_to_pathops, _pathops_to_svg_path
from svg_to_pptx import text_outline


class BooleanTests(unittest.TestCase):
    """Assert filled areas, deterministic fragments and actual glyph geometry."""

    def test_all_five_operations(self):
        """Two overlapping squares have independently known area results."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.svg'
            original = '<svg xmlns="http://www.w3.org/2000/svg"><rect id="a" width="10" height="10"/><rect id="b" x="5" width="10" height="10"/></svg>'
            source.write_text(original, encoding='utf-8')
            for operation, expected in [('union', 150), ('intersect', 50), ('subtract', 50), ('combine', 100), ('fragment', 150)]:
                with self.subTest(operation=operation):
                    fragment = render_boolean_svg_fragments(source, operation=operation, source_ids=['a', 'b'], output_id='result')
                    root = ET.fromstring('<svg>'+fragment+'</svg>')
                    paths = [_commands_to_pathops(_shape_commands(element), pathops) for element in root]
                    self.assertAlmostEqual(sum(item.area for item in paths), expected)
                    self.assertEqual(len(paths), 3 if operation == 'fragment' else 1)
                    self.assertEqual(fragment, render_boolean_svg_fragments(source, operation=operation, source_ids=['a', 'b'], output_id='result'))
            self.assertEqual(source.read_text(encoding='utf-8'), original)

    def test_quadratic_serialization_and_cli(self):
        """Quadratic-to-cubic serialization preserves closed filled geometry."""
        path = pathops.Path()
        path.moveTo(0, 0)
        path.quadTo(5, 10, 10, 0)
        path.lineTo(0, 0)
        path.close()
        text = _pathops_to_svg_path(path, pathops)
        self.assertIn('C ', text)
        self.assertTrue(text.endswith('Z'))
        rebuilt = _commands_to_pathops(_shape_commands(ET.fromstring(f'<path d="{text}"/>')), pathops)
        self.assertAlmostEqual(rebuilt.area, path.area, places=4)
        cli = Path(__file__).resolve().parents[1] / 'shape_boolean_svg.py'
        result = subprocess.run([sys.executable, '-I', '-B', str(cli), 'render', '--help'], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('union', result.stdout)

    def test_explicit_fonts_do_not_scan_defaults(self):
        """An explicit empty font directory must never fall back to private roots."""
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(text_outline, '_system_font_roots', side_effect=AssertionError('implicit scan')):
                self.assertEqual(text_outline._font_files((directory,)), ())
        home = Path.home()
        self.assertTrue(all(not root.is_relative_to(home) for root in text_outline._system_font_roots()))

    def test_actual_text_outline_with_explicit_font(self):
        """Use only an operator-authorized installed font copied into this fixture."""
        provided = os.environ.get('TL_PPTX_TEST_FONT')
        if not provided:
            self.skipTest('TL_PPTX_TEST_FONT must name a licensed operator-authorized font')
        font = Path(provided)
        self.assertTrue(font.is_file())
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            shutil.copyfile(font, base / font.name)
            element = ET.fromstring('<text x="10" y="80" font-family="Arial" font-size="48">Ab</text>')
            with patch.object(text_outline, '_system_font_roots', side_effect=AssertionError('implicit scan')):
                commands = text_outline.text_element_to_path_commands(element, {}, font_dirs=[base])
            outline = _commands_to_pathops(commands, pathops)
            self.assertGreater(outline.area, 0)
            self.assertTrue(any(command.cmd == 'C' for command in commands))
            text_outline._font_catalog.cache_clear()

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
