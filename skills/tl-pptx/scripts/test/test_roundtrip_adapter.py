"""Task-local native import/rebuild and adversarial workspace regression tests."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches
from authoring_roundtrip import ROUNDTRIP_PAGE_PLAN_SCHEMA
from tl_guard import ContractError
from tl_roundtrip import analyze_pptx, import_workspace, rebuild_workspace, inspect_workspace_zip, _validate_edit


class RoundtripAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous = os.getcwd()
        os.chdir(self.temporary.name)
        self.addCleanup(self.cleanup)
        deck = Presentation()
        for text in ('First slide', 'Second slide'):
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(3), Inches(2)).text = text
            slide.notes_slide.notes_text_frame.text = 'Note ' + text
        deck.save('source.pptx')
        self.original = Path('source.pptx').read_bytes()
        self.bundle, self.evidence = import_workspace('source.pptx')
        Path('workspace.zip').write_bytes(self.bundle)

    def cleanup(self):
        os.chdir(self.previous)
        self.temporary.cleanup()

    def test_unchanged_native_slide_bytes(self):
        output, evidence = rebuild_workspace('workspace.zip')
        original = zipfile.ZipFile(io.BytesIO(self.original))
        rebuilt = zipfile.ZipFile(io.BytesIO(output))
        for name in ('ppt/slides/slide1.xml', 'ppt/slides/slide2.xml'):
            self.assertEqual(original.read(name), rebuilt.read(name))
        source_notes = original.read('ppt/notesSlides/notesSlide1.xml')
        self.assertIn(source_notes, [rebuilt.read(name) for name in rebuilt.namelist() if name.startswith('ppt/notesSlides/')])
        self.assertEqual(evidence['passthrough_pages'], [1, 2])
        self.assertEqual(Path('source.pptx').read_bytes(), self.original)
        self.assertFalse(list(Path('.').glob('tl-pptx-*')))

    def test_edit_native_text(self):
        entries = inspect_workspace_zip(self.bundle)['entries']
        data = entries['authoring-svg-flat/slide_01.svg'].replace(b'First', b'Edited')
        Path('edited.svg').write_bytes(data)
        output, evidence = rebuild_workspace('workspace.zip', edits={'slide_01.svg': 'edited.svg'})
        self.assertIn(b'Edited', zipfile.ZipFile(io.BytesIO(output)).read('ppt/slides/slide1.xml'))
        self.assertEqual(evidence['patched_pages'], [1])

    def test_reorder_and_clone(self):
        Path('plan.json').write_text(json.dumps({'schema': ROUNDTRIP_PAGE_PLAN_SCHEMA,
                                              'pages': [{'source_slide': 2}, {'source_slide': 1}, {'source_slide': 2}]}))
        output, evidence = rebuild_workspace('workspace.zip', page_plan='plan.json')
        deck = Presentation(io.BytesIO(output))
        self.assertEqual(len(deck.slides), 3)
        self.assertEqual([slide.shapes[0].text for slide in deck.slides], ['Second slide', 'First slide', 'Second slide'])
        self.assertEqual(evidence['passthrough_pages'], [1, 2, 3])

    def test_notes_overlay(self):
        output, evidence = rebuild_workspace('workspace.zip', notes={'slide_01': 'Changed notes'})
        deck = Presentation(io.BytesIO(output))
        self.assertIn('Changed notes', deck.slides[0].notes_slide.notes_text_frame.text)
        self.assertEqual(evidence['patched_pages'], [1])

    def test_transition_overlay(self):
        config = json.loads(inspect_workspace_zip(self.bundle)['entries']['animations.json'])
        config.setdefault('slides', {}).setdefault('slide_01', {})['transition'] = {'effect': 'fade', 'duration': 0.5}
        output, evidence = rebuild_workspace('workspace.zip', animation_config=config)
        self.assertIn(b'fade', zipfile.ZipFile(io.BytesIO(output)).read('ppt/slides/slide1.xml'))
        self.assertEqual(evidence['patched_pages'], [1])

    def test_analysis_and_no_follow(self):
        data, _ = analyze_pptx('source.pptx')
        self.assertEqual(json.loads(data)['slide_count'], 2)
        self.assertIn('First slide', json.dumps(json.loads(data)['content']))
        Path('link.pptx').symlink_to('source.pptx')
        with self.assertRaises(OSError):
            analyze_pptx('link.pptx')

    def test_edited_external_resource_refused(self):
        entries = inspect_workspace_zip(self.bundle)['entries']
        text = entries['authoring-svg-flat/slide_01.svg'].decode().replace('</svg>', '<image href="file:///etc/passwd"/></svg>')
        Path('edited.svg').write_text(text)
        with self.assertRaises(ContractError):
            rebuild_workspace('workspace.zip', edits={'slide_01.svg': 'edited.svg'})

    def test_archive_attacks(self):
        cases = [('../escape', b'x'), ('payload.BIN', '<!DOCTYPE a [<!ENTITY x "boom">]><a>&x;</a>'.encode('utf-16')),
                 ('overflow.json', b'{"x":1e309}'), ('x', b'file')]
        for name, data in cases:
            with self.subTest(name=name):
                output = io.BytesIO()
                with zipfile.ZipFile(output, 'w') as archive:
                    archive.writestr(name, data)
                    if name == 'x':
                        archive.writestr('x/child', b'collision')
                with self.assertRaises(ContractError):
                    inspect_workspace_zip(output.getvalue())

    def test_manifest_hash_refused(self):
        entries = inspect_workspace_zip(self.bundle)['entries']
        entries['notes/slide_01.md'] += b'tamper'
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        with self.assertRaisesRegex(ContractError, 'hash'):
            inspect_workspace_zip(output.getvalue())

    def test_native_reference_amplification_refused(self):
        from native_payloads import serialize_native_payload_store
        root = Path('synthetic').resolve()
        (root / 'templates').mkdir(parents=True)
        (root / 'authoring-svg-flat').mkdir()
        raw = b'<p:style xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><!--' + b'x' * 1048576 + b'--></p:style>'
        digest = hashlib.sha256(raw).hexdigest()
        reference = 'project:templates/native_payloads.json.gz#sha256:' + digest
        shape = '<rect width="1" height="1" data-pptx-shape-style-ref="' + reference + '"/>'
        start = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        destination = root / 'authoring-svg-flat/slide.svg'
        destination.write_text(start + shape + '</svg>')
        (root / 'templates/native_payloads.json.gz').write_bytes(serialize_native_payload_store({digest: raw}))
        with self.assertRaisesRegex(ContractError, 'budget'):
            _validate_edit((start + shape * 40 + '</svg>').encode(), destination, root)

    def test_slide_budget_precedes_converter(self):
        from tl_roundtrip import _import_private
        presentation = (
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst>' + ''.join(f'<p:sldId id="{256 + index}" r:id="r{index}"/>' for index in range(51))
            + '</p:sldIdLst></p:presentation>'
        )
        parts = {
            '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
            'ppt/presentation.xml': presentation,
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, data in parts.items():
                archive.writestr(name, data)
        payload = output.getvalue()
        Path('oversized.pptx').write_bytes(payload)
        with patch('pptx_to_svg.converter.convert_pptx_to_svg') as convert:
            for operation in (lambda: analyze_pptx('oversized.pptx'),
                              lambda: import_workspace('oversized.pptx'),
                              lambda: _import_private(payload, Path('unused-private'))):
                with self.assertRaisesRegex(ContractError, 'fifty'):
                    operation()
            convert.assert_not_called()
        self.assertFalse(Path('unused-private').exists())

    def test_empty_directory_supported(self):
        entries = inspect_workspace_zip(self.bundle)['entries']
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('empty/', b'')
            for name, data in entries.items():
                archive.writestr(name, data)
        self.assertEqual(inspect_workspace_zip(output.getvalue())['metadata']['source_sha256'], hashlib.sha256(self.original).hexdigest())


if __name__ == '__main__':
    unittest.main()
