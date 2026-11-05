"""Simple document fastpath: instruction budget, safety and real create regressions."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'


class SimpleDocumentFastpathTests(unittest.TestCase):
    def test_compact_single_entry_with_essential_guards(self):
        content = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        self.assertLess(len(content.encode('utf-8')), 9000)
        for token in ('<skill-root>', '任务工作目录', '-I -S -B', '标准库',
                      '不可信数据', '真实退出码', 'stderr', 'run_docx_task.py',
                      'run_docx_edit.py', 'archive_status', 'cleanup_incomplete',
                      '不自动读取归档历史', '在普通文档任务中修改本 Skill'):
            self.assertIn(token, content)
        self.assertIn('不经 `generation.md` 索引', content)
        self.assertIn('成功交付不额外读取 `generation-runtime.md`', content)
        for token in ('"type":"title"', '"type":"paragraph"', '"type":"heading"',
                      '"type":"list"', '"type":"table"', '"theme":"official"'):
            self.assertIn(token, content)

    def test_only_one_reference_for_three_simple_plans(self):
        planner = SCRIPTS / 'reference_loading_audit.py'
        for task in ('simple', 'simple-list', 'simple-table'):
            with self.subTest(task=task):
                r = subprocess.run([sys.executable, '-I', '-S', '-B', str(planner),
                                    'plan', '--task', task], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                obj = json.loads(r.stdout)
                self.assertFalse(obj['is_observed'])
                self.assertEqual([f['file'] for f in obj['files']], ['SKILL.md'])
                self.assertEqual(obj['total_bytes'], (ROOT/'SKILL.md').stat().st_size)

    def test_simple_title_body_list_table_via_standard_runner(self):
        spec = {'theme':'official','blocks':[
            {'type':'title','text':'简单文档验收'},
            {'type':'heading','level':1,'text':'工作情况'},
            {'type':'paragraph','text':'本轮完成验证。'},
            {'type':'list','ordered':False,'items':['完成检查','输出文档']},
            {'type':'table','header':True,'rows':[['项目','状态'],['测试','通过']]},
        ]}
        runner = SCRIPTS/'run_docx_task.py'
        with tempfile.TemporaryDirectory() as temp:
            r = subprocess.run([sys.executable, '-I', '-S', '-B', str(runner),
                                'create', '简单验收.docx'], input=json.dumps(spec,ensure_ascii=False),
                               capture_output=True, text=True, cwd=temp)
            self.assertEqual(r.returncode,0,r.stderr+'\n'+r.stdout)
            out=Path(temp)/'简单验收.docx'
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size,0)
            with zipfile.ZipFile(out) as z:
                body=z.read('word/document.xml').decode('utf-8')
                for expected in ('简单文档验收','工作情况','本轮完成验证','完成检查','项目','通过'):
                    self.assertIn(expected, body)
            # In this single-task workspace the standard runner must not retain temp artifacts.
            self.assertEqual([p.name for p in Path(temp).iterdir()], ['简单验收.docx'])

if __name__=='__main__':
    unittest.main()
