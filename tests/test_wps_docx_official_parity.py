"""Repository-only parity checks; neither distributed skill imports the other."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/docx/scripts'))
sys.path.insert(0, str(ROOT / 'skills/wps/scripts'))
import create_docx
import wps_rtf_builder as wps


class OfficialParityTests(unittest.TestCase):
    def test_defaults_follow_current_docx_preset(self):
        blocks = [{'type': 'title', 'text': '标题'}, {'type': 'subtitle', 'text': '副标题'}]
        blocks += [{'type': 'heading', 'level': level, 'text': f'标题{level}'} for level in range(1, 5)]
        blocks += [{'type': 'paragraph', 'text': '正文 ABC 123'}]
        spec = {'blocks': blocks}
        docx = create_docx._validate_spec(spec, ROOT)
        rtf = wps.build_document_from_spec(spec)
        page = docx['page_twips']
        self.assertEqual((rtf.page_width_twips, rtf.page_height_twips), (page['width'], page['height']))
        for edge in ('top', 'bottom', 'left', 'right'):
            self.assertEqual(getattr(rtf, 'margin_' + edge + '_twips'), page[edge])
        self.assertEqual(rtf.page_numbering, docx['page_numbers'])
        for name, paragraph in zip(('title', 'subtitle', 'heading1', 'heading2', 'heading3', 'heading4', 'body'), rtf.elements):
            with self.subTest(style=name):
                style = docx['styles'][name]
                run = paragraph.runs[0]
                self.assertEqual(run.font, wps.FONT_MAP[style['font_east_asia'].lower()])
                self.assertEqual(run.latin_font, wps.FONT_MAP[style['font_latin'].lower()])
                self.assertEqual(run.size_pt, style['size_pt'])
                self.assertEqual(run.bold, style['bold'])
                self.assertEqual(paragraph.align, style['align'])
                self.assertEqual(paragraph.first_line_indent_chars, style['first_line_chars'])
                self.assertEqual(paragraph.space_before_pt, style['space_before_pt'])
                self.assertEqual(paragraph.space_after_pt, style['space_after_pt'])
                self.assertEqual(paragraph.line_spacing_exact_pt, style.get('line_spacing_pt'))
                if 'line_spacing_pt' not in style:
                    self.assertEqual(paragraph.line_spacing_mult, style.get('line_spacing_multiple', 1.15))

    def test_table_fonts_and_no_row_split_follow_docx(self):
        spec = {'blocks': [{'type': 'table', 'rows': [['表头'], ['内容']]}]}
        docx = create_docx._validate_spec(spec, ROOT)
        table = wps.build_document_from_spec(spec).elements[0]
        for name, row in zip(('table_header', 'table_body'), table.rows):
            style = docx['styles'][name]
            paragraph = row.cells[0].paragraphs[0]
            self.assertEqual(paragraph.runs[0].size_pt, style['size_pt'])
            self.assertEqual(paragraph.runs[0].font, wps.FONT_MAP[style['font_east_asia'].lower()])
            self.assertEqual(paragraph.line_spacing_mult, style['line_spacing_multiple'])
            self.assertEqual(row.keep_together, not docx['table_format']['allow_row_split'])


if __name__ == '__main__':
    unittest.main()
