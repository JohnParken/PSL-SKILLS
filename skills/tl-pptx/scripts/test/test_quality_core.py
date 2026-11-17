"""Full upstream quality-gate parity using only synthetic, local SVG inputs.

Expected diagnostics were independently captured from upstream 7e0df670.
Only temporary absolute paths are omitted; every other result field remains.
"""
import ast
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from svg_quality.checker import SVGQualityChecker
from svg_quality import checker as checker_module
from svg_quality.slot_capacity import slot_capacity_report
from svg_authoring_contract import canonical_authoring_errors
from svg_finalize.embed_icons import resolve_icon_path
import project_specs
import visualization_catalog
import text_measure

CASES = {
    'normal': '<g id="content"><rect x="10" y="10" width="100" height="50" fill="#123456"/></g>',
    'badpaint': '<g id="content"><rect width="100" height="50" fill="not-a-color"/></g>',
    'unsupported': '<foreignObject width="20" height="20"><div>content</div></foreignObject>',
    'hidden': '<g id="content" display="none"><rect width="100" height="50"/></g>',
    'opacity': '<g id="content"><rect width="100" height="50" opacity="5"/></g>',
    'text': '<g id="content"><text x="20" y="30" font-size="20">Hello</text></g>',
}


class QualityCoreTests(unittest.TestCase):
    """Real quality checks, full resource resolution, and contract helpers."""

    def test_full_checker_results_match_pinned_upstream(self):
        """All checks execute and produce unchanged receipts and diagnostics."""
        expected = json.loads(Path(__file__).with_name('quality_upstream_expected.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            for name, body in CASES.items():
                with self.subTest(case=name):
                    path = Path(directory) / (name + '.svg')
                    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
                                    'viewBox="0 0 1280 720">' + body + '</svg>', encoding='utf-8')
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = SVGQualityChecker().check_file(str(path))
                    result.pop('path', None)
                    self.assertEqual(result, expected[name])

    def test_no_optional_quality_dependency_is_silently_disabled(self):
        """Every upstream guarded dependency is actually supplied in this build."""
        tree = ast.parse(Path(checker_module.__file__).read_text(encoding='utf-8'))
        names = [alias.asname or alias.name
                 for node in tree.body if isinstance(node, ast.Try)
                 for statement in node.body if isinstance(statement, ast.ImportFrom)
                 for alias in statement.names]
        self.assertEqual(len(names), 104)
        self.assertEqual([name for name in names if getattr(checker_module, name, None) is None], [])

    def test_malformed_xml_and_missing_file_fail(self):
        """Invalid inputs are reported, never treated as quality passes."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'broken.svg'
            missing = SVGQualityChecker().check_file(str(path))
            self.assertFalse(missing['exists'])
            path.write_text('<svg><unclosed>', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                result = SVGQualityChecker().check_file(str(path))
            self.assertTrue(result['errors'])

    def test_all_visualization_resources_resolve(self):
        """Every live chart/table catalog entry has its exact packaged SVG."""
        entries = visualization_catalog.load_visualization_entries()
        self.assertEqual(len(entries), 39)
        for entry in entries.values():
            with self.subTest(reference=entry.reference):
                resolved = visualization_catalog.resolve_visualization_reference(entry.reference)
                self.assertTrue(resolved.path.is_file())
                ET.fromstring(resolved.path.read_bytes())

    def test_specification_assets_and_measurement_are_available(self):
        """Both schemas and scaffolds are packaged without a repository dependency."""
        self.assertEqual(len(project_specs.default_spec_lock_forbidden()), 3)
        for name in ('design_spec', 'spec_lock'):
            schema = project_specs.SCHEMA_DIR / (name + '.schema.json')
            scaffold = project_specs.SCAFFOLD_DIR / (name + '.txt')
            self.assertTrue(schema.is_file())
            self.assertTrue(scaffold.is_file())
            errors = project_specs.validate_markdown_schema(scaffold, schema)
            self.assertIsInstance(errors, list)
        self.assertGreater(text_measure.measure_text('Hello', size=20), 0)
        self.assertTrue(callable(slot_capacity_report))
        self.assertTrue(callable(canonical_authoring_errors))

    def test_slot_capacity_and_canonical_contract_execute(self):
        """Real placeholder geometry reaches the measurement and contract paths."""
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
               '<g id="slot" data-pptx-placeholder="body" data-pptx-bounds="10 10 300 100">'
               '<text data-pptx-carrier="true" x="10" y="30" font-size="20">Hello</text>'
               '</g></svg>')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'slot.svg'
            path.write_text(svg, encoding='utf-8')
            report = slot_capacity_report([path])
        self.assertTrue(report['advisory'])
        self.assertEqual(len(report['slots']), 1)
        self.assertEqual(report['slots'][0]['slot_id'], 'slot')
        self.assertIsInstance(canonical_authoring_errors(ET.fromstring(svg)), list)

    def test_icon_resolution_rejects_escape(self):
        """Project-local icon references cannot escape their supplied root."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in ('../secret', 'library/../../secret', '/tmp/secret'):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        resolve_icon_path(value, root)


if __name__ == '__main__':
    unittest.main()
