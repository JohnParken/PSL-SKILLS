"""Synthetic Note anchor packages with tolerant-read expectations."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import io
import sys
import unittest
import zipfile
from unittest.mock import patch
from xml.parsers import expat
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_sheet_comments import sample
from test_wps_ooxml_sheet import build_package, codes, result, R, P


def fixture(vml=None):
    with zipfile.ZipFile(io.BytesIO(sample())) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts['[Content_Types].xml'] = parts['[Content_Types].xml'].decode().replace(
        '</Types>', '<Default Extension="vml" ContentType="application/'
        'vnd.openxmlformats-officedocument.vmlDrawing"/></Types>').encode()
    parts['xl/worksheets/_rels/sheet1.xml.rels'] = parts[
        'xl/worksheets/_rels/sheet1.xml.rels'].decode().replace(
        '</Relationships>', '<Relationship Id="v1" Type="' + R + '/vmlDrawing" '
        'Target="../drawings/notes.vml"/></Relationships>').encode()
    parts['xl/worksheets/sheet1.xml'] = parts['xl/worksheets/sheet1.xml'].decode().replace(
        '</worksheet>', '<legacyDrawing r:id="v1"/></worksheet>').encode()
    parts['xl/drawings/notes.vml'] = (vml or (
        '<xml xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:x="urn:schemas-microsoft-com:office:excel"><v:shape id="note1">'
        '<v:textbox><div/></v:textbox><x:ClientData ObjectType="Note">'
        '<x:Anchor>1,15,0,2,3,15,3,16</x:Anchor><x:Row>1</x:Row>'
        '<x:Column>1</x:Column></x:ClientData></v:shape></xml>')).encode()
    return build_package(parts)


DEFAULT_VML = (
    '<xml xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:x="urn:schemas-microsoft-com:office:excel"><v:shape id="note1">'
    '<v:textbox><div/></v:textbox><x:ClientData ObjectType="Note">'
    '<x:Anchor>1,15,0,2,3,15,3,16</x:Anchor><x:Row>1</x:Row>'
    '<x:Column>1</x:Column></x:ClientData></v:shape></xml>')


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class NoteAnchors(unittest.TestCase):
    def test_source_and_cell_anchor_independent_rectangle(self):
        value = result(fixture())
        self.assertEqual(value['status'], 'partial', value)
        note = value['sheets'][0]['comments'][0]
        self.assertEqual((note['cell'], note['text']), ('B2', '核对余额'))
        self.assertEqual(note['anchor']['components'], [1, 15, 0, 2, 3, 15, 3, 16])
        self.assertEqual(note['anchor']['cell_row_zero_based'], 1)
        self.assertEqual(note['anchor']['source']['part'], 'xl/drawings/notes.vml')
        self.assertFalse(note['anchor']['layout_decoded'])
        self.assertEqual(value['cell_count'], 1)

    def test_bad_anchors_degrade_without_losing_comment_text(self):
        for replacement in (
                DEFAULT_VML.replace('<x:Row>1', '<x:Row>2'),
                DEFAULT_VML.replace('<x:Row>1</x:Row>', ''),
                DEFAULT_VML.replace('<x:Row>1</x:Row>', '<x:Row>1</x:Row>' * 2),
                DEFAULT_VML.replace('1,15,0,2,3,15,3,16', '3,15,0,2,1,15,3,16'),
                DEFAULT_VML.replace('<x:Column>1', '<x:Column>16384')):
            with self.subTest(replacement=replacement):
                value = result(fixture(replacement))
                self.assertEqual(value['status'], 'partial')
                self.assertEqual(value['sheets'][0]['comments'][0]['text'], '核对余额')
                self.assertNotIn('anchor', value['sheets'][0]['comments'][0])
                self.assertTrue(codes(value) & {
                    'vml_anchor_not_interpreted', 'vml_anchor_without_comment',
                    'duplicate_vml_anchor_skipped'})
        with patch('wps_vml_notes.MAX_SHAPES', 0):
            value = result(fixture())
            self.assertEqual(value['status'], 'partial')
            self.assertIn('vml_shape_limit_reached', codes(value))
            self.assertEqual(value['sheets'][0]['comments'][0]['text'], '核对余额')
            self.assertNotIn('anchor', value['sheets'][0]['comments'][0])

    def test_active_content_and_unknown_vml_elements_are_recorded(self):
        cases = {
            DEFAULT_VML.replace('<div/>', '<div>lost alt text</div>'):
                'vml_textbox_text_not_interpreted',
            DEFAULT_VML.replace('<v:shape id="note1"',
                                '<v:shape href="https://example.invalid" id="note1"'):
                'vml_active_attribute_not_executed',
            DEFAULT_VML.replace('<x:Row>1</x:Row>',
                                '<x:FmlaMacro>RUN</x:FmlaMacro>'):
                'vml_element_not_interpreted',
        }
        for vml, code in cases.items():
            with self.subTest(code=code):
                value = result(fixture(vml))
                self.assertEqual(value['status'], 'partial')
                self.assertIn(code, codes(value))
                self.assertEqual(value['sheets'][0]['comments'][0]['text'], '核对余额')
        parts = fixture()
        with zipfile.ZipFile(io.BytesIO(parts)) as archive:
            archive_parts = {name: archive.read(name) for name in archive.namelist()}
        archive_parts['xl/drawings/_rels/notes.vml.rels'] = (
            '<Relationships xmlns="' + P + '"><Relationship Id="i" Type="' + R +
            '/image" Target="https://example.invalid/x" TargetMode="External"/>'
            '</Relationships>').encode()
        value = result(build_package(archive_parts))
        self.assertEqual(value['status'], 'partial')
        self.assertIn('external_relationship_not_followed', codes(value))
        self.assertFalse(value['external_links_followed'])

    def test_relationship_id_and_content_type_variance_degrade(self):
        with zipfile.ZipFile(io.BytesIO(fixture())) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        bad_id = dict(parts)
        bad_id['xl/worksheets/sheet1.xml'] = parts['xl/worksheets/sheet1.xml'].decode().replace(
            'r:id="v1"', 'r:id="bad"').encode()
        value = result(build_package(bad_id))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['sheets'][0]['comments'][0]['text'], '核对余额')
        wrong_type = dict(parts)
        wrong_type['[Content_Types].xml'] = parts['[Content_Types].xml'].decode().replace(
            'application/vnd.openxmlformats-officedocument.vmlDrawing',
            'application/xml').encode()
        value = result(build_package(wrong_type))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['sheets'][0]['comments'][0]['text'], '核对余额')


if __name__ == '__main__':
    unittest.main()
