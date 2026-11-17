"""Regression checks for the responsibility-split upstream DrawingML core.

Expected values were independently checked against ppt-master commit
7e0df6705de0cd24345210d31f07fcb5a32b6900. Tests do not require an upstream checkout.
"""
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from svg_to_pptx.drawingml import paths, utils
from svg_to_pptx.drawingml.context import ConvertContext, resolve_text_flow
from svg_to_pptx.drawingml.elements import convert_rect
from svg_to_pptx.drawingml.elements_text_runs import _build_run_properties_xml


class DrawingmlSplitTests(unittest.TestCase):
    """Protect unit, geometry, paint, language, and cross-module calls."""

    def test_transform_order(self):
        """SVG transform composition must retain translation order."""
        self.assertEqual(utils.parse_transform_matrix('translate(10 20) scale(2)'),
                         (2.0, 0.0, 0.0, 2.0, 10.0, 20.0))

    def test_paint_alpha_and_short_hex(self):
        """Keep color expansion and alpha independent from fill semantics."""
        self.assertEqual(utils.parse_svg_color('#abc'), ('AABBCC', 1.0))
        self.assertEqual(utils.parse_project_paint('rgba(255, 0, 0, 0.5)', 'fill'),
                         ('color', 'FF0000', 0.5))

    def test_geometry_rejects_nonfinite(self):
        """Invalid dimensions cannot be normalized into output geometry."""
        for value in ('NaN', 'inf', '-inf'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                utils.parse_project_geometry_length(value, "width")

    def test_relative_path_geometry(self):
        """Relative path updates must advance both coordinates correctly."""
        commands = paths.svg_path_to_absolute(paths.parse_svg_path('m10 20 l5 -2 h4 v3 z'))
        self.assertEqual([(item.cmd, item.args) for item in commands], [
            ('M', [10.0, 20.0]), ('L', [15.0, 18.0]),
            ('L', [19.0, 18.0]), ('L', [19.0, 21.0]), ('Z', []),
        ])

    def test_grapheme_clusters(self):
        """Do not split combining marks or joined emoji when measuring text."""
        self.assertEqual(utils.split_project_text_clusters('A\u0301中👩‍💻'),
                         ['A\u0301', '中', '👩‍💻'])

    def test_font_fallback_and_language(self):
        """Retain independent Latin/East-Asian family and language selection."""
        self.assertEqual(utils.parse_font_family('Arial'),
                         {'latin': 'Arial', 'ea': 'Microsoft YaHei'})
        self.assertEqual(utils.detect_text_lang('你好 world'), 'zh-CN')

    def test_text_flow_conflict(self):
        """Conflicting public layout options must fail rather than win silently."""
        with self.assertRaises(ValueError):
            resolve_text_flow('preserve', True)

    def test_text_decoration_flags(self):
        """All validated decoration values preserve native underline/strike."""
        for value, underline, strike in (
            ('none', False, False), ('underline', True, False),
            ('line-through', False, True), ('underline line-through', True, True),
        ):
            with self.subTest(value=value):
                xml = _build_run_properties_xml(
                    {'text': 'Sample', 'text_decoration': value},
                    {'latin': 'Arial', 'ea': 'Microsoft YaHei'})
                self.assertEqual(' u="sng"' in xml, underline)
                self.assertEqual(' strike="sngStrike"' in xml, strike)

    def test_rectangle_native_geometry(self):
        """Check full cross-component shape generation, not only helper outputs."""
        element = ET.fromstring('<rect xmlns="http://www.w3.org/2000/svg" '
                                'x="10" y="20" width="100" height="50" fill="#123456"/>')
        context = ConvertContext()
        result = convert_rect(element, context)
        self.assertIsNotNone(result)
        root = ET.fromstring(
            '<root xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            + result.xml + '</root>')
        ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
        self.assertEqual(root.find('.//a:prstGeom', ns).get('prst'), 'rect')
        self.assertEqual(root.find('.//a:off', ns).attrib, {'x': '95250', 'y': '190500'})
        self.assertEqual(root.find('.//a:ext', ns).attrib, {'cx': '952500', 'cy': '476250'})
        self.assertEqual(root.find('.//a:srgbClr', ns).get('val'), '123456')


if __name__ == '__main__':
    unittest.main()
