"""Synthetic XML and object checks for creation typography and preset layers."""

from copy import deepcopy
from io import BytesIO
from docx import Document
from docx.shared import Pt
from pathlib import Path
import sys
import unittest

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from tl_create import build_document


def build(**options):
    """Create an in-memory document without external images."""
    return build_document(options, lambda _path: b'')


class CreationStylesTests(unittest.TestCase):
    def test_native_role_links_and_no_baked_in_defaults(self):
        """Save/reopen preserves role links and allows later style changes."""
        document = build(styles={'body': {'size_pt': 13}}, blocks=[
            {'type': 'paragraph', 'text': 'inherited'},
            {'type': 'paragraph', 'text': 'local', 'format': {'size_pt': 17}},
            {'type': 'heading', 'level': 1, 'text': 'heading'},
            {'type': 'table', 'header': True, 'rows': [['header'], ['body']]},
        ])
        document.styles['Body Text'].font.size = Pt(19)
        document.styles['Normal'].paragraph_format.right_indent = Pt(23)
        stream = BytesIO()
        document.save(stream)
        reopened = Document(BytesIO(stream.getvalue()))
        first, local, heading = reopened.paragraphs[:3]
        self.assertEqual(first.style.style_id, 'BodyText')
        self.assertEqual(first.style.base_style.style_id, 'Normal')
        self.assertIsNone(first.runs[0].font.size)
        self.assertEqual(first.style.font.size.pt, 19)
        self.assertEqual(local.runs[0].font.size.pt, 17)
        self.assertIsNone(first.paragraph_format.right_indent)
        self.assertIsNone(first.style.paragraph_format.right_indent)
        self.assertEqual(first.style.base_style.paragraph_format.right_indent.pt, 23)
        self.assertEqual(heading.style.style_id, 'Heading1')
        self.assertEqual(heading.style.element.pPr.find(qn('w:outlineLvl')).get(qn('w:val')), '0')
        self.assertEqual(reopened.tables[0].cell(0, 0).paragraphs[0].style.style_id, 'TableHeader')
        self.assertEqual(reopened.tables[0].cell(1, 0).paragraphs[0].style.style_id, 'TableBody')

    def test_local_run_font_override_does_not_bake_other_fonts(self):
        """An East Asian override keeps Latin font and size inherited."""
        paragraph = build(styles={'body': {'font_latin': 'Arial', 'size_pt': 14}}, blocks=[{
            'type': 'paragraph', 'runs': [{'text': 'A', 'font_east_asia': '宋体'}],
        }]).paragraphs[0]
        run = paragraph.runs[0]
        self.assertEqual(run._r.rPr.rFonts.get(qn('w:eastAsia')), '宋体')
        self.assertIsNone(run.font.name)
        self.assertIsNone(run.font.size)
        self.assertEqual(paragraph.style.font.name, 'Arial')
        self.assertEqual(paragraph.style.font.size.pt, 14)


    def test_profile_body_and_explicit_overrides(self):
        spec = {'profile': 'academic', 'styles': {'body': {'size_pt': 13}},
                'blocks': [{'type': 'paragraph', 'text': '正文', 'format': {'space_after_pt': 7}}]}
        before = deepcopy(spec)
        paragraph = build(**spec).paragraphs[0]
        self.assertEqual(spec, before)
        self.assertEqual(paragraph.style.font.size.pt, 13)
        self.assertEqual(paragraph.style.paragraph_format.line_spacing, 1.5)
        self.assertEqual(paragraph.paragraph_format.space_after.pt, 7)
        self.assertTrue(paragraph.style.paragraph_format.widow_control)
        self.assertEqual(paragraph.style.element.pPr.ind.get(qn('w:firstLineChars')), '200')

    def test_precedence_and_run_font_channels(self):
        paragraph = build(styles={'body': {'size_pt': 13, 'bold': True, 'align': 'right'}}, blocks=[{
            'type': 'paragraph', 'format': {'size_pt': 15, 'font_east_asia': '宋体', 'font_latin': 'Arial'},
            'align': 'center', 'runs': [{'text': '中文 A', 'font': 'Calibri', 'font_east_asia': '仿宋', 'bold': False},
                                     {'text': '第二段', 'size_pt': 17}],
        }]).paragraphs[0]
        self.assertEqual(paragraph.alignment, WD_ALIGN_PARAGRAPH.CENTER)
        first, second = paragraph.runs
        self.assertEqual(first.font.size.pt, 15)
        self.assertFalse(first.bold)
        self.assertEqual(first._r.rPr.rFonts.get(qn('w:eastAsia')), '仿宋')
        self.assertEqual(first.font.name, 'Calibri')
        self.assertEqual(second.font.name, 'Arial')
        self.assertEqual(second.font.size.pt, 17)

    def test_field_and_tab_run_offsets(self):
        paragraph = build(styles={'body': {'size_pt': 12}}, blocks=[{
            'type': 'paragraph', 'runs': [{'field': 'PAGE'}, {'tab': True}, {'text': 'A', 'size_pt': 19},
                                        {'field': 'NUMPAGES', 'display': '1'}, {'text': 'B', 'bold': True}],
        }]).paragraphs[0]
        runs = [run for run in paragraph.runs if run.text in ('A', 'B')]
        self.assertEqual(runs[0].font.size.pt, 19)
        self.assertTrue(runs[1].bold)

    def test_fixed_spacing_replaces_profile_multiple(self):
        paragraph = build(profile='academic', styles={'body': {'line_spacing_pt': 24}},
                          blocks=[{'type': 'paragraph', 'text': 'A'}]).paragraphs[0]
        self.assertEqual(paragraph.style.paragraph_format.line_spacing_rule, WD_LINE_SPACING.EXACTLY)
        self.assertEqual(paragraph.style.paragraph_format.line_spacing.pt, 24)

    def test_character_indent_and_hanging_are_exclusive_across_layers(self):
        paragraph = build(profile='academic', blocks=[{'type': 'paragraph', 'text': 'A',
                           'format': {'hanging_indent_pt': 12, 'left_indent_pt': 24}}]).paragraphs[0]
        self.assertEqual(paragraph.paragraph_format.first_line_indent.pt, -12)
        self.assertEqual(paragraph.paragraph_format.left_indent.pt, 24)
        self.assertEqual(paragraph._p.pPr.ind.get(qn('w:firstLineChars')), '0')

    def test_business_profile_page_and_table(self):
        document = build(profile='business-report', page={'margin_mm': 20, 'margin_top_mm': 25},
                         blocks=[{'type': 'table', 'header': True, 'rows': [['标题'], ['内容']]}])
        section = document.sections[0]
        self.assertAlmostEqual(section.top_margin.mm, 25, delta=0.02)
        self.assertAlmostEqual(section.left_margin.mm, 20, delta=0.02)
        paragraph = document.tables[0].cell(1, 0).paragraphs[0]
        self.assertEqual(paragraph.style.font.size.pt, 10.5)
        self.assertTrue(paragraph.style.paragraph_format.keep_together)

    def test_profiles_and_explicit_theme(self):
        for profile in ('official-basic', 'business-report', 'academic', 'presentation-material'):
            with self.subTest(profile=profile):
                build(profile=profile, blocks=[{'type': 'heading', 'level': 1, 'text': 'A'}])
        paragraph = build(profile='academic', theme='official', blocks=[{'type': 'paragraph', 'text': 'A'}]).paragraphs[0]
        self.assertEqual(paragraph.style.element.rPr.rFonts.get(qn('w:eastAsia')), '仿宋')
        self.assertEqual(paragraph.style.font.size.pt, 12)

    def test_page_dimensions_orientation_and_cm_compatibility(self):
        section = build(page={'width_mm': 148, 'height_mm': 210, 'orientation': 'landscape',
                              'margins_cm': {'top': 1}, 'margin_top_mm': 10}, blocks=[]).sections[0]
        self.assertAlmostEqual(section.page_width.mm, 210, delta=0.02)
        self.assertAlmostEqual(section.page_height.mm, 148, delta=0.02)
        self.assertAlmostEqual(section.top_margin.mm, 10, delta=0.02)

    def test_section_millimetre_dimensions_and_margins(self):
        document = build(blocks=[{'type': 'paragraph', 'text': 'A'},
                                {'type': 'section_break', 'width_mm': 148, 'height_mm': 210,
                                 'orientation': 'landscape', 'margin_mm': 15, 'margin_left_mm': 20},
                                {'type': 'paragraph', 'text': 'B'}])
        section = document.sections[1]
        self.assertAlmostEqual(section.page_width.mm, 210, delta=0.02)
        self.assertAlmostEqual(section.page_height.mm, 148, delta=0.02)
        self.assertAlmostEqual(section.left_margin.mm, 20, delta=0.02)
        self.assertAlmostEqual(section.right_margin.mm, 15, delta=0.02)
        self.assertAlmostEqual(document.sections[0].page_width.mm, 210, delta=0.02)
        with self.assertRaises(ValueError):
            build(blocks=[{'type': 'section_break', 'width_mm': 10}])

    def test_invalid_page_inputs(self):
        for page in ({'width_mm': True}, {'height_mm': float('nan')}, {'margin_mm': -1},
                     {'width_mm': 10}, {'margin_mm': 999},
                     {'margin_top_mm': 10, 'margins_cm': {'top': 2}}):
            with self.subTest(page=page), self.assertRaises(ValueError):
                build(page=page, blocks=[])

    def test_invalid_format_inputs(self):
        for formatting in ({'bold': 1}, {'size_pt': float('inf')}, {'align': []}, {'unknown': 1},
                           {'font_latin': ''}, {'color': '#000000'}, {'line_spacing_pt': 20, 'line_spacing_multiple': 1.2},
                           {'first_line_chars': 1, 'hanging_indent_pt': 2}):
            with self.subTest(formatting=formatting), self.assertRaises(ValueError):
                build(blocks=[{'type': 'paragraph', 'text': 'A', 'format': formatting}])

    def test_unknown_profile_and_styles_rejected(self):
        for options in ({'profile': 'custom'}, {'profile': []}, {'styles': {'custom': {}}}, {'styles': None}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                build(blocks=[], **options)

    def test_reference_blocks_and_missing_target(self):
        document = build(blocks=[{'type': 'heading', 'level': 1, 'text': 'A', 'bookmark': 'Target'},
                                 {'type': 'reference', 'bookmark': 'Target', 'kind': 'page', 'prefix': '见第', 'suffix': '页'}])
        paragraph = document.paragraphs[1]
        self.assertEqual(paragraph.text, '见第页')
        self.assertEqual([node.text for node in paragraph._p.iter(qn('w:instrText'))], [' PAGEREF Target '])
        self.assertEqual(paragraph.paragraph_format.first_line_indent.pt, 0)
        with self.assertRaises(ValueError):
            build(blocks=[{'type': 'reference', 'bookmark': 'Missing'}])

    def test_subtitle_spacing_and_no_automatic_page_break(self):
        paragraph = build(blocks=[{'type': 'subtitle', 'text': 'A'}]).paragraphs[0]
        self.assertEqual(paragraph.style.paragraph_format.space_after.pt, 8)
        self.assertIsNone(paragraph.paragraph_format.page_break_before)


if __name__ == '__main__':
    unittest.main()
