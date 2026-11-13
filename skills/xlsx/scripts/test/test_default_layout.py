"""Regression tests for default new-workbook readability."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stdlib_xlsx import NS, Workbook, xml
from xlsx_appearance import HEADER_COLOR


class DefaultLayoutTests(unittest.TestCase):
    def test_sparse_text_lengths_styles_and_formula(self):
        spec = {'sheets': [{'name': 'Data', 'cells': {
            'A1': {'value': '名称'}, 'B1': {'value': 'Description'},
            'A2': {'value': '中文长文本' * 30}, 'B2': {'value': 'English words ' * 20},
            'A3': {'value': 'first\n第二行'}, 'B3': {'value': None},
            'A4': {'value': '用户指定', 'style': 0}, 'B4': {'formula': '1+2'},
        }}]}
        book = Workbook.create(spec)
        styles = xml(book.parts['xl/styles.xml'])
        sheet = xml(book.parts[book.sheets['Data']])
        header = styles.find(NS + 'cellXfs')[int(book.cells['Data']['A1']['style'])]
        fill = styles.find(NS + 'fills')[int(header.get('fillId'))]
        self.assertEqual(fill.find(NS + 'patternFill/' + NS + 'fgColor').get('rgb'), HEADER_COLOR)
        self.assertEqual(book.cells['Data']['A4']['style'], '0')
        self.assertEqual(book.cells['Data']['B4']['formula'], '1+2')
        self.assertEqual(book.cells['Data']['A2']['value'], '中文长文本' * 30)
        self.assertEqual(sheet.find(NS + 'cols/' + NS + 'col').get('width'), '40')
        self.assertGreater(float(sheet.find(NS + "sheetData/" + NS + "row[@r='2']").get('ht')), 15)
        self.assertGreater(float(sheet.find(NS + "sheetData/" + NS + "row[@r='3']").get('ht')), 15)
        self.assertEqual(book.cells['Data']['B3']['style'], '0')
        body = styles.find(NS + 'cellXfs')[int(book.cells['Data']['A2']['style'])]
        self.assertEqual(body.find(NS + 'alignment').get('wrapText'), '1')
        self.assertNotEqual(body.get('borderId'), '0')

    def test_explicit_appearance_and_merges_remain_separate(self):
        spec = {'sheets': [{'name': 'Data', 'cells': {'A1': {'value': 'Title'}, 'A2': {'value': 'value'}}}]}
        appearance = {'preset': 'readable', 'sheets': {'Data': {'range': 'A1:B2', 'merges': ['A1:B1'], 'column_widths': {'A': 18}}}}
        book = Workbook.create(spec, appearance=appearance)
        root = xml(book.parts[book.sheets['Data']])
        self.assertEqual(root.find(NS + 'mergeCells/' + NS + 'mergeCell').get('ref'), 'A1:B1')
        self.assertEqual(root.find(NS + 'cols/' + NS + 'col').get('width'), '18')
        self.assertEqual(book.cells['Data']['A1']['value'], 'Title')

    def test_single_text_and_empty_sheet_are_not_inferred_as_header(self):
        book = Workbook.create({'sheets': [{'name': 'Data', 'cells': {'A1': {'value': 'note'}}}, {'name': 'Empty', 'cells': {}}]})
        styles = xml(book.parts['xl/styles.xml'])
        style = styles.find(NS + 'cellXfs')[int(book.cells['Data']['A1']['style'])]
        fill = styles.find(NS + 'fills')[int(style.get('fillId'))]
        self.assertNotEqual(fill.find(NS + 'patternFill/' + NS + 'fgColor').get('rgb'), HEADER_COLOR)
        self.assertFalse(book.cells['Empty'])


if __name__ == '__main__':
    unittest.main()
