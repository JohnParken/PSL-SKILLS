"""Default CLI useful-content contracts, with synthetic inputs only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from read_wps import read_bytes
from wps_content_view import project_content
from wps_maximum_pages import assemble_pages
from test_wps_ooxml_objects import native_package
from test_wps_ooxml import package
from test_wps_ooxml_related import package as related_package, root, rel
from test_wps_binary_charts import chart_fixture, chart_package
from test_wps_nested_tables import props
from test_wps_msdoc_maximum import cfb_payload, papx_document


class VisibleContentTests(unittest.TestCase):
    """Defaults include chart content without dense structural diagnostics."""

    def cli(self, data, *flags):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'input.wps').write_bytes(data)
            command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve().parents[1] / 'read_wps.py'), './input.wps', *flags]
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=False)
            self.assertIn(result.returncode, (0, 3), result.stderr)
            return json.loads(result.stdout)

    def test_default_ooxml_content_and_explicit_full_api(self):
        data = native_package()
        result = self.cli(data)
        self.assertEqual(result['schema_version'], 'wps_content_v1')
        self.assertEqual(result['charts'][0]['title']['text'], 'Visible title')
        self.assertEqual(result['charts'][0]['series'][0]['values']['saved_data'][0]['points'][0],
                         {'index': '0', 'value': '0.1250', 'display': '12.50%'})
        for field in ('visibility', 'diagnostics', 'reading_assessment', 'ooxml_charts', 'paragraphs', 'revision_content'):
            self.assertNotIn(field, result)
        encoded = json.dumps(result)
        self.assertNotIn('word/charts/', encoded)
        self.assertNotIn('relationship_id', encoded)
        self.assertNotIn('Sheet1!', encoded)
        full = self.cli(data, '--profile', 'full')
        self.assertEqual(full['ooxml_charts'], read_bytes(data)['ooxml_charts'])
        self.assertTrue(result['notes'])

    def test_default_binary_floating_and_inline_chart_without_png_decode(self):
        for kind in ('floating', 'inline'):
            data = chart_fixture(**{kind: chart_package()})
            with (patch('wps_binary_images.read_inline_png_preview', side_effect=AssertionError('PNG decoded')),
                  patch('wps_binary_images._floating_images', side_effect=AssertionError('PNG decoded'))):
                result = read_bytes(data)
            self.assertEqual(result['binary_charts']['chart_count'], 1)
            self.assertNotIn('binary_images', result)
            projected = project_content(result)
            self.assertEqual(projected['charts'][0]['title'], 'Synthetic chart')
            self.assertEqual(projected['charts'][0]['series'][0]['rows'][0]['raw_value'], '9.1250')
            self.assertIn('saved_chart', projected['charts'][0]['source'])
            self.assertFalse(projected['current_view_available'])

    def test_nested_binary_cells_preserve_text_and_verified_ownership(self):
        for first, inner, last in (('P', 'A', 'Q'), ('父段', '子😀', '尾段')):
            data = cfb_payload(papx_document((first + '\r', props(1)), (inner + '\r', props(2, cell=True)),
                                             ('\r', props(2, row=True)), (last + '\x07', props(1)),
                                             ('\x07', props(1, row=True)), ('\r', b'')))
            result = self.cli(data)
            full = read_bytes(data)
            for text in (first, inner, last):
                self.assertIn(text, result['text'])
                self.assertIn(text, full['text'])
            cell = result['nested_content']['rows'][0][0]
            self.assertEqual(cell['text'], first + '\n' + last)
            self.assertEqual(cell['nested_tables'][0]['rows'][0][0]['text'], inner)
            self.assertEqual(cell['nested_tables'][0]['grouping'], 'stored')
            self.assertNotIn('cp_start', json.dumps(result))

    def test_missing_chart_part_keeps_concise_gap_not_exception(self):
        result = self.cli(native_package(target='charts/missing.xml'))
        self.assertEqual(result['status'], 'partial')
        self.assertIn('missing_chart_part_or_content_type', result['charts'][0]['notes'])

    def test_ooxml_table_text_once_without_deleting_identical_independent_paragraphs(self):
        paragraph = '<w:p><w:r><w:t>SAME</w:t></w:r></w:p>'
        table = '<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc>' + paragraph + '</w:tc></w:tr></w:tbl>'
        data = package(paragraph + table + paragraph)
        result = self.cli(data)
        self.assertNotIn('text', result)
        self.assertEqual(result['body'], [{'text': 'SAME'}, {'table': 1}, {'text': 'SAME'}])
        self.assertEqual(result['tables'][0]['rows'][0][0]['text'], 'SAME')
        self.assertEqual(json.dumps(result).count('SAME'), 3)
        self.assertEqual(read_bytes(data)['text'], 'SAME\nSAME\nSAME')
        pages = []
        cursor = None
        while True:
            flags = ['--page-size', '200', '--max-output-bytes', '2000'] + (['--cursor', cursor] if cursor else [])
            page = self.cli(data, *flags)
            pages.append(page)
            cursor = page.get('next_cursor')
            if not cursor:
                break
        self.assertEqual(assemble_pages(pages), result)

    def test_ooxml_nested_table_and_header_each_keep_order_and_ownership_once(self):
        def paragraph(text):
            return '<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>'
        def table(contents):
            return '<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc>' + contents + '</w:tc></w:tr></w:tbl>'
        data = package(table(paragraph('P') + table(paragraph('A')) + paragraph('Q')))
        result = self.cli(data)
        self.assertEqual(result['body'], [{'table': 1}])
        self.assertEqual(result['tables'][0]['rows'][0][0]['content'], [{'text': 'P'}, {'table': 2}, {'text': 'Q'}])
        self.assertEqual(result['tables'][1]['rows'][0][0]['text'], 'A')
        self.assertNotIn('text', result['tables'][0]['rows'][0][0])
        body = paragraph('Main') + '<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>'
        data = related_package(body, rel('h', 'header', 'head.xml'), [('word/head.xml', 'header', root('header', table(paragraph('Header cell'))))])
        result = self.cli(data)
        self.assertNotIn('tables', result)
        self.assertEqual(result['text'], 'Main')
        header = result['related_content'][0]
        self.assertNotIn('text', header)
        self.assertEqual(header['body'], [{'table': 1}])
        self.assertEqual(header['tables'][0]['rows'][0][0]['text'], 'Header cell')
        self.assertEqual(json.dumps(result).count('Header cell'), 1)

    def test_content_pages_reassemble_same_projection_and_bind_profile(self):
        data = native_package()
        expected = self.cli(data)
        pages = []
        cursor = None
        while True:
            flags = ['--page-size', '300', '--max-output-bytes', '2000']
            if cursor:
                flags += ['--cursor', cursor]
            page = self.cli(data, *flags)
            pages.append(page)
            cursor = page.get('next_cursor')
            if not cursor:
                break
        self.assertEqual(assemble_pages(pages), expected)


if __name__ == '__main__':
    unittest.main()
