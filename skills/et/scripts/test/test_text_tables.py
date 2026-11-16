"""Synthetic independent expectations for text exports and CJK evidence."""
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_et import read_bytes
from wps_access import ParseError
from wps_numeric import interpret_number
from wps_text_tables import read_text_table


class TextTablesTests(unittest.TestCase):
    def test_explicit_legacy_html_charsets_keep_identifiers_and_diagnostics(self):
        for codec, value in (('gb18030', '中文𠀀'), ('gbk', '中文'), ('gb2312', '中文')):
            raw = ('<html><head><meta charset="' + codec + '"></head>'
                   '<table><tr><td>001</td><td>' + value + '</td></tr></table></html>').encode(codec)
            result = read_bytes(raw)
            self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '001')
            self.assertEqual(result['sheets'][0]['cells']['B1']['value'], value)
            self.assertEqual(result['encoding'], codec)
            self.assertEqual(result['encoding_source'], 'html_meta_declaration')
            self.assertFalse(result['encoding_declaration_verified'])
            self.assertEqual(result['status'], 'partial')
            self.assertIn('html_declared_legacy_encoding', [i['code'] for i in result['issues']])

    def test_html_http_equiv_and_repeated_equivalent_declarations(self):
        raw = ('<meta HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=GB18030">'
               '<meta charset="gb18030"><table><tr><td>中文</td></tr></table>').encode('gb18030')
        self.assertEqual(read_bytes(raw)['sheets'][0]['cells']['A1']['value'], '中文')

    def test_legacy_encoding_is_not_guessed_from_text_or_fake_meta(self):
        suffix = '<table><tr><td>中文</td></tr></table>'
        prefixes = ('', '<!--<meta charset="gb18030">-->',
                    '<script>"<meta charset=gb18030>"</script>',
                    '<body><meta charset="gb18030">',
                    '<!--' + 'x' * 8192 + '--><meta charset="gb18030">')
        for prefix in prefixes:
            with self.subTest(prefix=prefix[:30]), self.assertRaises(ParseError):
                read_bytes((prefix + suffix).encode('gb18030'))
        with self.assertRaises(ParseError):
            read_bytes('编号,金额\n001,中文'.encode('gb18030'))

    def test_conflicting_unsupported_or_invalid_charset_is_rejected(self):
        suffix = b'<table><tr><td>x</td></tr></table>'
        prefixes = (b'<meta charset="gbk"><meta charset="utf-8">',
                    b'<meta charset="windows-1252">',
                    b'<meta charset="gbk" charset="gb18030">',
                    b'\xef\xbb\xbf<meta charset="gb18030">')
        for prefix in prefixes:
            with self.subTest(prefix=prefix), self.assertRaises(ParseError):
                read_bytes(prefix + suffix)
        with self.assertRaises(ParseError):
            read_bytes(b'<meta charset="gb18030"><table><tr><td>\xff</td></tr></table>')

    def test_meta_inside_raw_text_context_never_declares_encoding(self):
        for tag in ('title', 'textarea', 'xmp', 'iframe', 'noembed', 'noframes', 'script', 'style', 'plaintext'):
            raw = ('<' + tag + '><meta charset=gbk></' + tag + '>'
                   '<table><tr><td>中文</td></tr></table>').encode('gbk')
            with self.subTest(tag=tag), self.assertRaises(ParseError):
                read_bytes(raw)

    def test_http_equiv_requires_actual_complete_charset_parameter(self):
        values = ('text/html; xcharset=gbk', 'text/html; charset=gbk!oops',
                  'text/html; note="x;charset=gbk"', 'text/plain; charset=gbk')
        for content in values:
            raw = ("<meta http-equiv='Content-Type' content='" + content + "'>"
                   '<table><tr><td>中文</td></tr></table>').encode('gbk')
            with self.subTest(content=content), self.assertRaises(ParseError):
                read_bytes(raw)
        raw = ("<meta http-equiv='Content-Type' content='text/html; note=ok; charset=\"GBK\"'>"
               '<table><tr><td>中文</td></tr></table>').encode('gbk')
        self.assertEqual(read_bytes(raw)['sheets'][0]['cells']['A1']['value'], '中文')

    def test_declared_utf8_and_existing_bom_paths_are_preserved(self):
        text = '<meta charset="utf-8"><table><tr><td>中文</td></tr></table>'
        for encoding in ('utf-8-sig', 'utf-16'):
            result = read_bytes(text.encode(encoding))
            self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '中文')
            self.assertEqual(result['status'], 'ok')

    def test_csv_identifiers_and_formula_text(self):
        result = read_bytes(b'id,value\n001,=1+2\n002,"a,b"')
        self.assertEqual(result['format'], 'csv_disguised')
        cells = result['sheets'][0]['cells']
        self.assertEqual(cells['A2']['value'], '001')
        self.assertEqual(cells['B2'], {'type': 'text', 'value': '=1+2'})
        self.assertEqual(cells['B3']['value'], 'a,b')
        self.assertFalse(result['formulas_executed'])

    def test_tsv_unicode_bom(self):
        result = read_bytes('编号\t金额\n001\t12'.encode('utf-16'))
        self.assertEqual(result['format'], 'tsv_disguised')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], '001')

    def test_empty_rows_still_consume_budget(self):
        from unittest.mock import patch
        with patch('wps_text_tables.MAX_ROWS', 3), self.assertRaises(ParseError) as raised:
            read_bytes(b'\n\n\n\n')
        self.assertEqual(raised.exception.status, 'resource_limit')

    def test_csv_field_limit_is_resource_error(self):
        import csv
        old_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(8)
            with self.assertRaises(ParseError) as raised:
                read_bytes(b'a,b\nlonglonglong,1')
            self.assertEqual(raised.exception.status, 'resource_limit')
        finally:
            csv.field_size_limit(old_limit)

    def test_csv_multiline(self):
        result = read_bytes(b'a,b\n001,"two\nlines"')
        self.assertEqual(result['sheets'][0]['cells']['B2']['value'], 'two\nlines')

    def test_reject_prose_binary_and_ambiguous(self):
        for data in (b'ordinary prose', b'\x00bad', b'a,b\nc', b'<random>a,b</random>', b'a,b\tc\nd,e\tf', b'\xff'):
            with self.subTest(data=data), self.assertRaises(ParseError):
                read_text_table(data)

    def test_html_text_and_active_content(self):
        data = b'<html><table><tr><th>ID</th><th>Text</th></tr><tr><td>001</td><td>A&amp;B<br>C<script>evil()</script></td></tr></table></html>'
        result = read_bytes(data)
        self.assertEqual(result['sheets'][0]['cells']['B2']['value'], 'A&B\nC')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], '001')

    def test_html_nested_partial(self):
        result = read_bytes(b'<table><tr><td>outer<table><tr><td>inner</td></tr></table>end</td></tr></table>')
        self.assertTrue(result['partial'])
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 'outerend')
        self.assertEqual(result['sheets'][1]['cells']['A1']['value'], 'inner')

    def test_html_span_and_truncation(self):
        result = read_bytes(b'<table><tr><td colspan="2">001')
        self.assertTrue(result['partial'])
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '001')

    def test_merged_layout_cannot_feed_coordinate_analysis(self):
        from et_table_data import extract_table
        cases = [
            b'<table><tr><td colspan="2">A</td><td>C</td></tr></table>',
            b'<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>Z</td></tr></table>',
            b'<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:s="urn:schemas-microsoft-com:office:spreadsheet">'
            b'<Worksheet><Table><Row><Cell s:MergeDown="1"><Data>A</Data></Cell></Row>'
            b'<Row><Cell s:Index="2"><Data>Z</Data></Cell></Row></Table></Worksheet></Workbook>',
        ]
        for data in cases:
            result = read_bytes(data)
            self.assertFalse(result['coordinates_verified'])
            self.assertEqual(result['coordinate_basis'], 'source_order_only_not_visual_grid')
            with self.assertRaises(ParseError):
                extract_table(result, {'sheet': result['sheets'][0]['name'], 'range': 'A1:C3'})

    def test_xml_sparse_typed_and_cached(self):
        data = b'''<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Worksheet ss:Name="Report"><Table><Row ss:Index="3"><Cell ss:Index="2"><Data ss:Type="String">001</Data></Cell>
<Cell ss:Formula="=1+2"><Data ss:Type="Number">3</Data></Cell></Row></Table></Worksheet></Workbook>'''
        result = read_bytes(data)
        cells = result['sheets'][0]['cells']
        self.assertEqual(cells['B3']['value'], '001')
        self.assertEqual(cells['C3']['value'], Decimal(3))
        self.assertEqual(cells['C3']['raw_value'], '3')
        self.assertEqual(cells['C3']['cache_status'], 'unverified')

    def test_xml_table_origins_and_relative_indexes_across_encodings(self):
        from et_table_data import extract_table
        templates = [
            ('', '', '', 'A1', 'B2'),
            ('s:TopCell="5"', '', '', 'A5', 'B6'),
            ('s:LeftCell="3"', '', '', 'C1', 'D2'),
            ('s:TopCell="5" s:LeftCell="3"', '', '', 'C5', 'D6'),
            ('s:TopCell="5" s:LeftCell="3"', 's:Index="2"', 's:Index="3"', 'E6', 'D7'),
        ]
        for attrs, row_index, col_index, first, second in templates:
            source = ('<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
                      'xmlns:s="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet s:Name="S">'
                      '<Table ' + attrs + '><Row ' + row_index + '><Cell ' + col_index + '>'
                      '<Data s:Type="Number">42</Data></Cell></Row><Row><Cell s:Index="2">'
                      '<Data s:Type="String">001</Data></Cell></Row></Table></Worksheet></Workbook>')
            for encoding, bom in [('utf-8', b''), ('utf-16-le', b'\xff\xfe'), ('utf-16-be', b'\xfe\xff')]:
                with self.subTest(attrs=attrs, encoding=encoding, indexes=(row_index, col_index)):
                    result = read_bytes(bom + source.encode(encoding))
                    self.assertEqual(result['status'], 'ok')
                    self.assertTrue(result['coordinates_verified'])
                    self.assertEqual(set(result['sheets'][0]['cells']), {first, second})
                    self.assertEqual(result['sheets'][0]['cells'][second]['value'], '001')
                    table = extract_table(result, {'sheet': 'S', 'range': first})
                    self.assertEqual(next(iter(table['rows'][0]['cells'].values()))['value'], Decimal(42))

    def test_xml_table_origin_and_shifted_grid_bounds_rejected(self):
        for attrs, indexes in [
                ('s:TopCell="0"', ''), ('s:LeftCell="0"', ''), ('s:TopCell="-1"', ''),
                ('s:LeftCell="16385"', ''), ('s:TopCell="1048577"', ''),
                ('s:TopCell="1048576"', 's:Index="2"'),
                ('s:LeftCell="16384"', 's:Index="2"')]:
            source = ('<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
                      'xmlns:s="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table ' + attrs + '>'
                      '<Row ' + indexes + '><Cell ' + indexes + '><Data>42</Data></Cell></Row>'
                      '</Table></Worksheet></Workbook>')
            with self.subTest(attrs=attrs), self.assertRaises(ParseError):
                read_bytes(source.encode('utf-8'))

    def test_html_reordered_row_groups_keep_source_order_but_reject_analysis(self):
        from et_table_data import extract_table
        for source in [
                b'<table><tfoot><tr><td>Total</td><td>30</td></tr></tfoot>'
                b'<tbody><tr><td>A</td><td>10</td></tr></tbody></table>',
                b'<table><tbody><tr><td>A</td><td>10</td></tr></tbody>'
                b'<thead><tr><td>Name</td><td>Value</td></tr></thead></table>']:
            result = read_bytes(source)
            self.assertEqual(result['status'], 'partial')
            self.assertFalse(result['coordinates_verified'])
            self.assertIn('html_row_group_order_not_reconstructed', [item['code'] for item in result['issues']])
            self.assertEqual(result['sheets'][0]['cells']['B1']['value'], '30' if b'Total' in source else '10')
            self.assertFalse(result['sheets'][0]['cells']['A1']['coordinate_verified'])
            with self.assertRaises(ParseError):
                extract_table(result, {'sheet': 'Table1', 'range': 'A1:B2'})
        ordered = read_bytes(b'<table><thead><tr><td>H</td></tr></thead><tbody><tr><td>A</td></tr></tbody>'
                             b'<tfoot><tr><td>Total</td></tr></tfoot></table>')
        self.assertTrue(ordered['coordinates_verified'])

    def test_xml_dtd_rejected(self):
        data = b'<!DOCTYPE Workbook [<!ENTITY x "bad">]><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">&x;</Workbook>'
        with self.assertRaises(ParseError):
            read_bytes(data)

    def test_xml_duplicate_cells_rejected(self):
        data = (b'<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
                b'xmlns:s="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table><Row>'
                b'<Cell s:Index="1"/><Cell s:Index="1"/></Row></Table></Worksheet></Workbook>')
        with self.assertRaises(ParseError):
            read_bytes(data)

    def test_cjk_family_without_locale_guess(self):
        for number_format in list(range(27, 37)) + list(range(50, 59)):
            result = interpret_number(Decimal('46340.5'), number_format, None, '1900')
            self.assertEqual(result['kind'], 'date_time_candidate')
            self.assertTrue(result['source_value_unchanged'])
            self.assertNotIn('display_text', result)
            self.assertNotIn('interpreted_value', result)

    def test_cjk_explicit_format_wins(self):
        result = interpret_number(Decimal('1'), 27, 'yyyy-mm-dd', '1900')
        self.assertEqual(result['interpreted_value'], '1900-01-01')
        self.assertEqual(interpret_number(Decimal('NaN'), 27, None, '1900')['kind'], 'unknown')


if __name__ == '__main__':
    unittest.main()
