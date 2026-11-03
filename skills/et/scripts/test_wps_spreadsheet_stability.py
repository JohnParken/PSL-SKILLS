"""Final-audit regressions, synthetic values with independent expectations."""
import sys
from pathlib import Path
import unittest
from xml.parsers import expat
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_wps_ooxml_sheet import package,result,T

@unittest.skipUnless(expat.version_info >= (2,6,0), 'requires Expat >=2.6')
class SpreadsheetStability(unittest.TestCase):
    def test_present_empty_string_cache_differs_from_absent_value(self):
        out=result(package('<c r="A1" t="str"><f>""</f><v/></c><c r="B1" t="str"><f>""</f></c>'))
        self.assertEqual(out['status'],'partial')
        a,b=(out['sheets'][0]['cells'][key] for key in ('A1','B1'))
        self.assertEqual((a['type'],a['value'],a['cache_status']),('text','','unverified'))
        self.assertEqual((b['type'],b['value'],b['cache_status']),('blank',None,'missing'))
        self.assertEqual([x['cell'] for x in out['issues'] if x['code']=='missing_formula_cache'],['B1'])

    def test_empty_numeric_value_is_not_a_blank_cell(self):
        out=result(package('<c r="A1"><v/></c>'))
        self.assertEqual(out['status'],'corrupt')

    def test_row_style_must_not_be_interpreted_as_general(self):
        xml=f'<worksheet xmlns="{T}"><sheetData><row r="1" s="3" customFormat="1"><c r="A1"><v>60</v></c><c r="B1" s="0"><v>60</v></c></row></sheetData></worksheet>'
        out=result(package(overrides={'xl/worksheets/sheet1.xml':xml}))
        self.assertEqual(out['status'],'partial')
        cells=out['sheets'][0]['cells']
        self.assertEqual(cells['A1']['numeric_interpretation']['status'],'unknown')
        self.assertEqual(cells['A1']['value'],60)
        self.assertEqual(cells['B1']['numeric_interpretation']['kind'],'number')
        self.assertEqual(out['sheets'][0]['visibility']['rows'][0]['style_index_raw'],'3')

    def test_malformed_nested_or_unwrapped_string_text_is_not_silently_lost(self):
        for content in ('<t><t>LOST</t></t>', 'LOST', '<t>kept</t>LOST', '<r>LOST<t>kept</t></r>', '<r><t><t>LOST</t></t></r>'):
            with self.subTest(content=content):
                out=result(package('<c r="A1" t="inlineStr"><is>'+content+'</is></c>'))
                self.assertEqual(out['status'],'corrupt')

    def test_column_style_preserved_without_guessing_display(self):
        out=result(package(sheet_extra='<cols><col min="1" max="1" style="2"/></cols>'))
        self.assertEqual(out['status'],'partial')
        item=out['sheets'][0]['cells']['A1']
        self.assertEqual(item['numeric_interpretation']['reason'],'row_or_column_style_inheritance_unresolved')
        self.assertNotIn('interpreted_value',item['numeric_interpretation'])
        self.assertEqual(out['sheets'][0]['visibility']['columns'][0]['style_index_raw'],'2')

if __name__=='__main__':unittest.main()
