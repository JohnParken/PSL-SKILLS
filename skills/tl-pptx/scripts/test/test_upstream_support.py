"""Isolated synthetic acceptance of the retained upstream support APIs."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pptx import Presentation
from pptx_embedded_fonts import (
    EmbeddedFontError, FONT_REL_TYPE, PML_NS, REL_NS, capture_embedded_fonts,
    embedded_font_typefaces, load_embedded_font_bundle, write_embedded_font_bundle,
)
from pptx_workspace import (
    inventory_package_resources, write_workspace_resources, workspace_resource_specs,
    slide_animation_config_sha256, validate_roundtrip_output_path, source_pptx_path,
)
from pptx_workspace_formats import _sniff_resource_format
from pptx_ooxml.clone import clone_presentation_slides
from pptx_opc_validation import canonical_opc_part_path


class SupportTests(unittest.TestCase):
    """Exercise only owned temporary fixtures, never scan user directories."""

    def test_font_sidecar_roundtrip_and_tamper(self):
        """Capture relationship-addressed bytes and reject modified sidecars."""
        root = ET.fromstring(
            f'<p:presentation xmlns:p="{PML_NS}" xmlns:r="{REL_NS}">'
            '<p:embeddedFontLst><p:embeddedFont><p:font typeface="Fixture"/>'
            '<p:regular r:id="rId5"/></p:embeddedFont></p:embeddedFontLst></p:presentation>'
        )
        bundle = capture_embedded_fonts(root, {'rId5': {
            'type': FONT_REL_TYPE, 'target': 'ppt/fonts/font1.fntdata',
        }}, lambda name: b'fixture-font-data' if name == 'ppt/fonts/font1.fntdata' else b'')
        self.assertIsNotNone(bundle)
        self.assertEqual(embedded_font_typefaces(bundle), ('Fixture',))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            descriptor, paths = write_embedded_font_bundle(base, bundle)
            self.assertEqual(load_embedded_font_bundle(base, descriptor), bundle)
            (base / paths[0]).write_bytes(b'tampered')
            with self.assertRaisesRegex(EmbeddedFontError, 'checksum'):
                load_embedded_font_bundle(base, descriptor)
            with self.assertRaises(EmbeddedFontError):
                load_embedded_font_bundle(base, {'manifest': '../escape.json'})

    def test_font_external_relationship_rejected_before_read(self):
        """External font relationships never invoke the caller's part reader."""
        root = ET.fromstring(
            f'<p:presentation xmlns:p="{PML_NS}" xmlns:r="{REL_NS}">'
            '<p:embeddedFontLst><p:embeddedFont><p:font typeface="Fixture"/>'
            '<p:bold r:id="r1"/></p:embeddedFont></p:embeddedFontLst></p:presentation>'
        )
        with self.assertRaisesRegex(EmbeddedFontError, 'internal font'):
            capture_embedded_fonts(root, {'r1': {'type': FONT_REL_TYPE, 'external': 'true'}},
                                   lambda _: self.fail('External reader invoked'))

    def test_resource_inventory_write_specs_and_collision(self):
        """Inventory exact package resources and preserve conflicting user bytes."""
        payload = b'\x89PNG\r\n\x1a\nfixture'
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('ppt/media/image1.png', payload)
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            inventory = inventory_package_resources(archive)
        self.assertEqual(len(inventory.resources), 1)
        self.assertEqual(inventory.resources[0].kind, 'image')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            paths = write_workspace_resources(base, inventory)
            specs = workspace_resource_specs(base, {'resources': inventory.manifest()})
            self.assertFalse(specs[0].changed)
            self.assertEqual(specs[0].source_sha256, hashlib.sha256(payload).hexdigest())
            self.assertEqual(write_workspace_resources(base, inventory), paths)
            (base / paths[0]).write_bytes(b'owned-other-data')
            with self.assertRaisesRegex(RuntimeError, 'collides'):
                write_workspace_resources(base, inventory)
            self.assertEqual((base / paths[0]).read_bytes(), b'owned-other-data')

    def test_formats_animation_digest_and_protected_output(self):
        """Pure format helpers and per-slide animation hashing retain behavior."""
        self.assertEqual(_sniff_resource_format(b'GIF89a'), 'gif')
        self.assertEqual(_sniff_resource_format(b'<svg/>'), 'svg')
        self.assertIsNone(_sniff_resource_format(b'not a resource'))
        config = {'duration': 1, 'slides': {'one': {'x': 1}, 'two': {'x': 2}}}
        digest = slide_animation_config_sha256(config, 'one')
        config['slides']['two']['x'] = 3
        self.assertEqual(slide_animation_config_sha256(config, 'one'), digest)
        config['slides']['one']['x'] = 9
        self.assertNotEqual(slide_animation_config_sha256(config, 'one'), digest)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'overwrite source'):
                validate_roundtrip_output_path(base, source_pptx_path(base), {})

    def test_clone_reordered_duplicate_slides_preserves_source(self):
        """Real PPTX clone is reopened with requested text roster and source intact."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, output = base / 'source.pptx', base / 'cloned.pptx'
            deck = Presentation()
            for text in ('First', 'Second'):
                slide = deck.slides.add_slide(deck.slide_layouts[6])
                slide.shapes.add_textbox(0, 0, 1000000, 1000000).text = text
            deck.save(source)
            original = source.read_bytes()
            clone_presentation_slides(source, (2, 1, 2), output)
            result = Presentation(output)
            self.assertEqual([slide.shapes[0].text for slide in result.slides], ['Second', 'First', 'Second'])
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse(list(base.glob('.pptx-clone-verify-*')))
            with self.assertRaisesRegex(RuntimeError, 'missing source slide'):
                clone_presentation_slides(source, (99,), base / 'invalid.pptx')
            self.assertFalse((base / 'invalid.pptx').exists())

    def test_opc_paths_reject_traversal(self):
        """Noncanonical package paths cannot be treated as valid OPC members."""
        self.assertEqual(canonical_opc_part_path('ppt/slides/slide1.xml'), 'ppt/slides/slide1.xml')
        self.assertIsNone(canonical_opc_part_path('../outside.xml'))
        self.assertIsNone(canonical_opc_part_path('ppt/../../outside.xml'))


if __name__ == '__main__':
    unittest.main()
