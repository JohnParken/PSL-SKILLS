"""Independent coverage accounting and explicit header-region preview checks."""
from contextlib import redirect_stdout
from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_et import main
from et_table_analysis import analyze
from et_table_preview import preview
from read_et import read_bytes
from test_et_table_analysis import BASE_QUERY, fixture, item
import test_et_table_analysis as analysis_fixtures
from test_wps_ooxml_sheet import package, T
from wps_access import ParseError


class TableQualityTests(unittest.TestCase):
    def test_numeric_partition_explains_every_matching_position_without_double_counting(self):
        result = analyze(fixture(), BASE_QUERY)
        quality = result['quality_report']
        amount = quality['columns']['B']
        self.assertEqual(amount['numeric_coverage'], {'numerator': 5, 'denominator': 12, 'partition': {
            'number_included': 5, 'unavailable': 3, 'blank': 1, 'missing': 1, 'non_numeric_usable': 2}})
        self.assertEqual(amount['usable_nonblank_values'], 7)
        self.assertEqual(amount['usable_value_types'], ['boolean', 'number', 'text'])
        self.assertTrue(amount['mixed_usable_types'])
        findings = {finding['code']: finding for finding in amount['findings']}
        for code, coordinates in (('formula_excluded', ['B5']), ('missing_cache', ['B10']), ('error_cache', ['B9']),
                                  ('blank', ['B6']), ('missing', ['B7']), ('non_numeric_value', ['B11', 'B12'])):
            self.assertEqual(findings[code]['sample_coordinates'], coordinates)
            self.assertEqual(findings[code]['positions'], len(coordinates))
        self.assertEqual(quality['assessment'], 'observations_only_no_correctness_score')
        self.assertFalse(quality['business_semantics_verified'])
        self.assertFalse(quality['formula_freshness_verified'])

    def test_quality_covers_matching_data_independent_of_preview_pages_or_projection(self):
        query = {**BASE_QUERY, 'exclude_rows': [5, 9, 10], 'filters': [{'column': 'B', 'op': 'ge', 'type': 'number', 'value': '0'}]}
        first = analyze(fixture(), {**query, 'limit': 0, 'select': ['C']})
        page = analyze(fixture(), {**query, 'limit': 1, 'offset': 2})
        self.assertEqual(first['quality_report'], page['quality_report'])
        self.assertEqual(first['quality_report']['columns']['B']['numeric_coverage']['numerator'], 4)
        self.assertEqual(first['quality_report']['columns']['B']['numeric_coverage']['denominator'], 4)
        self.assertEqual(first['quality_report']['row_counts']['explicitly_excluded_rows'], 3)
        self.assertEqual(first['source']['issues'], fixture()['issues'])

    def test_cache_reports_do_not_claim_freshness_or_treat_missing_as_zero(self):
        quality = analyze(fixture(), {**BASE_QUERY, 'formula_policy': 'cached'})['quality_report']
        amount = quality['columns']['B']
        self.assertEqual(amount['numeric_coverage']['numerator'], 6)
        self.assertEqual(amount['numeric_coverage']['partition']['unavailable'], 2)
        findings = {finding['code']: finding for finding in amount['findings']}
        self.assertEqual(findings['unverified_cache']['sample_coordinates'], ['B5'])
        self.assertEqual(findings['missing_cache']['sample_coordinates'], ['B10'])
        self.assertFalse(quality['formula_freshness_verified'])

    def test_samples_are_bounded_in_original_row_order_with_explicit_truncation(self):
        result = analyze(fixture(), {**BASE_QUERY, 'sort': [{'column': 'B', 'descending': True}], 'quality_sample_limit': 2})
        finding = result['quality_report']['columns']['A']['findings'][0]
        self.assertEqual(finding['positions'], 12)
        self.assertEqual(finding['sample_coordinates'], ['A2', 'A3'])
        self.assertTrue(finding['samples_truncated'])
        zero = analyze(fixture(), {**BASE_QUERY, 'quality_sample_limit': 0})['quality_report']['columns']['A']['findings'][0]
        self.assertEqual(zero['positions'], 12)
        self.assertEqual(zero['sample_coordinates'], [])
        self.assertTrue(zero['samples_truncated'])

    def test_quality_sample_limit_is_strictly_typed_and_bounded(self):
        for value in (True, -1, 21, '1', 1.0, None):
            with self.subTest(value=value), self.assertRaises(ParseError):
                analyze(fixture(), {**BASE_QUERY, 'quality_sample_limit': value})

    def test_empty_match_reports_zero_denominator_and_does_not_invent_a_score(self):
        result = analyze(fixture(), {**BASE_QUERY, 'exclude_rows': list(range(2, 14))})
        quality = result['quality_report']
        self.assertTrue(quality['empty_match'])
        self.assertEqual(quality['columns']['B']['numeric_coverage']['denominator'], 0)
        self.assertEqual(quality['columns']['B']['findings'], [])
        self.assertNotIn('score', quality)
        self.assertIsNone(result['profiles']['B']['sum'])

    def test_legitimate_text_and_blank_are_observations_not_new_partial_failures(self):
        source = fixture()
        source.update(status='ok', partial=False, issues=[])
        result = analyze(source, {'sheet': 'Data', 'range': 'A2:A3'})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['quality_report']['columns']['A']['numeric_coverage']['partition']['non_numeric_usable'], 2)
        self.assertEqual(result['quality_report']['header'], {'declared': False, 'inferred': False})

    def test_header_quality_identifies_duplicate_empty_and_unavailable_labels_without_renaming(self):
        source = fixture()
        source['sheets'][0]['cells'].update(C1=item('text', 'Region'), B1=item('text', 'Cached title', 'unverified'))
        result = analyze(source, {**BASE_QUERY, 'range': 'A1:D13'})
        header = result['quality_report']['header']
        self.assertEqual(header['duplicate_label_columns'], [['A', 'C']])
        self.assertEqual(header['empty_label_columns'], ['D'])
        self.assertEqual(header['unavailable_label_columns'], ['B'])
        self.assertEqual(result['columns'], ['A', 'B', 'C', 'D'])

    def test_group_coverage_counts_all_groups_even_when_no_group_preview_is_returned(self):
        result = analyze(fixture(), {**BASE_QUERY, 'group_by': ['B'], 'group_limit': 0})
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['quality_report']['group_coverage'], {
            'requested': True, 'matched_rows': 12, 'grouped_rows': 9, 'unavailable_key_rows_excluded': 3})

    def test_mean_rounding_and_propagated_labels_are_visible_in_quality(self):
        source = fixture()
        source['sheets'][0]['merges'] = [{'range': 'A2:A4'}]
        for row, value in ((2, '0'), (3, '0'), (4, '1')):
            source['sheets'][0]['cells']['B' + str(row)] = item('number', value)
        for coordinate in ('A3', 'A4'):
            source['sheets'][0]['cells'].pop(coordinate)
        result = analyze(source, {**BASE_QUERY, 'range': 'A1:C4', 'merge_policy': 'fill_text'})
        quality = result['quality_report']
        self.assertTrue(quality['columns']['B']['numeric_mean_inexact'])
        finding = next(item for item in quality['columns']['A']['findings'] if item['code'] == 'propagated_text')
        self.assertEqual(finding['sample_coordinates'], ['A3', 'A4'])
        self.assertEqual(finding['positions'], 2)

    def test_quality_does_not_modify_source_or_share_row_count_containers(self):
        source, query = fixture(), deepcopy(BASE_QUERY)
        before_source, before_query = deepcopy(source), deepcopy(query)
        result = analyze(source, query)
        result['quality_report']['row_counts']['matched_rows'] = -1
        self.assertEqual(result['row_counts']['matched_rows'], 12)
        self.assertEqual(source, before_source)
        self.assertEqual(query, before_query)

    def test_blank_and_empty_text_caches_are_not_included_as_numeric_zero(self):
        source = fixture()
        source['sheets'][0]['cells'].update(B2=item('blank', None, 'unverified'), B3=item('text', '', 'unverified'))
        result = analyze(source, {'sheet': 'Data', 'range': 'B2:B3', 'formula_policy': 'cached'})
        amount = result['quality_report']['columns']['B']
        self.assertEqual(amount['numeric_coverage']['partition'], {
            'number_included': 0, 'unavailable': 0, 'blank': 1, 'missing': 0, 'non_numeric_usable': 1})
        findings = {finding['code']: finding for finding in amount['findings']}
        self.assertEqual(findings['unverified_cache']['positions'], 2)
        self.assertEqual(result['profiles']['B']['formula_caches_used'], 1)
        self.assertIsNone(result['profiles']['B']['sum'])


class TablePreviewTests(unittest.TestCase):
    @unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
    def test_ooxml_preview_preserves_text_identifiers_missing_positions_and_cached_formula(self):
        sheet = (f'<worksheet xmlns="{T}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>001</t></is></c>'
                 '<c r="C1"><f>1+1</f><v>7</v></c></row></sheetData><mergeCells count="1">'
                 '<mergeCell ref="A1:B1"/></mergeCells></worksheet>')
        data = package(overrides={'xl/worksheets/sheet1.xml': sheet})
        result = preview(read_bytes(data), {'sheet': 'Data', 'range': 'A1:C2'})
        self.assertEqual(result['rows'][0]['cells']['A']['value'], '001')
        self.assertEqual(result['rows'][0]['cells']['B']['type'], 'missing')
        self.assertEqual(result['rows'][0]['cells']['C']['value'], Decimal(7))
        self.assertEqual(result['rows'][0]['cells']['C']['cache_status'], 'unverified')
        self.assertTrue(result['merges'][0]['anchor_in_page'])
        self.assertFalse(result['formulas_executed'])

    def test_rectangular_preview_preserves_absent_blank_text_and_unverified_cache(self):
        source = fixture()
        source['sheets'][0]['cells'].update(B1=item('text', 'Cached title', 'unverified'), C2=item('blank', None))
        source['sheets'][0]['cells'].pop('C3')
        before = deepcopy(source)
        result = preview(source, {'sheet': 'Data', 'range': 'A1:C3'})
        self.assertEqual(result['schema_version'], 'et_table_preview_v1')
        self.assertEqual(result['scope']['returned_positions'], 9)
        self.assertEqual(result['rows'][1]['cells']['C']['type'], 'blank')
        self.assertEqual(result['rows'][2]['cells']['C']['type'], 'missing')
        title = result['rows'][0]['cells']['B']
        self.assertEqual((title['value'], title['formula'], title['cache_status']), ('Cached title', True, 'unverified'))
        self.assertNotIn('analysis_state', title)
        self.assertFalse(result['header_rows_inferred'])
        self.assertFalse(result['statistics_computed'])
        self.assertEqual(result['source']['issues'], source['issues'])
        self.assertEqual(source, before)
        with self.assertRaises(ParseError):
            analyze(result, BASE_QUERY)

    def test_clipped_header_merge_is_explained_without_fetching_or_filling_outside_anchor(self):
        source = fixture()
        source['sheets'][0]['merges'] = [{'range': 'A1:B1'}]
        source['sheets'][0]['cells'].pop('B1')
        result = preview(source, {'sheet': 'Data', 'range': 'B1:C2'})
        self.assertEqual(result['merges'], [{'range': 'A1:B1', 'anchor': 'A1', 'clipped_by_selection': True, 'anchor_in_page': False}])
        self.assertEqual(result['rows'][0]['cells']['B']['type'], 'missing')
        self.assertFalse(result['values_filled'])
        with self.assertRaises(ParseError):
            analyze(source, {'sheet': 'Data', 'range': 'B1:C2', 'header_rows': '1'})

    def test_preview_page_keeps_original_row_numbers_and_identifies_off_page_merge_anchor(self):
        source = fixture()
        source['sheets'][0]['merges'] = [{'range': 'A1:A2'}]
        result = preview(source, {'sheet': 'Data', 'range': 'A1:C13', 'offset': 1, 'limit': 1})
        self.assertEqual(result['row_page'], {'offset': 1, 'returned': 1, 'total': 13, 'truncated': True, 'next_offset': 2})
        self.assertEqual(result['rows'][0]['source_row'], 2)
        self.assertFalse(result['merges'][0]['anchor_in_page'])
        self.assertFalse(result['merges'][0]['clipped_by_selection'])
        self.assertEqual(result['scope']['merges_scope'], 'entire selected rectangle')

    def test_preview_rejects_analysis_options_and_invalid_limits(self):
        for options in ({'header_rows': '1'}, {'formula_policy': 'cached'}, {'exclude_rows': [2]}, {'filters': []},
                        {'merge_policy': 'fill_text'}, {'limit': True}, {'limit': 0}, {'limit': 51}, {'offset': -1}):
            with self.subTest(options=options), self.assertRaises(ParseError):
                preview(fixture(), {'sheet': 'Data', 'range': 'A1:C13', **options})

    def test_preview_keeps_original_selection_and_page_budgets(self):
        for region, limit in (('A1:IV50', 50), ('A1:IV1000', 1)):
            with self.subTest(region=region), self.assertRaises(ParseError) as error:
                preview(fixture(), {'sheet': 'Data', 'range': region, 'limit': limit})
            self.assertEqual(error.exception.status, 'resource_limit')
        source = fixture()
        source['sheets'][0]['cells']['B13']['value'] = 'NaN'
        with self.assertRaises(ParseError):
            preview(source, {'sheet': 'Data', 'range': 'A1:C13', 'limit': 1})

    def test_preview_default_ten_rows_and_last_page_explain_incomplete_coverage(self):
        first = preview(fixture(), {'sheet': 'Data', 'range': 'A1:C13'})
        self.assertEqual(first['row_page']['returned'], 10)
        self.assertEqual(first['row_page']['next_offset'], 10)
        last = preview(fixture(), {'sheet': 'Data', 'range': 'A1:C13', 'offset': 10})
        self.assertEqual(last['row_page']['returned'], 3)
        self.assertIsNone(last['row_page']['next_offset'])
        self.assertTrue(last['row_page']['truncated'])

    def test_real_biff_parser_and_cli_preview_preserve_original_and_source_status(self):
        data = analysis_fixtures.AnalysisIntegrationTests().binary()
        parsed = read_bytes(data)
        direct = preview(parsed, {'sheet': '中文表1', 'range': 'A1:B3'})
        self.assertEqual(direct['rows'][2]['cells']['A']['value'], Decimal(999))
        self.assertEqual(direct['rows'][2]['cells']['B']['type'], 'missing')
        directory = Path('et-preview-' + uuid.uuid4().hex)
        self.assertFalse(directory.exists())
        directory.mkdir()
        try:
            source = directory / 'source.et'
            with source.open('xb') as handle:
                handle.write(data)
            command = [sys.executable, '-I', '-S', '-B', str((Path(__file__).resolve().parents[1] / 'analyze_et.py')), str(source),
                       '--sheet', '中文表1', '--range', 'A1:B3', '--preview']
            process = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(process.stderr, '')
            result = json.loads(process.stdout)
            self.assertEqual(result['schema_version'], 'et_table_preview_v1')
            self.assertEqual(result['status'], parsed['status'])
            self.assertEqual(result['source_sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(result['rows'][2]['cells']['A']['value'], '999')
            self.assertNotIn('profiles', result)
            invalid = subprocess.run(command + ['--header-rows', '1'], capture_output=True, text=True, check=False)
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, '')
            output = io.StringIO()
            with patch('read_et.MAX_JSON_OUTPUT_BYTES', 100), redirect_stdout(output):
                code = main(command[5:])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())['status'], 'resource_limit')
            self.assertEqual(source.read_bytes(), data)
        finally:
            shutil.rmtree(directory)


if __name__ == '__main__':
    unittest.main()
