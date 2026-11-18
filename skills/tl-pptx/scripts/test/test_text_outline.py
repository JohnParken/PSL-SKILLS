"""Synthetic local-font contract tests; not Office visual acceptance."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_guard import ContractError
from tl_text_outline import outline_text, SVG_NS


class TextOutlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import fitz
        # Bundled font is used only as a synthetic input snapshot in tests.
        cls.font = fitz.Font('china-s').buffer

    def run_outline(self, text='OB8', **changes):
        spec = {'text': text, 'font': 'authorized.ttf', 'font_authorized': True}
        spec.update(changes)
        with patch('tl_text_outline.read_local', return_value=self.font):
            return outline_text(spec)

    def test_latin_holes_and_no_font_references(self):
        root = ET.fromstring(self.run_outline())
        self.assertEqual(len(root), 3)
        for node, minimum in zip(root, (2, 3, 3)):
            self.assertEqual(node.tag, f'{{{SVG_NS}}}path')
            self.assertGreaterEqual(node.get('d').upper().count('M'), minimum)
            self.assertEqual(node.get('fill-rule'), 'nonzero')
        self.assertIsNone(root.find(f'{{{SVG_NS}}}defs'))

    def test_cjk_and_ascii_spaces(self):
        root = ET.fromstring(self.run_outline('中文 O 8', color='#123456'))
        self.assertEqual(len(root), 6)
        self.assertTrue(all(node.get('fill') == '#123456' for node in root))

    def test_complex_scripts_marks_and_controls_refused(self):
        for text in ('سلام', 'कर्म', 'a\u0301', 'a\nb', 'a\tb', '\u202eabc', '\U0001f600', 'á'):
            with self.subTest(text=text), self.assertRaises(ContractError):
                self.run_outline(text)

    def test_authorization_and_local_path_required(self):
        for change in ({'font_authorized': False}, {'font_authorized': 1}, {'font': '../font.ttf'},
                       {'font': '/font.ttf'}, {'font': './font.ttf'}, {'font': 'https://host/font.ttf'}, {'font': 'font.ttc'}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.run_outline(**change)

    def test_missing_glyph_refused(self):
        import fitz
        with patch.object(fitz.Font, 'has_glyph', return_value=0), self.assertRaises(ContractError):
            self.run_outline()

    def test_invalid_spec_and_font(self):
        for change in ({'font_size': float('nan')}, {'font_size': True}, {'color': 'url(x)'}, {'bogus': True}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.run_outline(**change)
        with patch('tl_text_outline.read_local', return_value=b'badfont'), self.assertRaises(ContractError):
            outline_text({'text': 'O', 'font': 'font.ttf', 'font_authorized': True})

    def test_export_substitution_refused(self):
        from tl_text_outline import _expand_svg
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><defs><path id="g" d="M0 0Z"/></defs></svg>'
        with self.assertRaises(ContractError):
            _expand_svg(svg, 'O', '#000000')

    def test_expanded_paths_match_export_pixels(self):
        import fitz
        original = fitz.Page.get_svg_image
        captured = []

        def capture(page, *args, **kwargs):
            result = original(page, *args, **kwargs)
            captured.append(result.encode('utf-8'))
            return result

        for text in ('OB8', '中文', 'fi AV'):
            with self.subTest(text=text), patch.object(fitz.Page, 'get_svg_image', capture):
                expanded = self.run_outline(text)
            with fitz.open(stream=captured[-1], filetype='svg') as source:
                expected = source[0].get_pixmap(alpha=False).samples
            with fitz.open(stream=expanded, filetype='svg') as result:
                self.assertEqual(expected, result[0].get_pixmap(alpha=False).samples)

    def test_canvas_is_pptx_compatible_without_scaling(self):
        root = ET.fromstring(self.run_outline('O', font_size=1))
        self.assertEqual(float(root.get('width')), 96)
        self.assertEqual(float(root.get('height')), 96)
        self.assertTrue(root[0].get('transform').startswith('matrix(1,0,0,-1,'))
        with self.assertRaisesRegex(ContractError, '56-inch limit'):
            self.run_outline('W' * 512, font_size=256)

    def test_no_rasterizer_invocation(self):
        import fitz
        with patch.object(fitz.Page, 'get_pixmap', side_effect=AssertionError('rasterization forbidden')):
            self.assertIn(b'path', self.run_outline('fi AV'))


if __name__ == '__main__':
    unittest.main()
