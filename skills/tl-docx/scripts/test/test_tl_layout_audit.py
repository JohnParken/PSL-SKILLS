"""Synthetic package-level static layout checks, without a rendering engine."""

from io import BytesIO
from pathlib import Path
import sys
import unittest

from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tl_create import build_document
from tl_layout_audit import audit_layout
from tl_package import Package


def package_of(document):
    """Serialize and re-open a real OPC package in memory."""
    stream = BytesIO()
    document.save(stream)
    return Package(stream.getvalue())


def codes(document):
    """Read issue codes while proving audit does not mutate the package."""
    package = package_of(document)
    before = package.data
    result = audit_layout(package)
    assert package.data == before
    assert result['visual_validation'] == 'not_run'
    return {issue['code'] for issue in result['issues']}


class StaticLayoutTests(unittest.TestCase):
    """Exercise geometry, malformed values and physical merged-grid boundaries."""

    def test_generated_role_properties_obey_schema_order(self):
        """Merging native style properties must retain pPr schema ordering."""
        for profile in ('official-basic', 'business-report', 'academic', 'presentation-material'):
            with self.subTest(profile=profile):
                document = build_document({'profile': profile, 'blocks': [
                    {'type': 'title', 'text': 'Title'}, {'type': 'subtitle', 'text': 'Subtitle'},
                    {'type': 'heading', 'level': 1, 'text': 'Heading'},
                    {'type': 'paragraph', 'text': 'Body'}]}, lambda _: None)
                self.assertNotIn('PARAGRAPH_PROPERTY_ORDER', codes(document))
                self.assertNotIn('DUPLICATE_PARAGRAPH_PROPERTY', codes(document))

    def test_default_document_and_zero_number_start(self):
        document = Document()
        document.add_paragraph('Text')
        numbering = OxmlElement('w:pgNumType')
        numbering.set(qn('w:start'), '0')
        document.sections[0]._sectPr.append(numbering)
        self.assertEqual(codes(document), set())

    def test_actual_owning_section_controls_table_width(self):
        document = Document()
        document.sections[0].page_width = Inches(12)
        document.add_table(rows=1, cols=2)
        document.add_section(WD_SECTION_START.NEW_PAGE).page_width = Inches(5)
        self.assertNotIn('TABLE_WIDTH_RISK', codes(document))
        document.tables[0]._tbl.tblGrid.gridCol_lst[0].set(qn('w:w'), '20000')
        self.assertIn('TABLE_WIDTH_RISK', codes(document))

    def test_malformed_and_impossible_page_dimensions(self):
        document = Document()
        page = document.sections[0]._sectPr.find(qn('w:pgSz'))
        page.set(qn('w:w'), 'bad')
        self.assertIn('INVALID_SECTION_PAGE', codes(document))
        page.set(qn('w:w'), '1')
        self.assertIn('INVALID_CONTENT_REGION', codes(document))

    def test_duplicate_and_out_of_order_paragraph_properties(self):
        document = Document()
        props = document.add_paragraph('text')._p.get_or_add_pPr()
        props.append(OxmlElement('w:jc'))
        props.append(OxmlElement('w:keepNext'))
        props.append(OxmlElement('w:keepNext'))
        self.assertTrue({'DUPLICATE_PARAGRAPH_PROPERTY', 'PARAGRAPH_PROPERTY_ORDER'} <= codes(document))

    def test_tab_positions_malformed_and_duplicates(self):
        document = Document()
        props = document.add_paragraph()._p.get_or_add_pPr()
        tabs = OxmlElement('w:tabs')
        props.append(tabs)
        for position in ('10', '10', 'bad'):
            tab = OxmlElement('w:tab')
            tab.set(qn('w:pos'), position)
            tabs.append(tab)
        self.assertIn('INVALID_TAB_STOPS', codes(document))

    def test_valid_merges_and_orphan_continuation(self):
        document = Document()
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).merge(table.cell(1, 0))
        self.assertEqual(codes(document), set())
        merge = table._tbl.tr_lst[0].tc_lst[0].tcPr.find(qn('w:vMerge'))
        merge.set(qn('w:val'), 'continue')
        self.assertIn('INVALID_VERTICAL_MERGE', codes(document))

    def test_grid_offsets_are_accounted_for(self):
        document = Document()
        table = document.add_table(rows=1, cols=2)
        row = table._tbl.tr_lst[0]
        row.remove(row.tc_lst[0])
        before = OxmlElement('w:gridBefore')
        before.set(qn('w:val'), '1')
        row.get_or_add_trPr().append(before)
        self.assertNotIn('TABLE_GRID_COVERAGE', codes(document))
        before.set(qn('w:val'), '2')
        self.assertIn('TABLE_GRID_COVERAGE', codes(document))

    def test_inline_extent_and_floating_limit(self):
        document = Document()
        run = document.add_paragraph().add_run()._r
        drawing = OxmlElement('w:drawing')
        inline = OxmlElement('wp:inline')
        extent = OxmlElement('wp:extent')
        extent.set('cx', '999999999')
        extent.set('cy', '1000')
        inline.append(extent)
        drawing.append(inline)
        drawing.append(OxmlElement('wp:anchor'))
        run.append(drawing)
        self.assertTrue({'INLINE_IMAGE_SIZE_RISK', 'FLOATING_DRAWING_UNCHECKED'} <= codes(document))
        extent.set('cx', 'bad')
        self.assertIn('INVALID_IMAGE_EXTENT', codes(document))

    def test_gutter_top_does_not_reduce_width(self):
        """Honor the documented gutter axis rather than reject a valid width."""
        document = Document()
        section = document.sections[0]
        section.page_width = Inches(4)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
        section.gutter = Inches(3)
        self.assertIn('INVALID_CONTENT_REGION', codes(document))
        document.settings.element.append(OxmlElement('w:gutterAtTop'))
        self.assertNotIn('INVALID_CONTENT_REGION', codes(document))

    def test_header_paragraphs_and_duplicate_references(self):
        document = Document()
        header = document.sections[0].header
        props = header.paragraphs[0]._p.get_or_add_pPr()
        props.append(OxmlElement('w:keepNext'))
        props.append(OxmlElement('w:keepNext'))
        duplicate = OxmlElement('w:headerReference')
        duplicate.set(qn('w:type'), 'default')
        duplicate.set(qn('r:id'), 'rId999')
        document.sections[0]._sectPr.append(duplicate)
        self.assertTrue({'DUPLICATE_PARAGRAPH_PROPERTY', 'INVALID_SECTION_REFERENCES'} <= codes(document))


if __name__ == '__main__':
    unittest.main()
