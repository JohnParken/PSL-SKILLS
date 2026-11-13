"""Final ET JSON delivery is explicit, verified and independently retryable."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(SCRIPTS)]
from test_wps_biff8 import workbook
from test_wps_cfb import synthetic_cfb
import read_et


class FinalDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='et-final-delivery-')
        self.task = Path(self.directory.name)
        self.source = synthetic_cfb({'Workbook': workbook()})
        (self.task / 'source.et').write_bytes(self.source)

    def tearDown(self):
        self.directory.cleanup()

    def call(self, arguments, question_id=None, script='read_et.py'):
        env = dict(os.environ)
        env.pop('ROUTER_CONFIG_VARIABLES', None)
        if question_id is not None:
            env['ROUTER_CONFIG_VARIABLES'] = json.dumps({'commonParam': [
                {'name': 'questionId', 'value': question_id}]})
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / script), *arguments],
                              cwd=self.task, env=env, capture_output=True, text=True, timeout=30)

    def test_intermediate_file_never_archived_and_final_partial_retained(self):
        internal = self.call(['source.et', '--output', 'internal.json'], 'one')
        self.assertIn(internal.returncode, (0, 3), internal.stderr)
        self.assertNotIn('delivery', json.loads(internal.stdout))
        self.assertFalse((self.task / 'output').exists())
        final = self.call(['source.et', '--output', 'final.json', '--delivery', '--compatibility', 'strict'], 'one')
        self.assertEqual(final.returncode, 3, final.stderr)
        receipt = json.loads(final.stdout)
        self.assertEqual(receipt['status'], 'partial')
        self.assertEqual(receipt['final_deliverables'], ['final.json'])
        self.assertEqual(receipt['delivery']['archive_status'], 'success')
        self.assertTrue(receipt['delivery']['files'][0]['archive_verified'])
        self.assertEqual((self.task / 'final.json').read_bytes(), (self.task / 'output/one/final.json').read_bytes())
        self.assertFalse((self.task / 'output/one/internal.json').exists())
        self.assertEqual((self.task / 'source.et').read_bytes(), self.source)

    def test_missing_id_preserves_json_and_archive_only_retry_uses_current_id(self):
        created = self.call(['source.et', '--output', 'final.json', '--delivery'])
        self.assertIn(created.returncode, (0, 3), created.stderr)
        self.assertEqual(json.loads(created.stdout)['delivery']['archive_status'], 'failed')
        before = (self.task / 'final.json').read_bytes()
        self.assertFalse((self.task / 'output').exists())
        for question in ('new', 'new', 'latest'):
            retry = self.call(['final.json', '--protect-input', 'source.et'], question, 'archive_delivery.py')
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(json.loads(retry.stdout)['archive_status'], 'success')
        self.assertEqual((self.task / 'final.json').read_bytes(), before)
        self.assertEqual((self.task / 'output/latest/final.json').read_bytes(), before)

    def test_pre_archive_verification_failure_keeps_final_file_record(self):
        old = Path.cwd()
        try:
            os.chdir(self.task)
            with patch.object(sys, 'argv', ['read_et', 'source.et', '--output', 'final.json', '--delivery']):
                with patch.object(read_et, '_verify_exported_json', side_effect=ValueError('injected verification failure')):
                    with redirect_stdout(io.StringIO()) as stdout:
                        self.assertIn(read_et.main(), (0, 3))
            result = json.loads(stdout.getvalue())['delivery']
            self.assertEqual(result['archive_status'], 'failed')
            self.assertEqual(result['files'][0]['output'], 'final.json')
            self.assertFalse(result['files'][0]['archive_verified'])
            self.assertTrue(Path('final.json').is_file())
            self.assertFalse(Path('output').exists())
        finally:
            os.chdir(old)

    def test_post_archive_mismatch_keeps_existing_copy_evidence(self):
        old = Path.cwd()
        receipt = {'archive_contract': 'archive-delivery-v2', 'archive_status': 'success', 'warnings': [],
                   'files': [{'output': 'final.json', 'archive_output': 'output/current/final.json',
                              'archive_status': 'copied', 'archive_verified': True, 'bytes': 1, 'sha256': 'incorrect'}]}
        try:
            os.chdir(self.task)
            with patch.object(sys, 'argv', ['read_et', 'source.et', '--output', 'final.json', '--delivery']):
                with patch('archive_delivery.finalize_delivery', return_value=receipt):
                    with redirect_stdout(io.StringIO()) as stdout:
                        self.assertIn(read_et.main(), (0, 3))
            result = json.loads(stdout.getvalue())['delivery']
            self.assertEqual(result['archive_status'], 'failed')
            self.assertEqual(result['files'][0]['archive_output'], 'output/current/final.json')
            self.assertEqual(result['files'][0]['archive_status'], 'failed')
            self.assertFalse(result['files'][0]['archive_verified'])
        finally:
            os.chdir(old)

    def test_final_json_verifier_does_not_use_32_mib_document_input_budget(self):
        path = self.task / 'large.json'
        chunk = b' ' * (1024 * 1024)
        digest = hashlib.sha256()
        with path.open('wb') as stream:
            for _ in range(33):
                stream.write(chunk)
                digest.update(chunk)
        read_et._verify_exported_json(path, {'bytes': 33 * len(chunk), 'sha256': digest.hexdigest()})
        with self.assertRaises(ValueError):
            read_et._verify_exported_json(path, {'bytes': 33 * len(chunk), 'sha256': 'changed'})

    def test_invalid_delivery_options_and_failed_read_never_publish(self):
        for arguments in (['source.et', '--delivery'], ['--check', '--delivery'],
                          ['source.et', '--summary', '--output', 'final.json', '--delivery']):
            result = self.call(arguments, 'one')
            self.assertEqual(result.returncode, 2, result.stdout)
        failed = self.call(['missing.et', '--output', 'final.json', '--delivery'], 'one')
        self.assertEqual(failed.returncode, 1)
        self.assertFalse((self.task / 'final.json').exists())
        self.assertFalse((self.task / 'output').exists())


if __name__ == '__main__':
    unittest.main()
