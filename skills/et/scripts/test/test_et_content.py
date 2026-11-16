"""Synthetic content/default/full regressions, independent of private samples."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(Path(__file__).resolve().parent)]
from et_content import project_content, _blank_ranges
from read_et import read_bytes
from test_wps_biff8 import rec, workbook
from test_wps_cfb import synthetic_cfb
from test_wps_ooxml_sheet import package
from wps_access import ParseError
from wps_biff8 import Reader, parse_stream


class ContentProfileTests(unittest.TestCase):
    """Assert table semantics and parser refusals, not just output size."""

    def assert_values_preserved(self, full, content):
        self.assertEqual(content['schema_version'], 'et_content_v1')
        self.assertEqual(content['source_schema_version'], full['schema_version'])
        for key in ('status', 'partial',
                    'cell_value_coverage', 'omissions_affect_cell_values', 'sheet_coverage'):
            if key in full:
                self.assertEqual(content[key], full[key], key)
        self.assertEqual(len(full['sheets']), len(content['sheets']))
        for old, new in zip(full['sheets'], content['sheets']):
            self.assertEqual(old['name'], new['name'])
            blanks, ranges = _blank_ranges(old['cells'])
            self.assertEqual(set(old['cells']) - blanks, set(new['cells']))
            if blanks:
                self.assertEqual(new['stored_blank_cells'], {'count': len(blanks), 'ranges': ranges})
            for ref, cell in old['cells'].items():
                if ref in blanks:
                    continue
                for key in ('value', 'type', 'cache_status', 'numeric_interpretation', 'text_truncated'):
                    if key in cell:
                        # Numeric provenance may contain hex, but its meaning cannot change.
                        actual = new['cells'][ref][key]
                        if key != 'numeric_interpretation':
                            self.assertEqual(actual, cell[key], (ref, key))
                        else:
                            for meaning in ('status', 'kind', 'interpreted_value', 'display_text', 'date_system'):
                                self.assertEqual(actual.get(meaning), cell[key].get(meaning))
            if 'merges' in old:
                self.assertEqual(old['merges'], new['merges'])

    def test_biff_values_unknown_metadata_and_coverage(self):
        raw = synthetic_cfb({'Workbook': workbook(extra_global=rec(0x7777, b'opaque' * 1000))})
        full = read_bytes(raw)
        content = read_bytes(raw, profile='content')
        self.assert_values_preserved(full, content)
        self.assertIn('uninterpreted_metadata', full)
        self.assertNotIn('uninterpreted_metadata', content)
        self.assertNotIn('bof_raw_hex', content['sheets'][0])
        self.assertLess(len(json.dumps(content, default=str)), len(json.dumps(full, default=str)))

    def test_biff_retention_does_not_materialize_opaque_hex(self):
        stream = workbook(extra_global=rec(0x7777, b'payload'))
        full = parse_stream(stream)
        content = parse_stream(stream, retain_metadata_payloads=False)
        original = next(item for item in full['uninterpreted_metadata'] if item['record_id'] == '0x7777')
        omitted = next(item for item in content['uninterpreted_metadata'] if item['record_id'] == '0x7777')
        self.assertEqual(original['payload_hex'], b'payload'.hex())
        self.assertNotIn('payload_hex', omitted)
        self.assertEqual(omitted['payload_bytes'], 7)
        self.assertTrue(omitted['payload_omitted'])
        self.assertEqual(full['issues'], content['issues'])
        self.assertEqual(full['sheets'], content['sheets'])

    def test_drawing_chart_and_later_cells_survive_without_visual_evidence(self):
        from test_wps_embedded_charts import chart_workbook
        raw = synthetic_cfb({'Workbook': chart_workbook()})
        full = read_bytes(raw)
        content = read_bytes(raw, profile='content')
        self.assert_values_preserved(full, content)
        self.assertTrue(full['embedded_charts'])
        self.assertEqual(content['chart_gaps'][0]['count'], 1)
        self.assertNotIn('embedded_charts', content)
        self.assertNotIn('note_drawings', content)
        self.assertEqual(content['sheets'][0]['cells']['A2']['value'], 42)

    def test_biff5_core_still_uses_separate_schema_and_values(self):
        from test_wps_biff5 import workbook as old_workbook, label, number
        raw = synthetic_cfb({'Book': old_workbook(label(b'000123') + number())})
        full = read_bytes(raw)
        content = read_bytes(raw, profile='content')
        self.assert_values_preserved(full, content)
        self.assertEqual(content['source_schema_version'], 'biff5_readonly_v1')
        self.assertEqual(content['sheets'][0]['cells']['A1']['value'], '000123')

    def test_retention_preserves_budget_accounting_and_font_dependency(self):
        reader = Reader(workbook(), retain_metadata_payloads=False)
        reader._append_metadata('globals', 0, 0x31, 'Font', b'font')
        self.assertEqual(reader.metadata[0]['payload_hex'], b'font'.hex())
        reader._append_metadata('globals', 4, 0x7777, 'Unknown', b'opaque')
        self.assertEqual(reader.metadata_bytes, 10)
        self.assertNotIn('payload_hex', reader.metadata[1])

    def test_ooxml_text_numbers_formula_caches_and_merges(self):
        raw = package(cells='<c r="A1" t="s"><v>0</v></c><c r="B1"><v>12.50</v></c>'
                      '<c r="C1"><f>B1*2</f><v>25</v></c><c r="D1"><f>B1+1</f></c>',
                      sheet_extra='<mergeCells count="1"><mergeCell ref="A2:B2"/></mergeCells>')
        full = read_bytes(raw)
        content = read_bytes(raw, profile='content')
        self.assert_values_preserved(full, content)
        self.assertEqual(content['sheets'][0]['cells']['C1']['formula']['text'], 'B1*2')
        self.assertTrue(content['sheets'][0]['cells']['C1']['formula']['cache_may_be_stale'])
        self.assertNotIn('styles', content)
        self.assertNotIn('rows', content['sheets'][0])

    def test_text_leading_zero_and_coordinate_risk_survive(self):
        for raw in (b'id,name\n001,Alice\n002,Bob\n',
                    b'<table><tr><td colspan="2">001</td></tr><tr><td>x</td><td>y</td></tr></table>'):
            full = read_bytes(raw)
            content = read_bytes(raw, profile='content')
            self.assert_values_preserved(full, content)
            for old, new in zip(full['sheets'], content['sheets']):
                self.assertEqual(old.get('coordinates_verified'), new.get('coordinates_verified'))

    def test_projection_is_nonmutating_and_keeps_formula_warning(self):
        source = {'schema_version': 'biff8_readonly_v1', 'status': 'partial', 'partial': True,
                  'issues': [{'code': 'missing_cache'}], 'opaque_objects': [{'payload_hex': 'FF' * 500}],
                  'sheets': [{'name': 'Data', 'cells': {'A1': {'type': 'number', 'value': '1.25',
                              'cache_status': 'unverified', 'formula': {'tokens_hex': 'AA', 'cache_may_be_stale': True}}}}]}
        before = deepcopy(source)
        content = project_content(source)
        self.assertEqual(source, before)
        formula = content['sheets'][0]['cells']['A1']['formula']
        self.assertNotIn('tokens_hex', formula)
        self.assertTrue(formula['cache_may_be_stale'])
        self.assertEqual([x['code'] for x in content['issues']], [x['code'] for x in source['issues']])

    def test_security_refusals_and_invalid_profile(self):
        for profile in ('content', 'full'):
            with self.assertRaises(ParseError):
                read_bytes(b'not a workbook', profile=profile)
            raw = package(overrides={'xl/worksheets/sheet1.xml': '<!DOCTYPE x [<!ENTITY e "bad">]><x>&e;</x>'})
            result = read_bytes(raw, profile=profile)
            self.assertNotIn(result['status'], ('ok', 'partial'))
        with self.assertRaises(ParseError):
            read_bytes(b'x', profile='unknown')

    def test_cli_defaults_full_opt_in_file_and_cursor_binding(self):
        raw = synthetic_cfb({'Workbook': workbook()})
        with tempfile.TemporaryDirectory() as task:
            Path(task, 'input.et').write_bytes(raw)
            command = [sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'read_et.py'), 'input.et']
            def invoke(*args):
                run = subprocess.run(command + list(args), cwd=task, capture_output=True, text=True, check=False)
                self.assertIn(run.returncode, (0, 1, 3), run.stderr)
                return json.loads(run.stdout)
            self.assertEqual(invoke()['schema_version'], 'et_content_v1')
            self.assertEqual(invoke('--profile', 'full')['schema_version'], 'biff8_readonly_v1')
            manifest = invoke('--output', 'content.json')
            self.assertEqual(manifest['output_projection']['kind'], 'stored_table_content')
            self.assertEqual(json.loads(Path(task, 'content.json').read_text())['schema_version'], 'et_content_v1')
            first = invoke('--page-size', '1')
            self.assertTrue(first['has_more'])
            second = invoke('--page-size', '1', '--cursor', first['next_cursor'])
            self.assertEqual(second['start'], 1)
            rejected = invoke('--profile', 'full', '--page-size', '1', '--cursor', first['next_cursor'])
            self.assertEqual(rejected['status'], 'output_view_error')


if __name__ == '__main__':
    unittest.main()
