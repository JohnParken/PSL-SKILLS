"""Behavioral tests of the memory-only PPT Master package adapter."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pptx import Presentation
from tl_assemble import OFFICE_REL_NS, REL_NS, assemble_pptx

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="100" height="50" fill="#ff0000"/></svg>'
SLIDE = '''<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
 <p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Editable"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
 <p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="952500" cy="476250"/></a:xfrm>
 <a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:sp></p:spTree></p:cSld></p:sld>'''


class TestRegistry:
    """Strict enough to observe shared-registry wiring in adapter tests."""
    __test__ = False

    def __init__(self, content_types):
        self.content_types = content_types
        self.names = set()

    def reserve_source(self, entries, _owner):
        """Record base reserved parts."""
        self.names.update(entries)


def fake_converter(_path, **_options):
    """Return one native shape without extra package resources."""
    return SLIDE, {}, [], [], {}, {}


class AssemblyTests(unittest.TestCase):
    """Validate relationships, notes and failure boundaries through the API."""

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.root = Path(self.workspace.name)
        self.svg = self.root / 'page.svg'
        self.svg.write_text(SVG, encoding='utf-8')

    def tearDown(self):
        self.workspace.cleanup()

    def assemble(self, converter=fake_converter, **options):
        """Call the real assembler with a controlled converter boundary."""
        return assemble_pptx([self.svg], resource_root=self.root, converter=converter,
                             registry_factory=TestRegistry, **options)

    def test_native_shape_and_notes_survive(self):
        payload, report = self.assemble(notes=['中文 speaker notes'])
        presentation = Presentation(io.BytesIO(payload))
        self.assertEqual(len(presentation.slides), 1)
        self.assertEqual(presentation.slides[0].shapes[0].name, 'Editable')
        self.assertIn('中文 speaker notes', presentation.slides[0].notes_slide.notes_text_frame.text)
        self.assertEqual(report['visual_validation'], 'not_run')
        self.assertEqual(list(self.root.iterdir()), [self.svg])

    def test_generated_parts_and_notes_relationship_collision(self):
        def converter(path, **options):
            result = list(fake_converter(path, **options))
            result[1] = {'embedded.png': b'not rendered in this structural fixture'}
            result[2] = [
                {'id': 'rId2', 'type': OFFICE_REL_NS + '/image', 'target': '../media/embedded.png'},
                {'id': 'rId3', 'type': OFFICE_REL_NS + '/chart', 'target': '../charts/chart1.xml'},
            ]
            result[4] = {
                'ppt/charts/chart1.xml': b'<chart/>',
                'ppt/charts/_rels/chart1.xml.rels': (
                    f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" '
                    f'Type="{OFFICE_REL_NS}/package" Target="../embeddings/data.xlsx"/></Relationships>'
                ).encode(),
                'ppt/embeddings/data.xlsx': b'workbook fixture',
            }
            result[5] = {'ppt/charts/chart1.xml': 'application/vnd.openxmlformats-officedocument.drawingml.chart+xml'}
            return tuple(result)
        payload, _report = self.assemble(converter, notes=['Keep me'])
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            self.assertEqual(package.read('ppt/embeddings/data.xlsx'), b'workbook fixture')
            root = ET.fromstring(package.read('ppt/slides/_rels/slide1.xml.rels'))
            ids = [item.get('Id') for item in root]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertIn('rId4', ids)
            self.assertIn('ppt/charts/chart1.xml', package.namelist())

    def test_passive_external_hyperlink_survives_real_converter(self):
        from tl_guard import validate_svg
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
               '<a href="https://example.com/page"><rect x="10" y="10" width="100" '
               'height="50" fill="#ff0000"/></a></svg>')
        validate_svg(svg.encode())
        self.svg.write_text(svg, encoding='utf-8')
        payload, _report = assemble_pptx([self.svg], resource_root=self.root)
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            relationships = ET.fromstring(package.read('ppt/slides/_rels/slide1.xml.rels'))
            links = [node for node in relationships if node.get('Type') == OFFICE_REL_NS + '/hyperlink']
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0].get('Target'), 'https://example.com/page')
            self.assertEqual(links[0].get('TargetMode'), 'External')

    def test_duplicate_relationship_rejected(self):
        def converter(path, **options):
            result = list(fake_converter(path, **options))
            result[2] = [{'id': 'rId1', 'type': OFFICE_REL_NS + '/slide', 'target': 'slide1.xml'}]
            return tuple(result)
        with self.assertRaisesRegex(ValueError, 'duplicate relationship'):
            self.assemble(converter)

    def test_dangling_part_rejected(self):
        def converter(path, **options):
            result = list(fake_converter(path, **options))
            result[2] = [{'id': 'rId2', 'type': OFFICE_REL_NS + '/chart', 'target': '../charts/missing.xml'}]
            return tuple(result)
        with self.assertRaisesRegex(ValueError, 'Dangling'):
            self.assemble(converter)

    def test_traversing_package_part_rejected(self):
        def converter(path, **options):
            result = list(fake_converter(path, **options))
            result[4] = {'../../outside.xml': b'<bad/>'}
            return tuple(result)
        with self.assertRaisesRegex(ValueError, 'part name'):
            self.assemble(converter)

    def test_base_part_collision_rejected(self):
        def converter(path, **options):
            result = list(fake_converter(path, **options))
            result[4] = {'ppt/presentation.xml': b'<bad/>'}
            return tuple(result)
        with self.assertRaisesRegex(ValueError, 'collision'):
            self.assemble(converter)

    def test_unsafe_xml_rejected(self):
        self.svg.write_text('<!DOCTYPE svg [<!ENTITY x "bad">]>' + SVG, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.assemble()

    def test_canvas_mismatch_rejected(self):
        second = self.root / 'other.svg'
        second.write_text(SVG.replace('1280 720', '800 600'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            assemble_pptx([self.svg, second], resource_root=self.root,
                          converter=fake_converter, registry_factory=TestRegistry)

    def test_converter_registry_shared_across_slides(self):
        seen = []
        def converter(path, **options):
            seen.append(options['package_parts'])
            return fake_converter(path, **options)
        payload, report = assemble_pptx([self.svg, self.svg], resource_root=self.root,
                                       converter=converter, registry_factory=TestRegistry)
        self.assertIs(seen[0], seen[1])
        self.assertEqual(report['slide_count'], 2)
        self.assertEqual(len(Presentation(io.BytesIO(payload)).slides), 2)


if __name__ == '__main__':
    unittest.main()
