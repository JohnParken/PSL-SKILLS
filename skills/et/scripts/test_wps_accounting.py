"""Independently specified accounting values; synthetic, not real display evidence."""
from decimal import Decimal
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wps_numeric import interpret_number

FORMAT = '_("$"* #,##0.00_);_("$"* \\(#,##0.00\\);_(\\$* "-"??_);_(@_)'

class AccountingTests(unittest.TestCase):
    def value(self, value, fmt=FORMAT):
        return interpret_number(Decimal(value), 181, fmt, '1900')

    def test_positive_negative_zero_sections(self):
        for value, expected, section in [('1234.565','$1,234.57',0),('-1234.565','$(1,234.57)',1),('0','$-',2),('-0','$-',2)]:
            result=self.value(value)
            self.assertEqual(result['display_text'],expected)
            self.assertEqual(result['selected_section'],section)
            self.assertEqual(result['interpreted_value'],value)
            self.assertTrue(result['alignment_omitted'])
            self.assertFalse(result['display_width_verified'])

    def test_exact_large_decimal_and_two_sections(self):
        value='123456789012345678901234567890.125'
        self.assertEqual(self.value(value)['display_text'],'$123,456,789,012,345,678,901,234,567,890.13')
        self.assertEqual(self.value('-2.5','0.00;(0.00)')['display_text'],'(2.50)')
        self.assertEqual(self.value('0','0.00;(0.00)')['display_text'],'0.00')
        self.assertEqual(self.value('-2.5','0.00;0.00')['display_text'],'2.50')
        self.assertEqual(self.value('-0.004')['display_text'],'$(0.00)')

    def test_no_fabricated_width_or_currency_code(self):
        self.assertEqual(self.value('1')['currency_symbol'],'$')
        self.assertIsNone(self.value('1')['currency_code'])

    def test_unsupported_grammar_never_falls_back(self):
        for fmt in ['[Red]0;(0)','[>=0]0;(0)','[$$-409]0;(0)','0%;(0%)','0,;(0,)','0;0;0;"text"@','0;0;','0;0;"0"','0;0;0;@;@','0;0;0*X','0;0;"-;"','0;0;"-";"secret"@']:
            with self.subTest(fmt=fmt):
                result=self.value('1',fmt)
                self.assertEqual(result['status'],'unknown')
                self.assertNotIn('display_text',result)

    def test_budget_and_unchanged_value(self):
        self.assertEqual(self.value('1','0;0;'+'x'*256)['status'],'unknown')
        self.assertEqual(self.value('1E2000')['status'],'unknown')
        value=Decimal('-12.345'); before=value.as_tuple()
        interpret_number(value,181,FORMAT,'1900')
        self.assertEqual(value.as_tuple(),before)

if __name__=='__main__': unittest.main()
