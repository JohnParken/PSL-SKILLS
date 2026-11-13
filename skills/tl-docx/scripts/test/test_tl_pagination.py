"""Synthetic regressions for static tl-docx pagination auditing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from tl_create import build_document
from tl_pagination import audit_pagination


PART = 'word/document.xml'
WORD_PPR = qn('w:pPr')


def set_property(parent, property_name, value='1'):
    """Set a paragraph property on a paragraph or paragraph style element."""
    properties = parent.find(WORD_PPR)
    if properties is None:
        properties = OxmlElement('w:pPr')
        parent.append(properties)
    property_tag = qn('w:' + property_name)
    prop = properties.find(property_tag)
    if prop is None:
        prop = OxmlElement('w:' + property_name)
        properties.append(prop)
    prop.set(qn('w:val'), value)


def add_page_break(document):
    """Append one standalone explicit page-break paragraph."""
    paragraph = document.add_paragraph()
    paragraph.add_run().add_break(WD_BREAK.PAGE)
    return paragraph


def add_section_properties(paragraph, break_type):
    """Attach section properties with a specified section-start type."""
    properties = paragraph._p.get_or_add_pPr()  # pylint: disable=protected-access
    section = OxmlElement('w:sectPr')
    section_type = OxmlElement('w:type')
    section_type.set(qn('w:val'), break_type)
    section.append(section_type)
    properties.append(section)


def add_section_marker(document, break_type):
    """Add a section-ending paragraph carrying its own section properties."""
    paragraph = document.add_paragraph('Section boundary')
    add_section_properties(paragraph, break_type)
    return paragraph


def set_section_type(section, break_type):
    """Set the section-start type on a sectPr element."""
    section_type = section.find(qn('w:type'))
    if section_type is None:
        section_type = OxmlElement('w:type')
        section.append(section_type)
    section_type.set(qn('w:val'), break_type)


def set_document_default(document, property_name, value):
    """Set a paragraph property in styles.xml document defaults."""
    root = document.styles._element  # pylint: disable=protected-access
    defaults = root.find(qn('w:docDefaults'))
    if defaults is None:
        defaults = OxmlElement('w:docDefaults')
        root.insert(0, defaults)
    paragraph_default = defaults.find(qn('w:pPrDefault'))
    if paragraph_default is None:
        paragraph_default = OxmlElement('w:pPrDefault')
        defaults.append(paragraph_default)
    properties = paragraph_default.find(WORD_PPR)
    if properties is None:
        properties = OxmlElement('w:pPr')
        paragraph_default.append(properties)
    prop = properties.find(qn('w:' + property_name))
    if prop is None:
        prop = OxmlElement('w:' + property_name)
        properties.append(prop)
    prop.set(qn('w:val'), value)


def add_outline_style(document, name, based_on=None, keep_next=None):
    """Create an outline paragraph style with optional inherited keepNext."""
    style = document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    if based_on is not None:
        style.base_style = based_on
    set_property(style._element, 'outlineLvl', '0')  # pylint: disable=protected-access
    if keep_next is not None:
        set_property(style._element, 'keepNext', keep_next)  # pylint: disable=protected-access
    return style


def run_audit(document, body=None):
    """Audit the selected body against its in-memory style definitions."""
    target_body = body if body is not None else document._element.body  # pylint: disable=protected-access
    styles_root = document.styles._element  # pylint: disable=protected-access
    return audit_pagination(target_body, styles_root, PART)


def issue_codes(issues):
    """Return issue codes for concise independent-case assertions."""
    return {item['code'] for item in issues}


class PaginationAuditTests(unittest.TestCase):
    def test_leading_repeated_and_trailing_breaks(self):
        document = Document()
        add_page_break(document)
        add_page_break(document)
        document.add_paragraph('Body')
        add_page_break(document)

        codes = issue_codes(run_audit(document))

        self.assertTrue(
            {'LEADING_PAGE_BREAK', 'REPEATED_PAGE_BREAK', 'TRAILING_PAGE_BREAK'} <= codes
        )

    def test_multiple_manual_breaks_in_one_paragraph(self):
        document = Document()
        document.add_paragraph('Before')
        paragraph = document.add_paragraph()
        run = paragraph.add_run()
        run.add_break(WD_BREAK.PAGE)
        run.add_break(WD_BREAK.PAGE)
        document.add_paragraph('After')

        self.assertIn(
            'MULTIPLE_PAGE_BREAKS_IN_PARAGRAPH', issue_codes(run_audit(document))
        )

    def test_inline_manual_break(self):
        document = Document()
        paragraph = document.add_paragraph()
        run = paragraph.add_run('Text before')
        run.add_break(WD_BREAK.PAGE)
        run.add_text('Text after')

        self.assertIn('INLINE_PAGE_BREAK_RISK', issue_codes(run_audit(document)))

    def test_page_break_conflict_uses_next_section_properties(self):
        document = Document()
        add_page_break(document)
        add_section_marker(document, 'continuous')
        next_section = document.add_paragraph('New section')
        add_section_properties(next_section, 'nextPage')

        issues = run_audit(document)
        conflict = next(
            item for item in issues if item['code'] == 'PAGE_BREAK_SECTION_CONFLICT'
        )

        self.assertEqual(conflict['location'], f'{PART}:body[1]')

    def test_continuous_section_does_not_conflict_with_adjacent_page_break(self):
        document = Document()
        add_page_break(document)
        add_section_marker(document, 'nextPage')
        next_section = document.add_paragraph('Continuous section')
        add_section_properties(next_section, 'continuous')

        self.assertNotIn('PAGE_BREAK_SECTION_CONFLICT', issue_codes(run_audit(document)))

    def test_redundant_page_break_before_heading(self):
        document = Document()
        add_page_break(document)
        heading = document.add_paragraph('Heading', style='Heading 1')
        set_property(heading._p, 'pageBreakBefore', '1')  # pylint: disable=protected-access
        set_property(heading._p, 'keepNext', '1')  # pylint: disable=protected-access
        document.add_paragraph('Body')

        self.assertIn(
            'REDUNDANT_HEADING_PAGE_BREAK', issue_codes(run_audit(document))
        )

    def test_redundant_page_break_before_paragraph(self):
        document = Document()
        add_page_break(document)
        paragraph = document.add_paragraph('Body')
        set_property(paragraph._p, 'pageBreakBefore', '1')  # pylint: disable=protected-access

        self.assertIn(
            'REDUNDANT_PARAGRAPH_PAGE_BREAK', issue_codes(run_audit(document))
        )

    def test_inherited_page_break_before_and_explicit_false(self):
        document = Document()
        parent = document.styles.add_style('PageStartParent', WD_STYLE_TYPE.PARAGRAPH)
        set_property(parent._element, 'pageBreakBefore', '1')  # pylint: disable=protected-access
        child = document.styles.add_style('PageStartChild', WD_STYLE_TYPE.PARAGRAPH)
        child.base_style = parent
        add_page_break(document)
        inherited = document.add_paragraph('Inherited', style=child)
        document.add_paragraph('Between')
        add_page_break(document)
        overridden = document.add_paragraph('Overridden', style=child)
        set_property(overridden._p, 'pageBreakBefore', '0')  # pylint: disable=protected-access

        issues = run_audit(document)
        redundant_locations = {
            item['location']
            for item in issues
            if item['code'] == 'REDUNDANT_PARAGRAPH_PAGE_BREAK'
        }
        self.assertIn(f'{PART}:body[0]', redundant_locations)
        self.assertNotIn(f'{PART}:body[3]', redundant_locations)
        self.assertIsNotNone(inherited)

    def test_document_default_page_break_before(self):
        document = Document()
        set_document_default(document, 'pageBreakBefore', '1')
        add_page_break(document)
        document.add_paragraph('Uses default')

        self.assertIn(
            'REDUNDANT_PARAGRAPH_PAGE_BREAK', issue_codes(run_audit(document))
        )

    def test_default_paragraph_style_inheritance(self):
        document = Document()
        normal = document.styles['Normal']
        set_property(normal._element, 'pageBreakBefore', '1')  # pylint: disable=protected-access
        add_page_break(document)
        paragraph = document.add_paragraph('Uses default paragraph style')

        self.assertIn(
            'REDUNDANT_PARAGRAPH_PAGE_BREAK', issue_codes(run_audit(document))
        )
        set_property(paragraph._p, 'pageBreakBefore', '0')  # pylint: disable=protected-access
        self.assertNotIn(
            'REDUNDANT_PARAGRAPH_PAGE_BREAK', issue_codes(run_audit(document))
        )

    def test_section_page_break_before_heading_and_continuous_exception(self):
        document = Document()
        add_section_marker(document, 'continuous')
        heading = document.add_paragraph('Heading', style='Heading 1')
        set_property(heading._p, 'pageBreakBefore', '1')  # pylint: disable=protected-access
        set_property(heading._p, 'keepNext', '1')  # pylint: disable=protected-access
        set_section_type(document.sections[-1]._sectPr, 'nextPage')  # pylint: disable=protected-access

        self.assertIn('SECTION_HEADING_PAGE_BREAK', issue_codes(run_audit(document)))

        set_section_type(document.sections[-1]._sectPr, 'continuous')  # pylint: disable=protected-access
        self.assertNotIn('SECTION_HEADING_PAGE_BREAK', issue_codes(run_audit(document)))

    def test_heading_separated_by_section(self):
        document = Document()
        heading = document.add_paragraph('Heading', style='Heading 1')
        set_property(heading._p, 'keepNext', '1')  # pylint: disable=protected-access
        add_section_marker(document, 'nextPage')
        document.add_paragraph('Body')

        self.assertIn(
            'HEADING_SEPARATED_FROM_CONTENT', issue_codes(run_audit(document))
        )

    def test_terminal_heading(self):
        document = Document()
        heading = document.add_paragraph('Final heading', style='Heading 1')
        set_property(heading._p, 'keepNext', '1')  # pylint: disable=protected-access

        self.assertIn('HEADING_WITHOUT_CONTENT', issue_codes(run_audit(document)))

    def test_direct_body_outline_overrides_a_builtin_heading_style(self):
        document = Document()
        paragraph = document.add_paragraph('Ordinary text', style='Heading 1')
        set_property(paragraph._p, 'outlineLvl', '9')  # pylint: disable=protected-access
        self.assertNotIn('HEADING_WITHOUT_CONTENT', issue_codes(run_audit(document)))

    def test_heading_inherited_keep_next_and_explicit_false(self):
        document = Document()
        parent = add_outline_style(document, 'OutlineParent', keep_next='1')
        child = add_outline_style(document, 'OutlineChild', based_on=parent)
        inherited = document.add_paragraph('Inherited heading', style=child)
        document.add_paragraph('Body')
        overridden = document.add_paragraph('Orphan heading', style=child)
        set_property(overridden._p, 'keepNext', 'false')  # pylint: disable=protected-access
        document.add_paragraph('Another body')

        codes = issue_codes(run_audit(document))

        self.assertNotIn('HEADING_ORPHAN_RISK', {item['code'] for item in run_audit(document) if item['location'].endswith('body[0]')})
        self.assertIn('HEADING_ORPHAN_RISK', codes)
        self.assertIsNotNone(inherited)

    def test_document_default_keep_next_protects_outline_heading(self):
        document = Document()
        set_document_default(document, 'keepNext', '1')
        style = add_outline_style(document, 'DefaultKeptHeading')
        heading = document.add_paragraph('Heading', style=style)
        document.add_paragraph('Body')

        self.assertNotIn('HEADING_ORPHAN_RISK', issue_codes(run_audit(document)))
        self.assertIsNotNone(heading)

    def test_table_paragraphs_do_not_create_body_break_boundaries(self):
        document = Document()
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).paragraphs[0].add_run().add_break(WD_BREAK.PAGE)
        document.add_paragraph('Body')

        codes = issue_codes(run_audit(document))

        self.assertFalse(
            {'LEADING_PAGE_BREAK', 'REPEATED_PAGE_BREAK', 'TRAILING_PAGE_BREAK'} & codes
        )

    def test_cached_last_rendered_break_is_ignored(self):
        document = Document()
        paragraph = document.add_paragraph('Body')
        cached_break = OxmlElement('w:lastRenderedPageBreak')
        paragraph._p.append(cached_break)  # pylint: disable=protected-access

        self.assertEqual([], run_audit(document))

    def test_audit_does_not_mutate_document_or_style_xml(self):
        document = Document()
        add_page_break(document)
        heading = document.add_paragraph('Heading', style='Heading 1')
        set_property(heading._p, 'keepNext', '0')  # pylint: disable=protected-access
        before_body = document._element.body.xml  # pylint: disable=protected-access
        before_styles = document.styles._element.xml  # pylint: disable=protected-access

        run_audit(document)

        self.assertEqual(before_body, document._element.body.xml)  # pylint: disable=protected-access
        self.assertEqual(before_styles, document.styles._element.xml)  # pylint: disable=protected-access

    def test_issue_shape_and_part_location(self):
        document = Document()
        add_page_break(document)
        issues = audit_pagination(
            document._element.body,  # pylint: disable=protected-access
            document.styles._element,  # pylint: disable=protected-access
            'word/custom.xml',
        )

        self.assertTrue(issues)
        self.assertTrue(all(item['level'] == 'warning' for item in issues))
        self.assertTrue(all(item['location'].startswith('word/custom.xml:body[0]') for item in issues))
        self.assertTrue(all(set(item) == {'level', 'code', 'location', 'message'} for item in issues))

    def test_created_terminal_heading_reports_combined_risks(self):
        document = build_document(
            {
                'blocks': [
                    {'type': 'page_break'},
                    {'type': 'page_break'},
                    {
                        'type': 'heading',
                        'level': 1,
                        'text': '末尾标题',
                        'page_break_before': True,
                        'keep_with_next': False,
                    },
                ]
            },
            lambda _path: b'',
        )

        codes = issue_codes(run_audit(document))
        expected = {
            'LEADING_PAGE_BREAK',
            'REPEATED_PAGE_BREAK',
            'REDUNDANT_HEADING_PAGE_BREAK',
            'HEADING_WITHOUT_CONTENT',
            'HEADING_ORPHAN_RISK',
        }

        self.assertTrue(expected <= codes)

    def test_ordinary_created_headings_keep_natural_pagination(self):
        document = build_document(
            {
                'blocks': [
                    {'type': 'heading', 'level': 1, 'text': '第一部分'},
                    {'type': 'paragraph', 'text': '第一部分正文。'},
                    {'type': 'heading', 'level': 1, 'text': '第二部分'},
                    {'type': 'paragraph', 'text': '第二部分正文。'},
                ]
            },
            lambda _path: b'',
        )

        self.assertEqual([], run_audit(document))


if __name__ == '__main__':
    unittest.main()
