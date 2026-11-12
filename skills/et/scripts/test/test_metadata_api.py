"""Metadata projection keeps complete producer validation, status and diagnostics."""
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import et_cli
from et_cli_protocol import PROTOCOL, dumps, loads
from read_et import read_bytes
from test_wps_biff8 import workbook, formula, special, rec
from test_wps_cfb import synthetic_cfb
from wps_access import ParseError
from test_wps_ooxml_sheet import package


class MetadataTests(unittest.TestCase):
    def invoke(self, raw, command='metadata', params=None):
        request = {'protocol': PROTOCOL, 'id': 91, 'operation': command,
                   'parameters': {'raw': raw, 'source': 'source.et'} if params is None else params}
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/'et_cli.py'), command],
                               input=dumps(request), capture_output=True)
        self.assertEqual(child.stderr, b'')
        return child, loads(child.stdout.strip())

    def test_complete_and_partial_keep_names_order_and_diagnostics(self):
        for records in ([b'', b''], [formula(0, 0, special(0)), b'']):
            raw = synthetic_cfb({'Workbook': workbook(records, names=['Z first', 'A second'])})
            original = read_bytes(raw, source='source.et')
            child, response = self.invoke(raw)
            metadata = response['result']['metadata']
            self.assertEqual(metadata['sheet_names'], ['Z first', 'A second'])
            self.assertEqual(metadata['schema_version'], 'workbook_metadata_v1')
            self.assertIs(metadata['native'], False)
            self.assertEqual(metadata['input_schema'], original['schema_version'])
            self.assertEqual(metadata['source_status'], original['status'])
            self.assertEqual(metadata['source_issues'], original['issues'])
            self.assertEqual(metadata['source_coverage'], {key: original[key] for key in
                             ('cell_value_coverage', 'omissions_affect_cell_values', 'reading_assessment') if key in original})
            self.assertNotIn('cells', metadata)
            self.assertNotIn('merges', metadata)
            self.assertEqual(child.returncode, 3 if original['status'] == 'partial' else 0)
            self.assertEqual(response['result']['exit_code'], child.returncode)

    def test_unknown_schema_and_bad_sheet_names_fail_closed(self):
        original = read_bytes(synthetic_cfb({'Workbook': workbook([b''])}))
        for changes in ({'schema_version': 'wps_read_view_v1'}, {'biff_parsed': False},
                        {'status': 'failed'}, {'sheets': []}, {'sheets': [{'name': None}]},
                        {'sheets': [{'name': 'Same'}, {'name': 'Same'}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                et_cli.metadata({**original, **changes})
        projected = et_cli.metadata(original)
        projected['source_coverage']['reading_assessment'].clear()
        self.assertTrue(original['reading_assessment'])

    def test_invalid_container_nonselected_sheet_and_parameters_fail(self):
        child, response = self.invoke(b'unknown')
        self.assertEqual(child.returncode, 1)
        self.assertFalse(response['ok'])
        self.assertNotIn('result', response)
        # A malformed cell record is now a tolerated cell-level omission: metadata
        # is still available and the result is partial.
        child, response = self.invoke(synthetic_cfb({'Workbook': workbook([b'', rec(0x0203)])}))
        self.assertEqual(child.returncode, 3)
        self.assertTrue(response['ok'])
        self.assertEqual(response['result']['metadata']['source_status'], 'partial')
        for params in ({}, {'raw': b'', 'source': 1}, {'raw': '', 'source': 'x'},
                       {'raw': b'', 'source': 'x', 'sheet': 'first'}, []):
            child, response = self.invoke(b'', params=params)
            self.assertEqual(child.returncode, 1)
            self.assertFalse(response['ok'])

    def test_ooxml_metadata_preserves_missing_cache_partial(self):
        raw = package('<c r="A1"><f>1+1</f></c>')
        original = read_bytes(raw)
        child, response = self.invoke(raw)
        value = response['result']['metadata']
        self.assertEqual(child.returncode, 3)
        self.assertEqual(value['input_schema'], 'ooxml_spreadsheet_readonly_v1')
        self.assertEqual(value['source_issues'], original['issues'])
        self.assertEqual(value['sheet_names'], ['Data'])
        self.assertIs(value['native'], False)

    def test_projection_failure_is_error_exit_not_success_or_partial(self):
        raw = synthetic_cfb({'Workbook': workbook([b''])})
        request = dumps({'protocol': PROTOCOL, 'id': 1, 'operation': 'metadata',
                         'parameters': {'raw': raw, 'source': 'source.et'}})
        stdin = type('Input', (), {'buffer': io.BytesIO(request)})()
        stdout = type('Output', (), {'buffer': io.BytesIO()})()
        with patch.object(sys, 'argv', ['et_cli.py', 'metadata']), patch.object(sys, 'stdin', stdin), patch.object(sys, 'stdout', stdout):
            with patch.object(et_cli, 'metadata', side_effect=ValueError('unsupported schema')):
                code = et_cli.main()
        self.assertEqual(code, 1)
        self.assertFalse(loads(stdout.buffer.getvalue().strip())['ok'])

    def test_metadata_does_not_serialize_the_full_extraction(self):
        raw = synthetic_cfb({'Workbook': workbook([b''])})
        request = dumps({'protocol': PROTOCOL, 'id': 1, 'operation': 'metadata',
                         'parameters': {'raw': raw, 'source': 'source.et'}})
        stdin = type('Input', (), {'buffer': io.BytesIO(request)})()
        stdout = type('Output', (), {'buffer': io.BytesIO()})()
        with patch.object(sys, 'argv', ['et_cli.py', 'metadata']), patch.object(sys, 'stdin', stdin), patch.object(sys, 'stdout', stdout):
            with patch('wps_result.encode_result',
                       side_effect=ParseError('resource_limit', {'stage': 'output'}, 'budget')) as encode:
                code = et_cli.main()
        self.assertIn(code, (0, 3))
        self.assertEqual(encode.call_count, 0)
        response = loads(stdout.buffer.getvalue().strip())
        self.assertTrue(response['ok'])
        self.assertEqual(response['result']['metadata']['schema_version'], 'workbook_metadata_v1')


if __name__ == '__main__':
    unittest.main()
