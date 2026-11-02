"""Optional receipt projection; old output contract and failure diagnostics remain."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
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
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue((Path(work)/'验收.docx').is_file())
                obj = json.loads(r.stdout)
                self.assertEqual(obj['status'], 'verified')
                self.assertEqual(obj['task_status'], 'completed')
                self.assertEqual(obj['output'], '验收.docx')
                self.assertTrue(obj['cleanup']['cleanup_complete'])
                self.assertTrue(obj['cleanup']['deliverables_unchanged'])
                values.append((len(r.stdout.encode('utf-8')), obj))
        self.assertLess(values[1][0], values[0][0] * 0.5)
        self.assertIn('environment', values[0][1])
        self.assertNotIn('environment', values[1][1])
        self.assertIn('archive_status', values[1][1])

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
