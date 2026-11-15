"""Synthetic parse-window parity, failures, cursor and memory checks."""
import gc
from pathlib import Path
import sys
import tracemalloc
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from et_parse_page import read_page
from wps_access import ParseError
from wps_ooxml_sheet import read_bytes
from test_wps_ooxml_sheet import package, T, R, SHEET_TYPE


def rows_xml(rows, extra=''):
    return '<worksheet xmlns="' + T + '"><sheetData>' + rows + '</sheetData>' + extra + '</worksheet>'


def sample(count):
    rows = ''.join(f'<row r="{i}"><c r="A{i}"><v>{i}</v></c></row>' for i in range(1, count + 1))
    return package(overrides={'xl/worksheets/sheet1.xml': rows_xml(rows)})


class ParsePageTests(unittest.TestCase):
    def assert_pages(self, data, size=2, sheet=None, mode='parse_time_cell_window'):
        full = read_bytes(data)
        expected = [(entry['name'], ref, value) for entry in full['sheets']
                    if sheet is None or entry['name'] == sheet
                    for ref, value in entry['cells'].items()]
        actual = []
        cursor = None
        while True:
            page = read_page(data, page_size=size, cursor=cursor, sheet=sheet)
            self.assertEqual(page['parse_mode'], mode)
            for key in ('status', 'issues', 'cell_value_coverage', 'omissions_affect_cell_values', 'text_budget'):
                self.assertEqual(page[key], full[key], key)
            self.assertEqual(page['start'], len(actual))
            self.assertEqual(page['total_items'], len(expected))
            self.assertEqual(page['source_cell_count'], full['cell_count'])
            actual.extend((item['sheet'], item['cell'], item['data']) for item in page['items'])
            cursor = page['next_cursor']
            self.assertEqual(page['has_more'], cursor is not None)
            if cursor is None:
                break
        self.assertEqual(actual, expected)
        return actual

    def test_reassembly_and_stored_order(self):
        data = package(cells='<c r="C1"><v>3</v></c><c r="A1"><v>1</v></c><c r="B1"><v>2</v></c>')
        self.assertEqual([item[1] for item in self.assert_pages(data, 1)], ['C1', 'A1', 'B1'])

    def test_duplicates_invalid_cells_and_missing_cache(self):
        data = package(cells='<c r="A1" t="b"><v>invalid</v></c><c r="A1"><v>7</v></c>'
                             '<c r="B1"><v>2</v></c><c r="A1"><v>99</v></c>'
                             '<c r="C1"><f>SUM(A1:B1)</f></c>')
        values = self.assert_pages(data, 1)
        self.assertEqual(values[0][2]['value'], 7)
        self.assertEqual(len(values), 3)

    def test_text_formula_precision_and_shared_strings(self):
        data = package(cells='<c r="A1" t="inlineStr"><is><t>0001😀_x000D_</t></is></c>'
                             '<c r="B1" t="s"><v>0</v></c>'
                             '<c r="C1"><f>1+1</f><v>2</v></c>'
                             '<c r="D1"><v>12345678901234567890.125</v></c>')
        self.assert_pages(data, 1)

    def test_tail_and_unselected_sheet_diagnostics(self):
        from test_wps_ooxml_sheet import text_parts_of, build_package
        parts = text_parts_of(sample(3))
        parts['xl/workbook.xml'] = parts['xl/workbook.xml'].replace('</sheets>', '<sheet name="Other" sheetId="8" r:id="s2"/></sheets>')
        parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].replace(
            '</Relationships>', '<Relationship Id="s2" Type="' + R + '/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace(
            '</Types>', '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="' + SHEET_TYPE + '"/></Types>')
        parts['xl/worksheets/sheet2.xml'] = rows_xml('<row r="1"><c r="A1"><f>1</f></c></row>')
        self.assert_pages(build_package(parts), 1, sheet='Data')

    def test_inherited_styles_fall_back_without_losing_diagnostics(self):
        for extra, rows in [
            ('<cols><col min="1" max="1" style="0"/></cols>', '<row r="1"><c r="A1"><v>3</v></c></row>'),
            ('', '<row r="1" s="0" customFormat="1"><c r="A1"><v>3</v></c></row>'),
        ]:
            data = package(overrides={'xl/worksheets/sheet1.xml': rows_xml(rows, extra)})
            self.assert_pages(data, 1, mode='full_parse_fallback')

    def test_cursor_binding_and_invalid_positions(self):
        data = sample(4)
        token = read_page(data, page_size=1)['next_cursor']
        for kwargs in ({'page_size': 2}, {'page_size': 1, 'sheet': 'Data'}):
            with self.assertRaises(ParseError):
                read_page(data, cursor=token, **kwargs)
        with self.assertRaises(ParseError):
            read_page(sample(5), page_size=1, cursor=token)
        for token in ('!', 'e30=', 'W10='):
            with self.assertRaises(ParseError):
                read_page(data, cursor=token)
        page = read_page(data, page_size=1, cursor=read_page(data, page_size=1)['next_cursor'], max_bytes=20000)
        self.assertEqual(page['items'][0]['cell'], 'A2')

    def test_empty_unknown_and_invalid_options(self):
        self.assertEqual(read_page(package(cells=''))['items'], [])
        for kwargs in ({'sheet': 'Missing'}, {'page_size': True}, {'page_size': 0}, {'max_bytes': 511}):
            with self.assertRaises(ParseError):
                read_page(sample(2), **kwargs)
        with self.assertRaises(ParseError):
            read_page(b'not an ooxml package')

    def test_output_budget_is_explicit_failure(self):
        with self.assertRaises(ParseError):
            read_page(sample(3), max_bytes=512)

    def test_xml_security_and_corrupt_tail_never_publish_page(self):
        for xml in (rows_xml('<row r="1"><c r="A1"><v>2</v></c></row>') + '<broken',
                    '<!DOCTYPE worksheet [<!ENTITY x "boom">]>' + rows_xml('')):
            data = package(overrides={'xl/worksheets/sheet1.xml': xml})
            full = read_bytes(data)
            page = read_page(data, page_size=1)
            self.assertEqual(page['status'], full['status'])
            if full['status'] in ('ok', 'partial'):
                self.assertEqual(page['issues'], full['issues'])
                self.assertEqual(page['items'], [])
                self.assertTrue(page['omissions_affect_cell_values'])
            else:
                self.assertNotIn('items', page)

    def test_global_text_and_cell_limits_do_not_reset_at_page_boundary(self):
        data = package(cells=''.join(f'<c r="{col}1" t="inlineStr"><is><t>abc</t></is></c>' for col in 'ABCDE'))
        with patch('wps_ooxml_worksheet.MAX_CELLS', 3):
            self.assert_pages(data, 1)
        from wps_ooxml_text import TextReader
        original = TextReader.__init__
        def small_budget(reader, *args, **kwargs):
            original(reader, *args, **kwargs)
            reader.limit = 5
        with patch.object(TextReader, '__init__', small_budget):
            self.assert_pages(data, 1)

    def test_cell_payload_peak_is_lower_than_full_result(self):
        data = sample(5000)
        def peak(call):
            gc.collect()
            tracemalloc.start()
            result = call()
            high = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            return result, high
        full, full_peak = peak(lambda: read_bytes(data))
        page, page_peak = peak(lambda: read_page(data, page_size=10))
        self.assertEqual(full['cell_count'], 5000)
        self.assertEqual(page['returned_items'], 10)
        self.assertLess(page_peak, full_peak * 0.65)

    def test_explicit_styles_remain_on_optimized_path(self):
        from test_wps_ooxml_sheet import text_parts_of, build_package, STYLES_TYPE
        parts = text_parts_of(package(cells='<c r="A1" s="0"><v>123.5</v></c><c r="B1" s="0"><v>2</v></c>'))
        parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].replace(
            '</Relationships>', '<Relationship Id="style" Type="' + R + '/styles" Target="styles.xml"/></Relationships>')
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace(
            '</Types>', '<Override PartName="/xl/styles.xml" ContentType="' + STYLES_TYPE + '"/></Types>')
        parts['xl/styles.xml'] = '<styleSheet xmlns="' + T + '"><cellXfs count="1"><xf numFmtId="2"/></cellXfs></styleSheet>'
        self.assert_pages(build_package(parts), 1)

    def test_independent_readonly_cli_and_source_unchanged(self):
        import json
        import shutil
        import subprocess
        import tempfile
        skill = Path(__file__).resolve().parents[2]
        repository = skill.parents[1]
        manifest = json.loads((repository / 'packaging/runtime-manifests/et.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(prefix='et-page-independent-') as directory:
            root = Path(directory)
            install = root / 'isolated'
            task = root / 'task'
            task.mkdir()
            for relative in manifest['files']:
                target = install / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(skill / relative, target)
                target.chmod(0o444)
            data = sample(5)
            source = task / 'input.et'
            source.write_bytes(data)
            script = install / 'scripts/et_parse_page.py'
            def invoke(*arguments):
                result = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), *arguments],
                                        cwd=task, capture_output=True, check=False)
                self.assertIn(result.returncode, (0, 3), result.stderr)
                return json.loads(result.stdout)
            self.assertEqual(invoke('--capabilities')['schema_version'], 'et_parse_page_v1')
            first = invoke('input.et', '--page-size', '2')
            second = invoke('input.et', '--page-size', '2', '--cursor', first['next_cursor'])
            third = invoke('input.et', '--page-size', '2', '--cursor', second['next_cursor'])
            self.assertEqual([first['start'], second['start'], third['start']], [0, 2, 4])
            self.assertIsNone(third['next_cursor'])
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual(list(task.iterdir()), [source])

    def test_source_failure_obeys_utf8_budget_in_python_api(self):
        from test_wps_ooxml_sheet import text_parts_of, build_package
        from wps_read_view import encoded
        parts = text_parts_of(package())
        long_path = 'xl/' + 'x' * 1000 + '.xml'
        parts[long_path] = '<broken'
        parts['_rels/.rels'] = parts['_rels/.rels'].replace('xl/workbook.xml', long_path)
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace('/xl/workbook.xml', '/' + long_path)
        data = build_package(parts)
        result = read_page(data, max_bytes=4096)
        self.assertEqual(result['status'], 'corrupt')
        size = len((encoded(result) + '\n').encode('utf-8'))
        self.assertGreater(size, 512)
        self.assertEqual(read_page(data, max_bytes=size), result)
        for limit in (512, size - 1):
            with self.assertRaises(ParseError) as caught:
                read_page(data, max_bytes=limit)
            self.assertEqual(caught.exception.status, 'output_view_error')

    def test_fallback_memory_scope_is_not_claimed_optimized(self):
        rows = '<row r="1" s="0"><c r="A1"><v>3</v></c></row>'
        data = package(overrides={'xl/worksheets/sheet1.xml': rows_xml(rows)})
        result = read_page(data)
        self.assertEqual(result['parse_mode'], 'full_parse_fallback')
        self.assertEqual(result['memory_scope'], 'full_cell_payloads_retained; no_parse_window_memory_optimization')


if __name__ == '__main__':
    unittest.main()
