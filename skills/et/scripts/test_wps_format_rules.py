"""Independent expectations for bounded conditions; all fixtures are synthetic."""
from decimal import Decimal
from pathlib import Path
import sys
import unittest
from xml.parsers import expat
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wps_numeric import interpret_number
from test_wps_row_column_formats import fixture

class FormatRules(unittest.TestCase):
    def value(self, value, code):
        return interpret_number(Decimal(value),164,code,'1900')

    def test_constant_conditions_first_match_and_fallback(self):
        code='[>=1000]0.0,;[<0]0.00;0.0'
        for raw,display,section in [('1250','1.3',0),('-12.345','-12.35',1),('7.25','7.3',2),('1000','1.0',0),('0','0.0',2)]:
            item=self.value(raw,code)
            self.assertEqual(item['status'],'interpreted')
            self.assertEqual(item['display_text'],display)
            self.assertEqual(item['selected_section'],section)
            self.assertEqual(item['format_code'],code)
        self.assertEqual(self.value('2','[>0]0;[>1]0.00;0.000')['selected_section'],0)

    def test_operators_exact_decimal_no_float_or_formula(self):
        for operator,raw,section in [('=', '0.1',0),('<>','0.1',1),('<=','0.1',0),('>','0.1',1),('<','0.09',0)]:
            self.assertEqual(self.value(raw,'['+operator+'0.1]0.00;0.0')['selected_section'],section)
        value=Decimal('123456789012345678901234567890.125')
        result=interpret_number(value,164,'[>99999999999999999999999999999]0.00;0','1900')
        self.assertEqual(result['display_text'],'123456789012345678901234567890.13')
        self.assertEqual(value,Decimal('123456789012345678901234567890.125'))

    def test_invalid_or_unsupported_all_sections_unknown(self):
        for code in ['[>0]0','[>0]0;[<0]0','[>0]0;0;0','[>0]0;[<0]0;0;@','[>0]0;[x]0','[>0]0;"secret"','[>0]0;[Red]0','[>0]0;SUM(A1)','[>0]0;0;','[>1e3]0;0','[>'+('9'*65)+']0;0','[>=0]yyyy-mm-dd;0']:
            with self.subTest(code=code):
                item=self.value('2',code)
                self.assertEqual(item['status'],'unknown')
                self.assertNotIn('display_text',item)

    def test_locale_recorded_without_fabricated_rendering(self):
        for code,symbol,number in [('[$$-409]#,##0.00','$',1033),('[$¥-804]0.00','¥',2052),('[$-F800]yyyy-mm-dd','',63488)]:
            item=self.value('12.5',code)
            self.assertEqual(item['status'],'unknown')
            self.assertEqual(item['locale_directive']['currency_symbol_raw'],symbol)
            self.assertEqual(item['locale_directive']['lcid_integer'],number)
            self.assertFalse(item['locale_directive']['resolved'])
            self.assertNotIn('display_text',item)

    @unittest.skipUnless(expat.version_info>=(2,6,0),'requires Expat >=2.6')
    def test_dual_number_format_agreement_not_precedence(self):
        cols='<cols><col min="1" max="1" style="1"/></cols>'
        cell=fixture('s="1" customFormat="1"',cols)['sheets'][0]['cells']['A1']
        item=cell['numeric_interpretation']
        self.assertEqual(item['display_text'],'12.50%')
        self.assertEqual(item['style_source']['kind'],'row_and_column_equivalent')
        self.assertEqual(len(item['number_format_source']['equivalent_sources']),2)
        for row in ['s="0" customFormat="1"','s="1" customFormat="0"','s="99" customFormat="1"']:
            item=fixture(row,cols)['sheets'][0]['cells']['A1']['numeric_interpretation']
            self.assertEqual(item['status'],'unknown')
            self.assertNotIn('display_text',item)

if __name__=='__main__': unittest.main()
