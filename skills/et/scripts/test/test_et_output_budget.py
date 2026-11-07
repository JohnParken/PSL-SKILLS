"""UTF-8 stdout and continuation contracts, with independent size/coverage checks."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_et import main
from et_output_page import page
from et_table_analysis import analyze
from et_table_preview import preview
from test_et_table_analysis import BASE_QUERY, fixture, item
from test_wps_ooxml_sheet import package
from wps_access import ParseError
from wps_read_view import encoded, project


def size(value):
    return len((json.dumps(value, ensure_ascii=False, default=str, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8'))


class OutputBudgetTests(unittest.TestCase):
    def test_read_bytes_bound_unicode_and_preserve_every_value(self):
        source = fixture()
        source['sheets'][0]['cells'] = {f'A{i}': item('text', '😀中"\\\n' * 40) for i in range(1, 31)}
        cursor, collected = None, []
        while True:
            result = project(source, b'original', {}, page_size=10, max_chars=10000, max_bytes=2000, cursor=cursor)
            self.assertLessEqual(size(result), 2000)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['has_more'], result['next_cursor'] is not None)
            collected.extend(result['items'])
            cursor = result['next_cursor']
            if cursor is None:
                break
        self.assertEqual([value['cell'] for value in collected], [f'A{i}' for i in range(1, 31)])
        self.assertTrue(all(value['data']['value'] == '😀中"\\\n' * 40 for value in collected))

    def test_terminal_cursor_disappearance_cells_and_text(self):
        for source in ({'status': 'ok', 'text': '😀'},
                       {'status': 'ok', 'sheets': [{'name': 'Data', 'cells': {'A1': item('text', '😀')}}]}):
            expected = project(source, b'x', {}, max_chars=10000)
            budget = max(512, size(expected))
            actual = project(source, b'x', {}, max_chars=10000, max_bytes=budget)
            self.assertEqual(actual, expected)
            self.assertIsNone(actual['next_cursor'])
            self.assertLessEqual(size(actual), budget)

    def test_read_budget_and_scope_changes(self):
        source = fixture()
        first = project(source, b'x', {}, page_size=1, max_bytes=3000)
        # Changing only byte budget is allowed.
        project(source, b'x', {}, page_size=1, max_bytes=4000, cursor=first['next_cursor'])
        for data, options, count in [(b'y', {}, 1), (b'x', {'compatibility': 'strict'}, 1), (b'x', {}, 2)]:
            with self.assertRaises(ParseError):
                project(source, data, options, page_size=count, max_bytes=4000, cursor=first['next_cursor'])
        for budget in (0, 511, True, 16777217):
            with self.assertRaises(ParseError):
                project(source, b'x', {}, max_bytes=budget)

    def test_read_long_cell_and_diagnostics_refuse_without_clipping(self):
        for field in ('cell', 'diagnostics'):
            source = fixture()
            if field == 'cell':
                source['sheets'][0]['cells'] = {'A1': item('text', '😀' * 10000)}
            else:
                source['diagnostics'] = ['😀' * 10000]
            with self.assertRaisesRegex(ParseError, 'budget'):
                project(source, b'x', {}, max_chars=100000, max_bytes=2000)

    def test_analysis_all_pages_reassemble_beyond_old_limit_and_preserve_statistics(self):
        query = {**BASE_QUERY, 'limit': 2, 'group_by': ['A'], 'group_limit': 1,
                 'aggregates': [{'op': 'sum', 'column': 'B', 'as': 'amount'}]}
        expected = analyze(fixture(), {**query, 'limit': 100, 'group_limit': 100})
        cursor, rows, groups = None, [], []
        while True:
            result = page(fixture(), b'original', query, max_bytes=30000, cursor=cursor)
            self.assertLessEqual(size(result), 30000)
            for key in ('profiles', 'totals', 'quality_report', 'row_counts', 'source', 'partial', 'statistics_scope'):
                self.assertEqual(result[key], expected[key], key)
            self.assertEqual(result['query'], query)
            rows.extend(result['rows'])
            groups.extend(result['groups'])
            cursor = result['next_cursor']
            if cursor is None:
                break
        self.assertEqual(rows, expected['rows'])
        self.assertEqual(groups, expected['groups'])
        self.assertEqual(len(rows), 12)
        self.assertEqual(len(groups), 2)

    def test_budget_reduces_rows_only_not_formula_denominators(self):
        source = fixture()
        for row in range(2, 14):
            source['sheets'][0]['cells'][f'C{row}'] = item('text', '😀' * 2000)
        query = {**BASE_QUERY, 'formula_policy': 'cached'}
        first = page(source, b'x', query, max_bytes=25000)
        self.assertGreater(len(first['rows']), 0)
        self.assertLess(len(first['rows']), 12)
        complete = analyze(source, query)
        self.assertEqual(first['quality_report'], complete['quality_report'])
        self.assertEqual(first['profiles']['B']['formula_caches_used'], complete['profiles']['B']['formula_caches_used'])
        self.assertLessEqual(size(first), 25000)

    def test_analysis_cursor_binds_source_query_and_mode(self):
        query = {**BASE_QUERY, 'limit': 1}
        token = page(fixture(), b'x', query, max_bytes=30000)['next_cursor']
        for data, changed, mode in [(b'y', query, False), (b'x', {**query, 'limit': 2}, False),
                                    (b'x', {**query, 'formula_policy': 'cached'}, False), (b'x', query, True)]:
            with self.assertRaises(ParseError):
                page(fixture(), data, changed, is_preview=mode, max_bytes=30000, cursor=token)
        for bad in ('!', 'e30=', 'W10=', token[:-1]):
            with self.assertRaises(ParseError):
                page(fixture(), b'x', query, max_bytes=30000, cursor=bad)

    def test_zero_limit_and_offset_coverage(self):
        query = {**BASE_QUERY, 'limit': 0, 'group_limit': 0}
        result = page(fixture(), b'x', query, max_bytes=30000)
        self.assertEqual(result['rows'], [])
        self.assertFalse(result['has_more'])
        self.assertEqual(result['coverage']['rows']['scope'], 'not_requested')
        self.assertEqual(result['profiles'], analyze(fixture(), BASE_QUERY)['profiles'])
        result = page(fixture(), b'x', {**BASE_QUERY, 'offset': 10}, max_bytes=30000)
        self.assertEqual([row['source_row'] for row in result['rows']], [12, 13])
        self.assertEqual(result['coverage']['rows']['from_offset'], 10)
        self.assertFalse(result['has_more'])
        self.assertTrue(result['row_page']['truncated'])
        with self.assertRaisesRegex(ParseError, 'offset'):
            page(fixture(), b'x', {**BASE_QUERY, 'offset': 13}, max_bytes=30000)
        # Old default API remains permissive and stats-only still works.
        self.assertEqual(analyze(fixture(), {**BASE_QUERY, 'offset': 13})['rows'], [])

    def test_analysis_long_metadata_and_single_row_fail(self):
        source = fixture()
        source['diagnostics'] = ['😀' * 10000]
        with self.assertRaisesRegex(ParseError, 'metadata'):
            page(source, b'x', BASE_QUERY, max_bytes=20000)
        source = fixture()
        source['sheets'][0]['cells']['C2'] = item('text', '😀' * 10000)
        with self.assertRaisesRegex(ParseError, 'single result row/group'):
            page(source, b'x', BASE_QUERY, max_bytes=20000)

    def test_preview_pages_have_exact_coverage_and_preserve_merges(self):
        source = fixture()
        query = {'sheet': 'Data', 'range': 'A1:C13', 'limit': 2}
        expected = preview(source, {**query, 'limit': 50})
        cursor, rows = None, []
        while True:
            result = page(source, b'x', query, is_preview=True, max_bytes=4000, cursor=cursor)
            self.assertLessEqual(size(result), 4000)
            self.assertEqual(result['scope']['selected_positions'], 39)
            self.assertEqual(result['scope']['returned_positions'], len(result['rows']) * 3)
            self.assertFalse(result['statistics_computed'])
            rows.extend(result['rows'])
            cursor = result['next_cursor']
            if cursor is None:
                break
        self.assertEqual(rows, expected['rows'])

    def test_analysis_terminal_page_exact_budget_includes_newline(self):
        query = {'sheet': 'Data', 'range': 'A1:A1'}
        expected = page(fixture(), b'x', query, is_preview=True, max_bytes=30000)
        budget = size(expected)
        result = page(fixture(), b'x', query, is_preview=True, max_bytes=budget)
        self.assertEqual(result, expected)
        self.assertEqual(size(result), budget)
        with self.assertRaises(ParseError):
            page(fixture(), b'x', query, is_preview=True, max_bytes=budget - 1)

    def test_group_only_long_keys_fail_and_groups_continue_after_rows(self):
        source = fixture()
        source['sheets'][0]['cells']['C2'] = item('text', '😀' * 10000)
        with self.assertRaisesRegex(ParseError, 'single result row/group'):
            page(source, b'x', {**BASE_QUERY, 'limit': 0, 'group_by': ['C']}, max_bytes=20000)
        query = {**BASE_QUERY, 'limit': 0, 'group_by': ['A'], 'group_limit': 1}
        first = page(fixture(), b'x', query, max_bytes=30000)
        self.assertEqual(len(first['groups']), 1)
        self.assertFalse(first['row_page']['has_more'])
        second = page(fixture(), b'x', query, max_bytes=30000, cursor=first['next_cursor'])
        self.assertEqual(len(second['groups']), 1)
        self.assertFalse(second['has_more'])
        self.assertNotEqual(first['groups'], second['groups'])

    def test_wide_preview_row_is_not_silently_split(self):
        with self.assertRaisesRegex(ParseError, 'single result row/group'):
            page(fixture(), b'x', {'sheet': 'Data', 'range': 'A1:IV1'}, is_preview=True, max_bytes=10000)
        with self.assertRaises(ParseError) as error:
            page(fixture(), b'x', {'sheet': 'Data', 'range': 'A1:IV50', 'limit': 50}, is_preview=True, max_bytes=10000)
        self.assertEqual(error.exception.status, 'resource_limit')

    def test_budget_trimmed_preview_recomputes_merge_anchor_presence(self):
        source = fixture()
        source['sheets'][0]['merges'] = [{'range': 'A2:A3'}]
        query = {'sheet': 'Data', 'range': 'A1:C3', 'limit': 3}
        expected = page(source, b'x', {**query, 'limit': 1}, is_preview=True, max_bytes=10000)
        # Query echo has equal serialized width (1 vs 3), same cursor token length.
        first = page(source, b'x', query, is_preview=True, max_bytes=size(expected))
        self.assertEqual(len(first['rows']), 1)
        self.assertFalse(first['merges'][0]['anchor_in_page'])
        self.assertEqual(first['scope']['returned_positions'], 3)
        second = page(source, b'x', query, is_preview=True, max_bytes=10000, cursor=first['next_cursor'])
        self.assertTrue(second['merges'][0]['anchor_in_page'])
        self.assertEqual(second['scope']['returned_positions'], 6)

    def test_cli_stdout_bytes_errors_defaults_and_standalone_install(self):
        raw = package('<c r="A1" t="inlineStr"><is><t>😀中文</t></is></c>')
        with tempfile.TemporaryDirectory(prefix='.et-budget-test-', dir=Path.cwd()) as directory:
            work = Path(directory)
            install = work / 'isolated-et'
            shutil.copytree(Path(__file__).resolve().parents[1], install, ignore=shutil.ignore_patterns('test', '__pycache__'))
            (work / 'input.et').write_bytes(raw)
            for script, arguments in [('read_et.py', ['--max-output-bytes', '3000']),
                                      ('analyze_et.py', ['--sheet', 'Data', '--range', 'A1:A1', '--preview', '--max-output-bytes', '3000'])]:
                proc = subprocess.run([sys.executable, '-I', '-S', '-B', str(install / script), './input.et', *arguments],
                                      cwd=work, capture_output=True, check=False)
                self.assertIn(proc.returncode, (0, 3), proc.stderr + proc.stdout)
                self.assertLessEqual(len(proc.stdout), 3000)
                self.assertTrue(proc.stdout.endswith(b'\n'))
                self.assertEqual(json.loads(proc.stdout)['source_sha256'], hashlib.sha256(raw).hexdigest())
                argv = [str(install / script), './input.et', *arguments]
                wrapper = ("import runpy,sys; sys.stdout.reconfigure(encoding='ascii', newline='\\r\\n'); "
                           + 'sys.argv=' + repr(argv) + '; runpy.run_path(sys.argv[0], run_name="__main__")')
                translated = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', wrapper], cwd=work, capture_output=True, check=False)
                self.assertEqual(translated.returncode, proc.returncode, translated.stderr)
                self.assertEqual(translated.stdout, proc.stdout)

            command = [sys.executable, '-I', '-S', '-B', str(install / 'analyze_et.py'), './input.et', '--sheet', 'Data', '--range', 'A1:A1', '--preview']
            plain = subprocess.run(command, cwd=work, capture_output=True, check=False)
            self.assertNotIn('next_cursor', json.loads(plain.stdout))
            too_small = subprocess.run(command + ['--max-output-bytes', '512'], cwd=work, capture_output=True, check=False)
            self.assertEqual(too_small.returncode, 1)
            self.assertLessEqual(len(too_small.stdout), 512)
            self.assertEqual(json.loads(too_small.stdout)['status'], 'output_view_error')

    def test_cli_preserves_long_failure_as_explicit_budget_failure(self):
        output = io.StringIO()
        with patch('analyze_et.read_input', return_value=b'x'), patch('analyze_et.read_bytes', side_effect=OSError('😀' * 10000)):
            with redirect_stdout(output):
                code = main(['./x.et', '--sheet', 'Data', '--range', 'A1', '--preview', '--max-output-bytes', '512'])
        self.assertEqual(code, 1)
        self.assertLessEqual(len(output.getvalue().encode('utf-8')), 512)
        self.assertEqual(json.loads(output.getvalue())['status'], 'output_view_error')


if __name__ == '__main__':
    unittest.main()
