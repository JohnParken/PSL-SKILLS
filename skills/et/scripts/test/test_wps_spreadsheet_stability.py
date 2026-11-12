"""Final-audit regressions, synthetic values with independently stated expectations."""
import sys
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from xml.parsers import expat
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_ooxml_sheet import package, result, codes, issue, T


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class SpreadsheetStability(unittest.TestCase):
    def test_present_empty_string_cache_differs_from_absent_value(self):
        out = result(package(
            '<c r="A1" t="str"><f>""</f><v/></c><c r="B1" t="str"><f>""</f></c>'))
        self.assertEqual(out['status'], 'partial')
        a, b = (out['sheets'][0]['cells'][key] for key in ('A1', 'B1'))
        self.assertEqual((a['type'], a['value'], a['cache_status']),
                         ('text', '', 'unverified'))
        self.assertEqual((b['type'], b['value'], b['cache_status']),
                         ('blank', None, 'missing'))
        self.assertEqual(issue(out, 'missing_formula_cache')['cell'], 'B1')

    def test_empty_numeric_value_is_omitted_with_diagnostics(self):
        out = result(package('<c r="A1"><v/></c>'))
        self.assertEqual(out['status'], 'partial')
        self.assertNotIn('A1', out['sheets'][0]['cells'])
        self.assertIn('numeric_cell_value_not_interpreted', codes(out))
        self.assertEqual(out['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')
        self.assertTrue(out['omissions_affect_cell_values'])

    def test_row_style_must_not_be_interpreted_as_general(self):
        xml = (f'<worksheet xmlns="{T}"><sheetData><row r="1" s="3" customFormat="1">'
               '<c r="A1"><v>60</v></c><c r="B1" s="0"><v>60</v></c>'
               '</row></sheetData></worksheet>')
        out = result(package(overrides={'xl/worksheets/sheet1.xml': xml}))
        self.assertEqual(out['status'], 'partial')
        cells = out['sheets'][0]['cells']
        self.assertEqual(cells['A1']['numeric_interpretation']['status'], 'unknown')
        self.assertEqual(cells['A1']['value'], 60)
        self.assertEqual(cells['B1']['numeric_interpretation']['kind'], 'number')
        self.assertEqual(out['sheets'][0]['visibility']['rows'][0]['style_index_raw'], '3')

    def test_malformed_string_text_is_degraded_not_fatal(self):
        cases = {
            '<t><t>LOST</t></t>': ('', 'string_text_container_not_interpreted'),
            'LOST': ('', 'string_raw_text_not_interpreted'),
            '<t>kept</t>LOST': ('kept', 'string_tail_text_ignored'),
            '<r>LOST<t>kept</t></r>': ('kept', 'string_raw_text_not_interpreted'),
            '<r><t><t>LOST</t></t></r>': ('', 'string_text_container_not_interpreted'),
        }
        for content, (text, code) in cases.items():
            with self.subTest(content=content):
                out = result(package('<c r="A1" t="inlineStr"><is>' + content + '</is></c>'))
                self.assertEqual(out['status'], 'partial')
                cell = out['sheets'][0]['cells']['A1']
                self.assertEqual((cell['type'], cell['value']), ('text', text))
                self.assertIn(code, codes(out))

    def test_column_style_preserved_without_guessing_display(self):
        out = result(package(sheet_extra='<cols><col min="1" max="1" style="2"/></cols>'))
        self.assertEqual(out['status'], 'partial')
        item = out['sheets'][0]['cells']['A1']
        self.assertEqual(item['numeric_interpretation']['reason'],
                         'row_or_column_style_inheritance_unresolved')
        self.assertNotIn('interpreted_value', item['numeric_interpretation'])
        self.assertEqual(out['sheets'][0]['visibility']['columns'][0]['style_index_raw'],
                         '2')


if __name__ == '__main__':
    unittest.main()
