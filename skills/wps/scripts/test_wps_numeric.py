"""Independent synthetic expectations, not producer rendering evidence."""
from pathlib import Path
import sys,unittest
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wps_numeric import interpret_number
class NumericTests(unittest.TestCase):
    def number(self,value,fmt,system='1900'): return interpret_number(Decimal(value),164,fmt,system)
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
        for fmt in ['[Red]0','0;[Red]-0','[$USD-409]0','0.##','@','yyyy-mm-dd;@','0E+00','0/0']:
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
if __name__=='__main__': unittest.main()
