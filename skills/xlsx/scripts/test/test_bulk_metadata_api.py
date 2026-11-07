"""Additive bulk conversion and metadata-only open preserve provider guarantees."""
from decimal import Decimal
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from xlsx_cli import Session, capabilities
from xlsx_cli_protocol import PROTOCOL, dumps, loads
from stdlib_xlsx import Workbook


def package(parts=None):
    book = Workbook.create({'sheets': [
        {'name': 'Z first', 'cells': {'A1': {'value': '001'}, 'B1': {'formula': '1+1'}}},
        {'name': 'A second', 'cells': {'A1': {'value': 7}}}]})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in (book.parts if parts is None else parts).items():
            archive.writestr(name, raw)
    return stream.getvalue()


class BulkMetadataTests(unittest.TestCase):
    def test_batch_exact_scalar_equivalence_and_limits(self):
        session = Session()
        cells = [{}, {'value': None}, {'value': ''}, {'value': '001'}, {'value': True},
                 {'value': Decimal('12345678901234567890.0000000001')},
                 {'value': '2026-11-07', 'type': 'd'},
                 {'value': Decimal('7'), 'formula': '1+6', 'cache_status': 'unverified'},
                 {'value': None, 'formula': 'A1', 'cache_status': 'missing'},
                 {'value': '#VALUE!', 'type': 'e', 'formula': 'A1', 'cache_status': 'error_unverified'}]
        cells += [cells[5]]
        expected = [session.execute('typed_value', {'cell': cell}) for cell in cells]
        self.assertEqual(session.execute('typed_values', {'cells': cells}), expected)
        self.assertEqual(loads(dumps(expected)), expected)
        self.assertEqual(session.execute('typed_values', {'cells': []}), [])
        self.assertEqual(len(session.execute('typed_values', {'cells': [{}] * 10000})), 10000)
        for params in (None, [], {}, {'cells': None}, {'cells': {}}, {'cells': (), 'extra': 1},
                       {'cells': [{}] * 10001}, {'cells': [{}, None]}, {'cells': [], 'extra': 1}):
            with self.subTest(params=str(params)[:80]), self.assertRaises(ValueError):
                session.execute('typed_values', params)

    def test_metadata_bypasses_view_and_preserves_open_diagnostics(self):
        raw = package()
        session = Session()
        original = session.execute('open', {'raw': raw, 'source': 'source.xlsx'})
        with patch.object(session, 'view', side_effect=AssertionError('full view forbidden')):
            metadata = session.execute('open_metadata', {'raw': raw, 'source': 'source.xlsx'})
        self.assertEqual(metadata['schema_version'], 'workbook_metadata_v1')
        self.assertEqual(metadata['sheet_names'], ['Z first', 'A second'])
        self.assertEqual(set(metadata), {'schema_version', 'handle', 'sheet_names', 'native',
                                        'source_status', 'source_issues', 'source_coverage', 'input_schema'})
        for key in ('native', 'source_status', 'source_issues', 'source_coverage', 'input_schema'):
            self.assertEqual(metadata[key], original[key])
        self.assertTrue(metadata['native'])
        self.assertEqual(session.execute('region', {'handle': metadata['handle'], 'sheet': 'A second',
                                                    'range': 'A1:A1', 'options': {'header': False}}),
                         session.execute('region', {'handle': original['handle'], 'sheet': 'A second',
                                                    'range': 'A1:A1', 'options': {'header': False}}))
        session.execute('release', {'handle': metadata['handle']})
        with self.assertRaises(ValueError):
            session.book(metadata['handle'])

    def test_metadata_rejects_corrupt_second_sheet_and_limits(self):
        raw = package()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        parts['xl/worksheets/sheet2.xml'] = b'<broken'
        session = Session()
        for operation in ('open', 'open_metadata'):
            with self.assertRaises(Exception):
                session.execute(operation, {'raw': package(parts), 'source': 'source.xlsx'})
        self.assertEqual(session.books, {})
        for params in ({'raw': raw}, {'raw': raw, 'source': 3}, {'raw': 'bad', 'source': 'x.xlsx'},
                       {'raw': raw, 'source': 'x.xlsx', 'sheet': 'Z first'}, None):
            with self.assertRaises(ValueError):
                session.execute('open_metadata', params)
        session.snapshot_bytes = 256 * 1024 * 1024
        with self.assertRaisesRegex(ValueError, 'budget'):
            session.execute('open_metadata', {'raw': raw, 'source': 'x.xlsx'})
        session.snapshot_bytes = 0
        session.books = {i: None for i in range(64)}
        with self.assertRaisesRegex(ValueError, '64'):
            session.execute('open_metadata', {'raw': raw, 'source': 'x.xlsx'})

    def test_wire_batch_error_has_no_partial_result_and_recovers(self):
        operations = [('typed_values', {'cells': [{'value': Decimal('0.10000000001')}, {'value': '001'}]}),
                      ('typed_values', {'cells': [{}, 1]}), ('typed_values', {'cells': []}),
                      ('open_metadata', {'raw': package(), 'source': 'x.xlsx'})]
        raw = b''.join(dumps({'protocol': PROTOCOL, 'id': i, 'operation': op, 'parameters': params}) + b'\n'
                       for i, (op, params) in enumerate(operations))
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'xlsx_cli.py'), 'serve'],
                               input=raw, capture_output=True)
        replies = [loads(line) for line in child.stdout.splitlines()]
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(replies[0]['result'], [('number', Decimal('0.10000000001')), ('text', '001')])
        self.assertFalse(replies[1]['ok'])
        self.assertNotIn('result', replies[1])
        self.assertEqual(replies[2]['result'], [])
        self.assertEqual(replies[3]['result']['sheet_names'], ['Z first', 'A second'])
        self.assertIn('typed_values', capabilities()['operations'])
        self.assertIn('open_metadata', capabilities()['operations'])


if __name__ == '__main__':
    unittest.main()
