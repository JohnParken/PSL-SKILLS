"""Controlled template imports, immutable bundles and hostile resource preflight."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import gzip
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from pptx import Presentation
from tl_guard import ContractError
from tl_roundtrip import inspect_workspace_zip, pack_workspace
from tl_templates import _bundle, _guard_svg_resources, import_template, inspect_payload_stores


@contextmanager
def task_directory():
    """Run only on an isolated test-owned task directory."""
    previous = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            yield Path(directory)
        finally:
            os.chdir(previous)


class TemplateAdapterTests(unittest.TestCase):
    """No gate bypass: registration/application/preview remain fail-closed."""

    def test_import_creates_portable_bundle_and_preserves_source(self):
        with task_directory() as root:
            deck = Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[0])
            slide.shapes.title.text = 'Template import'
            slide.placeholders[1].text = 'Editable source'
            deck.save('source.pptx')
            source = Path('source.pptx').read_bytes()
            payload, report = import_template('source.pptx', inheritance_mode='both')
            self.assertEqual(report['slide_count'], 1)
            self.assertEqual(report['master_count'], 1)
            self.assertFalse(report['template_created'])
            bundle = inspect_workspace_zip(payload)
            self.assertEqual(bundle['entries']['sources/source.pptx'], source)
            self.assertEqual(bundle['metadata']['kind'], 'template-source')
            self.assertIn('analysis/native_structure.json', bundle['entries'])
            self.assertTrue(any(name.startswith('authoring-svg-flat/') for name in bundle['entries']))
            self.assertEqual(Path('source.pptx').read_bytes(), source)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['source.pptx'])

    def test_import_rejects_directory_filename_and_invalid_options(self):
        with self.assertRaises(ContractError):
            import_template('../source.pptx')
        with self.assertRaises(ContractError):
            import_template('source.pptx', inheritance_mode='invented')

    def test_slide_budget_stops_before_converter_or_private_workspace(self):
        with task_directory() as root:
            deck = Presentation()
            for _index in range(51):
                deck.slides.add_slide(deck.slide_layouts[6])
            deck.save('large.pptx')
            with patch('pptx_to_svg.convert_pptx_to_svg') as converter:
                with self.assertRaisesRegex(ContractError, 'before conversion'):
                    import_template('large.pptx')
                converter.assert_not_called()
            self.assertEqual([path.name for path in root.iterdir()], ['large.pptx'])

    def test_bundle_identity_must_match(self):
        with task_directory():
            root = Path('owned')
            root.mkdir()
            (root / 'note.txt').write_text('author data', encoding='utf-8')
            payload = pack_workspace(root, kind='project-workspace')
            Path('input.zip').write_bytes(payload)
            result = _bundle('input.zip')
            self.assertEqual(result['entries']['note.txt'], b'author data')
            with self.assertRaises(ContractError):
                _bundle('./input.zip')

    def test_relative_bundled_image_is_allowed_without_external_read(self):
        stream = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(stream, format='PNG')
        svg = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
               b'<image href="../images/logo.png" width="10" height="10"/></svg>')
        entries = {'templates/page.svg': svg, 'images/logo.png': stream.getvalue()}
        _guard_svg_resources(entries)
        self.assertEqual(entries['templates/page.svg'], svg)

    def test_bundle_icon_is_materialized_in_returned_snapshot_only(self):
        page = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
                b'<use data-icon="imported/item" x="5" y="8" width="20" height="20"/></svg>')
        icon = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" data-icon-style="preserve-color">'
                b'<rect width="20" height="20" fill="#123456"/></svg>')
        entries = {'templates/page.svg': page, 'icons/imported/item.svg': icon}
        snapshots = _guard_svg_resources(entries)
        self.assertEqual(entries['templates/page.svg'], page)
        self.assertNotIn(b'data-icon=', snapshots['templates/page.svg'])
        self.assertIn(b'#123456', snapshots['templates/page.svg'])
        self.assertNotEqual(snapshots['templates/page.svg'], page)

    def test_external_resource_and_escape_are_rejected(self):
        for target in ('../../secret.png', 'file:///secret.png', 'https://example.com/logo.png'):
            svg = f'<svg xmlns="http://www.w3.org/2000/svg"><image href="{target}"/></svg>'.encode()
            with self.subTest(target=target), self.assertRaises(ContractError):
                _guard_svg_resources({'templates/page.svg': svg})

    def test_gzip_budget_and_native_xml_safety(self):
        with patch('tl_templates.MAX_MEMBER_BYTES', 64):
            with self.assertRaisesRegex(ContractError, 'decompression budget'):
                inspect_payload_stores({'native-payloads/store.json.gz': gzip.compress(b'x' * 65)})
        hostile = b'<!DOCTYPE x [<!ENTITY e "unsafe">]><x>&e;</x>'
        document = json.dumps({'payloads': {'a': base64.b64encode(hostile).decode()}}).encode()
        with self.assertRaises(ContractError):
            inspect_payload_stores({'native-payloads/store.json.gz': gzip.compress(document)})

    def test_gzip_valid_store_is_only_preflight_not_hydration(self):
        document = json.dumps({'payloads': {'a': base64.b64encode(b'<shape/>').decode()}}).encode()
        result = inspect_payload_stores({'native-payloads/store.json.gz': gzip.compress(document)})
        self.assertEqual(result['gzip_resources'], 1)
        self.assertGreater(result['expanded_store_bytes'], len(document))


if __name__ == '__main__':
    unittest.main()
