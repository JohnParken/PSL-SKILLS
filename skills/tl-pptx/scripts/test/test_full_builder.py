"""Full upstream builder integration, with synthetic local-only inputs.

Upstream: hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
MIT, Copyright (c) 2025-2026 Hugo He. Fixtures test structure, not Office rendering.
"""

from pathlib import Path
import sys
import posixpath
import hashlib
import tempfile
import unittest
from unittest.mock import patch
import wave
from xml.etree import ElementTree as ET
from zipfile import ZipFile

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from svg_to_pptx.drawingml.theme_fonts import MasterTextStyleSpec
from svg_to_pptx.pptx_package.builder import create_pptx_with_native_svg
from svg_to_pptx.pptx_package import builder_export_slides, narration
from svg_to_pptx.pptx_package.template_structure import (
    NativeLayoutSpec, NativeSlideSpec, NativeStructureContract,
)

NS = {
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
}


class FullBuilderTests(unittest.TestCase):
    """Exercise the complete upstream package builder, separate from basic API."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tl-full-builder-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'deck.pptx'

    def svg(self, *, structured=False, viewbox='0 0 1280 720'):
        """Create an independent valid two-shape page."""
        attrs = ''
        body = ''
        if structured:
            attrs = ('data-pptx-master="m" data-pptx-master-name="Master" '
                     'data-pptx-layout="l" data-pptx-layout-name="Layout"')
            body = ('<rect id="background" data-pptx-layer="master" '
                    'width="1280" height="720" fill="#FFFFFF"/>')
        body += ('<g id="hero" data-pptx-bounds="50 50 200 100">'
                 '<rect x="50" y="50" width="200" height="100" fill="#124567"/>'
                 '<text x="70" y="100" font-size="24">Native page</text></g>')
        path = self.root / 'page.svg'
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" {attrs}>{body}</svg>', encoding='utf-8')
        return path

    def export(self, path, **options):
        """Export in the private test directory and return every package part."""
        settings = {'pptx_structure': 'flat', 'verbose': False,
                    'use_compat_mode': False, 'native_objects': True,
                    'image_optimize': False}
        settings.update(options)
        self.assertTrue(create_pptx_with_native_svg([path], self.output, resource_root=self.root, **settings))
        with ZipFile(self.output) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        self.assertFalse(list(self.root.glob('.pptx-build-*')))
        return parts

    def audio(self):
        """Write a 100 ms PCM narration fixture."""
        path = self.root / 'voice.wav'
        with wave.Wave_write(str(path)) as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b'\0\0' * 800)
        return path

    def test_flat_native_slide_and_custom_canvas(self):
        """Keep editable shapes and an explicit custom portrait page size."""
        parts = self.export(self.svg(viewbox='0 0 720 1280'))
        slide = ET.fromstring(parts['ppt/slides/slide1.xml'])
        self.assertGreaterEqual(len(slide.findall('.//p:sp', NS)), 2)
        size = ET.fromstring(parts['ppt/presentation.xml']).find('p:sldSz', NS)
        self.assertEqual(size.get('type'), 'custom')
        self.assertEqual((size.get('cx'), size.get('cy')), ('6858000', '12192000'))

    def test_notes_and_notes_master_are_registered(self):
        """Preserve speaker notes and their package relationship closure."""
        parts = self.export(self.svg(), notes={'page': '# Intro\nSpeaker note'})
        note = ET.fromstring(parts['ppt/notesSlides/notesSlide1.xml'])
        self.assertIn('Speaker note', ' '.join(node.text or '' for node in note.findall('.//a:t', NS)))
        self.assertIn('ppt/notesMasters/notesMaster1.xml', parts)
        self.assertIn(b'notesSlide', parts['ppt/slides/_rels/slide1.xml.rels'])
        self.assertIn(b'notesMasterId', parts['ppt/presentation.xml'])

    def test_structured_master_and_layout_survive(self):
        """Use complete upstream reusable master and layout construction."""
        parts = self.export(self.svg(structured=True), pptx_structure='structured',
                            master_text_style_spec=MasterTextStyleSpec(3000, 1800))
        master = ET.fromstring(parts['ppt/slideMasters/slideMaster1.xml'])
        self.assertIsNotNone(master.find('p:cSld/p:bg', NS))
        relationships = ET.fromstring(parts['ppt/slides/_rels/slide1.xml.rels'])
        layout = next(row.get('Target') for row in relationships if row.get('Type', '').endswith('/slideLayout'))
        self.assertIn(posixpath.normpath(posixpath.join('ppt/slides', layout)), parts)
        self.assertIn(b'Master', parts['ppt/slideMasters/slideMaster1.xml'])

    def test_transition_object_animation_and_advance(self):
        """Preserve editable native timing and page transition XML."""
        parts = self.export(self.svg(), animation='entrance_fade', transition='push',
                            animation_duration=0.3, auto_advance=3)
        slide = ET.fromstring(parts['ppt/slides/slide1.xml'])
        self.assertIsNotNone(slide.find('p:timing', NS))
        transition = slide.find('p:transition', NS)
        self.assertEqual(transition.get('advTm'), '3000')
        self.assertIsNotNone(transition.find('p:push', NS))

    def test_narration_audio_and_recorded_timing(self):
        """Embed actual audio bytes; isolate only the optional ffprobe process."""
        audio = self.audio()
        with patch.object(builder_export_slides, 'probe_audio_duration', return_value=0.1):
            parts = self.export(self.svg(), narration_audio={'page': audio}, use_narration_timings=True)
        self.assertIn(audio.read_bytes(), parts.values())
        slide = ET.fromstring(parts['ppt/slides/slide1.xml'])
        self.assertEqual(len(slide.findall('.//p:audio', NS)), 1)
        self.assertEqual(slide.find('p:transition', NS).get('advClick'), '0')
        self.assertIn(b'/audio', parts['ppt/slides/_rels/slide1.xml.rels'])

    def test_unreadable_recorded_duration_fails_and_cleans(self):
        """Missing ffprobe duration must not silently omit requested timings."""
        with patch.object(builder_export_slides, 'probe_audio_duration', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'ffprobe'):
                self.export(self.svg(), narration_audio={'page': self.audio()}, use_narration_timings=True)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.pptx-build-*')))

    def preserved_source(self):
        """Build a valid local source package and explicit preservation contract."""
        path = self.svg()
        parts = self.export(path)
        source = self.root / 'source.pptx'
        self.output.rename(source)
        relationships = ET.fromstring(parts['ppt/slides/_rels/slide1.xml.rels'])
        layout_target = next(row.get('Target') for row in relationships if row.get('Type', '').endswith('/slideLayout'))
        layout_part = posixpath.normpath(posixpath.join('ppt/slides', layout_target))
        contract = NativeStructureContract(
            source_template=source, contract_path=self.root / 'native_structure.json',
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            slide_size_emu=(12192000, 6858000),
            layouts=(NativeLayoutSpec('retained', 'Retained', layout_part, 'original'),),
            slides=(NativeSlideSpec(1, 'ppt/slides/slide1.xml', 'retained'),),
        )
        path.write_text(path.read_text(encoding='utf-8').replace('<svg ', '<svg data-pptx-layout="retained" ', 1), encoding='utf-8')
        return path, source, parts, contract

    def test_preserve_roundtrip_retains_source_slide_and_master_bytes(self):
        """Preserve-mode passthrough must retain native source shapes and theme."""
        path, source, original, contract = self.preserved_source()
        before = source.read_bytes()
        parts = self.export(path, pptx_structure='preserve', native_structure_contract=contract,
                            roundtrip_passthrough_slides={1})
        for part in ('ppt/slides/slide1.xml', 'ppt/slideMasters/slideMaster1.xml', 'ppt/theme/theme1.xml'):
            self.assertEqual(parts[part], original[part])
        self.assertEqual(source.read_bytes(), before)

    def test_preserve_page_plan_can_clone_source_slide(self):
        """The complete source slide-cloning branch remains available."""
        path, source, original, contract = self.preserved_source()
        before = source.read_bytes()
        second = self.root / 'page_copy.svg'
        second.write_bytes(path.read_bytes())
        self.assertTrue(create_pptx_with_native_svg(
            [path, second], self.output, resource_root=self.root, verbose=False,
            pptx_structure='preserve', native_structure_contract=contract,
            roundtrip_page_sources=(1, 1), roundtrip_passthrough_slides={1, 2},
            native_objects=True, use_compat_mode=False,
        ))
        with ZipFile(self.output) as archive:
            self.assertEqual(archive.read('ppt/slides/slide1.xml'), original['ppt/slides/slide1.xml'])
            self.assertEqual(archive.read('ppt/slides/slide2.xml'), original['ppt/slides/slide1.xml'])
        self.assertEqual(source.read_bytes(), before)

    def test_upstream_repeated_narration_replaces_owned_audio_only(self):
        """Adapted from upstream test_edit_native_batch_a test_a6."""
        parts = self.export(self.svg())
        xml = parts['ppt/slides/slide1.xml'].decode('utf-8')
        for number in (1, 2):
            xml = narration.inject_narration(
                xml, shape_id=narration.next_shape_id(xml), shape_name='narration1.wav',
                audio_rid=f'rId{number * 3}', media_rid=f'rId{number * 3 + 1}',
                poster_rid=f'rId{number * 3 + 2}', start_delay=0.8,
            )
        final = ET.fromstring(xml)
        self.assertEqual(len(final.findall('.//p:pic', NS)), 1)
        self.assertEqual(len(final.findall('.//p:audio', NS)), 1)


if __name__ == '__main__':
    unittest.main()
