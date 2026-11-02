"""Synthetic projections preserve source partial, caches, precision and pagination."""
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import read_bytes
from wps_access import ParseError
from wps_read_view import project, encoded
from test_wps_cfb import synthetic_cfb
from test_wps_biff8 import workbook


def source(cells=None):
    return {'status': 'partial', 'partial': True, 'schema_version': 'biff8_readonly_v1',
            'issues': [{'code': 'test_omission'}], 'reading_assessment': {'completeness': 'partial'},
            'cell_value_coverage': 'known', 'omissions_affect_cell_values': False,
            'sheets': [{'name': 'Data', 'cells': {} if cells is None else cells, 'merges': []}]}


class WpsViewTests(unittest.TestCase):
    def test_partial_numeric_and_cache_provenance_preserved(self):
        cell = {'type': 'number', 'value': Decimal('12345678901234567890.125'),
                'formula': {'text': 'A1+1'}, 'cache_status': 'unverified', 'numeric_interpretation': {'kind': 'number'}}
        result = project(source({'A1': cell}), b'x', {})
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['issues'], [{'code': 'test_omission'}])
        self.assertEqual(result['reading_assessment'], {'completeness': 'partial'})
        self.assertEqual(result['items'][0]['data'], cell)
        self.assertIn('12345678901234567890.125', encoded(result))
        self.assertEqual(result['sheets'][0]['metadata_omitted'], ['merges'])

    def test_page_reassembly_and_source_options_binding(self):
        original = source({f'A{i}': {'type': 'text', 'value': f'000{i}'} for i in range(1, 20)})
        cursor = None
        refs = []
        while True:
            view = project(original, b'x', {'compatibility': 'data'}, page_size=3, cursor=cursor)
            refs.extend(x['cell'] for x in view['items'])
            cursor = view['next_cursor']
            if cursor is None:
                break
        self.assertEqual(refs, [f'A{i}' for i in range(1, 20)])
        token = project(original, b'x', {}, page_size=1)['next_cursor']
        for data, options in [(b'y', {}), (b'x', {'compatibility': 'strict'})]:
            with self.assertRaises(ParseError):
                project(original, data, options, page_size=1, cursor=token)
        for token in ['!', 'e30=', 'W10=']:
            with self.assertRaises(ParseError):
                project(original, b'x', {}, cursor=token)

    def test_main_text_reassembles_escaped_unicode_under_budget(self):
        text = '中文😀\n"\\' * 500
        original = {'status': 'ok', 'text': text, 'paragraphs': ['duplicate'], 'related_stories': [{'text': 'not in main body'}]}
        cursor = None
        parts = []
        while True:
            view = project(original, b'x', {}, page_size=1000, cursor=cursor, max_chars=1200)
            self.assertLessEqual(len(encoded(view)) + 1, 1200)
            self.assertIn('related_stories', view['metadata_omitted'])
            parts.append(view['text'])
            cursor = view['next_cursor']
            if cursor is None:
                break
        self.assertEqual(''.join(parts), text)

    def test_summary_empty_data_and_failed_source(self):
        view = project(source(), b'x', {}, summary=True)
        self.assertEqual(view['total_items'], 0)
        self.assertNotIn('items', view)
        self.assertEqual(project(source(), b'x', {})['returned_items'], 0)
        self.assertEqual(project({'status': 'ok', 'text': ''}, b'x', {})['text'], '')
        failed = {'status': 'corrupt', 'text': None}
        self.assertIs(project(failed, b'x', {}), failed)

    def test_large_cell_and_diagnostics_budget_fail_explicitly(self):
        for original in [source({'A1': {'value': 'x' * 10000}}), {**source(), 'issues': ['x' * 10000]}]:
            with self.assertRaises(ParseError):
                project(original, b'x', {}, max_chars=1000)
        for options in [{'page_size': 0}, {'max_chars': 0}, {'sheet': 'missing'}, {'summary': True, 'cursor': 'x'}]:
            with self.assertRaises(ParseError):
                project(source(), b'x', {}, **options)

    def test_cli_partial_exit_and_changed_source_cursor_rejection(self):
        import struct
        from test_wps_biff8 import rec, cell
        script = str(Path(__file__).with_name('read_wps.py'))
        def payload(value):
            body = b''.join(rec(0x203, cell(i, 0) + struct.pack('<d', value + i)) for i in range(2))
            return synthetic_cfb({'Workbook': workbook([body], extra_global=rec(0x42, struct.pack('<H', 936)))})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.et'
            path.write_bytes(payload(1))
            command = [sys.executable, '-I', '-S', '-B', script, './source.et', '--page-size', '1']
            first = subprocess.run(command, cwd=tmp, capture_output=True, text=True)
            self.assertEqual(first.returncode, 3)
            result = json.loads(first.stdout)
            self.assertEqual(result['status'], 'partial')
            self.assertIn('codepage_metadata_not_applied', [i['code'] for i in result['issues']])
            token = result['next_cursor']
            path.write_bytes(payload(9))
            child = subprocess.run(command + ['--cursor', token], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(child.returncode, 1)
            self.assertEqual(json.loads(child.stdout)['status'], 'output_view_error')

    def test_check_options_refused_and_schema_is_not_full_adapter_input(self):
        script = str(Path(__file__).with_name('read_wps.py'))
        child = subprocess.run([sys.executable, '-I', '-S', '-B', script, '--check', '--summary'], capture_output=True, text=True)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(child.stdout, '')
        self.assertEqual(project(source(), b'x', {})['schema_version'], 'wps_read_view_v1')

    def test_cli_full_compatibility_summary_failure_and_partial_exit(self):
        script = str(Path(__file__).with_name('read_wps.py'))
        raw = synthetic_cfb({'Workbook': workbook()})
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'source.et').write_bytes(raw)
            def run(*args):
                return subprocess.run([sys.executable, '-I', '-S', '-B', script, './source.et', *args], cwd=tmp, capture_output=True, text=True)
            child = run()
            self.assertIn(child.returncode, (0, 3))
            self.assertEqual(json.loads(child.stdout)['schema_version'], read_bytes(raw)['schema_version'])
            child = run('--summary')
            self.assertIn(child.returncode, (0, 3))
            self.assertEqual(json.loads(child.stdout)['schema_version'], 'wps_read_view_v1')
            child = run('--page-size', '0')
            self.assertEqual(child.returncode, 1)
            self.assertEqual(json.loads(child.stdout)['status'], 'output_view_error')
            child = run('--summary', '--include-related')
            self.assertEqual(child.returncode, 2)
            self.assertIn('main content only', child.stderr)


if __name__ == '__main__':
    unittest.main()
