"""Independent synthetic expectations, not producer rendering evidence."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sys,unittest
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wps_numeric import interpret_number
class NumericTests(unittest.TestCase):
    def number(self,value,fmt,system='1900'):
        return interpret_number(Decimal(value),164,fmt,system)
    def test_dates(self):
        for value,expected in [('1','1900-01-01'),('59','1900-02-28'),('61','1900-03-01')]:
            self.assertEqual(self.number(value,'yyyy-mm-dd')['interpreted_value'],expected)
        self.assertEqual(self.number('60','yyyy-mm-dd')['kind'],'fictional_date')
        self.assertFalse(self.number('60','yyyy-mm-dd')['valid_gregorian_date'])
        self.assertEqual(self.number('0','yyyy-mm-dd','1904')['interpreted_value'],'1904-01-01')
    def test_time_duration(self):
        self.assertEqual(self.number('1.5','[h]:mm:ss')['interpreted_value'],'36:00:00')
        self.assertEqual(self.number('1.5','h:mm:ss')['interpreted_value'],'12:00:00')
        self.assertEqual(self.number('2.25','yyyy-mm-dd hh:mm:ss')['interpreted_value'],'1900-01-02T06:00:00')
    def test_money_percent_scale_identifiers(self):
        self.assertEqual(self.number('1234567.125','¥#,##0.00')['display_text'],'¥1,234,567.13')
        self.assertEqual(self.number('0.125','0.00%')['display_text'],'12.50%')
        self.assertEqual(self.number('1234000','0.0,,')['display_text'],'1.2')
        self.assertEqual(self.number('123','000000')['display_text'],'000123')
        self.assertEqual(self.number('-1.235','$0.00')['display_text'],'$-1.24')
    def test_no_guessing(self):
        for fmt in ['[Color57]0','0;-[Red]0','[$USD-409]0','0.#0','@','yyyy-mm-dd;@','#0E+00','0/0']:
            self.assertEqual(self.number('100',fmt)['status'],'unknown')
        self.assertEqual(self.number('-1','yyyy-mm-dd')['status'],'unknown')
        self.assertEqual(self.number('1e100000','0')['status'],'unknown')
        self.assertEqual(self.number('1e100','yyyy-mm-dd')['status'],'unknown')
    def test_decimal_source_preserved(self):
        value=Decimal('999999999999999999999999.125')
        result=interpret_number(value,164,'0.00','1900')
        self.assertEqual(result['display_text'],'999999999999999999999999.13')
        self.assertEqual(value,Decimal('999999999999999999999999.125'))
    def test_date_only_fraction_does_not_roll_calendar(self):
        self.assertEqual(self.number('59.99999999','yyyy-mm-dd')['interpreted_value'],'1900-02-28')
        self.assertEqual(self.number('60.99999999','yyyy-mm-dd')['interpreted_value'],'1900-02-29')
    def test_style_inheritance_unknown(self):
        from wps_numeric import interpret_cell_number
        raw=bytearray(20)
        self.assertEqual(interpret_cell_number(Decimal('1'),1,None,'1900',raw.hex())['reason'],'style_inheritance_unresolved')
        raw[9]=4
        self.assertEqual(interpret_cell_number(Decimal('1'),1,None,'1900',raw.hex())['status'],'interpreted')
        raw[4]=4
        self.assertEqual(interpret_cell_number(Decimal('1'),1,None,'1900',raw.hex())['status'],'unknown')
    def test_biff_additive(self):
        from test_wps_biff8 import workbook
        from wps_biff8 import parse_stream
        result=parse_stream(workbook())
        numeric=[c for s in result['sheets'] for c in s['cells'].values() if c['type']=='number']
        self.assertTrue(numeric)
        self.assertTrue(all('numeric_interpretation' in c for c in numeric))

    def test_biff_general_display_keeps_exact_binary_and_scaled_rk_values(self):
        from struct import pack
        from et_table_preview import preview
        from read_et import read_bytes
        from test_wps_biff8 import cell, rec, workbook
        from test_wps_cfb import synthetic_cfb

        xf = bytearray(20)
        xf[9] = 4
        records = rec(0x0203, cell(0, 0, 2) + pack('<d', 0.1)) + rec(0x027E, cell(0, 1, 2) + pack('<I', 7))
        stream = workbook(sheet_records=[records], extra_global=rec(0x00E0, xf))
        source = read_bytes(synthetic_cfb({'Workbook': stream}))
        cells = source['sheets'][0]['cells']
        self.assertEqual(cells['A1']['value'], Decimal.from_float(0.1))
        self.assertEqual(cells['A1']['numeric_interpretation']['interpreted_value'], str(Decimal.from_float(0.1)))
        self.assertEqual(cells['A1']['numeric_interpretation']['display_text'], '0.1')
        self.assertEqual(cells['B1']['value'], Decimal('0.01'))
        self.assertNotIn('display_text', cells['B1']['numeric_interpretation'])
        projected = preview(source, {'sheet': '中文表1', 'range': 'A1:B1'})['rows'][0]['cells']
        self.assertEqual(projected['A']['value'], Decimal.from_float(0.1))
        self.assertEqual(projected['A']['display_text'], '0.1')
        self.assertNotIn('display_text', projected['B'])

    def test_scientific_formats_round_and_carry_the_exponent(self):
        cases = [('12200000', None, '1.22E+07'), ('0.00124', '0.0E-0', '1.2E-3'),
                 ('-999.5', '0.00E+00', '-1.00E+03'), ('0', '0.00E-000', '0.00E000')]
        for raw, code, expected in cases:
            with self.subTest(raw=raw, code=code):
                item = interpret_number(Decimal(raw), 11, code, '1900')
                self.assertEqual((item['kind'], item['display_text'], item['interpreted_value']), ('scientific', expected, raw))

    def test_builtin_mixed_fractions_bound_the_denominator(self):
        for raw, format_id, expected in [('0.125', 12, '1/8'), ('-2.37', 12, '-2 3/8'), ('2.04', 13, '2 1/25'), ('1.99', 12, '2'), ('-0.01', 12, '0')]:
            with self.subTest(raw=raw, format_id=format_id):
                item = interpret_number(Decimal(raw), format_id, None, '1900')
                self.assertEqual((item['kind'], item['display_text'], item['interpreted_value']), ('fraction', expected, raw))
                self.assertTrue(item['alignment_omitted'])

    def test_single_section_color_is_metadata(self):
        item = self.number('12.5', '[Color 56]0.00')
        self.assertEqual((item['display_text'], item['format_color_raw'], item['format_code']), ('12.50', 'Color 56', '[Color 56]0.00'))
        self.assertNotIn('display_text', self.number('12.5', '[Red][Blue]0'))

    def test_bounded_literal_affixes_do_not_change_the_number(self):
        for code, expected in [('0.00" kg"', '12.50 kg'), ('0.0\\%', '12.5%'), ('"¥"#,##0.00', '¥12.50')]:
            with self.subTest(code=code):
                item = self.number('12.5', code)
                self.assertEqual(item['display_text'], expected)
                self.assertEqual(item['interpreted_value'], '12.5')
        self.assertEqual(self.number('12.5', '0.00"unterminated')['status'], 'unknown')

    def test_optional_decimals_respect_padding_and_rounding(self):
        for value, code, expected in [('120', '0.##', '120'), ('12.500', '0.0##', '12.5'), ('0.999', '000.##', '001'),
                                      ('0.125', '0.##%', '12.5%'), ('1234.001', '#,##0.##', '1,234')]:
            with self.subTest(value=value, code=code):
                self.assertEqual(self.number(value, code)['display_text'], expected)
        for code in ('0.', '0.' + '0' * 12 + '#'):
            self.assertEqual(self.number('1', code)['status'], 'unknown')

    def test_explicit_cjk_date_labels_and_saved_serial(self):
        for value, code, system, expected in [('1.99999999', 'yyyy"年"m"月"d"日"', '1900', '1900年1月1日'),
                                               ('0', 'yyyy年mm月dd日', '1904', '1904年01月01日'),
                                               ('60', 'yyyy年m月d日', '1900', '1900年2月29日')]:
            with self.subTest(value=value, code=code, system=system):
                result = self.number(value, code, system)
                self.assertEqual(result['display_text'], expected)
                self.assertEqual(result['valid_gregorian_date'], value != '60')

    def test_escaped_slash_date_from_pinned_wps_template(self):
        item = self.number('61.75', r'm\/d\/yyyy')
        self.assertEqual((item['interpreted_value'], item['display_text']), ('1900-03-01', '3/1/1900'))
        self.assertEqual(self.number('61.75', 'mm/dd/yyyy')['display_text'], '03/01/1900')

    def test_linked_biff_styles_preserve_raw_formats_and_formula_cache(self):
        from struct import pack
        from read_et import read_bytes
        from test_wps_biff8 import cell, rec, string, workbook
        from test_wps_cfb import synthetic_cfb

        def xf(fmt, parent, style=False, override=False):
            flags = (parent << 4) | (4 if style else 0)
            return rec(0x00E0, pack('<3H', 0, fmt, flags) + bytes((0, 0, 0, 4 if override else 0)) + bytes(10))

        currency = '"¥"#,##0.00_);[Red]("¥"#,##0.00)'
        extra = (rec(0x041E, pack('<H', 8) + string(currency)) + xf(164, 0xfff, style=True) + xf(164, 2)
                 + xf(10, 2, override=True) + xf(10, 2) + xf(164, 3) + xf(164, 40) + xf(164, 2, style=True) + xf(164, 8)
                 + xf(8, 0xfff, style=True) + xf(8, 10))
        numbers = b''.join(rec(0x0203, cell(0, col, index) + pack('<d', value)) for col, (index, value) in
                           enumerate(((3, 61), (4, 0.125), (5, 0.125), (6, 61), (7, 61), (9, 61))))
        cache = rec(6, cell(0, 6, 3) + pack('<dHIH', 61, 0, 0, 3) + b'\x1e\x01\x00')
        amount = rec(0x0203, cell(0, 7, 11) + pack('<d', -1200.5))
        stream = workbook(sheet_records=[numbers + cache + amount], extra_global=extra)
        result = read_bytes(synthetic_cfb({'Workbook': stream}))
        cells = result['sheets'][0]['cells']
        date = cells['A1']
        self.assertEqual((date['value'], date['number_format_id_raw'], date['custom_format_raw']), (Decimal(61), 164, 'yyyy-mm-dd'))
        self.assertEqual(date['numeric_interpretation']['interpreted_value'], '1900-03-01')
        self.assertEqual(date['numeric_interpretation']['number_format_source'], {'style_xf_index': 2, 'parent_num_fmt_id': 164})
        self.assertEqual(cells['B1']['numeric_interpretation']['display_text'], '12.50%')
        self.assertEqual(cells['C1']['numeric_interpretation']['reason'], 'cell_parent_number_format_conflict')
        for address in ('D1', 'E1', 'F1'):
            self.assertEqual(cells[address]['numeric_interpretation']['reason'], 'style_inheritance_unresolved')
        self.assertEqual((cells['G1']['value'], cells['G1']['cache_status']), (Decimal(61), 'unverified'))
        self.assertEqual(cells['G1']['numeric_interpretation']['interpreted_value'], '1900-03-01')
        self.assertEqual(cells['H1']['custom_format_raw'], currency)
        self.assertEqual(cells['H1']['numeric_interpretation']['display_text'], '(¥1,200.50)')
        self.assertEqual(cells['H1']['value'], Decimal('-1200.5'))
        self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')

if __name__=='__main__':
    unittest.main()
