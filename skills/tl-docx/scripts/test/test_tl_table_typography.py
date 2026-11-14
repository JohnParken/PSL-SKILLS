"""Table text roles and local overrides preserve explicit caller intent."""
from io import BytesIO
from pathlib import Path
import sys
import unittest

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_create import build_document


def reread(spec):
    """Exercise actual save/reopen rather than only inspecting input options."""
    document = build_document(spec, lambda _path: b'')
    stream = BytesIO()
    document.save(stream)
    return Document(BytesIO(stream.getvalue()))


class TableTypographyTests(unittest.TestCase):
    """Separate table header/body formatting from global document body style."""

    def test_local_overrides_global_roles_and_false_cancels_bold(self):
        """Apply local fonts, alignment, and mutually exclusive spacing modes."""
        table = {'type': 'table', 'header': True, 'rows': [['表头'], ['正文']],
                 'body_format': {'size_pt': 13, 'line_spacing_multiple': 1.5, 'font_latin': 'Arial'},
                 'header_format': {'bold': False, 'font_east_asia': '楷体', 'align': 'right'}}
        doc = reread({'styles': {'body': {'size_pt': 30}, 'table_body': {'size_pt': 10, 'line_spacing_pt': 22},
                               'table_header': {'font_east_asia': '黑体', 'bold': True}}, 'blocks': [table]})
        header = doc.tables[0].cell(0, 0).paragraphs[0]
        body = doc.tables[0].cell(1, 0).paragraphs[0]
        self.assertFalse(header.runs[0].bold)
        self.assertEqual('楷体', header.runs[0]._r.rPr.rFonts.get(qn('w:eastAsia')))  # pylint: disable=protected-access
        self.assertEqual(WD_ALIGN_PARAGRAPH.RIGHT, header.alignment)
        self.assertEqual(13, body.runs[0].font.size.pt)
        self.assertEqual('Arial', body.runs[0].font.name)
        self.assertEqual(1.5, body.paragraph_format.line_spacing)
        self.assertEqual(WD_LINE_SPACING.ONE_POINT_FIVE, body.paragraph_format.line_spacing_rule)

    def test_local_header_does_not_change_body_or_later_table(self):
        """Local role values apply to every header row, not data or other tables."""
        first = {'type': 'table', 'header_rows': 2, 'rows': [['上层'], ['下层'], ['数据']],
                 'header_format': {'color': '123456', 'size_pt': 12},
                 'body_format': {'space_after_pt': 9, 'first_line_chars': 1}}
        second = {'type': 'table', 'header': True, 'rows': [['原表头'], ['原正文']]}
        doc = reread({'blocks': [first, second]})
        for row in (0, 1):
            run = doc.tables[0].cell(row, 0).paragraphs[0].runs[0]
            self.assertEqual('123456', str(run.font.color.rgb))
            self.assertEqual(12, run.font.size.pt)
        body = doc.tables[0].cell(2, 0).paragraphs[0]
        self.assertEqual(9, body.paragraph_format.space_after.pt)
        self.assertEqual(16, body.style.font.size.pt)
        self.assertEqual(16, doc.tables[1].cell(0, 0).paragraphs[0].style.font.size.pt)

    def test_merged_header_uses_anchor_role(self):
        """Merged headers retain their text and receive the same role formatting."""
        doc = reread({'blocks': [{'type': 'table', 'header_rows': 1,
                                 'rows': [[{'text': '合并', 'col_span': 2}, None], ['甲', '乙']],
                                 'header_format': {'size_pt': 14, 'bold': False}}]})
        run = doc.tables[0].cell(0, 0).paragraphs[0].runs[0]
        self.assertEqual('合并', run.text)
        self.assertEqual(14, run.font.size.pt)
        self.assertFalse(run.bold)

    def test_invalid_role_values_rejected_before_save(self):
        """Reject unknown, ambiguous, null, boolean numeric, and non-finite values."""
        invalid = ({'size_pt': True}, {'size_pt': float('nan')}, {'color': '#123456'},
                   {'line_spacing_pt': 20, 'line_spacing_multiple': 1.2}, {'font': 'Arial'}, None,
                   {'first_line_chars': 1, 'hanging_indent_pt': 12}, {'align': 'unknown'})
        for role in ('body_format', 'header_format'):
            for value in invalid:
                with self.subTest(role=role, value=value), self.assertRaises(ValueError):
                    reread({'blocks': [{'type': 'table', 'rows': [['a']], role: value}]})


if __name__ == '__main__':
    unittest.main()
