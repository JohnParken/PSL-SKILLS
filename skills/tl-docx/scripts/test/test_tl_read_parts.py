"""Real OPC fixtures for unconventional support-part names and fault isolation."""

from io import BytesIO
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_package import Package
from tl_read import report


def fixture(change=None, rename=False):
    """Create a Word package then independently alter its OPC byte members."""
    doc = Document()
    doc.add_heading('Body heading', 1)
    doc.add_paragraph('Normal body')
    doc.sections[0].header.paragraphs[0].text = 'Custom header text'
    doc.sections[0].footer.paragraphs[0].text = 'Custom footer text'
    doc.core_properties.title = 'Core title'
    raw = BytesIO()
    doc.save(raw)
    with ZipFile(BytesIO(raw.getvalue())) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    if rename:
        replacements = {'header1.xml': 'custom-header.xml', 'footer1.xml': 'custom-footer.xml',
                        'styles.xml': 'custom-style.xml', 'numbering.xml': 'custom-number.xml',
                        'core.xml': 'custom-core.xml'}
        renamed = {}
        for name, value in parts.items():
            for old, new in replacements.items():
                name = name.replace(old, new)
                if name.endswith(('.xml', '.rels')):
                    value = value.replace(old.encode(), new.encode())
            renamed[name] = value
        parts = renamed
    if change:
        change(parts)
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as target:
        for name, value in parts.items():
            target.writestr(name, value)
    return Package(output.getvalue())


class PartDiscoveryTests(unittest.TestCase):
    """Supported parts are independent of conventional ZIP member names."""

    def test_renamed_support_parts(self):
        records, issues, coverage = report(fixture(rename=True))
        paragraphs = {item.get('text'): item for item in records if item['type'] == 'paragraph'}
        self.assertEqual(paragraphs['Body heading']['heading_level'], 1)
        self.assertEqual(paragraphs['Custom header text']['part'], 'word/custom-header.xml')
        self.assertEqual(paragraphs['Custom footer text']['part'], 'word/custom-footer.xml')
        self.assertTrue(any(item['type'] == 'numbering_definition' and item['part'] == 'word/custom-number.xml' for item in records))
        self.assertTrue(any(item['type'] == 'metadata' and item['values']['title'] == 'Core title' for item in records))
        section = next(item for item in records if item['type'] == 'section')
        self.assertEqual({item['target_part'] for item in section['story_references']},
                         {'word/custom-header.xml', 'word/custom-footer.xml'})
        self.assertEqual(coverage['availability']['headers'], 'available')
        self.assertFalse(issues)

    def test_malformed_header_keeps_body_and_footer(self):
        package = fixture(lambda parts: parts.update({'word/header1.xml': b'<broken'}))
        records, issues, coverage = report(package)
        texts = [item.get('text') for item in records]
        self.assertIn('Normal body', texts)
        self.assertIn('Custom footer text', texts)
        self.assertEqual(coverage['status'], 'partial')
        self.assertEqual(coverage['availability']['headers'], 'unreadable')
        self.assertTrue(any(item['code'] == 'support_part_xml_unreadable' and item['part'] == 'word/header1.xml' for item in issues))

    def test_malformed_styles_and_core_keep_body(self):
        package = fixture(lambda parts: parts.update({'word/styles.xml': b'<broken', 'docProps/core.xml': b'<broken'}))
        records, issues, coverage = report(package)
        self.assertIn('Normal body', [item.get('text') for item in records])
        self.assertEqual(coverage['availability']['styles'], 'unreadable')
        self.assertEqual(coverage['availability']['core_metadata'], 'unreadable')
        self.assertEqual(len([item for item in issues if item['code'] == 'support_part_xml_unreadable']), 2)

    def test_orphan_declared_story_is_read_and_diagnosed(self):
        def change(parts):
            from lxml import etree
            root = etree.fromstring(parts['word/_rels/document.xml.rels'])
            for relation in list(root):
                if relation.get('Type', '').endswith('/header'):
                    root.remove(relation)
            parts['word/_rels/document.xml.rels'] = etree.tostring(root)
        records, issues, coverage = report(fixture(change))
        self.assertIn('Custom header text', [item.get('text') for item in records])
        header = next(item for item in records if item.get('text') == 'Custom header text')
        self.assertTrue(header['orphan_part'])
        self.assertEqual(header['document_display_association'], 'unconfirmed')
        self.assertEqual(coverage['status'], 'partial')
        self.assertIn('unreferenced_readable_part', {item['code'] for item in issues})

    def test_dtd_and_unsafe_relationship_remain_blocking(self):
        dtd = b'<!DOCTYPE hdr [<!ENTITY a "secret">]><hdr/>'
        with self.assertRaisesRegex(ValueError, 'DTD'):
            report(fixture(lambda parts: parts.update({'word/header1.xml': dtd})))
        def unsafe(parts):
            parts['word/_rels/document.xml.rels'] = parts['word/_rels/document.xml.rels'].replace(
                b'Target="header1.xml"', b'Target="../../../secret.xml"')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            report(fixture(unsafe))

    def test_smartart_unknown_drawing_and_orphan_context(self):
        """Unsupported diagram structure is explicit while text remains readable."""
        def change(parts):
            drawing = (b'<w:p><w:r><w:drawing><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                       b'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">'
                       b'<dgm:relIds xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"/>'
                       b'</a:graphicData></a:graphic></w:drawing></w:r></w:p>')
            parts['word/document.xml'] = parts['word/document.xml'].replace(b'</w:body>', drawing + b'</w:body>')
        records, issues, coverage = report(fixture(change))
        self.assertIn('Normal body', [item.get('text') for item in records])
        codes = {item['code'] for item in issues}
        self.assertIn('smartart_content_not_decoded', codes)
        self.assertIn('drawing_graphic_data_unsupported', codes)
        self.assertEqual(coverage['status'], 'partial')

    def test_numbering_style_inheritance_through_report(self):
        """Full report forwards inherited numbering labels, not only unit helpers."""
        from test_tl_read import report_fixture
        package = report_fixture()
        style = b'<w:style w:styleId="ListCustom" w:type="paragraph"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        package._parts['word/styles.xml'] = package._parts['word/styles.xml'].replace(b'</w:styles>', style + b'</w:styles>')
        para = b'<w:p><w:pPr><w:pStyle w:val="ListCustom"/></w:pPr><w:r><w:t>Inherited list</w:t></w:r></w:p>'
        package._parts['word/document.xml'] = package._parts['word/document.xml'].replace(b'</w:body>', para + b'</w:body>')
        records, _, _ = report(package)
        item = next(item for item in records if item.get('text') == 'Inherited list')
        self.assertEqual(item['numbering']['label'], '2.')

    def test_chart_extra_dimensions_forwarded_by_report(self):
        """Report keeps saved precision and new dimension source metadata."""
        from test_tl_chart_dimensions import source
        from test_tl_read import report_fixture
        package = report_fixture()
        content = source('xVal', [(0, '001.20')], formula='Data!A1') + source('yVal', [(0, '2')]) + source('bubbleSize', [(0, '0.50')])
        xml = ('<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
               '<c:chart><c:plotArea><c:bubbleChart><c:ser>' + content
               + '</c:ser></c:bubbleChart></c:plotArea></c:chart></c:chartSpace>')
        package._parts['word/charts/chart1.xml'] = xml.encode()
        records, _, _ = report(package)
        series = next(item for item in records if item['type'] == 'chart_series')
        self.assertEqual(series['x_formula'], 'Data!A1')
        self.assertEqual(series['points'][0]['x'], '001.20')
        self.assertEqual(series['points'][0]['bubble_size'], '0.50')

    def test_chart_xml_safety_is_not_swallowed(self):
        """A chart XML DTD remains a safety rejection, ordinary damage is partial."""
        from test_tl_read import report_fixture
        from tl_package import parse
        package = report_fixture()
        package.xml = lambda part: parse(package.read(part))
        package._parts['word/charts/chart1.xml'] = b'<!DOCTYPE chartSpace><chartSpace/>'
        with self.assertRaisesRegex(ValueError, 'DTD'):
            report(package)
        package._parts['word/charts/chart1.xml'] = b'<broken'
        records, issues, _ = report(package)
        self.assertTrue(any(item['type'] == 'paragraph' for item in records))
        self.assertIn('chart_part_unreadable', {item['code'] for item in issues})

    def test_excluded_stories_are_not_parsed(self):
        records, issues, coverage = report(fixture(lambda parts: parts.update({'word/header1.xml': b'<broken'})), include_related=False)
        self.assertIn('Normal body', [item.get('text') for item in records])
        self.assertEqual(coverage['availability']['headers'], 'excluded')
        self.assertFalse(any(item['code'] == 'support_part_xml_unreadable' for item in issues))


if __name__ == '__main__':
    unittest.main()
