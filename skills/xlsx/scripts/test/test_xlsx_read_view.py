"""Bounded cell views: independent expected coordinates, values and CLI compatibility."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stdlib_xlsx import Workbook
from xlsx_read_view import read_view, snapshot, encode


class ReadViewTests(unittest.TestCase):
    def book(self, cells=None):
        return Workbook.create({'sheets': [{'name': 'Data', 'cells': {} if cells is None else cells},
                                           {'name': 'Empty', 'cells': {}}]})

    def test_pages_reassemble_row_major_with_exact_values(self):
        cells = {'B20': {'value': '000123'}, 'A10': {'value': 10}, 'C1': {'value': '中文'}}
        book = self.book(cells)
        cursor = None
        found = []
        while True:
            result = read_view(book, b'fixed', page_size=1, cursor=cursor)
            self.assertLessEqual(len(encode(result)) + 1, 12000)
            found.extend((x['cell'], x['data']['value']) for x in result['items'])
            cursor = result['next_cursor']
            if not result['truncated']:
                break
        self.assertEqual(found, [('C1', '中文'), ('A10', 10), ('B20', '000123')])

    def test_summary_and_empty_sheet(self):
        book = self.book({'A1': {'value': 'secret value'}})
        summary = read_view(book, b'x', summary=True)
        self.assertNotIn('secret value', encode(summary))
        empty = read_view(book, b'x', sheet='Empty')
        self.assertEqual(empty['total_items'], 0)
        self.assertIsNone(empty['next_cursor'])
        self.assertFalse(empty['truncated'])

    def test_range_and_sparse_wide_sheet(self):
        book = self.book({'A1': {'value': 'omit'}, 'B2': {'value': 2}, 'XFD1048576': {'value': 'last'}})
        result = read_view(book, b'x', sheet='Data', cell_range='B2:XFD1048576')
        self.assertEqual([x['cell'] for x in result['items']], ['B2', 'XFD1048576'])

    def test_changed_input_options_and_malformed_cursor_rejected(self):
        book = self.book({'A1': {'value': 1}, 'A2': {'value': 2}})
        token = read_view(book, b'x', page_size=1)['next_cursor']
        for args in ({'data': b'y', 'page_size': 1, 'cursor': token},
                     {'data': b'x', 'page_size': 2, 'cursor': token},
                     {'data': b'x', 'page_size': 1, 'sheet': 'Data', 'cursor': token},
                     {'data': b'x', 'cursor': ''}, {'data': b'x', 'cursor': '!'}, {'data': b'x', 'cursor': 'e30='}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                read_view(book, **args)

    def test_large_cell_and_bad_options_fail_without_partial_json(self):
        book = self.book({'A1': {'value': '中' * 5000}})
        for options in ({'max_chars': 1000}, {'page_size': 0}, {'page_size': 10001}, {'max_chars': 0},
                        {'cell_range': 'A1:B2'}, {'sheet': 'missing'}, {'summary': True, 'cursor': 'x'},
                        {'sheet': 'Data', 'cell_range': 'B2:A1'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                read_view(book, b'x', **options)
        self.assertEqual(len(read_view(book, b'x', max_chars=10000)['items']), 1)

    def test_budget_paging_has_progress_and_no_missing_cells(self):
        book = self.book({f'A{i}': {'value': 'x' * 120} for i in range(1, 31)})
        cursor = None
        refs = []
        while True:
            view = read_view(book, b'x', cursor=cursor, max_chars=1500)
            self.assertLessEqual(len(encode(view)) + 1, 1500)
            refs.extend(x['cell'] for x in view['items'])
            cursor = view['next_cursor']
            if cursor is None:
                break
        self.assertEqual(refs, [f'A{i}' for i in range(1, 31)])

    def test_snapshot_and_from_bytes_retain_package_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.xlsx'
            self.book({'A1': {'value': 7}}).save(path)
            data = snapshot(path)
            self.assertEqual(Workbook.load(path).inspect(), Workbook.from_bytes(data, path).inspect())
            path.write_bytes(b'changed')
            self.assertEqual(Workbook.from_bytes(data, 'old.xlsx').cells['Data']['A1']['value'], 7)
        for data in (b'invalid', bytes.fromhex('d0cf11e0a1b11ae1')):
            with self.assertRaises((ValueError, zipfile.BadZipFile)):
                Workbook.from_bytes(data, 'file.xlsx')

    def test_snapshot_resource_and_changed_file_detection(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input.xlsx'
            path.write_bytes(b'abc')
            with patch('xlsx_read_view.MAX_SOURCE_BYTES', 2), self.assertRaises(ValueError):
                snapshot(path)
            before = SimpleNamespace(st_mode=0o100600, st_size=3, st_mtime_ns=1, st_ctime_ns=1)
            after = SimpleNamespace(st_mode=0o100600, st_size=3, st_mtime_ns=2, st_ctime_ns=2)
            with patch('xlsx_read_view.os.fstat', side_effect=[before, after]), self.assertRaises(ValueError):
                snapshot(path)

    def test_cli_changed_file_rejects_cursor_and_csv_view_is_explicit(self):
        script = str((Path(__file__).resolve().parents[1] / 'stdlib_xlsx.py'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.xlsx'
            self.book({'A1': {'value': 1}, 'A2': {'value': 2}}).save(path)
            command = [sys.executable, '-I', '-S', '-B', script, 'read', './source.xlsx', '--page-size', '1']
            first = subprocess.run(command, cwd=tmp, capture_output=True, text=True)
            token = json.loads(first.stdout)['next_cursor']
            replacement = Path(tmp) / 'replacement.xlsx'
            self.book({'A1': {'value': 9}, 'A2': {'value': 2}}).save(replacement)
            replacement.replace(path)
            child = subprocess.run(command + ['--cursor', token], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(child.returncode, 1)
            self.assertIn('changed', json.loads(child.stdout)['message'])
            (Path(tmp) / 'data.csv').write_text('a,b\n1,2\n', encoding='utf-8')
            child = subprocess.run([sys.executable, '-I', '-S', '-B', script, 'read', './data.csv', '--summary'],
                                   cwd=tmp, capture_output=True, text=True)
            self.assertEqual(child.returncode, 1)
            self.assertIn('CSV/TSV', json.loads(child.stdout)['message'])

    def test_cli_defaults_unchanged_opt_in_valid_and_failures_json(self):
        script = str((Path(__file__).resolve().parents[1] / 'stdlib_xlsx.py'))
        with tempfile.TemporaryDirectory() as tmp:
            self.book({'A1': {'value': '00001'}}).save(Path(tmp) / 'source.xlsx')
            def run(*args):
                child = subprocess.run([sys.executable, '-I', '-S', '-B', script, 'read', *args], cwd=tmp, capture_output=True, text=True)
                return child, json.loads(child.stdout)
            child, full = run('./source.xlsx')
            self.assertEqual(child.returncode, 0)
            self.assertNotIn('schema_version', full)
            child, summary = run('./source.xlsx', '--summary')
            self.assertEqual(child.returncode, 0)
            self.assertEqual(summary['schema_version'], 'xlsx_read_view_v1')
            child, failed = run('./source.xlsx', '--page-size', '0')
            self.assertEqual(child.returncode, 1)
            self.assertEqual(failed['status'], 'failed')
            child, failed = run('./missing.xlsx', '--summary')
            self.assertEqual(child.returncode, 1)
            self.assertFalse(failed['output_written'])


if __name__ == '__main__':
    unittest.main()
