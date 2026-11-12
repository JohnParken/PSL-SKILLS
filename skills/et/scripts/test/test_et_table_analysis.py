"""Stored-value analysis expectations specified independently of the implementation."""
from copy import deepcopy
from contextlib import redirect_stdout
from decimal import Decimal, Inexact, Rounded, localcontext
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_et import load_query, main
from et_table_analysis import analyze
from et_table_preview import preview
from read_et import read_bytes
from test_wps_biff8 import workbook, rec, cell, formula, special
from test_wps_biff8_salvage import values
from test_wps_cfb import synthetic_cfb
from test_wps_ooxml_sheet import package, T
from wps_access import ParseError
from verify_et_analysis import verify_bytes


def item(kind, value, cache=None):
    output = {'type': kind, 'value': Decimal(value) if kind == 'number' else value}
    if cache is not None:
        output.update(formula={'text': 'not evaluated'}, cache_status=cache)
    return output


def fixture():
    cells = {'A1': item('text', 'Region'), 'B1': item('text', 'Amount'), 'C1': item('text', 'ID')}
    rows = [
        ('North', item('number', '10.1'), '001'),
        ('South', item('number', '20.2'), '002'),
        ('North', item('number', '-0.3'), '001'),
        ('North', item('number', '999', 'unverified'), '003'),
        ('South', item('blank', None), None),
        ('North', None, ''),
        ('South', item('number', '0'), '004'),
        ('North', item('error', '#DIV/0!', 'error_unverified'), '005'),
        ('North', item('blank', None, 'missing'), '006'),
        ('North', item('text', '100'), '007'),
        ('North', item('boolean', True), '008'),
        ('North', item('number', '10.1'), '001'),
    ]
    for row, (region, amount, identity) in enumerate(rows, 2):
        cells['A' + str(row)] = item('text', region)
        if amount is not None:
            cells['B' + str(row)] = amount
        if identity is not None:
            cells['C' + str(row)] = item('text', identity)
    return {'schema_version': 'biff8_readonly_v1', 'status': 'partial', 'partial': True,
            'issues': [{'code': 'source_style_opaque'}], 'reading_assessment': {'full_format_verified': False},
            'cell_value_coverage': 'complete_within_declared_sheets', 'omissions_affect_cell_values': False,
            'sheets': [{'name': 'Data', 'cells': cells, 'merges': []}]}


BASE_QUERY = {'sheet': 'Data', 'range': 'A1:C13', 'header_rows': '1'}


class TableAnalysisTests(unittest.TestCase):
    def run_query(self, **options):
        return analyze(fixture(), {**BASE_QUERY, **options})

    def test_exact_profiles_distinguish_blank_missing_error_boolean_text_and_formula(self):
        result = self.run_query()
        amount = result['profiles']['B']
        self.assertEqual(amount['sum'], Decimal('40.1'))
        self.assertEqual(amount['numeric_count'], 5)
        self.assertEqual(amount['count'], 7)
        self.assertEqual(amount['median'], Decimal('10.1'))
        self.assertEqual(amount['mean'], Decimal('8.02'))
        self.assertEqual(amount['types'], {'number': 6, 'blank': 2, 'missing': 1, 'error': 1, 'text': 1, 'boolean': 1})
        self.assertEqual(amount['analysis_states'], {'usable': 9, 'formula_excluded': 1, 'error_cache': 1, 'missing_cache': 1})
        self.assertEqual(result['rows'][3]['cells']['B']['value'], Decimal(999))
        self.assertEqual(result['rows'][3]['cells']['B']['analysis_state'], 'formula_excluded')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['formulas_executed'])
        self.assertFalse(result['external_links_followed'])

    def test_empty_string_cache_status_rejects_nonempty_text(self):
        source = fixture()
        source['sheets'][0]['cells']['B2'] = {
            'type': 'text', 'value': 'not empty',
            'formula': {'text': 'not evaluated'},
            'cache_status': 'unverified_empty_string'}
        with self.assertRaisesRegex(ParseError, 'Empty-string cache status'):
            analyze(source, {**BASE_QUERY, 'formula_policy': 'cached'})

    def test_cached_opt_in_uses_saved_999_without_recalculating_and_keeps_gaps(self):
        result = self.run_query(formula_policy='cached')
        self.assertEqual(result['profiles']['B']['sum'], Decimal('1039.1'))
        self.assertEqual(result['profiles']['B']['numeric_count'], 6)
        self.assertEqual(result['profiles']['B']['formula_caches_used'], 1)
        self.assertEqual(result['profiles']['B']['analysis_states']['missing_cache'], 1)
        self.assertIn('formula_cache_used', [issue['code'] for issue in result['analysis_issues']])

    def test_empty_string_saved_formula_cache_is_usable_when_cached_is_selected(self):
        records = (rec(0x00FD, cell(0, 0) + struct.pack('<I', 0))
                   + formula(1, 0, special(3)))
        source = read_bytes(synthetic_cfb({
            'Workbook': workbook(sheet_records=[records], strings=['Header'])}))
        result = analyze(source, {'sheet': '中文表1', 'range': 'A1:A2',
                                  'header_rows': '1', 'formula_policy': 'cached'})
        selected = result['rows'][0]['cells']['A']
        self.assertEqual(selected['value'], '')
        self.assertEqual(selected['analysis_state'], 'usable')
        self.assertEqual(selected['cache_status'], 'unverified_empty_string')
        self.assertTrue(selected['formula'])
        projected = preview(source, {'sheet': '中文表1', 'range': 'A1:A2'})
        self.assertIn('rows', projected)

    def test_group_sums_counts_and_numeric_coverage(self):
        result = self.run_query(group_by=['A'], aggregates=[{'op': 'sum', 'column': 'B', 'as': 'amount'}, {'op': 'rows', 'as': 'records'}])
        north, south = result['groups']
        self.assertEqual((north['keys'][0]['value'], north['row_count'], north['measures']['amount']['value']), ('North', 9, Decimal('19.9')))
        self.assertEqual((south['keys'][0]['value'], south['row_count'], south['measures']['amount']['value']), ('South', 3, Decimal('20.2')))
        self.assertEqual(north['measures']['amount']['numeric_count'], 3)
        self.assertEqual(result['totals']['amount']['value'], Decimal('40.1'))
        self.assertEqual(result['totals']['records']['value'], 12)

    def test_filter_distinct_sort_and_select_have_explicit_order(self):
        result = self.run_query(filters=[{'column': 'B', 'op': 'ge', 'type': 'number', 'value': '0'}],
                                distinct_by=['A', 'C'], sort=[{'column': 'B', 'descending': True}], select=['C', 'B'])
        self.assertEqual([row['source_row'] for row in result['rows']], [3, 2, 8])
        self.assertEqual(list(result['rows'][0]['cells']), ['C', 'B'])
        self.assertEqual(result['row_counts']['filtered_out_rows'], 8)
        self.assertEqual(result['row_counts']['duplicate_rows_removed'], 1)
        self.assertEqual(result['profiles']['B']['sum'], Decimal('30.3'))

    def test_typed_filters_do_not_coerce_identifiers_booleans_or_empty_text(self):
        cases = [({'column': 'C', 'op': 'eq', 'type': 'number', 'value': '1'}, []),
                 ({'column': 'C', 'op': 'eq', 'type': 'text', 'value': '001'}, [2, 4, 13]),
                 ({'column': 'B', 'op': 'eq', 'type': 'number', 'value': '1'}, []),
                 ({'column': 'B', 'op': 'eq', 'type': 'boolean', 'value': True}, [12]),
                 ({'column': 'C', 'op': 'eq', 'type': 'text', 'value': ''}, [7]),
                 ({'column': 'B', 'op': 'is_blank'}, [6]),
                 ({'column': 'B', 'op': 'is_missing'}, [7]),
                 ({'column': 'B', 'op': 'is_unavailable'}, [5, 9, 10]),
                 ({'column': 'A', 'op': 'contains', 'type': 'text', 'value': 'South'}, [3, 6, 8])]
        for condition, expected in cases:
            with self.subTest(condition=condition):
                result = self.run_query(filters=[condition])
                self.assertEqual([row['source_row'] for row in result['rows']], expected)

    def test_nulls_and_unavailable_values_sort_last_in_both_directions(self):
        for descending in (False, True):
            result = self.run_query(sort=[{'column': 'B', 'descending': descending}])
            self.assertEqual([row['source_row'] for row in result['rows'][-5:]], [5, 6, 7, 9, 10])
        result = self.run_query(sort=[{'column': 'A'}, {'column': 'B', 'descending': True}])
        self.assertEqual([row['source_row'] for row in result['rows'][-3:]], [3, 8, 6])

    def test_pagination_does_not_change_full_statistics_or_group_coverage(self):
        result = self.run_query(group_by=['A'], limit=1, offset=2, group_limit=1)
        self.assertEqual(result['row_page'], {'offset': 2, 'returned': 1, 'total': 12, 'truncated': True})
        self.assertEqual(result['profiles']['B']['sum'], Decimal('40.1'))
        self.assertEqual(result['group_page']['total'], 2)
        self.assertTrue(result['group_page']['truncated'])
        self.assertEqual(self.run_query(limit=0)['rows'], [])

    def test_empty_match_has_null_numeric_statistics_not_fabricated_zero(self):
        result = self.run_query(filters=[{'column': 'B', 'op': 'gt', 'type': 'number', 'value': '1000'}],
                                aggregates=[{'op': 'sum', 'column': 'B', 'as': 'total'}, {'op': 'count', 'column': 'B', 'as': 'count'}])
        self.assertEqual(result['rows'], [])
        self.assertIsNone(result['profiles']['B']['sum'])
        self.assertIsNone(result['totals']['total']['value'])
        self.assertEqual(result['totals']['count']['value'], 0)

    def test_unknown_group_keys_are_excluded_and_unknown_distinct_keys_are_preserved(self):
        result = self.run_query(group_by=['B'], distinct_by=['B'])
        self.assertEqual(result['row_counts']['duplicate_rows_removed'], 1)
        self.assertEqual(result['row_counts']['distinct_key_unavailable_rows_preserved'], 3)
        self.assertEqual(result['group_page']['unavailable_key_rows'], 3)
        self.assertEqual(sum(group['row_count'] for group in result['groups']), 8)
        kinds = {group['keys'][0]['type'] for group in result['groups']}
        self.assertIn('blank', kinds)
        self.assertIn('missing', kinds)

    def test_type_separated_grouping_and_distinct_counts(self):
        source = fixture()
        for index, entry in enumerate((item('number', '1'), item('boolean', True), item('text', '1'), item('number', '1.00')), 2):
            source['sheets'][0]['cells']['B' + str(index)] = entry
        result = analyze(source, {**BASE_QUERY, 'range': 'A1:C5', 'group_by': ['B']})
        self.assertEqual(len(result['groups']), 3)
        self.assertEqual(result['groups'][0]['row_count'], 2)
        self.assertEqual(result['profiles']['B']['count_distinct'], 3)

    def test_exact_sum_extreme_cancellation_matches_fraction_and_ignores_global_precision(self):
        source = fixture()
        values_to_sum = [Decimal('1e1000'), Decimal('1e-1000'), Decimal('-1e1000')]
        for row, value in enumerate(values_to_sum, 2):
            source['sheets'][0]['cells']['B' + str(row)] = item('number', value)
        with localcontext() as context:
            context.prec = 3
            context.Emax = 10
            context.Emin = -10
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            result = analyze(source, {**BASE_QUERY, 'range': 'A1:C4'})
        self.assertEqual(Fraction(result['profiles']['B']['sum']), sum(map(Fraction, values_to_sum)))
        self.assertEqual(result['profiles']['B']['median'], Decimal('1e-1000'))

    def test_mean_rounding_and_even_median_are_declared(self):
        source = fixture()
        for row, value in enumerate(('0', '0', '1'), 2):
            source['sheets'][0]['cells']['B' + str(row)] = item('number', value)
        result = analyze(source, {**BASE_QUERY, 'range': 'A1:C4'})
        self.assertEqual(result['profiles']['B']['mean'], Decimal('0.' + '3' * 34))
        self.assertTrue(result['profiles']['B']['mean_inexact'])
        self.assertEqual(result['profiles']['B']['mean_precision'], 34)
        result = analyze(source, {**BASE_QUERY, 'range': 'A3:C4', 'header_rows': None})
        self.assertEqual(result['profiles']['B']['median'], Decimal('0.5'))

    def test_source_query_and_original_diagnostics_remain_unchanged(self):
        source, query = fixture(), {**BASE_QUERY, 'select': ['B']}
        before_source, before_query = deepcopy(source), deepcopy(query)
        result = analyze(source, query)
        self.assertEqual(source, before_source)
        self.assertEqual(query, before_query)
        self.assertEqual(result['source']['issues'], source['issues'])
        result['source']['issues'][0]['code'] = 'changed result only'
        self.assertEqual(source, before_source)

    def test_full_json_roundtrip_preserves_numeric_types_and_calculations(self):
        source = fixture()
        reloaded = json.loads(json.dumps(source, default=str))
        original = analyze(source, BASE_QUERY)
        result = analyze(reloaded, BASE_QUERY)
        self.assertEqual(result['profiles'], original['profiles'])
        self.assertEqual(result['rows'], original['rows'])

    def test_bad_query_fields_types_ranges_and_resource_limits_fail(self):
        invalid = [{'unknown': True}, {'select': ['Z']}, {'select': []}, {'select': ['A', 'A']},
                   {'formula_policy': 'recalculate'}, {'merge_policy': 'fill_numbers'}, {'offset': True}, {'limit': -1},
                   {'filters': [{'column': 'B', 'op': 'exec', 'value': 'x'}]},
                   {'filters': [{'column': 'B', 'op': 'eq', 'type': 'number', 'value': True}]},
                   {'filters': [{'column': 'B', 'op': 'eq', 'type': {}, 'value': 'x'}]},
                   {'aggregates': [{'op': {}, 'as': 'x'}]},
                   {'aggregates': [{'op': 'rows', 'as': '\ud800'}]},
                   {'sort': [{'column': 'A', 'descending': 'false'}]}, {'sort': [{'column': 'A'}] * 2},
                   {'aggregates': [{'op': 'sum', 'column': 'B', 'as': 'x'}] * 2},
                   {'range': 'A1:XFD1048576'}, {'range': 'A1:A200001'}, {'range': 'B4:A1'}]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ParseError):
                self.run_query(**options)
        with patch('et_table_analysis.MAX_GROUPS', 1), self.assertRaises(ParseError) as error:
            self.run_query(group_by=['A'])
        self.assertEqual(error.exception.status, 'resource_limit')

    def test_invalid_numeric_values_and_projection_schema_fail(self):
        for value in (True, 1.5, 'NaN', 'Infinity', '1_000', ' 1', '1e9999'):
            source = fixture()
            source['sheets'][0]['cells']['B2']['value'] = value
            with self.subTest(value=value), self.assertRaises(ParseError):
                analyze(source, BASE_QUERY)
        for schema, status in (('wps_read_view_v1', 'partial'), ('biff8_readonly_v1', 'corrupt'), ([], 'partial')):
            source = fixture()
            source.update(schema_version=schema, status=status)
            with self.assertRaises(ParseError):
                analyze(source, BASE_QUERY)


class MergedTableTests(unittest.TestCase):
    def source(self):
        source = fixture()
        sheet = source['sheets'][0]
        sheet['merges'] = [{'range': 'A2:A4'}, {'range': 'B2:B4'}]
        for address in ('A3', 'A4', 'B3', 'B4'):
            sheet['cells'].pop(address)
        return source

    def test_default_does_not_repeat_merged_labels_or_numeric_totals(self):
        result = analyze(self.source(), {**BASE_QUERY, 'range': 'A1:C4'})
        self.assertEqual(result['profiles']['B']['sum'], Decimal('10.1'))
        self.assertEqual(result['profiles']['A']['count'], 1)
        self.assertEqual(result['rows'][1]['cells']['A']['type'], 'missing')

    def test_explicit_fill_text_only_is_traceable_and_never_fills_numbers(self):
        source = self.source()
        before = deepcopy(source)
        result = analyze(source, {**BASE_QUERY, 'range': 'A1:C4', 'merge_policy': 'fill_text', 'group_by': ['A']})
        self.assertEqual(result['scope']['filled_text_positions'], 2)
        self.assertEqual(result['rows'][1]['cells']['A']['propagated_from'], 'A2')
        self.assertEqual(result['rows'][1]['cells']['A']['original_type'], 'missing')
        self.assertEqual(result['profiles']['B']['sum'], Decimal('10.1'))
        self.assertEqual(result['groups'][0]['row_count'], 3)
        self.assertEqual(source, before)

    def test_clipped_data_merge_does_not_read_anchor_outside_selection(self):
        result = analyze(self.source(), {'sheet': 'Data', 'range': 'A3:B4', 'merge_policy': 'fill_text'})
        self.assertEqual(result['scope']['filled_text_positions'], 0)
        self.assertTrue(all(item['clipped_by_selection'] for item in result['merges']))
        self.assertIsNone(result['profiles']['B']['sum'])

    def test_text_fill_refuses_to_overwrite_stored_content(self):
        source = self.source()
        source['sheets'][0]['cells']['A3'] = item('text', '')
        with self.assertRaisesRegex(ParseError, 'overwrite'):
            analyze(source, {**BASE_QUERY, 'merge_policy': 'fill_text'})
        self.assertEqual(analyze(source, BASE_QUERY)['rows'][1]['cells']['A']['value'], '')

    def test_header_merge_reconstruction_uses_physical_ids_and_rejects_clipping(self):
        source = fixture()
        source['sheets'][0]['merges'] = [{'range': 'A1:B1'}]
        source['sheets'][0]['cells'].pop('B1')
        result = analyze(source, BASE_QUERY)
        self.assertEqual(result['header']['columns'][1]['levels'][0]['source'], 'A1')
        self.assertEqual(result['columns'], ['A', 'B', 'C'])
        with self.assertRaises(ParseError):
            analyze(source, {**BASE_QUERY, 'range': 'B1:C13'})
        source['sheets'][0]['merges'] = [{'range': 'A1:A2'}]
        with self.assertRaises(ParseError):
            analyze(source, BASE_QUERY)

    def test_formula_header_and_label_are_not_silently_used_or_filled(self):
        source = self.source()
        source['sheets'][0]['cells']['A1'] = item('text', 'cached header', 'unverified')
        source['sheets'][0]['cells']['A2'] = item('text', 'cached label', 'unverified')
        query = {**BASE_QUERY, 'range': 'A1:C4', 'merge_policy': 'fill_text'}
        result = analyze(source, query)
        self.assertEqual(result['header']['columns'][0]['path'][0]['type'], 'unavailable')
        self.assertEqual(result['scope']['filled_text_positions'], 0)
        cached = analyze(source, {**query, 'formula_policy': 'cached'})
        self.assertEqual(cached['header']['columns'][0]['path'][0]['value'], 'cached header')
        self.assertEqual(cached['scope']['filled_text_positions'], 0)


class AnalysisIntegrationTests(unittest.TestCase):
    def binary(self):
        payload = (rec(0x203, cell(0, 0) + struct.pack('<d', 1.25)) + rec(0x203, cell(1, 0) + struct.pack('<d', 2.5)) +
                   formula(2, 0, struct.pack('<d', 999)))
        return synthetic_cfb({'Workbook': workbook([payload], strings=[])})

    def test_real_parser_biff_bytes_saved_cache_values_and_input_are_preserved(self):
        data = self.binary()
        digest = hashlib.sha256(data).hexdigest()
        source = read_bytes(data)
        before = deepcopy(source)
        name = source['sheets'][0]['name']
        result = analyze(source, {'sheet': name, 'range': 'A1:A3'})
        self.assertEqual(result['profiles']['A']['sum'], Decimal('3.75'))
        cached = analyze(source, {'sheet': name, 'range': 'A1:A3', 'formula_policy': 'cached'})
        self.assertEqual(cached['profiles']['A']['sum'], Decimal('1002.75'))
        self.assertEqual(values(source), values(before))
        self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

    @unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
    def test_ooxml_decimal_saved_values_and_date_text_not_coerced(self):
        sheet = (f'<worksheet xmlns="{T}"><sheetData><row r="1"><c r="A1"><v>0.1</v></c></row>'
                 '<row r="2" hidden="1"><c r="A2"><v>0.2</v></c></row>'
                 '<row r="3"><c r="A3" t="d"><v>2026-11-03</v></c></row></sheetData></worksheet>')
        source = read_bytes(package(overrides={'xl/worksheets/sheet1.xml': sheet}))
        result = analyze(source, {'sheet': 'Data', 'range': 'A1:A3'})
        self.assertEqual(result['profiles']['A']['sum'], Decimal('0.3'))
        self.assertEqual(result['profiles']['A']['types'], {'number': 2, 'text': 1})
        self.assertEqual(result['rows'][2]['cells']['A']['value'], '2026-11-03')
        self.assertIn('including hidden', result['scope']['saved_visibility'])

    def test_cli_json_partial_exit_and_invalid_queries_without_files_changed(self):
        with tempfile.TemporaryDirectory(prefix='et-analysis-', dir='.') as directory:
            source = Path(directory) / 'synthetic.ett'
            query = Path(directory) / 'query.json'
            data = self.binary()
            with source.open('xb') as handle:
                handle.write(data)
            with query.open('x', encoding='utf-8') as handle:
                json.dump({'formula_policy': 'cached', 'limit': 0}, handle)
            command = [sys.executable, '-I', '-S', '-B', str((Path(__file__).resolve().parents[1] / 'analyze_et.py')), str(source),
                       '--sheet', '中文表1', '--range', 'A1:A3', '--query', str(query)]
            process = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, 3, process.stderr + process.stdout)
            self.assertEqual(process.stderr, '')
            result = json.loads(process.stdout)
            self.assertEqual(result['profiles']['A']['sum'], '1002.75')
            self.assertEqual(result['source_sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(source.read_bytes(), data)
            for raw in ('{"limit":1,"limit":2}', '{"limit":NaN}', '{"sheet":"Data"}', '[]', '{'):
                query.write_text(raw, encoding='utf-8')
                failed = subprocess.run(command, capture_output=True, text=True, check=False)
                self.assertEqual(failed.returncode, 1)
                self.assertEqual(json.loads(failed.stdout)['status'], 'analysis_error')
                self.assertEqual(failed.stderr, '')

    def test_cli_selfcheck_usage_and_oversized_query(self):
        script = str((Path(__file__).resolve().parents[1] / 'analyze_et.py'))
        process = subprocess.run([sys.executable, '-I', '-S', '-B', script, '--check'], capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0)
        self.assertIn('environment-ok', process.stdout)
        process = subprocess.run([sys.executable, '-I', '-S', '-B', script], capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, '')
        with tempfile.TemporaryDirectory(prefix='et-query-', dir='.') as directory:
            query = Path(directory) / 'large.json'
            with query.open('xb') as handle:
                handle.write(b' ' * 65537)
            with self.assertRaises(ParseError) as error:
                load_query(query)
            self.assertEqual(error.exception.status, 'resource_limit')

    def test_output_budget_failure_emits_one_complete_failure_json(self):
        with tempfile.TemporaryDirectory(prefix='et-output-', dir='.') as directory:
            source = Path(directory) / 'synthetic.ett'
            data = self.binary()
            with source.open('xb') as handle:
                handle.write(data)
            stdout = io.StringIO()
            with patch('read_et.MAX_JSON_OUTPUT_BYTES', 100), redirect_stdout(stdout):
                code = main([str(source), '--sheet', '中文表1', '--range', 'A1:A3'])
            self.assertEqual(code, 1)
            failure = json.loads(stdout.getvalue())
            self.assertEqual(failure['status'], 'resource_limit')
            self.assertNotIn('profiles', failure)
            self.assertEqual(source.read_bytes(), data)

    def test_synthetic_workbook_cannot_claim_frozen_original_provenance(self):
        with self.assertRaisesRegex(ValueError, 'Original hash'):
            verify_bytes(self.binary())


if __name__ == '__main__':
    unittest.main()
