"""P3 opt-in dynamic reference loading and telemetry regression tests."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'reference_loading_audit.py'


class ReferenceLoadingTests(unittest.TestCase):
    def call(self, *args, cwd=None):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), *args],
                              cwd=cwd, capture_output=True, text=True)

    def test_planner_default_does_not_open_runtime(self):
        p = self.call('plan','--task','simple')
        self.assertEqual(p.returncode, 0, p.stderr)
        result = json.loads(p.stdout)
        self.assertEqual([x['file'] for x in result['files']], ['SKILL.md'])
        self.assertFalse(result['is_observed'])
        self.assertGreater(result['total_bytes'], 1000)

    def test_complex_planning_is_not_a_real_trace(self):
        p=self.call('plan','--task','full-report')
        self.assertEqual(p.returncode, 0, p.stderr)
        o=json.loads(p.stdout)
        self.assertFalse(o['is_observed'])
        self.assertNotIn('references/generation-runtime.md', [x['file'] for x in o['files']])
        self.assertEqual(o['total_bytes'], sum(x['bytes'] for x in o['files']))

    def test_init_read_range_and_duplicate_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.call('init','--trace','session.jsonl',cwd=folder)
            self.assertEqual(p.returncode, 0, p.stderr)
            p=self.call('init','--trace','session.jsonl',cwd=folder)
            self.assertEqual(p.returncode, 2)
            p=self.call('read','--file','references/generation-core.md','--start-line','1','--end-line','2','--trace','session.jsonl',cwd=folder)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertIn('生成核心规范',p.stdout)
            p=self.call('read','--file','references/generation-core.md','--start-line','2','--end-line','3','--trace','session.jsonl',cwd=folder)
            self.assertEqual(p.returncode,0,p.stderr)
            p=self.call('audit','--trace','session.jsonl',cwd=folder)
            self.assertEqual(p.returncode,0,p.stderr)
            o=json.loads(p.stdout)
            self.assertEqual(o['totals']['reference_reads'],2)
            self.assertEqual(o['files'][0]['ranges'],[[1,2],[2,3]])
            self.assertGreater(o['totals']['repeated_bytes'],0)
            self.assertEqual(o['totals']['unique_bytes']+o['totals']['repeated_bytes'],o['totals']['read_bytes'])
            self.assertEqual(o['reference_token_measurement'],'not_available')
            self.assertIsNone(o['provider_model_usage'])

    def test_external_usage_is_separate_from_read_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            trace=Path(folder)/'sample.jsonl'
            trace.write_text('\n'.join([
                json.dumps({'type':'trace_start','version':1}),
                json.dumps({'type':'reference_read','path':'SKILL.md','start_line':1,'end_line':1}),
                json.dumps({'type':'model_usage','input_tokens':123,'output_tokens':14,'cached_tokens':40}),
            ])+'\n',encoding='utf-8')
            p=self.call('audit','--trace','sample.jsonl','--task','simple',cwd=folder)
            self.assertEqual(p.returncode,0,p.stderr)
            o=json.loads(p.stdout)
            self.assertEqual(o['provider_model_usage']['input_tokens'],123)
            self.assertEqual(o['totals']['measured_reference_tokens'],0)
            self.assertEqual(o['planned_but_not_observed'],[])

    def test_reject_unsafe_path_and_symlink_log(self):
        with tempfile.TemporaryDirectory() as folder:
            for bad in ['../SKILL.md','/etc/passwd','references/../../etc/passwd','scripts/create_docx.py']:
                p=self.call('read','--file',bad,cwd=folder)
                self.assertEqual(p.returncode,2,(bad,p.stdout,p.stderr))
            p=self.call('init','--trace','../trace.jsonl',cwd=folder)
            self.assertEqual(p.returncode,2)
            path=Path(folder)/'symlink.jsonl'
            path.symlink_to(ROOT/'SKILL.md')
            p=self.call('audit','--trace','symlink.jsonl',cwd=folder)
            self.assertEqual(p.returncode,2)

    def test_no_implicit_runtime_reference_for_success(self):
        main=(ROOT/'SKILL.md').read_text('utf-8')
        styles=(ROOT/'references/generation-styles.md').read_text('utf-8')
        self.assertNotIn('按 `generation-runtime.md#生成后格式引导`',main)
        self.assertNotIn('generation-runtime.md#生成后格式引导',styles)
        self.assertIn('不额外读取 `generation-runtime.md`',main)
        self.assertIn('不经 `generation.md` 索引',main)
        self.assertIn('不自动截获其他工具读取或模型 Token',main)

    def test_trace_not_created_by_plain_read(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.call('read','--file','references/generation-core.md','--start-line','1','--end-line','1',cwd=folder)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(list(Path(folder).iterdir()),[])


if __name__=='__main__':
    unittest.main()
