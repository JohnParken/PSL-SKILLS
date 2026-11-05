"""Synthetic content-routing regressions; suffixes are not producer evidence."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from test_task_cli_support import run_task_cli
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_wps import read, encode_result, ParseError
from test_wps import document, synthetic_cfb


class RoutingTests(unittest.TestCase):
    def result(self, data, suffix='.wps', **options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ('sample' + suffix)
            path.write_bytes(data)
            return read(path, **options)

    def test_content_not_suffix(self):
        word = synthetic_cfb({path[0]: data for path, data in document().items()})
        sheet = synthetic_cfb({'Workbook': b'\t\x08\x10\x00\x00\x06\x05\x00\xbb\x01\xcc\x0c\x00\x00\x00\x00'})
        for suffix in ('.wps', '.et', '.ett', '.docx', '.xlsx', '.bin'):
            result = self.result(word, suffix)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
            result = self.result(sheet, suffix)
            self.assertEqual(result['status'], 'unsupported')

    def test_ambiguity_and_encryption(self):
        streams = {path[0]: data for path, data in document().items()}
        streams['Workbook'] = b'extra'
        self.assertEqual(self.result(synthetic_cfb(streams))['status'], 'unsupported')
        self.assertEqual(self.result(synthetic_cfb({'EncryptedPackage': b'data'}))['status'], 'encrypted')
        self.assertEqual(self.result(synthetic_cfb({'Workbook': b'data'}))['status'], 'unsupported')

    def test_unknown_and_branch_options(self):
        self.assertEqual(self.result(b'private unknown') ['status'], 'unsupported')
        self.assertEqual(self.result(synthetic_cfb({'Other': b'abc'}))['status'], 'unsupported')
        word = synthetic_cfb({path[0]: data for path, data in document().items()})
        self.assertEqual(self.result(word, revisions='accepted')['status'], 'unsupported')
        self.assertEqual(self.result(word, revisions='all')['status'], 'ok')
        sheet = synthetic_cfb({'Workbook': b'dummy'})
        self.assertEqual(self.result(sheet, include_related=True)['status'], 'unsupported')

    def test_serialization_budget_precedes_complete_output(self):
        with patch('read_wps.MAX_JSON_OUTPUT_BYTES', 32):
            with self.assertRaises(ParseError) as error:
                encode_result({'values': ['中文' * 8] * 4})
            self.assertEqual(error.exception.status, 'resource_limit')
            self.assertEqual(error.exception.location, {'stage': 'output'})

    def test_nonzero_word_clsid_has_scope_diagnostic(self):
        data = bytearray(synthetic_cfb({path[0]: value for path, value in document().items()}))
        data[8:24] = bytes.fromhex('0609020000000000c000000000000046')
        result = self.result(bytes(data), compatibility='strict')
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['location'], {'stream': 'CFB header', 'offset': 8})

    @unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
    def test_partial_text_cli_keeps_diagnostics(self):
        from test_wps_ooxml import package
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fields.wps'
            path.write_bytes(package('<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:t>1</w:t></w:r></w:p>'))
            process = run_task_cli([sys.executable, '-I', '-S', '-B',
                                      str((Path(__file__).parents[1] / 'read_wps.py')), str(path), '--format', 'text'],
                                     capture_output=True, text=True, timeout=10, env={'PATH': ''})
            self.assertEqual(process.returncode, 3)
            self.assertEqual(process.stdout, '1')
            self.assertTrue(json.loads(process.stderr)['issues'])

    def test_biff_text_cli_has_explicit_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'table.et'
            path.write_bytes(synthetic_cfb({'Workbook': b'dummy'}))
            process = run_task_cli([sys.executable, '-I', '-S', '-B',
                                      str((Path(__file__).parents[1] / 'read_wps.py')), str(path), '--format', 'text'],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(process.stdout, '')
            self.assertEqual(json.loads(process.stderr)['location']['stage'], 'routing')


if __name__ == '__main__':
    unittest.main()
