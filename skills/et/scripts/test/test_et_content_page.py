"""Bounded content pages preserve explicit values, blanks and chart semantics."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(Path(__file__).resolve().parent)]
from et_content_page import project
from et_read_view import project as overview
from read_et import read_bytes
from test_et_visible_content import chart_package
from test_wps_ooxml_sheet import package
from wps_access import ParseError


def content():
    """An independently specified content result with all pagination item kinds."""
    return {
        'schema_version': 'et_content_v1', 'source_schema_version': 'ooxml_spreadsheet_readonly_v1',
        'status': 'partial', 'partial': True, 'issues': [{'code': 'source_gap', 'count': 7}],
        'cell_value_coverage': 'unknown', 'omissions_affect_cell_values': True,
        'content_scope': {'not_rendered': True},
        'sheets': [
            {'name': 'First', 'hidden_state': 'hidden',
             'cells': {'B2': {'type': 'text', 'value': '001'}, 'A1': {'type': 'number', 'value': '1.2500'}},
             'stored_blank_cells': {'count': 100, 'ranges': ['C1:C100']},
             'merges': ['A3:B3'], 'comments': [{'cell': 'A1', 'text': 'comment'}],
             'hyperlinks': [{'cell': 'B2', 'display': 'link', 'followed': False}],
             'charts': [{'title': {'text': 'Saved chart'}, 'empty': [], 'unknown': {},
                         'series': [{'name': {'text': 'Series'}, 'points': [{'index': n, 'value': str(n)} for n in range(40)]}]}]},
            {'name': 'Second', 'cells': {'A1': {'type': 'text', 'value': 'last sheet'}}},
            {'name': 'Empty', 'cells': {}},
        ],
    }


class ContentPageTests(unittest.TestCase):
    """No source parse coverage is inferred from finishing an output cursor."""

    def pages(self, source, **kwargs):
        cursor = None
        found = []
        expected_start = 0
        for _ in range(1000):
            page = project(source, b'source', {'profile': 'content'}, cursor=cursor, **kwargs)
            self.assertEqual(page['schema_version'], 'et_content_page_v1')
            self.assertEqual(page['start'], expected_start)
            self.assertEqual(page['returned_items'], len(page['items']))
            self.assertEqual(page['status'], source['status'])
            self.assertEqual(page['partial'], source['partial'])
            self.assertEqual(page['omissions_affect_cell_values'], source['omissions_affect_cell_values'])
            self.assertLessEqual(len((json.dumps(page, ensure_ascii=False, default=str, separators=(',', ':')) + '\n').encode()), kwargs.get('max_bytes', 32768))
            found.append(page)
            expected_start += len(page['items'])
            if not page['has_more']:
                self.assertIsNone(page['next_cursor'])
                self.assertEqual(expected_start, page['total_items'])
                return found
            self.assertGreater(page['returned_items'], 0)
            self.assertNotEqual(cursor, page['next_cursor'])
            cursor = page['next_cursor']
        self.fail('cursor did not terminate')

    def test_all_sheets_values_blanks_annotations_and_hidden_scope(self):
        source = content()
        before = deepcopy(source)
        pages = self.pages(source, page_size=3)
        self.assertGreater(len(pages), 1)
        items = [item for page in pages for item in page['items']]
        cells = [item for item in items if item['kind'] == 'cell']
        self.assertEqual([(item['sheet'], item['cell'], item['data']['value']) for item in cells],
                         [('First', 'A1', '1.2500'), ('First', 'B2', '001'), ('Second', 'A1', 'last sheet')])
        self.assertEqual(sum(item['kind'] == 'worksheet' for item in items), 3)
        for kind in ('blank_range', 'merge', 'comment', 'hyperlink'):
            self.assertEqual(sum(item['kind'] == kind for item in items), 1)
        self.assertEqual(pages[0]['sheets'][0]['stored_cells'], 102)
        self.assertEqual([sheet['name'] for sheet in pages[0]['sheets']], ['First', 'Second', 'Empty'])
        self.assertEqual(source, before)

    def test_chart_fragments_are_unique_and_all_paths_preserve_source(self):
        source = content()
        pages = self.pages(source, page_size=2)
        fragments = [item for page in pages for item in page['items'] if item['kind'] == 'chart_fragment']
        self.assertGreater(len(fragments), 3)
        paths = set()
        scalar_points = []
        for item in fragments:
            path = tuple(item['path'])
            self.assertNotIn(path, paths)
            paths.add(path)
            original = source['sheets'][0]['charts'][item['chart_index']]
            for key in path:
                original = original[key]
            self.assertEqual(item['data'], original)
            if isinstance(original, dict) and 'index' in original and 'value' in original:
                scalar_points.append(original['index'])
        self.assertEqual(sorted(scalar_points), list(range(40)))
        self.assertIn(('empty',), paths)
        self.assertIn(('unknown',), paths)

    def test_utf8_budget_and_selected_sheet(self):
        pages = self.pages(content(), page_size=100, max_bytes=5000, max_chars=5000)
        self.assertGreater(len(pages), 1)
        selected = self.pages(content(), sheet='Second', page_size=1)
        items = [item for page in selected for item in page['items']]
        self.assertTrue(all(item['sheet'] == 'Second' for item in items))
        self.assertEqual(sum(item['kind'] == 'cell' for item in items), 1)

    def test_cursor_rejects_changed_bytes_and_scope(self):
        source = content()
        first = project(source, b'source', {'profile': 'content'}, page_size=2)
        for raw, options, kwargs in [(b'changed', {'profile': 'content'}, {}),
                                      (b'source', {'profile': 'full'}, {}),
                                      (b'source', {'profile': 'content'}, {'sheet': 'Second'})]:
            with self.assertRaises(ParseError):
                project(source, raw, options, page_size=2, cursor=first['next_cursor'], **kwargs)

    def test_oversized_single_value_is_explicit_failure(self):
        source = content()
        source['sheets'] = [{'name': 'Huge', 'cells': {'A1': {'type': 'text', 'value': '中文' * 100000}}}]
        first = project(source, b'source', {}, page_size=1)
        self.assertTrue(first['has_more'])
        with self.assertRaises(ParseError):
            project(source, b'source', {}, page_size=1, cursor=first['next_cursor'])

    def test_summary_counts_compressed_blanks_without_expansion(self):
        source = content()
        value = overview(source, b'source', {'profile': 'content'}, summary=True, max_chars=32768)
        self.assertEqual(value['sheets'][0]['stored_cells'], 102)
        self.assertEqual(value['total_items'], 103)

    def test_real_parser_chart_content_and_api_default_unchanged(self):
        source = read_bytes(chart_package(), profile='content')
        self.assertEqual(read_bytes(package())['schema_version'], 'ooxml_spreadsheet_readonly_v1')
        pages = self.pages(source, page_size=2)
        fragments = [item for page in pages for item in page['items'] if item['kind'] == 'chart_fragment']
        self.assertTrue(fragments)
        serialized = json.dumps(fragments, ensure_ascii=False)
        for text in ('收入', '销售额', '第一季', '第三季', '1.2500'):
            self.assertIn(text, serialized)

    def test_cli_default_bounded_and_explicit_compatibility_routes(self):
        raw = package(''.join(f'<c r="A{i}" t="inlineStr"><is><t>row{i}</t></is></c>' for i in range(1, 501)))
        with tempfile.TemporaryDirectory() as task:
            Path(task, 'input.et').write_bytes(raw)
            command = [sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'et_cli.py'), 'run', 'input.et']
            def invoke(*args):
                run = subprocess.run(command + list(args), cwd=task, capture_output=True, check=False)
                self.assertIn(run.returncode, (0, 3), run.stderr)
                return json.loads(run.stdout), len(run.stdout)
            first, size = invoke()
            self.assertEqual(first['schema_version'], 'et_content_page_v1')
            self.assertLessEqual(size, 32768)
            self.assertTrue(first['has_more'])
            second, _ = invoke('--content-page', '--cursor', first['next_cursor'])
            self.assertEqual(second['start'], first['returned_items'])
            self.assertEqual(invoke('--all-content')[0]['schema_version'], 'et_content_v1')
            self.assertEqual(invoke('--profile', 'full')[0]['schema_version'], 'ooxml_spreadsheet_readonly_v1')
            self.assertEqual(invoke('--page-size', '1')[0]['schema_version'], 'wps_read_view_v1')
            self.assertEqual(invoke('--output', 'result.json')[0]['schema_version'], 'et_output_manifest_v1')
            self.assertEqual(json.loads(Path(task, 'result.json').read_text())['schema_version'], 'et_content_v1')


if __name__ == '__main__':
    unittest.main()
