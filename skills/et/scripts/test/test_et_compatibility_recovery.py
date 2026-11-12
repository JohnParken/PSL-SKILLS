"""Independent expectations for value recovery, coverage and delivery integrity."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from et_table_analysis import analyze
from et_table_preview import preview
from et_read_view import project
from test_et_table_analysis import BASE_QUERY, fixture
from test_wps_biff8 import bof, cell, formula, rec, special, workbook
from test_wps_cfb import synthetic_cfb
from test_wps_embedded_charts import CHART, chart_workbook
from test_wps_ooxml_sheet import (R, T, build_package, cells_of, codes, package,
                                  result, text_parts_of)
from read_et import read_bytes
from wps_access import ParseError
from wps_biff8 import parse_stream
from wps_result import encode_result, write_result_file


def sheet_package(sheet):
    """Replace only the fixture worksheet; no production parser decides expected values."""
    parts = text_parts_of(package())
    parts['xl/worksheets/sheet1.xml'] = sheet
    return build_package(parts)


def _biff_record_offsets(data):
    offsets = []
    position = 0
    while position + 4 <= len(data):
        kind, length = struct.unpack_from('<HH', data, position)
        offsets.append((position, kind, length))
        position += 4 + length
    return offsets


def _biff_tweak(data, record_index, field, value):
    changed = bytearray(data)
    position = _biff_record_offsets(data)[record_index][0]
    struct.pack_into('<I' if field == 'I' else '<H', changed, position + 4, value)
    return bytes(changed)


class SheetCoverageTests(unittest.TestCase):
    def two_sheet_workbook(self):
        return workbook(sheet_records=[
            rec(0x0203, cell(0, 0) + struct.pack('<d', 1)),
            rec(0x0203, cell(0, 0) + struct.pack('<d', 2))], names=['S', 'T'])

    def test_unmatched_sheet_offset_is_not_reported_as_complete(self):
        base = self.two_sheet_workbook()
        # BoundSheet8 records are indices 6 and 7; sheet S starts at 9.
        result = parse_stream(_biff_tweak(base, 6, 'I', 0xFFFFFF))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')
        self.assertTrue(result['omissions_affect_cell_values'])
        self.assertEqual(result['sheet_coverage']['not_read'], ['S'])
        self.assertEqual(result['sheets'][0]['read_status'], 'not_read')
        self.assertEqual(result['sheets'][1]['cells']['A1']['value'], Decimal(2))

    def test_invalid_worksheet_bof_is_not_reported_as_complete(self):
        base = self.two_sheet_workbook()
        result = parse_stream(_biff_tweak(base, 9, 'H', 0x0005))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')
        self.assertTrue(result['omissions_affect_cell_values'])
        self.assertEqual(result['sheet_coverage']['not_read'], ['S'])
        self.assertEqual(result['sheets'][0]['read_status'], 'not_read')
        self.assertEqual(result['sheets'][1]['cells']['A1']['value'], Decimal(2))


class TextBudgetTests(unittest.TestCase):
    def test_inline_and_shared_strings_share_one_retained_text_budget(self):
        inline = package(cells=(
            '<c r="A1" t="inlineStr"><is><t>abcdefghijkl</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>mnopqrstuvwx</t></is></c>'))
        shared_parts = text_parts_of(package(cells=(
            '<c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>')))
        shared_parts['xl/sharedStrings.xml'] = (
            f'<sst xmlns="{T}" count="2" uniqueCount="2">'
            '<si><t>abcdefghijkl</t></si><si><t>mnopqrstuvwx</t></si></sst>')
        shared = build_package(shared_parts)
        for label, data in (('inline', inline), ('shared', shared)):
            with self.subTest(kind=label), patch('wps_ooxml_text.MAX_TEXT', 10):
                result = read_bytes(data)
                cells = result['sheets'][0]['cells']
                retained = sum(len(item['value']) for item in cells.values())
                self.assertLessEqual(retained, 10)
                self.assertTrue(any(item.get('text_truncated')
                                    for item in cells.values()))
                self.assertEqual(result['cell_value_coverage'],
                                 'unverified_due_to_best_effort_salvage')
                self.assertTrue(result['omissions_affect_cell_values'])
                self.assertIn('text_limit_reached', codes(result))
                self.assertEqual(result['text_budget']['unit'], 'unicode_code_points')
                self.assertEqual(result['text_budget']['limit'], 10)
                self.assertEqual(result['text_budget']['retained'], 10)
                self.assertTrue(result['text_budget']['limited'])


class CoverageStateTests(unittest.TestCase):
    def test_biff_coverage_survives_issue_truncation(self):
        stream = workbook(sheet_records=[rec(0x0081, b''),
                                         formula(0, 0, special(0))])
        with patch('wps_biff8.MAX_ISSUES', 1):
            result = parse_stream(stream)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['issues_truncated'])
        self.assertEqual(result['cell_value_coverage'], 'missing_formula_caches')
        self.assertTrue(result['omissions_affect_cell_values'])

    def test_issue_truncation_does_not_change_saved_values(self):
        biff = workbook(sheet_records=[
            rec(0x00FD, cell(0, 0) + struct.pack('<I', 0)),
            rec(0x0203, cell(0, 1) + struct.pack('<d', 12.5))])
        normal = parse_stream(biff)
        with patch('wps_biff8.MAX_ISSUES', 0):
            truncated = parse_stream(biff)
        self.assertEqual(truncated['sheets'][0]['cells'], normal['sheets'][0]['cells'])
        data = package(cells='<c r="A1"><v>12.5</v></c>')
        normal = read_bytes(data)
        with patch('wps_ooxml.MAX_ISSUE_CODES', 0):
            truncated = read_bytes(data)
        self.assertEqual(truncated['sheets'][0]['cells'], normal['sheets'][0]['cells'])

    def test_ooxml_coverage_survives_issue_truncation(self):
        parts = text_parts_of(package(cells='<c r="A1"><f>1+1</f></c>'))
        parts['xl/_rels/workbook.xml.rels'] = parts[
            'xl/_rels/workbook.xml.rels'].replace(
                '</Relationships>',
                f'<Relationship Id="theme" Type="{R}/theme" '
                'Target="theme/theme1.xml"/></Relationships>')
        parts['xl/theme/theme1.xml'] = (
            b'<theme xmlns="http://schemas.openxmlformats.org/'
            b'spreadsheetml/2006/main"/>')
        data = build_package(parts)
        with patch('wps_ooxml.MAX_ISSUE_CODES', 1):
            result = read_bytes(data)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['issues_truncated'])
        self.assertEqual(result['cell_value_coverage'], 'missing_formula_caches')
        self.assertTrue(result['omissions_affect_cell_values'])

    def test_missing_eof_marks_the_sheet_partial_and_uncertain(self):
        data = workbook(sheet_records=[rec(0x0203, cell(0, 0) + struct.pack('<d', 1))])
        result = parse_stream(data[:-4])
        self.assertEqual(result['sheets'][0]['read_status'], 'partial')
        self.assertEqual(result['sheet_coverage']['partial'], ['中文表1'])
        self.assertEqual(result['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')
        self.assertTrue(result['omissions_affect_cell_values'])

    def test_malformed_xf_keeps_its_index_slot(self):
        bad = rec(0x00E0, bytes(19))
        xf7 = rec(0x00E0, bytes(2) + struct.pack('<H', 7) + bytes(16))
        xf200 = rec(0x00E0, bytes(2) + struct.pack('<H', 200) + bytes(16))
        cells = (rec(0x0203, cell(0, 0, 3) + struct.pack('<d', 7))
                 + rec(0x0203, cell(1, 0, 4) + struct.pack('<d', 8))
                 + rec(0x0203, cell(2, 0, 2) + struct.pack('<d', 9)))
        result = parse_stream(workbook(sheet_records=[cells],
                                       extra_global=bad + xf7 + xf200))
        self.assertEqual([style['number_format_id_raw']
                          for style in result['styles']], [0, 164, 0, 7, 200])
        self.assertFalse(result['styles'][2]['valid'])
        cells = result['sheets'][0]['cells']
        self.assertEqual(cells['A1']['number_format_id_raw'], 7)
        self.assertEqual(cells['A2']['number_format_id_raw'], 200)
        self.assertEqual(cells['A3']['number_format_id_raw'], 0)
        self.assertTrue(cells['A3']['xf_slot_invalid'])
        self.assertIn('xf_slot_invalid_fallback',
                      [item['code'] for item in result['issues']])


class ChartRecoveryTests(unittest.TestCase):
    def test_missing_end_and_eof_recovers_number_with_uncertain_coverage(self):
        damaged = CHART.replace(rec(0x1034), b'')[:-4]
        source = parse_stream(chart_workbook(chart=damaged))
        self.assertEqual(source['sheets'][0]['cells']['A2']['value'], 42)
        self.assertTrue(source['omissions_affect_cell_values'])
        self.assertEqual(source['cell_value_coverage'], 'unverified_due_to_best_effort_salvage')

    def test_rk_and_multi_cell_records_end_broken_chart_at_any_depth(self):
        tail = (rec(0x027E, cell(2, 0) + struct.pack('<I', (7 << 2) | 2))
                + rec(0x00BD, struct.pack('<HHHIHIH', 3, 1, 0, (8 << 2) | 2, 0, (9 << 2) | 2, 2))
                + rec(0x00BE, struct.pack('<5H', 4, 1, 0, 0, 2)))
        damaged = CHART.replace(rec(0x1034), b'')[:-4] + tail
        source = parse_stream(chart_workbook(chart=damaged))
        cells = source['sheets'][0]['cells']
        self.assertEqual([cells[ref]['value'] for ref in ('A2', 'A3', 'B4', 'C4')], [42, 7, 8, 9])
        self.assertEqual(cells['B5']['type'], 'blank')

    def test_real_chart_series_values_stay_out_of_worksheet(self):
        cached_point = rec(0x0203, cell(9, 0) + struct.pack('<d', 999))
        chart = CHART[:-4] + rec(0x1065, struct.pack('<H', 1)) + cached_point + rec(0xA)
        source = parse_stream(chart_workbook(chart=chart))
        self.assertEqual(set(source['sheets'][0]['cells']), {'A2'})
        self.assertEqual(source['sheets'][0]['cells']['A2']['value'], 42)
        self.assertFalse(source['omissions_affect_cell_values'])

    def test_incomplete_chart_body_eof_does_not_end_worksheet(self):
        source = parse_stream(chart_workbook(chart=bof(0x20) + rec(0x1001, bytes(2)) + rec(0xA)))
        self.assertEqual(source['sheets'][0]['cells']['A2']['value'], 42)
        self.assertTrue(source['omissions_affect_cell_values'])

    def test_ambiguous_missing_chart_eof_never_claims_complete(self):
        chart = CHART[:-4] + rec(0x1065, struct.pack('<H', 1))
        source = parse_stream(chart_workbook(chart=chart))
        self.assertTrue(source['omissions_affect_cell_values'])
        self.assertIn('worksheet_eof_missing', [issue['code'] for issue in source['issues']])


class MetadataRecoveryTests(unittest.TestCase):
    def test_invalid_filter_info_does_not_poison_later_valid_definition(self):
        records = (rec(0x9D, struct.pack('<H', 0)) + rec(0x9D, struct.pack('<H', 2))
                   + rec(0x9E, struct.pack('<H', 1) + bytes(22))
                   + rec(0x203, cell(0, 0) + struct.pack('<d', 17)))
        source = parse_stream(workbook(sheet_records=[records]))
        sheet = source['sheets'][0]
        self.assertEqual(sheet['cells']['A1']['value'], 17)
        self.assertEqual(sheet['visibility']['filters']['column_count'], 2)
        self.assertEqual(sheet['visibility']['filters']['records'][0]['column_relative'], 1)
        self.assertFalse(source['omissions_affect_cell_values'])

    def test_overlapping_merges_preserve_stats_and_preview_but_reject_fill(self):
        source = fixture()
        source['sheets'][0]['merges'] = [{'range': 'A2:B2'}, {'range': 'B2:B3'}]
        original = deepcopy(source)
        analysis = analyze(source, BASE_QUERY)
        self.assertEqual(analysis['profiles']['B']['sum'], analyze(fixture(), BASE_QUERY)['profiles']['B']['sum'])
        self.assertIn('overlapping_merge_mapping_not_applied', [issue['code'] for issue in analysis['analysis_issues']])
        view = preview(source, {'sheet': 'Data', 'range': 'A1:C3'})
        self.assertEqual(view['status'], 'partial')
        self.assertNotIn('merge_anchor', view['rows'][1]['cells']['B'])
        with self.assertRaisesRegex(ParseError, 'text fill'):
            analyze(source, {**BASE_QUERY, 'merge_policy': 'fill_text'})
        self.assertEqual(source, original)

    def test_header_overlap_still_rejects_ambiguous_labels(self):
        source = fixture()
        source['sheets'][0]['merges'] = [{'range': 'A1:B1'}, {'range': 'B1:C1'}]
        del source['sheets'][0]['cells']['B1']
        del source['sheets'][0]['cells']['C1']
        with self.assertRaisesRegex(ParseError, 'Overlapping header'):
            analyze(source, BASE_QUERY)

    def test_clipped_single_position_and_high_ooxml_merges(self):
        source = fixture()
        source['schema_version'] = 'ooxml_spreadsheet_readonly_v1'
        source['sheets'][0]['merges'] = [{'range': 'XFC100000:XFD100001'}]
        view = preview(source, {'sheet': 'Data', 'range': 'XFD100001'})
        self.assertEqual(view['rows'][0]['cells']['XFD']['merge_anchor'], 'XFC100000')


@unittest.skipIf(expat.version_info < (2, 6, 0), 'OOXML requires Expat >=2.6')
class XmlRecoveryTests(unittest.TestCase):
    def test_repeated_data_and_rows_keep_distinct_cells_and_first_conflict(self):
        sheet = (f'<worksheet xmlns="{T}"><sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData>'
                 '<sheetData><row r="1"><c r="A1"><v>999</v></c><c r="B1"><v>2</v></c></row>'
                 '<row r="2"><c r="A2"><v>3</v></c></row></sheetData></worksheet>')
        source = result(sheet_package(sheet))
        self.assertEqual({ref: item['value'] for ref, item in cells_of(source).items()}, {'A1': 1, 'B1': 2, 'A2': 3})
        self.assertTrue(source['omissions_affect_cell_values'])
        self.assertIn('duplicate_sheet_data_recovered', codes(source))
        self.assertIn('duplicate_cell_ignored', codes(source))
        self.assertTrue(source['reading_assessment']['cell_values']['omissions_affect_cell_values'])

    def test_unread_data_elements_and_missing_data_mark_uncertain(self):
        for contents in ('', '<sheetData><unexpected/></sheetData>',
                         '<sheetData><row r="1"><unexpected/></row></sheetData>'):
            with self.subTest(contents=contents):
                source = result(sheet_package(f'<worksheet xmlns="{T}">{contents}</worksheet>'))
                self.assertTrue(source['omissions_affect_cell_values'])


class OutputIntegrityTests(unittest.TestCase):
    def test_cli_summary_survives_many_optional_metadata_diagnostics(self):
        records = rec(0x208, b'bad') * 50 + rec(0x203, cell(0, 0) + struct.pack('<d', 17))
        raw = synthetic_cfb({'Workbook': workbook(sheet_records=[records])})
        with tempfile.TemporaryDirectory() as task:
            Path(task, 'source.et').write_bytes(raw)
            script = Path(__file__).resolve().parents[1] / 'et_cli.py'
            proc = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), 'run', 'source.et',
                                   '--summary', '--max-output-chars', '6000', '--max-output-bytes', '6000'],
                                  cwd=task, capture_output=True, timeout=20, check=False)
            self.assertEqual(proc.returncode, 3, proc.stderr + proc.stdout)
            self.assertEqual(proc.stderr, b'')
            view = json.loads(proc.stdout)
            self.assertEqual(view['total_items'], 1)
            self.assertTrue(view['diagnostic_summary']['details_omitted'])
            self.assertLessEqual(len(proc.stdout), 6000)

    def test_summary_diagnostic_overview_keeps_risk_and_explicit_omissions(self):
        source = fixture()
        source['omissions_affect_cell_values'] = True
        source['issues_truncated'] = True
        source['issues'] = [{'code': 'cell_omitted', 'cell': 'A' + str(i), 'message': 'x' * 1000} for i in range(1000)]
        original = deepcopy(source)
        view = project(source, b'original', {}, summary=True, max_chars=6000, max_bytes=6000)
        self.assertTrue(view['omissions_affect_cell_values'])
        self.assertEqual(view['status'], 'partial')
        self.assertEqual(view['diagnostic_summary']['fields']['issues']['source_entries'], 1000)
        self.assertTrue(view['diagnostic_summary']['details_omitted'])
        self.assertTrue(view['diagnostic_summary']['source_issues_truncated'])
        self.assertEqual(view['coverage'], 'overview_only_no_values_or_text')
        self.assertEqual(source, original)

    def test_file_manifest_hash_and_budget_cover_actual_bytes(self):
        source = fixture()
        payload = (encode_result(source) + '\n').encode('utf-8')
        with tempfile.TemporaryDirectory() as task:
            old = Path.cwd()
            try:
                os.chdir(task)
                manifest = write_result_file('full.json', source, limit=len(payload))
                actual = Path('full.json').read_bytes()
                self.assertEqual(actual, payload)
                self.assertEqual(manifest['sha256'], hashlib.sha256(actual).hexdigest())
                self.assertEqual(manifest['bytes'], len(actual))
                with self.assertRaises(ParseError):
                    write_result_file('too-small.json', source, limit=len(payload) - 1)
                self.assertFalse(Path('too-small.json').exists())
                self.assertEqual(json.loads(actual)['status'], source['status'])
            finally:
                os.chdir(old)


if __name__ == '__main__':
    unittest.main()
