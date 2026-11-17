"""Controlled Boolean adapter tests using the real pathops and upstream core."""
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pathops
from tl_geometry import boolean_document
from tl_guard import ContractError
from svg_to_pptx import text_outline
from svg_to_pptx.shape_boolean import _shape_commands
from svg_to_pptx.shape_boolean_paths import _commands_to_pathops

SOURCE = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
          b'<g transform="translate(20 30)"><rect id="a" width="10" height="10"/>'
          b'<rect id="b" x="5" width="10" height="10"/></g></svg>')


@contextmanager
def task_directory():
    """Use a synthetic task cwd and restore it even if the adapter fails."""
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        try:
            os.chdir(directory)
            yield Path(directory)
        finally:
            os.chdir(previous)


class GeometryAdapterTests(unittest.TestCase):
    """Verify filled geometry, source protection and constrained resource access."""

    def test_all_five_operations(self):
        """Known square areas and transforms survive actual runtime conversion."""
        with task_directory() as task:
            Path('input.svg').write_bytes(SOURCE)
            for operation, area in [('union', 150), ('combine', 100), ('fragment', 150), ('intersect', 50), ('subtract', 50)]:
                with self.subTest(operation=operation):
                    document, evidence = boolean_document('input.svg', {
                        'operation': operation, 'source_ids': ['a', 'b'], 'output_id': 'merged', 'style': {'fill': '#112233'},
                    })
                    root = ET.fromstring(document)
                    paths = [_commands_to_pathops(_shape_commands(node), pathops) for node in root]
                    self.assertAlmostEqual(sum(path.area for path in paths), area)
                    self.assertEqual(len(paths), 3 if operation == 'fragment' else 1)
                    self.assertTrue(all(path.bounds[0] >= 20 and path.bounds[1] >= 30 for path in paths))
                    self.assertTrue(all(node.get('fill') == '#112233' for node in root))
                    self.assertEqual(root.get('viewBox'), '0 0 100 100')
                    self.assertEqual(evidence['coordinate_space'], 'SVG-root')
                    self.assertIn('z-order', evidence['placement'])
            self.assertEqual(Path('input.svg').read_bytes(), SOURCE)
            self.assertEqual([item.name for item in task.iterdir()], ['input.svg'])

    def test_text_missing_fonts_does_not_fallback(self):
        """An explicit empty font roster fails rather than scanning private fonts."""
        with task_directory() as task:
            Path('input.svg').write_bytes(SOURCE.replace(
                b'<rect id="b" x="5" width="10" height="10"/>',
                b'<text id="b" x="5" y="10" font-size="12" font-family="NotARealFont">X</text>'))
            with patch.object(text_outline, '_system_font_roots', side_effect=AssertionError('implicit scan')):
                with self.assertRaisesRegex(RuntimeError, 'no readable OpenType fonts'):
                    boolean_document('input.svg', {
                        'operation': 'union', 'source_ids': ['a', 'b'], 'output_id': 'merged', 'fonts': [],
                    })
            self.assertEqual([item.name for item in task.iterdir()], ['input.svg'])

    def test_reject_escape_and_external_resources(self):
        """Only bare input/fonts and inline SVG resources enter the core."""
        config = {'operation': 'union', 'source_ids': ['a', 'b'], 'output_id': 'merged'}
        with task_directory():
            Path('input.svg').write_bytes(SOURCE)
            for name in ('../input.svg', './input.svg', '/input.svg'):
                with self.assertRaises(ContractError):
                    boolean_document(name, config)
            with self.assertRaises(ContractError):
                boolean_document('input.svg', {**config, 'fonts': ['../secret.ttf']})
            with self.assertRaises(ContractError):
                boolean_document('input.svg', {**config, 'style': {'fill': 'url(https://example.com/a)'}})
            Path('input.svg').write_bytes(SOURCE.replace(b'</svg>', b'<image href="private.png"/></svg>'))
            with self.assertRaises(ContractError):
                boolean_document('input.svg', config)

    def test_explicit_font_snapshot_shapes_real_text(self):
        """Use an authorized font fixture, with implicit system lookup disabled."""
        provided = os.environ.get('TL_PPTX_TEST_FONT')
        if not provided:
            self.skipTest('TL_PPTX_TEST_FONT must identify an authorized installed font')
        font = Path(provided).read_bytes()
        with task_directory() as task:
            Path('fixture.ttf').write_bytes(font)
            Path('input.svg').write_bytes(SOURCE.replace(
                b'<rect id="b" x="5" width="10" height="10"/>',
                b'<text id="b" x="5" y="10" font-size="12" font-family="Arial">X</text>'))
            with patch.object(text_outline, '_system_font_roots', side_effect=AssertionError('implicit scan')):
                document, evidence = boolean_document('input.svg', {
                    'operation': 'union', 'source_ids': ['a', 'b'], 'output_id': 'merged', 'fonts': ['fixture.ttf'],
                })
            self.assertEqual(len(ET.fromstring(document)), 1)
            self.assertEqual(evidence['fonts'][0]['file'], 'fixture.ttf')
            self.assertEqual(Path('fixture.ttf').read_bytes(), font)
            self.assertEqual(sorted(item.name for item in task.iterdir()), ['fixture.ttf', 'input.svg'])

    def test_text_budget_precedes_geometry_and_font_reads(self):
        """Do not hand enormous glyph runs to Harfbuzz before enforcing limits."""
        with task_directory():
            Path('input.svg').write_bytes(SOURCE.replace(b'</svg>', b'<text>' + b'X' * 5001 + b'</text></svg>'))
            with patch('svg_to_pptx.shape_boolean.render_boolean_svg_fragments', side_effect=AssertionError('must not run')):
                with self.assertRaisesRegex(ContractError, 'text execution budget'):
                    boolean_document('input.svg', {
                        'operation': 'union', 'source_ids': ['a', 'b'], 'output_id': 'merged', 'fonts': ['missing.ttf'],
                    })

    def test_referenced_paint_is_not_silently_lost(self):
        """Standalone paths must not retain dangling source gradient references."""
        config = {'operation': 'union', 'source_ids': ['a', 'b'], 'output_id': 'merged'}
        with task_directory():
            Path('input.svg').write_bytes(SOURCE.replace(b'id="a"', b'id="a" fill="url(#gradient)"'))
            with self.assertRaisesRegex(ContractError, 'Unknown SVG paint'):
                boolean_document('input.svg', config)
            document, _ = boolean_document('input.svg', {**config, 'style': {'fill': 'red'}})
            self.assertEqual(ET.fromstring(document)[0].get('fill'), 'red')

    def test_gradient_dependency_closure_preserves_original_definitions(self):
        """Copy IDs, stops and gradient transforms without old drawing ancestors."""
        definitions = (
            b'<defs><linearGradient id="base" gradientUnits="userSpaceOnUse" gradientTransform="rotate(20)">'
            b'<stop offset="0" stop-color="#123456"/><stop offset="1" stop-color="#abcdef"/>'
            b'</linearGradient><linearGradient id="paint" href="#base" gradientTransform="translate(2 3)"/>'
            b'<linearGradient id="unused"><stop offset="0" stop-color="red"/></linearGradient></defs>'
        )
        source = SOURCE.replace(b'<g ', definitions + b'<g ').replace(b'id="a"', b'id="a" fill="url (#paint)"')
        with task_directory():
            Path('input.svg').write_bytes(source)
            document, evidence = boolean_document('input.svg', {
                'operation': 'fragment', 'source_ids': ['a', 'b'], 'output_id': 'merged',
            })
            root = ET.fromstring(document)
            original = ET.fromstring(source)
            by_id = {node.get('id'): node for node in root.iter() if node.get('id')}
            original_ids = {node.get('id'): node for node in original.iter() if node.get('id')}
            for name in ('base', 'paint'):
                self.assertEqual(ET.tostring(by_id[name]), ET.tostring(original_ids[name]))
            self.assertNotIn('unused', by_id)
            self.assertNotIn('a', by_id)
            self.assertEqual(evidence['result_ids'], ['merged-1', 'merged-2', 'merged-3'])
            self.assertTrue(all(node.get('fill') == 'url (#paint)' for node in list(root)[1:]))
            self.assertNotIn('transform', root.attrib)
            self.assertEqual(Path('input.svg').read_bytes(), source)

    def test_definition_cycles_and_drawing_dependencies_fail(self):
        """Cycles and references to ordinary source drawings are not copied."""
        config = {'operation': 'union', 'source_ids': ['a', 'b'], 'output_id': 'merged'}
        with task_directory():
            for definitions in (
                b'<defs><linearGradient id="paint" href="#paint"/></defs>',
                b'<defs><linearGradient id="paint" href="#a"/></defs>',
            ):
                source = SOURCE.replace(b'<g ', definitions + b'<g ').replace(b'id="a"', b'id="a" fill="url(#paint)"')
                Path('input.svg').write_bytes(source)
                with self.assertRaises(ContractError):
                    boolean_document('input.svg', config)

    def test_invalid_manifest_is_rejected(self):
        """Bad types and options cannot reach upstream parsing or directory scans."""
        for config in ([], {'operation': []}, {'operation': 'union', 'source_ids': [[], []]},
                       {'operation': 'union', 'source_ids': ['a', 'b'], 'output_id': 'r', 'font_dirs': ['.']}):
            with self.assertRaises(ContractError):
                boolean_document('input.svg', config)


if __name__ == '__main__':
    unittest.main()
