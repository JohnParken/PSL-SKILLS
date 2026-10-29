"""Compatibility coverage for the deprecated generation CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent


class LegacyEntryTests(unittest.TestCase):
    def invoke(self, work, script, *args):
        environment = dict(os.environ)
        environment.pop('ROUTER_CONFIG_VARIABLES', None)
        environment['DOCX_DEBUG_ROUTER_CONFIG'] = '0'
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(SCRIPTS / script), *args],
            cwd=work, env=environment, text=True, capture_output=True, check=False)

    def test_generation_keeps_json_and_readable_document(self):
        with tempfile.TemporaryDirectory() as work:
            Path(work, 'spec.json').write_text(json.dumps({'blocks': [
                {'type': 'paragraph', 'text': 'Legacy compatibility evidence'}]}))
            result = self.invoke(work, 'create_docx.py', 'spec.json', 'report.docx', '--verify')
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary['status'], 'verified')
            self.assertEqual(summary['output'], 'report.docx')
            self.assertEqual(summary['archive_status'], 'skipped')
            self.assertEqual(result.stderr.count('generation-deprecated:'), 1)
            read = self.invoke(work, 'read_docx.py', 'report.docx')
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertIn('Legacy compatibility evidence', read.stdout)

    def test_check_and_preflight_have_no_generation_notice(self):
        with tempfile.TemporaryDirectory() as work:
            Path(work, 'spec.json').write_text('{"blocks": []}')
            for args in [('--check',), ('spec.json', '--preflight')]:
                with self.subTest(args=args):
                    result = self.invoke(work, 'create_docx.py', *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn('generation-deprecated:', result.stderr)
                    if '--preflight' in args:
                        self.assertEqual(json.loads(result.stdout)['status'], 'preflight-ok')


if __name__ == '__main__':
    unittest.main()
