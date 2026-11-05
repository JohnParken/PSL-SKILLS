#!/usr/bin/env python3
"""Formula recalculation through workbook state, analysis and persisted XLSX."""
from decimal import Decimal
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import subprocess
import sys
import tempfile
import unittest
import uuid

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from stdlib_xlsx import Workbook
from xlsx_analysis import FormulaCalculationError, profile, summarize


class FormulaWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / ('xlsx_formulas_' + uuid.uuid4().hex)
        self.owned = []
        self.root.mkdir()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for path in reversed(self.owned):
            path.unlink(missing_ok=True)
        self.root.rmdir()

    def path(self, name):
        path = self.root / name
        self.assertFalse(path.exists())
        self.owned.append(path)
        return path

    def book(self, cells):
        return Workbook.create({'sheets': [{'name': 'Data', 'cells': cells}]})

    def test_calculation_refreshes_inspection_and_values_only_state(self):
        book = self.book({'A1': {'value': 3}, 'B1': {'formula': 'A1*2'}})
        self.assertEqual(book.calculate()['calculated_count'], 1)
        self.assertEqual(book.inspect()['sheets'][0]['cells']['B1']['value'], 6)
        book.patch({'sheets': [{'name': 'Data', 'cells': {'A1': {'value': 7}}}]})
        self.assertEqual(book.calculate()['calculated_count'], 1)
        self.assertEqual(book.cells['Data']['B1']['value'], 14)
        self.assertEqual(book.calculate(values_only=True)['calculated_count'], 1)
        self.assertIsNone(book.cells['Data']['B1']['formula'])
        self.assertEqual(book.calculate()['formula_count'], 0)
        self.assertEqual(book.cells['Data']['B1']['value'], 14)

    def test_analysis_recalculates_missing_and_stale_caches_without_writes(self):
        book = self.book({'A1': {'value': 'Group'}, 'B1': {'value': 'Amount'},
                          'A2': {'value': 'East'}, 'B2': {'formula': 'ROUND(19.9*12*0.95,2)'},
                          'A3': {'value': 'East'}, 'B3': {'formula': 'B2+0.14'}})
        doc, node = book.cell_node('Data', 'B2')
        book.put_value(doc, node, Decimal(999), cache=True)
        source = self.path('input.xlsx')
        book.save(source)
        before = source.read_bytes()
        result = summarize(source, 'Data', {'group_by': ['A'], 'measure': 'B'}, formulas='calculate')
        self.assertEqual(result['groups'][0]['numeric']['sum'], Decimal('453.86'))
        self.assertEqual(result['unverified_formula_caches'], 0)
        self.assertEqual(result['recalculated_formula_cells'], 2)
        self.assertEqual(result['formula_calculation']['calculated_count'], 2)
        self.assertFalse(result['formula_calculation']['output_written'])
        self.assertFalse(result['formula_calculation']['excel_equivalence_verified'])
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [source])
        self.assertEqual(Workbook.load(source).cells['Data']['B2']['value'], 999)

    def test_empty_string_result_is_not_a_missing_cache(self):
        book = self.book({'A1': {'value': 'Amount'}, 'A2': {'formula': 'IF(TRUE,"",1)'}, 'A3': {'formula': '2+3'}})
        book.calculate()
        self.assertEqual(book.cells['Data']['A2']['value'], '')
        self.assertEqual(book.cells['Data']['A2']['cache_status'], 'unverified')
        source = self.path('input.xlsx')
        book.save(source)
        for mode in ('cached', 'calculate'):
            with self.subTest(mode=mode):
                result = profile(source, 'Data', formulas=mode)
                self.assertEqual(result['columns'][0]['empty_text'], 1)
                self.assertEqual(result['columns'][0]['numeric']['sum'], 5)
        self.assertEqual(Workbook.load(source).cells['Data']['A2']['value'], '')

    def test_incomplete_or_failed_calculation_blocks_analysis(self):
        for index, formula in enumerate(('XLOOKUP(1,A1,A2)', '1/0', 'A2')):
            book = self.book({'A1': {'value': 'Amount'}, 'A2': {'formula': formula}})
            doc, node = book.cell_node('Data', 'A2')
            book.put_value(doc, node, Decimal(999), cache=True)
            source = self.path('input%d.xlsx' % index)
            book.save(source)
            before = source.read_bytes()
            with self.subTest(formula=formula), self.assertRaises(FormulaCalculationError) as caught:
                profile(source, 'Data', formulas='calculate')
            report = caught.exception.calculation
            self.assertEqual(report['issues'][0]['cell'], 'A2')
            self.assertEqual(report['status'], 'incomplete' if index == 0 else 'failed')
            self.assertEqual(source.read_bytes(), before)

    def test_full_workbook_errors_outside_selected_region_are_not_hidden(self):
        book = self.book({'A1': {'value': 'Amount'}, 'A2': {'formula': '1+2'}, 'Z9': {'formula': '1/0'}})
        source = self.path('input.xlsx')
        book.save(source)
        with self.assertRaises(FormulaCalculationError) as caught:
            profile(source, 'Data', cell_range='A1:A2', formulas='calculate')
        self.assertEqual(caught.exception.calculation['issues'][0]['cell'], 'Z9')

    def test_calculation_refuses_wps_extraction_json(self):
        with self.assertRaisesRegex(ValueError, 'native XLSX/XLSM'):
            profile(self.root / 'extraction.json', 'Data', formulas='calculate')

    def test_new_formula_functions_cross_sheets_and_save_reopen(self):
        book = Workbook.create({'sheets': [
            {'name': 'Items', 'cells': {
                'A1': {'value': 'East'}, 'B1': {'value': 2}, 'C1': {'value': Decimal('1.25')},
                'A2': {'value': 'West'}, 'B2': {'value': 3}, 'C2': {'value': Decimal('2.50')},
                'A3': {'value': 'East'}, 'B3': {'value': 4}, 'C3': {'value': Decimal('0.50')},
            }},
            {'name': 'Data', 'cells': {
                'A1': {'value': 'Amount'},
                'A2': {'formula': 'SUMPRODUCT(Items!B1:B3,Items!C1:C3)'},
                'B2': {'formula': 'SUMIFS(Items!B1:B3,Items!A1:A3,"East",Items!C1:C3,">=1")'},
                'C2': {'formula': 'COUNTIFS(Items!A1:A3,"East",Items!B1:B3,">1")'},
                'D2': {'formula': 'IF(C2>0,ROUND(A2/C2,2),0)'},
            }},
        ]})
        report = book.calculate()
        self.assertEqual(report['status'], 'calculated_subset', report)
        self.assertEqual(report['calculated_count'], 4)
        source = self.path('calculated.xlsx')
        book.save(source)
        reopened = Workbook.load(source)
        for ref, expected in {'A2': '12', 'B2': '2', 'C2': '2', 'D2': '6'}.items():
            self.assertEqual(reopened.cells['Data'][ref]['value'], Decimal(expected))
        result = profile(source, 'Data', cell_range='A1:A2', formulas='calculate')
        self.assertEqual(result['columns'][0]['numeric']['sum'], 12)

    def test_cli_recalculation_success_and_structured_failure(self):
        for index, formula in enumerate(('1+2', 'XLOOKUP(1,B1,B2)')):
            source = self.path('input%d.xlsx' % index)
            self.book({'A1': {'value': 'Amount'}, 'A2': {'formula': formula}}).save(source)
            run = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'xlsx_analysis.py'),
                                  'profile', source.name, '--sheet', 'Data', '--formulas', 'calculate'],
                                 cwd=self.root, capture_output=True, text=True)
            self.assertEqual(run.returncode, index, run.stdout + run.stderr)
            self.assertEqual(run.stderr, '')
            result = json.loads(run.stdout)
            if index == 0:
                self.assertEqual(result['columns'][0]['numeric']['sum'], '3')
                self.assertEqual(result['recalculated_formula_cells'], 1)
            else:
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['calculation']['status'], 'incomplete')
                self.assertEqual(result['calculation']['issues'][0]['code'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
