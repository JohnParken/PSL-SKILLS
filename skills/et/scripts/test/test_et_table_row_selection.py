"""Report-row selection and numeric accuracy with independent expected results."""
from copy import deepcopy
from decimal import Decimal, Inexact, Rounded, localcontext
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import random
import shutil
import struct
import subprocess
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from et_table_analysis import analyze
from test_et_table_analysis import item
from test_wps_biff8 import workbook, rec, cell, formula
from test_wps_cfb import synthetic_cfb
from wps_access import ParseError


def report_fixture():
    cells = {'A1': item('text', 'Quarterly report'), 'A2': item('text', 'Team'),
             'B2': item('text', 'Item'), 'C2': item('text', 'Amount'), 'D2': item('text', 'ID')}
    for row, value in ((3, '0.10'), (4, '0.20'), (7, '-0.05'), (8, '0.125')):
        cells['C' + str(row)] = item('number', value)
        cells['D' + str(row)] = item('text', '001' if row == 3 else str(row))
    for row, value in ((5, '0.30'), (9, '0.075'), (10, '0.375')):
        cells['B' + str(row)] = item('text', 'Subtotal' if row != 10 else 'Grand total')
        cells['C' + str(row)] = item('number', value, 'unverified')
    cells.update(A3=item('text', 'North'), A7=item('text', 'South'),
                 A6=item('text', 'Team'), C6=item('text', 'Amount'),
                 C12=item('blank', None), C13=item('error', '#DIV/0!', 'error_unverified'), C14=item('text', '0.5'))
    return {'schema_version': 'biff8_readonly_v1', 'status': 'partial', 'partial': True,
            'issues': [{'code': 'source_layout_opaque'}],
            'sheets': [{'name': 'Report', 'cells': cells,
                        'merges': [{'range': 'A1:D1'}, {'range': 'A3:A5'}, {'range': 'A7:A9'}]}]}


QUERY = {'sheet': 'Report', 'range': 'A1:D14', 'header_rows': '1:2', 'formula_policy': 'cached'}


class ReportRowsTests(unittest.TestCase):
    def test_explicit_subtotal_and_repeated_header_exclusions_match_hand_total(self):
        source = report_fixture()
        before = deepcopy(source)
        entire = analyze(source, QUERY)
        self.assertEqual(entire['profiles']['C']['sum'], Decimal('1.125'))
        result = analyze(source, {**QUERY, 'exclude_rows': [10, 5, 9, 6], 'merge_policy': 'fill_text',
                                  'group_by': ['A'], 'aggregates': [{'op': 'sum', 'column': 'C', 'as': 'amount'}]})
        self.assertEqual(result['profiles']['C']['sum'], Decimal('0.375'))
        self.assertEqual(result['profiles']['C']['numeric_count'], 4)
        self.assertEqual(result['profiles']['C']['mean'], Decimal('0.09375'))
        self.assertEqual(result['profiles']['C']['median'], Decimal('0.1125'))
        self.assertEqual(result['profiles']['C']['formula_caches_used'], 0)
        self.assertEqual([row['source_row'] for row in result['rows']], [3, 4, 7, 8, 11, 12, 13, 14])
        self.assertEqual(result['row_counts']['input_rows'], 12)
        self.assertEqual(result['row_counts']['explicitly_excluded_rows'], 4)
        self.assertEqual(result['row_counts']['filtered_out_rows'], 0)
        self.assertEqual(result['scope']['excluded_source_rows'], [5, 6, 9, 10])
        self.assertEqual(result['scope']['filled_text_positions'], 2)
        self.assertEqual([group['measures']['amount']['value'] for group in result['groups']],
                         [Decimal('0.30'), Decimal('0.075'), None])
        self.assertEqual(result['header']['columns'][0]['path'], [{'type': 'text', 'value': 'Team'}])
        self.assertEqual(result['header']['context_headings'][0]['source'], 'A1')
        self.assertEqual(source, before)

    def test_exclusions_precede_value_filters_distinct_sort_and_pagination(self):
        source = report_fixture()
        source['sheets'][0]['cells']['D4'] = item('text', '001')
        result = analyze(source, {**QUERY, 'exclude_rows': [3, 5, 6, 9, 10], 'distinct_by': ['D'],
                                  'filters': [{'column': 'C', 'op': 'ge', 'type': 'number', 'value': '0'}],
                                  'sort': [{'column': 'C', 'descending': True}], 'limit': 1})
        self.assertEqual(result['rows'][0]['source_row'], 4)
        self.assertEqual(result['profiles']['C']['sum'], Decimal('0.325'))
        self.assertEqual(result['row_counts']['input_rows'], 12)
        self.assertEqual(result['row_counts']['explicitly_excluded_rows'], 5)
        self.assertEqual(result['row_counts']['filtered_out_rows'], 5)
        self.assertEqual(result['row_counts']['duplicate_rows_removed'], 0)
        self.assertEqual(result['row_counts']['matched_rows'], 2)
        self.assertTrue(result['row_page']['truncated'])

    def test_excluded_merge_anchor_is_not_used_to_fill_remaining_data(self):
        result = analyze(report_fixture(), {**QUERY, 'exclude_rows': [3], 'merge_policy': 'fill_text'})
        row = next(row for row in result['rows'] if row['source_row'] == 4)
        self.assertEqual(row['cells']['A']['type'], 'missing')
        self.assertNotIn('propagated_from', row['cells']['A'])
        self.assertEqual(result['scope']['filled_text_positions'], 2)

    def test_excluded_non_anchor_content_is_preserved_without_attempted_fill(self):
        source = report_fixture()
        source['sheets'][0]['cells']['A5'] = item('text', 'Saved subtotal label')
        before = deepcopy(source)
        result = analyze(source, {**QUERY, 'exclude_rows': [5], 'merge_policy': 'fill_text'})
        self.assertEqual(result['scope']['filled_text_positions'], 3)
        self.assertEqual(source, before)
        with self.assertRaisesRegex(ParseError, 'overwrite'):
            analyze(source, {**QUERY, 'merge_policy': 'fill_text'})

    def test_excluding_every_data_row_returns_empty_statistics_and_keeps_source_partial(self):
        result = analyze(report_fixture(), {**QUERY, 'exclude_rows': list(range(3, 15)), 'group_by': ['A']})
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['row_counts']['matched_rows'], 0)
        self.assertIsNone(result['profiles']['C']['sum'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['source']['issues'], [{'code': 'source_layout_opaque'}])

    def test_invalid_exclusions_reject_ambiguous_or_out_of_region_rows(self):
        for excluded in (None, '5', [True], [5.0], ['5'], [[5]], [5, 5], [0], [-1], [1], [2], [15]):
            with self.subTest(excluded=excluded), self.assertRaises(ParseError):
                analyze(report_fixture(), {**QUERY, 'exclude_rows': excluded})
        with self.assertRaises(ParseError):
            analyze(report_fixture(), {**QUERY, 'header_rows': '2', 'exclude_rows': [1]})

    def test_exclusions_do_not_bypass_cell_structure_or_selection_budgets(self):
        source = report_fixture()
        source['sheets'][0]['cells']['C5']['value'] = 'NaN'
        with self.assertRaises(ParseError):
            analyze(source, {**QUERY, 'exclude_rows': [5]})
        with self.assertRaises(ParseError) as error:
            analyze(report_fixture(), {**QUERY, 'range': 'A1:D65536', 'exclude_rows': [5]})
        self.assertEqual(error.exception.status, 'resource_limit')

    def test_exclusion_list_is_bounded_and_empty_list_has_no_effect(self):
        with self.assertRaises(ParseError) as error:
            analyze(report_fixture(), {**QUERY, 'range': 'A1:A10003', 'header_rows': None,
                                      'exclude_rows': list(range(1, 10002))})
        self.assertEqual(error.exception.status, 'resource_limit')
        original = analyze(report_fixture(), QUERY)
        empty = analyze(report_fixture(), {**QUERY, 'exclude_rows': []})
        self.assertEqual(original['profiles'], empty['profiles'])
        self.assertEqual(original['rows'], empty['rows'])

    def test_diagnostics_distinguish_selected_rectangle_from_matching_data(self):
        result = analyze(report_fixture(), {**QUERY, 'exclude_rows': [5, 9, 10, 13]})
        issues = {issue['code']: issue for issue in result['analysis_issues']}
        self.assertEqual(issues['formula_cache_used']['positions'], 3)
        self.assertEqual(issues['formula_cache_used']['matched_data_positions'], 0)
        self.assertEqual(issues['error_cache']['positions'], 1)
        self.assertEqual(issues['error_cache']['matched_data_positions'], 0)
        self.assertEqual(issues['error_cache']['scope'], 'selected_rectangle_before_processing')
        self.assertEqual(result['status'], 'partial')

    def test_header_cache_is_visible_as_header_without_claiming_data_cache_use(self):
        source = report_fixture()
        source['sheets'][0]['cells']['C2'] = item('text', 'Cached heading', 'unverified')
        result = analyze(source, {**QUERY, 'exclude_rows': [5, 9, 10]})
        issue = next(item for item in result['analysis_issues'] if item['code'] == 'formula_cache_used')
        self.assertEqual((issue['positions'], issue['matched_data_positions']), (4, 0))
        self.assertEqual(result['header']['columns'][2]['path'][0]['value'], 'Cached heading')
        self.assertEqual(result['profiles']['C']['formula_caches_used'], 0)

    def test_missing_blank_empty_text_boolean_and_text_numbers_keep_distinct_meaning(self):
        source = report_fixture()
        source['sheets'][0]['cells'].update(C13=item('text', ''), C14=item('boolean', True))
        result = analyze(source, {**QUERY, 'exclude_rows': list(range(3, 11))})
        self.assertEqual(result['profiles']['C']['types'], {'missing': 1, 'blank': 1, 'text': 1, 'boolean': 1})
        self.assertEqual(result['profiles']['C']['count'], 2)
        self.assertEqual(result['profiles']['C']['numeric_count'], 0)
        self.assertIsNone(result['profiles']['C']['sum'])

    def test_seeded_fraction_oracle_checks_filtered_group_sums_and_even_medians(self):
        randomizer = random.Random(20261104)
        decimals = [Decimal(randomizer.randrange(-100000, 100001)).scaleb(-randomizer.randrange(0, 8)) for _ in range(120)]
        source = {'schema_version': 'biff8_readonly_v1', 'status': 'ok',
                  'sheets': [{'name': 'Numbers', 'cells': {}, 'merges': []}]}
        excluded = [row for row in range(1, 121) if row % 7 == 0]
        expected = {str(group): [] for group in range(3)}
        for row, value in enumerate(decimals, 1):
            source['sheets'][0]['cells']['A' + str(row)] = item('number', value)
            source['sheets'][0]['cells']['B' + str(row)] = item('text', str(row % 3))
            if row not in excluded and value >= Decimal('-10'):
                expected[str(row % 3)].append(Fraction(value))
        with localcontext() as context:
            context.prec = 3
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            result = analyze(source, {'sheet': 'Numbers', 'range': 'A1:B120', 'exclude_rows': excluded,
                                     'filters': [{'column': 'A', 'op': 'ge', 'type': 'number', 'value': '-10'}],
                                     'group_by': ['B'], 'aggregates': [{'op': 'sum', 'column': 'A', 'as': 'sum'},
                                                                     {'op': 'median', 'column': 'A', 'as': 'median'}], 'limit': 0})
        for group in result['groups']:
            numbers = sorted(expected[group['keys'][0]['value']])
            middle = len(numbers) // 2
            median = numbers[middle] if len(numbers) % 2 else (numbers[middle - 1] + numbers[middle]) / 2
            self.assertEqual(Fraction(group['measures']['sum']['value']), sum(numbers))
            self.assertEqual(Fraction(group['measures']['median']['value']), median)
        self.assertEqual(Fraction(result['profiles']['A']['sum']), sum(value for numbers in expected.values() for value in numbers))

    def test_cli_excludes_physical_biff_row_without_writing_original(self):
        payload = (rec(0x203, cell(0, 0) + struct.pack('<d', 1.25)) + rec(0x203, cell(1, 0) + struct.pack('<d', 2.5)) +
                   formula(2, 0, struct.pack('<d', 999)))
        data = synthetic_cfb({'Workbook': workbook([payload], strings=[])})
        # Register the exclusive task-local tree before creating any test files.
        directory = Path('et-row-selection-' + uuid.uuid4().hex)
        self.assertFalse(directory.exists())
        directory.mkdir()
        try:
            source, query = directory / 'source.ett', directory / 'query.json'
            with source.open('xb') as handle:
                handle.write(data)
            with query.open('x', encoding='utf-8') as handle:
                json.dump({'formula_policy': 'cached', 'exclude_rows': [2], 'limit': 0}, handle)
            process = subprocess.run([sys.executable, '-I', '-S', '-B', str((Path(__file__).resolve().parents[1] / 'analyze_et.py')),
                                      str(source), '--sheet', '中文表1', '--range', 'A1:A3', '--query', str(query)],
                                     capture_output=True, text=True, check=False, env={**os.environ, 'PATH': ''})
            self.assertEqual(process.returncode, 3, process.stderr + process.stdout)
            self.assertEqual(process.stderr, '')
            result = json.loads(process.stdout)
            self.assertEqual(result['profiles']['A']['sum'], '1000.25')
            self.assertEqual(result['profiles']['A']['formula_caches_used'], 1)
            self.assertEqual(result['scope']['excluded_source_rows'], [2])
            self.assertEqual(result['row_counts']['explicitly_excluded_rows'], 1)
            self.assertEqual(result['source_sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(source.read_bytes(), data)
        finally:
            shutil.rmtree(directory)


if __name__ == '__main__':
    unittest.main()
