"""Literal RTF expectations for supported outline/tab/table semantics."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import unittest
from wps_rtf_builder import build_document_from_spec
from read_wps import read_bytes


def render(blocks):
    return build_document_from_spec({'page_numbering': False, 'blocks': blocks}).render()


class StructureTests(unittest.TestCase):
    def test_heading_outline_levels(self):
        text = render([{'type': 'heading', 'level': n, 'text': str(n)} for n in (1, 2, 9)])
        for control in (r'\outlinelevel0', r'\outlinelevel1', r'\outlinelevel8'):
            self.assertIn(control, text)
        self.assertEqual([p['text'] for p in read_bytes(text.encode('ascii'), reading_mode='standard')['paragraphs']], ['1', '2', '9'])

    def test_hanging_indents_tabs_and_alignment(self):
        text = render([{'type': 'paragraph', 'text': '甲\t乙', 'align': 'right', 'left_indent_pt': 36,
                        'right_indent_pt': 12, 'first_line_indent_pt': -18, 'tabs_pt': [72, 144]}])
        for control in (r'\qr', r'\li720', r'\ri240', r'\fi-360', r'\tx1440\tx2880'):
            self.assertIn(control, text)
        self.assertEqual(read_bytes(text.encode('ascii'), reading_mode='standard')['paragraphs'][0]['text'], '甲\t乙')
        multiple = render([{'type': 'paragraph', 'text': '倍数', 'line_spacing_mult': 1.5}])
        self.assertIn(r'\sl360\slmult1', multiple)
        self.assertNotIn(r'\sl-560', multiple)

    def test_repeat_header_and_column_alignment(self):
        table = {'type': 'table', 'rows': [['头1', '头2'], ['值', '12']], 'col_widths': [72, 144], 'col_alignments': ['left', 'right']}
        text = render([table])
        self.assertEqual(text.count(r'\trhdr'), 1)
        self.assertIn(r'\cellx1440', text)
        self.assertIn(r'\cellx4320', text)
        table['repeat_header'] = False
        self.assertNotIn(r'\trhdr', render([table]))
        self.assertIn(r'\qr', text)
        self.assertIn(r'\trkeepfollow', text)
        self.assertIn(r'\trkeep', text)

    def test_widow_control_uses_paragraph_scope(self):
        text = render([{'type': 'paragraph', 'text': '甲', 'widow_control': True},
                       {'type': 'paragraph', 'text': '乙', 'widow_control': False}])
        self.assertIn(r'\widctlpar', text)
        self.assertIn(r'\nowidctlpar', text)
        self.assertNotIn(r'\widowctrl', text)

    def test_literal_list_start_not_native_numbering(self):
        text = render([{'type': 'list', 'ordered': True, 'start': 3, 'items': ['甲', '乙']}])
        self.assertIn('3. ', text)
        self.assertIn('4. ', text)
        self.assertNotIn(r'\listtable', text)

    def test_unknown_style_option_warns_without_breaking_old_specs(self):
        doc = build_document_from_spec({'styles': {'Custom': {}}, 'blocks': [{'type': 'paragraph', 'text': '保留正文', 'style': 'Custom'}]})
        self.assertEqual(doc.spec_warning_count, 2)
        self.assertEqual([item['path'] for item in doc.spec_warnings], ['styles', 'blocks[0].style'])
        self.assertIn('保留正文', read_bytes(doc.render().encode('ascii'), reading_mode='standard')['text'])

    def test_invalid_or_unsupported_new_parameters(self):
        for block in [dict(type='heading', level=0), dict(type='heading', level=True),
                      dict(type='paragraph', tabs_pt=[72, 36]), dict(type='paragraph', tabs_pt=[float('nan')]),
                      dict(type='list', start=0), dict(type='list', list_level=2),
                      dict(type='table', rows=[['x']], col_alignments=['decimal']),
                      dict(type='table', rows=[['x']], repeat_header='yes')]:
            with self.subTest(block=block), self.assertRaises(ValueError):
                render([block])


if __name__ == '__main__':
    unittest.main()
