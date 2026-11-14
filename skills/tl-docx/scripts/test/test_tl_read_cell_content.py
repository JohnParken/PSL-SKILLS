"""Synthetic paragraph-boundary and drawing-coverage regressions; no user data."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_read import report
from test_tl_read import MemoryPackage

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
WPS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def fixture(content):
    """Wrap manually specified XML in a test-only OPC facade."""
    xml = f'<w:document xmlns:w="{W}" xmlns:a="{A}" xmlns:wps="{WPS}" xmlns:r="{R}"><w:body>{content}</w:body></w:document>'
    return MemoryPackage({'word/document.xml': xml})


def table(content):
    """Return one explicitly provided physical table cell."""
    return '<w:tbl><w:tr><w:tc>' + content + '</w:tc></w:tr></w:tbl>'


def paragraph(text):
    """Create a plain paragraph in synthetic XML."""
    return '<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>'


class CellContentTests(unittest.TestCase):
    """Retain text boundaries without inventing layout or image transcription."""

    def test_multiline_empty_and_inline_breaks(self):
        """Empty paragraphs are real boundaries; run breaks remain unchanged."""
        content = paragraph('First') + '<w:p/>' + '<w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r></w:p>'
        records, _, _ = report(fixture(table(content)))
        row = next(item for item in records if item['type'] == 'table_row')
        self.assertEqual(row['cells'][0]['text'], 'First\n\nA\tB\nC')

    def test_wrapped_and_nested_paragraph_order(self):
        """Cell summary keeps nested content with boundaries in XML order."""
        content = paragraph('Outer') + '<w:sdt><w:sdtContent>' + paragraph('Wrapped') + '</w:sdtContent></w:sdt>'
        content += table(paragraph('Nested')) + paragraph('Last')
        records, _, _ = report(fixture(table(content)))
        rows = [item for item in records if item['type'] == 'table_row']
        self.assertEqual(rows[0]['cells'][0]['text'], 'Outer\nWrapped\nNested\nLast')
        self.assertEqual(rows[1]['cells'][0]['text'], 'Nested')

    def test_revision_selection_and_single_paragraph_compatibility(self):
        """Block revision filtering agrees with selected visible paragraphs."""
        content = paragraph('Stable') + '<w:ins>' + paragraph('Added') + '</w:ins>'
        for mode, expected in [('accepted', 'Stable\nAdded'), ('original', 'Stable'), ('all', 'Stable\nAdded')]:
            records, _, _ = report(fixture(table(content)), revisions=mode)
            row = next(item for item in records if item['type'] == 'table_row')
            self.assertEqual(row['cells'][0]['text'], expected)
        records, _, _ = report(fixture(table(paragraph('One'))))
        self.assertEqual(next(item for item in records if item['type'] == 'table_row')['cells'][0]['text'], 'One')

    def test_legacy_cell_does_not_reintroduce_deleted_block_text(self):
        """Paragraph boundary repair must not lose the ancestor deletion context."""
        content = paragraph('Keep') + '<w:del>' + paragraph('Deleted') + '</w:del>'
        records, _, _ = report(fixture(table(content)))
        row = next(item for item in records if item['type'] == 'table_row')
        self.assertEqual(row['cells'][0]['text'], 'Keep')
        self.assertEqual(row['cells'][0]['deleted_text'], 'Deleted')

    def test_vector_shape_text_is_kept_but_geometry_is_partial(self):
        """Readable shape text does not imply geometry or rendering support."""
        drawing = '<w:p><w:r><w:drawing><wps:wsp><wps:txbx><w:txbxContent>' + paragraph('Shape text')
        drawing += '</w:txbxContent></wps:txbx></wps:wsp></w:drawing></w:r></w:p>'
        records, issues, coverage = report(fixture(drawing))
        self.assertTrue(any(item['type'] == 'textbox' and item['text'] == 'Shape text' for item in records))
        self.assertIn('drawing_geometry_not_rendered', {item['code'] for item in issues})
        self.assertEqual(coverage['status'], 'partial')
        self.assertEqual(coverage['drawing_content']['unsupported_locations'], 1)

    def test_empty_or_unknown_drawing_is_diagnosed(self):
        """Unrecognized drawings cannot silently look fully read."""
        for child in ('', '<unknown xmlns="urn:unimplemented"/>'):
            _, issues, coverage = report(fixture('<w:p><w:r><w:drawing>' + child + '</w:drawing></w:r></w:p>'))
            self.assertIn('drawing_content_not_read', {item['code'] for item in issues})
            self.assertEqual(coverage['status'], 'partial')

    def test_supported_image_is_not_misclassified_as_unknown(self):
        """Image references are read without claiming OCR or visual rendering."""
        package = fixture('<w:p><w:r><w:drawing><a:blip r:embed="image1"/></w:drawing></w:r></w:p>')
        package.names.add('word/media/image.png')
        package._parts['word/media/image.png'] = b'synthetic image bytes'
        package._relationships[(package.main, 'image1')] = {'type': R + '/image', 'target': 'word/media/image.png', 'external': False}
        records, issues, coverage = report(package)
        self.assertTrue(any(item['type'] == 'image' for item in records))
        self.assertNotIn('drawing_content_not_read', {item['code'] for item in issues})
        self.assertEqual(coverage['drawing_content']['image_ocr'], 'not_performed')
        self.assertEqual(coverage['drawing_content']['visual_rendering'], 'not_performed')


if __name__ == '__main__':
    unittest.main()
