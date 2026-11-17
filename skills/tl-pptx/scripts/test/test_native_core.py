"""Standalone native formula, chart workbook, and JSON safety regression tests."""
import io
import json
import math
import sys
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from svg_to_pptx.native_objects.formula_compiler import compile_latex_to_omml
from svg_to_pptx.native_objects.marker_common import _load_payload
from svg_to_pptx.native_objects.payload_limits import validate_payload_limits
from svg_to_pptx.native_objects.workbook import _minimal_workbook


class NativeCoreTests(unittest.TestCase):
    """Exercise supported native content and refusal boundaries."""

    def test_formula_grammar(self):
        """Formula mixins retain scripts, fractions, delimiters, and matrices."""
        for latex in [r'x^2+\frac{a}{b}', r'\sqrt{x}', r'\sum_{i=1}^{n} i',
                      r'\left(\frac{a}{b}\right)', r'\begin{matrix}a&b\\c&d\end{matrix}',
                      r'\newcommand{\foo}[1]{#1^2}\foo{x}', r'\ce{H2O}']:
            with self.subTest(latex=latex):
                xml = compile_latex_to_omml(latex)
                self.assertIn('oMath', ET.fromstring(xml).tag)

    def test_plain_native_payload(self):
        """New chart JSON stays readable without source package transport."""
        element = ET.Element('g', {'data-pptx-json': json.dumps({'categories': ['A'], 'series': [{'name': 'S', 'values': [1]}]})})
        self.assertEqual(_load_payload(element, 'chart')['categories'], ['A'])

    def test_malformed_source_package_rejected(self):
        """Malformed source inventory fails before downstream restoration."""
        element = ET.Element('g', {'data-pptx-json': '{"source_package":{"parts":[]}}'})
        with self.assertRaisesRegex(RuntimeError, 'source chart inventory'):
            _load_payload(element, 'chart')

    def test_json_depth(self):
        """Deep metadata fails before recursive field validation."""
        payload = {}
        for _ in range(41):
            payload = {'child': payload}
        with self.assertRaisesRegex(RuntimeError, 'depth'):
            validate_payload_limits(payload)

    def test_json_nodes(self):
        """Large shallow arrays have a separate resource limit."""
        with self.assertRaisesRegex(RuntimeError, 'node'):
            validate_payload_limits([0] * 20001)

    def test_nonfinite_json(self):
        """NaN and infinities cannot become malformed numeric XML."""
        for value in [math.inf, -math.inf, math.nan]:
            with self.assertRaisesRegex(RuntimeError, 'finite'):
                validate_payload_limits({'value': value})

    def test_workbook_strings_are_not_formulas(self):
        """Chart strings cannot turn into formulas or hyperlinks."""
        data = _minimal_workbook([['=1+1', 'https://example.invalid'], [1, 2]])
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            worksheet = package.read('xl/worksheets/sheet1.xml')
            self.assertNotIn(b'<f>', worksheet)
            self.assertNotIn(b'<hyperlink', worksheet)


if __name__ == '__main__':
    unittest.main()
