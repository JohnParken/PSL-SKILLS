"""Independent RTF control and content expectations; no visual QA claim."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from test_task_cli_support import task_api

import tempfile
import unittest
from wps_rtf_builder import build_document_from_spec, escape_rtf_text
from read_wps import read_bytes
from run_wps_task import create_document
from run_wps_edit import execute_edit_task


class LayoutTests(unittest.TestCase):
    def test_unicode_and_exact_line_spacing(self):
        self.assertEqual(escape_rtf_text('😀'), r'\u-10179?\u-8704?')
        doc = build_document_from_spec({'page_numbering': False, 'blocks': [{'type': 'paragraph', 'text': '甲😀乙'}]})
        raw = doc.render().encode('ascii')
        self.assertIn(br'\sl-560\slmult0', raw)
        self.assertEqual(read_bytes(raw, reading_mode='standard')['paragraphs'][0]['text'], '甲😀乙')

    def test_wide_table_fits_printable_area(self):
        doc = build_document_from_spec({'blocks': [{'type': 'table', 'rows': [['x'] * 12]}]})
        width = 11906 - doc.margin_left_twips - doc.margin_right_twips
        self.assertLessEqual(sum(cell.width_twips for cell in doc.elements[0].rows[0].cells), width)
        for rows in ([], [['a'], ['b', 'c']]):
            with self.assertRaises(ValueError):
                build_document_from_spec({'blocks': [{'type': 'table', 'rows': rows}]})

    def test_custom_page_and_pagination_controls(self):
        doc = build_document_from_spec({'page': {'width_pt': 600, 'height_pt': 800, 'margin_left_pt': 40},
              'blocks': [{'type': 'paragraph', 'text': '正文', 'page_break_before': True, 'keep_together': True}]})
        rendered = doc.render()
        self.assertIn(r'\paperw12000\paperh16000', rendered)
        self.assertIn(r'\margl800', rendered)
        self.assertIn(r'\pagebb', rendered)
        self.assertIn(r'\keep', rendered)
        with self.assertRaises(ValueError):
            build_document_from_spec({'page': {'width_pt': 1}, 'blocks': [{'type': 'paragraph', 'text': 'x'}]})

    def test_edit_retains_interleaved_structure(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / 'in.wps', Path(temporary) / 'out.wps'
            task_api(create_document, {'page_numbering': False, 'blocks': [{'type': 'paragraph', 'text': '前'},
                            {'type': 'table', 'rows': [['单元格']]}, {'type': 'paragraph', 'text': '后'}]}, source)
            before = source.read_bytes()
            task_api(execute_edit_task, source, output, [{'operation': 'replace_text', 'find': '后', 'replace': '末'}])
            parsed = read_bytes(output.read_bytes(), reading_mode='standard')
            self.assertEqual([item['type'] for item in parsed['blocks']], ['paragraph', 'table', 'paragraph'])
            self.assertEqual(parsed['paragraphs'][-1]['text'], '末')
            self.assertEqual(source.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
