"""Native template contract and install-planning regressions on isolated fixtures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pptx import Presentation
from pptx.util import Inches
from apply_template import ApplyTemplateError, apply_templates, read_template_install
from pptx_ooxml.analyzer import _has_keyword, analyze_pptx
from register_template import SpecParseError, get_library_paths, is_safe_template_id, validate_style_workspace
from template_import.canvas_formats import CANVAS_FORMATS
from template_import.manifest import build_manifest
from template_import.native_structure import build_native_structure, write_native_structure_bundle


class TemplateCoreTests(unittest.TestCase):
    """Separate upstream library semantics from the future TL public safety entry."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.presentation = self.root / 'source.pptx'
        deck = Presentation()
        page = deck.slides.add_slide(deck.slide_layouts[0])
        page.shapes.title.text = 'Quarterly report'
        page.placeholders[1].text = 'Template preservation fixture'
        table = page.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(4), Inches(1)).table
        table.cell(0, 0).text = 'Item'
        table.cell(0, 1).text = 'Value'
        table.cell(1, 0).text = 'A'
        table.cell(1, 1).text = '10'
        deck.save(self.presentation)
        self.original = self.presentation.read_bytes()

    def tearDown(self):
        self.assertEqual(self.presentation.read_bytes(), self.original)
        self.temporary.cleanup()

    def style(self, name='style'):
        root = self.root / name
        (root / 'templates').mkdir(parents=True)
        shutil.copyfile(Path(__file__).with_name('template_style_fixture.txt'), root / 'templates/design_spec.md')
        return root

    def project(self):
        root = self.root / 'project'
        (root / 'templates').mkdir(parents=True)
        return root

    def test_manifest_preserves_native_hierarchy_and_text(self):
        manifest = build_manifest(self.presentation, self.root / 'imported', include_flat_svg=True)
        self.assertEqual(len(manifest['masters']), 1)
        self.assertEqual(len(manifest['layouts']), 11)
        self.assertEqual(len(manifest['slides']), 1)
        self.assertIn('Quarterly report', manifest['slides'][0]['textSamples'])
        self.assertEqual(manifest['slides'][0]['layoutPath'], 'ppt/slideLayouts/slideLayout1.xml')
        self.assertEqual(manifest['slideSize']['width_emu'], 9144000)
        contract = build_native_structure(self.presentation, manifest)
        self.assertTrue(contract['strategy']['preservationEligible'])
        self.assertEqual(contract['strategy']['recommendedMode'], 'preserve')
        self.assertEqual(contract['slides'][0]['layoutKey'], 'layout_01')
        self.assertEqual(contract['source']['sha256'], hashlib.sha256(self.original).hexdigest())

    def test_native_bundle_copies_source_bytes_and_contract(self):
        manifest = build_manifest(self.presentation, self.root / 'analysis')
        output = self.root / 'bundle'
        contract = write_native_structure_bundle(self.presentation, output, manifest)
        self.assertEqual((output / contract['source']['templateFile']).read_bytes(), self.original)
        self.assertEqual(list(output.rglob('native_structure.json'))[0].read_text().strip(),
                         json.dumps(contract, ensure_ascii=False, indent=2))

    def test_incomplete_graph_reports_template_mode(self):
        contract = build_native_structure(self.presentation, {'masters': [], 'layouts': [], 'slides': []})
        self.assertFalse(contract['strategy']['preservationEligible'])
        self.assertEqual(contract['strategy']['recommendedMode'], 'template')
        self.assertIn('incomplete-master-layout-graph', contract['strategy']['reasonCodes'])

    def test_analyzer_reports_real_text_and_table(self):
        result = analyze_pptx(self.presentation)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertIn('Quarterly report', serialized)
        self.assertIn('Value', serialized)
        self.assertIn('10', serialized)
        self.assertTrue(_has_keyword('目\n录', ('目录',)))
        self.assertFalse(_has_keyword('Qatar', ('qa',)))

    def test_style_schema_and_catalogs(self):
        result = validate_style_workspace(self.style())
        self.assertEqual(result['entry']['keywords'], ['postmortem', 'incident', 'reliability', 'root-cause', 'blameless'])

    def test_invalid_style_rejected(self):
        root = self.style()
        path = root / 'templates/design_spec.md'
        path.write_text(path.read_text().replace('## II. Communication Method', '## II. Missing section'), encoding='utf-8')
        with self.assertRaises(SpecParseError):
            validate_style_workspace(root)

    def test_catalog_id_mapping_keeps_nonletter_names(self):
        path = Path(__file__).resolve().parents[2] / 'assets/template-catalogs/image-renderings/catalog.json'
        catalog = json.loads(path.read_text())
        self.assertIn('3d-isometric', catalog)
        self.assertEqual(catalog['3d-isometric'], 'resource_3d-isometric.txt')
        self.assertTrue((path.parent / catalog['3d-isometric']).is_file())
        root = self.style()
        spec = root / 'templates/design_spec.md'
        spec.write_text(spec.read_text().replace('digital-dashboard', '3d-isometric'), encoding='utf-8')
        self.assertTrue(validate_style_workspace(root))
        spec.write_text(spec.read_text().replace('3d-isometric', 'not-a-real-catalog-id'), encoding='utf-8')
        with self.assertRaisesRegex(SpecParseError, 'unknown catalog id'):
            validate_style_workspace(root)

    def test_missing_yaml_is_explicit(self):
        root = self.style()
        with patch('register_template_parts.specification.yaml', None):
            with self.assertRaisesRegex(SpecParseError, 'PyYAML is not installed'):
                validate_style_workspace(root)

    def test_apply_dry_run_has_no_writes(self):
        root = self.style()
        project = self.project()
        plan = apply_templates(project, [str(root)], dry_run=True, validate=False)
        self.assertEqual(len(plan.mappings), 1)
        self.assertEqual(list((project / 'templates').iterdir()), [])
        self.assertFalse((project / 'template_install.json').exists())

    def test_apply_idempotence_and_receipt(self):
        root = self.style()
        project = self.project()
        first = apply_templates(project, [str(root)], validate=False)
        second = apply_templates(project, [str(root)], validate=False)
        self.assertEqual(first.installed_specs, ['design_spec.style.incident-postmortem.md'])
        self.assertEqual([mapping.status for mapping in second.mappings], ['identical'])
        receipt = json.loads((project / 'template_install.json').read_text())
        self.assertTrue(receipt)
        self.assertIsNotNone(read_template_install(project, second.root_snapshots, second.selection_sha256))

    def test_duplicate_root_and_collision_rejected(self):
        root = self.style()
        project = self.project()
        with self.assertRaises(ApplyTemplateError):
            apply_templates(project, [str(root), str(root)], validate=False)
        target = project / 'templates/design_spec.style.incident-postmortem.md'
        target.write_text('Do not overwrite', encoding='utf-8')
        with self.assertRaises(ApplyTemplateError):
            apply_templates(project, [str(root)], validate=False)
        self.assertEqual(target.read_text(), 'Do not overwrite')

    def test_explicit_task_library_and_safe_ids(self):
        directory, index = get_library_paths('brand', 'local', templates_dir=self.root)
        self.assertEqual(directory, self.root / 'local/brands')
        self.assertEqual(index, directory / 'brands_index.json')
        for value in ('../escape', '.', '..', 'trailing.', 'a/b'):
            self.assertFalse(is_safe_template_id(value))
        self.assertTrue(is_safe_template_id('brand-name'))
        self.assertEqual(CANVAS_FORMATS['ppt169']['viewbox'], '0 0 1280 720')


if __name__ == '__main__':
    unittest.main()
