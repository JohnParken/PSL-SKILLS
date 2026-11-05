"""Optional receipt projection; old output contract and failure diagnostics remain."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import json
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT/'run_docx_task.py'


class CompactReceiptTests(unittest.TestCase):
    def test_success_reduces_stdout_without_hiding_archive_or_cleanup(self):
        spec = {'theme':'official','blocks':[{'type':'title','text':'验收'},
                                               {'type':'paragraph','text':'示例正文。'}]}
        values = []
        for flag in (False, True):
            with tempfile.TemporaryDirectory() as work:
                cmd = [sys.executable, '-I', '-S', '-B', str(RUNNER), 'create', '验收.docx']
                if flag:
                    cmd.append('--compact-receipt')
                r = subprocess.run(cmd, cwd=work, input=json.dumps(spec, ensure_ascii=False),
                                   capture_output=True, text=True, env={**os.environ, "ROUTER_CONFIG_VARIABLES": json.dumps(
                                       {"commonParam": [{"name": "questionId", "value": "receipt-test"}]})})
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue((Path(work)/'验收.docx').is_file())
                archive = Path(work) / 'output/receipt-test/验收.docx'
                self.assertTrue(archive.is_file())
                self.assertEqual(archive.read_bytes(), (Path(work) / '验收.docx').read_bytes())
                obj = json.loads(r.stdout)
                self.assertEqual(obj['status'], 'verified')
                self.assertEqual(obj['archive_user_notice'],
                                 '🎉 文档已经放到你的云空间啦，随时都能找到～ ☁️✨')
                self.assertEqual(obj['task_status'], 'completed')
                self.assertEqual(obj['output'], '验收.docx')
                self.assertTrue(obj['cleanup']['cleanup_complete'])
                self.assertTrue(obj['cleanup']['deliverables_unchanged'])
                values.append((len(r.stdout.encode('utf-8')), obj))
        self.assertLess(values[1][0], values[0][0] * 0.5)
        self.assertIn('environment', values[0][1])
        self.assertNotIn('environment', values[1][1])
        self.assertIn('archive_status', values[1][1])

    def test_explicit_id_archives_without_environment_and_rejects_conflict(self):
        spec = {'blocks': [{'type': 'paragraph', 'text': '归档正文'}]}
        for conflict in (False, True):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as work:
                environment = dict(os.environ)
                environment.pop('ROUTER_CONFIG_VARIABLES', None)
                if conflict:
                    environment['ROUTER_CONFIG_VARIABLES'] = json.dumps(
                        {'commonParam': [{'name': 'questionId', 'value': 'different'}]})
                result = subprocess.run(
                    [sys.executable, '-I', '-S', '-B', str(RUNNER), 'create', 'report.docx',
                     '--question-id', 'explicit-task', '--compact-receipt'],
                    cwd=work, env=environment, input=json.dumps(spec), capture_output=True, text=True)
                payload = json.loads(result.stdout)
                if conflict:
                    self.assertEqual(result.returncode, 1)
                    self.assertFalse((Path(work) / 'report.docx').exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(payload['archive_status'], 'copied')
                    self.assertEqual(payload['archive_output'], 'output/explicit-task/report.docx')
                    self.assertEqual((Path(work) / payload['archive_output']).read_bytes(),
                                     (Path(work) / 'report.docx').read_bytes())

    def test_skipped_and_failed_archive_receipts_include_user_notice(self):
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as work:
                environment = dict(os.environ)
                environment.pop('ROUTER_CONFIG_VARIABLES', None)
                if failed:
                    environment['ROUTER_CONFIG_VARIABLES'] = json.dumps(
                        {'commonParam': [{'name': 'questionId', 'value': 'notice-task'}]})
                    (Path(work) / 'output').write_bytes(b'blocked directory')
                result = subprocess.run(
                    [sys.executable, '-I', '-S', '-B', str(RUNNER), 'create', 'notice.docx', '--compact-receipt'],
                    cwd=work, env=environment, input=json.dumps({'blocks': [{'type': 'paragraph', 'text': '正文'}]}),
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt['archive_status'], 'failed' if failed else 'skipped')
                self.assertIn('没能' if failed else '未执行', receipt['archive_user_notice'])
                self.assertNotIn('已经放到', receipt['archive_user_notice'])

    def test_error_keeps_full_diagnostics(self):
        with tempfile.TemporaryDirectory() as work:
            r = subprocess.run([sys.executable, '-I', '-S', '-B', str(RUNNER), 'create',
                                'invalid.docx','--compact-receipt'],
                               input='{}', cwd=work, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            obj=json.loads(r.stdout)
            self.assertNotEqual(obj['status'], 'verified')
            self.assertIn('next_action',obj)
            self.assertIn('error_code',obj)

if __name__=='__main__':
    unittest.main()
