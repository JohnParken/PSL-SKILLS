#!/usr/bin/env python3
"""Independent numeric expectations and source-to-group reconciliation regressions."""
from decimal import Decimal, Inexact, ROUND_DOWN, Rounded, localcontext
from fractions import Fraction
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sys
import tempfile
import os
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stdlib_xlsx import Workbook
from xlsx_analysis import statistics, summarize
from xlsx_advanced import describe, exact, rounded
from xlsx_tabular import import_table
from xlsx_result_export import export_result


class CalculationReliabilityTests(unittest.TestCase):
    def setUp(self):
        # Register exclusively owned maintenance paths before creating them.
        self.root = Path(tempfile.gettempdir()) / ('xlsx_reliability_' + uuid.uuid4().hex)
        self.owned = []
        self.root.mkdir()
        self.previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for path in reversed(self.owned):
            path.unlink(missing_ok=True)
        os.chdir(self.previous)
        self.root.rmdir()

    def path(self, name):
        path = Path(name)
        self.assertFalse(path.exists())
        self.owned.append(path)
        return path

    def save_rows(self, rows):
        from xlsx_tabular import column_name
        cells = {column_name(col) + str(row): {'value': value}
                 for row, values in enumerate(rows, 1) for col, value in enumerate(values, 1)}
        source = self.path('input.xlsx')
        Workbook.create({'sheets': [{'name': 'Data', 'cells': cells}]}).save(source)
        return source

    def test_statistics_ignore_caller_context_and_leave_it_unchanged(self):
        values = [('number', Decimal('1e-100')), ('number', Decimal('2e-100'))]
        with localcontext() as caller:
            caller.prec = 2
            caller.rounding = ROUND_DOWN
            caller.Emin = -2
            caller.Emax = 2
            caller.clamp = 1
            caller.traps[Rounded] = True
            caller.traps[Inexact] = True
            caller.flags[Inexact] = True
            before = str(caller)
            result = statistics(values)
            self.assertEqual(result['sum'], Decimal('3e-100'))
            self.assertEqual(result['mean'], Decimal('1.5e-100'))
            self.assertFalse(result['mean_rounded'])
            self.assertEqual(exact(lambda: Decimal('1e100') + Decimal('.01')),
                             Decimal('1' + '0' * 100 + '.01'))
            ratio = rounded(lambda: Decimal(1) / 3, 34)
            self.assertEqual(ratio['value'], Decimal('0.' + '3' * 34))
            self.assertTrue(ratio['rounded'])
            self.assertEqual(str(caller), before)

    def test_group_totals_reconcile_with_exact_fraction_oracle(self):
        amounts = ['999999999999.99', '0.01', '-999999999999.99', '123.45', '-23.45']
        source = self.save_rows([['Group', 'Amount']] + [[str(i % 2), Decimal(value)]
                                for i, value in enumerate(amounts)] + [['0', 'text'], ['1', None], ['omit', 999]])
        before = source.read_bytes()
        result = summarize(source, 'Data', {'group_by': ['A'], 'measure': 'B', 'mixed': 'ignore',
                                          'filters': [{'column': 'A', 'op': 'ne', 'value': 'omit'}]})
        check = result['reconciliation']
        oracle = sum((Fraction(value) for value in amounts), Fraction(0))
        self.assertEqual(Fraction(check['source_sum']), oracle)
        self.assertEqual(Fraction(check['group_sum']), oracle)
        self.assertEqual((check['selected_rows'], check['group_rows'], check['numeric_count']), (7, 7, 5))
        self.assertEqual((check['blank_count'], check['ignored_non_numeric']), (1, 1))
        self.assertTrue(all(check['checks'].values()))
        self.assertEqual(source.read_bytes(), before)

    def test_reconciliation_blocks_corrupt_group_total(self):
        source = self.save_rows([['Group', 'Amount'], ['A', 1], ['B', 2]])
        original = statistics
        calls = []

        def corrupted(values):
            result = original(values)
            calls.append(True)
            if len(calls) == 1:
                result['sum'] = Decimal(100)
            return result

        with patch('xlsx_analysis.statistics', side_effect=corrupted):
            with self.assertRaisesRegex(ValueError, 'reconciliation failed: amount_sum'):
                summarize(source, 'Data', {'group_by': ['A'], 'measure': 'B'})

    def test_empty_and_count_only_groups_preserve_null_semantics(self):
        source = self.save_rows([['Group', 'Amount'], ['A', None], ['B', '']])
        result = summarize(source, 'Data', {'measure': 'B'})
        self.assertIsNone(result['reconciliation']['source_sum'])
        self.assertIsNone(result['reconciliation']['group_sum'])
        empty = summarize(source, 'Data', {'measure': 'B', 'filters': [{'column': 'A', 'op': 'eq', 'value': 'none'}]})
        self.assertEqual(empty['reconciliation']['selected_rows'], 0)
        counts = summarize(source, 'Data', {'group_by': ['A']})
        self.assertEqual(counts['reconciliation']['checks'], {'row_count': True})

    def test_advanced_statistics_stable_under_hostile_context(self):
        source = self.save_rows([['Amount'], [Decimal('1000000000000.01')], [Decimal('1000000000000.03')]])
        with localcontext() as caller:
            caller.prec = 3
            caller.Emax = 2
            caller.Emin = -2
            caller.traps[Rounded] = True
            result = describe(source, 'Data', {'columns': ['A']})['columns'][0]
        self.assertEqual(result['mean']['value'], Decimal('1000000000000.02'))
        self.assertEqual(result['variance']['population']['variance']['value'], Decimal('.0001'))
        self.assertEqual(result['variance']['sample']['variance']['value'], Decimal('.0002'))

    def test_csv_precision_guard_checks_original_digits_without_rounding(self):
        for index, text in enumerate(('10000000000000000000000000001', '1234567890123456')):
            source = self.path('input%d.csv' % index)
            source.write_text('Amount\n' + text + '\n', encoding='utf-8')
            output = self.path('rejected%d.xlsx' % index)
            with localcontext() as caller:
                caller.prec = 2
                with self.assertRaisesRegex(ValueError, 'More than 15 significant digits'):
                    import_table(source, output, numeric_columns=['A'])
            self.assertFalse(output.exists())

    def test_csv_trailing_zero_amounts_remain_numeric_and_exact(self):
        source = self.path('input.csv')
        source.write_text('Amount\n123456789012345.00\n', encoding='utf-8')
        output = self.path('output.xlsx')
        with localcontext() as caller:
            caller.prec = 2
            import_table(source, output, numeric_columns=['A'])
        self.assertEqual(Workbook.load(output).cells['Sheet1']['A2']['value'], Decimal('123456789012345.00'))

    def test_formula_to_summary_export_roundtrip_preserves_rounding_policy(self):
        source = self.path('formulas.xlsx')
        book = Workbook.create({'sheets': [{'name': 'Data', 'cells': {
            'A1': {'value': 'Policy'}, 'B1': {'value': 'Amount'},
            'A2': {'value': 'per item'}, 'B2': {'formula': 'ROUND(0.005,2)+ROUND(0.005,2)'},
            'A3': {'value': 'at total'}, 'B3': {'formula': 'ROUND(SUM(0.005,0.005),2)'},
        }}]})
        calculation = book.calculate()
        self.assertEqual(calculation['status'], 'calculated_subset')
        book.save(source)
        reopened = Workbook.load(source)
        self.assertEqual(reopened.cells['Data']['B2']['value'], Decimal('.02'))
        self.assertEqual(reopened.cells['Data']['B3']['value'], Decimal('.01'))
        result = summarize(source, 'Data', {'group_by': ['A'], 'measure': 'B'}, formulas='cached')
        self.assertEqual(result['unverified_formula_caches'], 2)
        self.assertEqual(result['reconciliation']['source_sum'], Decimal('.03'))
        output = self.path('summary.xlsx')
        export_result(result, output)
        exported = Workbook.load(output)
        cells = exported.cells['Results']
        sum_column = next(ref.rstrip('0123456789') for ref, cell in cells.items() if cell['value'] == 'Sum')
        self.assertEqual(cells[sum_column + '2']['value'], Decimal('.02'))
        self.assertEqual(cells[sum_column + '3']['value'], Decimal('.01'))
        metadata = ''.join(str(cell['value']) for cell in exported.cells['Metadata'].values())
        self.assertIn('reconciliation', metadata)
        self.assertIn('unverified_formula_caches', metadata)


if __name__ == '__main__':
    unittest.main()
