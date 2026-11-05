"""PPT Master 6.8 text, geometry, and SVG-finalization regressions."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = REPOSITORY_ROOT / 'skills' / 'pptx' / 'scripts'
SCRIPT = SCRIPT_ROOT / 'pptx.py'
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(SCRIPT_ROOT / '_engine'))

from svg_finalize.flatten_tspan import flatten_text_with_tspans
from svg_to_pptx.drawingml.elements import estimate_single_line_text_frame_width
from svg_to_pptx.drawingml.text_baseline import drawingml_text_baseline_offset
from svg_to_pptx.drawingml.utils import estimate_text_cluster_widths, get_font_advances


SVG_NS = 'http://www.w3.org/2000/svg'
NAMESPACES = {
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    's': SVG_NS,
}


def svg(content: str) -> str:
    return f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">{content}</svg>'


class Render680Tests(unittest.TestCase):
    def export(self, content: str, *, text_flow: str = 'preserve') -> ET.Element:
        payload = {
            'schema': 'psl.pptx.v1',
            'text_flow': text_flow,
            'slides': [{'svg': svg(content)}],
        }
        with tempfile.TemporaryDirectory(prefix='psl-pptx-v680-render-') as directory:
            process = subprocess.run(
                [sys.executable, '-I', '-S', '-B', str(SCRIPT), 'create', '-', 'render.pptx'],
                cwd=directory,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                encoding='utf-8',
                capture_output=True,
                env={**os.environ, 'PATH': '', 'PYTHONNOUSERSITE': '1'},
                timeout=45,
                check=False,
            )
            self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
            result = json.loads(process.stdout)
            self.assertTrue(result['ok'])
            self.assertEqual(result['structural_validation'], 'passed')
            self.assertEqual(process.stderr, '')
            data = (Path(directory) / 'render.pptx').read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return ET.fromstring(archive.read('ppt/slides/slide1.xml'))

    def test_font_metrics_set_authored_text_baselines(self) -> None:
        slide = self.export(
            '<text x="20" y="100" font-size="40" font-family="Arial">Latin</text>'
            '<text x="20" y="160" font-size="64" font-family="SimSun">中文</text>'
        )
        text_shapes = slide.findall('.//p:sp', NAMESPACES)
        self.assertEqual(len(text_shapes), 2)
        # The metric fixture gives Arial's first line 29 pt above its top;
        # SimSun 64 px lands on the 49.5 pt tie, rounded upwards to 50 pt.
        # These are frame coordinates in the emitted slide, not an installed
        # font measurement or a desktop visual-fidelity assertion.
        expected_tops = [584200, 889000]
        actual_tops = [int(shape.find('p:spPr/a:xfrm/a:off', NAMESPACES).get('y')) for shape in text_shapes]
        self.assertEqual(actual_tops, expected_tops)
        fonts = {'latin': 'Unknown Test Font', 'ea': 'Unknown Test Font', 'cs': 'Unknown Test Font'}
        offset = drawingml_text_baseline_offset([{'text': 'abc', 'font_size': 40}], fonts, default_size=40)
        self.assertAlmostEqual(offset, 116 / 3)

    def test_preserved_line_break_keeps_explicit_run_size(self) -> None:
        slide = self.export(
            '<text x="40" y="100" font-family="Arial" font-size="40">'
            '<tspan x="40" dy="0">First line</tspan>'
            '<tspan x="40" dy="48">Second line</tspan></text>'
        )
        breaks = slide.findall('.//a:br', NAMESPACES)
        self.assertEqual(len(breaks), 1)
        properties = breaks[0].find('a:rPr', NAMESPACES)
        self.assertIsNotNone(properties)
        self.assertEqual(properties.get('sz'), '3000')
        self.assertIsNone(properties.find('a:hlinkClick', NAMESPACES))

    def test_expanded_font_advance_data_are_used(self) -> None:
        for family in ('Cambria', 'Trebuchet MS', 'Tahoma', 'Garamond', 'Segoe UI'):
            with self.subTest(family=family):
                self.assertIsNotNone(get_font_advances(family))
        # Fixed numeric expectations from the bundled metric-compatible
        # Segoe UI and Garamond advance tables; no font executable is used.
        values = estimate_text_cluster_widths('Wi', 100, font_family='Segoe UI')
        self.assertAlmostEqual(values[0], 93.41)
        self.assertAlmostEqual(values[1], 24.22)
        values = estimate_text_cluster_widths('Wi', 100, font_family='Garamond')
        self.assertAlmostEqual(values[0], 88.52)
        self.assertAlmostEqual(values[1], 22.9)

    def test_unicode_spaces_and_accented_letters_keep_their_widths(self) -> None:
        self.assertEqual(estimate_text_cluster_widths('\u2009\u202f\u200a', 20), [4.0, 4.0, 2.0])
        for composed, decomposed, plain in (('Ș', 'S\u0326', 'S'), ('ḿ', 'm\u0301', 'm'), ('ł', 'ł', 'l')):
            with self.subTest(character=composed):
                expected = estimate_text_cluster_widths(plain, 30, font_family='Arial')
                self.assertEqual(estimate_text_cluster_widths(composed, 30, font_family='Arial'), expected)
                self.assertEqual(estimate_text_cluster_widths(decomposed, 30, font_family='Arial'), expected)

    def test_unicode_and_monotype_names_are_not_monospace_hints(self) -> None:
        def width(text: str, family: str) -> float:
            return estimate_single_line_text_frame_width(
                [{'text': text, 'font_size': 30, 'font_family': family}],
                include_headroom=False,
            )

        for family in ('Arial Unicode MS', 'Monotype Corsiva'):
            with self.subTest(family=family):
                self.assertGreater(width('WWWW', family), width('iiii', family))
        self.assertEqual(width('WWWW', 'Fira Code'), width('iiii', 'Fira Code'))

    def test_tiny_thick_arc_retains_native_geometry_and_gradient(self) -> None:
        slide = self.export(
            '<defs><linearGradient id="arc-fill" x1="0" y1="0" x2="1" y2="0">'
            '<stop offset="0" stop-color="#112233"/>'
            '<stop offset="1" stop-color="#445566"/></linearGradient></defs>'
            '<circle cx="100" cy="100" r="60" fill="none" stroke="url(#arc-fill)" '
            'stroke-width="12" stroke-dasharray="0.25 400"/>'
        )
        shapes = slide.findall('.//p:sp', NAMESPACES)
        self.assertEqual(len(shapes), 1)
        geometry = shapes[0].find('p:spPr/a:custGeom/a:pathLst/a:path', NAMESPACES)
        self.assertIsNotNone(geometry)
        self.assertGreater(int(geometry.get('w')), 0)
        self.assertLess(int(geometry.get('w')), 4763)
        self.assertIsNotNone(geometry.find('a:close', NAMESPACES))
        colors = [node.get('val') for node in shapes[0].findall('p:spPr/a:gradFill/a:gsLst/a:gs/a:srgbClr', NAMESPACES)]
        self.assertEqual(colors, ['112233', '445566'])

    def test_split_text_keeps_identity_visibility_and_link_runs(self) -> None:
        root = ET.fromstring(svg(
            '<text id="label-line-2" x="10" y="40">Existing</text>'
            '<text id="label" data-pptx-role="body" x="10" y="80" visibility="hidden" '
            'data-pptx-text-baseline="metrics-v1">'
            '<tspan id="first-run" x="10" dy="0">First</tspan>'
            '<a href="https://example.com"> link</a>'
            '<tspan x="10" dy="30">Second</tspan></text>'
        ))
        self.assertTrue(flatten_text_with_tspans(ET.ElementTree(root)))
        rows = root.findall('s:text', NAMESPACES)
        self.assertEqual([row.get('id') for row in rows], ['label-line-2', 'label', 'label-line-2-line'])
        self.assertEqual(rows[1].find('s:tspan', NAMESPACES).get('id'), 'first-run')
        link = rows[1].find('s:a', NAMESPACES)
        self.assertIsNotNone(link)
        self.assertEqual(link.get('href'), 'https://example.com')
        self.assertEqual(''.join(rows[1].itertext()), 'First link')
        self.assertEqual(''.join(rows[2].itertext()), 'Second')
        for row in rows[1:]:
            self.assertEqual(row.get('visibility'), 'hidden')
            self.assertEqual(row.get('data-pptx-role'), 'body')
            self.assertEqual(row.get('data-pptx-text-baseline'), 'metrics-v1')

    def test_split_mode_preserves_collision_free_native_shape_names(self) -> None:
        slide = self.export(
            '<text id="label-line-2" x="40" y="40" font-size="30">Existing</text>'
            '<text id="label" x="40" y="80" font-size="30">'
            '<tspan x="40" dy="0">First</tspan>'
            '<tspan x="40" dy="36">Second</tspan></text>',
            text_flow='split',
        )
        self.assertEqual([node.text for node in slide.findall('.//a:t', NAMESPACES)], ['Existing', 'First', 'Second'])
        names = [node.get('name') for node in slide.findall('.//p:sp/p:nvSpPr/p:cNvPr', NAMESPACES)]
        self.assertEqual(names, ['label-line-2', 'label', 'label-line-2-line'])

    def test_hyperlink_wrapped_group_keeps_its_native_group(self) -> None:
        slide = self.export(
            '<a href="https://example.com"><g id="linked-group">'
            '<rect x="20" y="20" width="200" height="80" fill="#112233"/>'
            '<text x="30" y="65" font-size="28">Linked</text></g></a>'
        )
        groups = slide.findall('.//p:grpSp', NAMESPACES)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].find('p:nvGrpSpPr/p:cNvPr', NAMESPACES).get('name'), 'linked-group')
        clicks = slide.findall('.//a:hlinkClick', NAMESPACES)
        self.assertEqual(len(clicks), 2)
        self.assertEqual(len({node.get(f'{{{NAMESPACES["r"]}}}id') for node in clicks}), 1)


if __name__ == '__main__':
    unittest.main()
