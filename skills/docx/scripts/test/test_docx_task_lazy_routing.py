"""P3 instruction/router budget regression: common tasks avoid low-frequency refs."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import importlib.util
import json
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
PLANNER = ROOT/'scripts'/'reference_loading_audit.py'


class TaskLazyRoutingTests(unittest.TestCase):
    def plan(self, task):
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(PLANNER),
                                 'plan', '--task', task], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload['is_observed'])
        return [item['file'] for item in payload['files']], payload['total_bytes']

    def test_entry_token_budget_and_metadata(self):
        text = (ROOT/'SKILL.md').read_text('utf-8')
        self.assertLess((ROOT/'SKILL.md').stat().st_size, 7000)
        version = re.search(r'^version: "([^"]+)"$', text, re.M)
        self.assertIsNotNone(version)
        self.assertRegex(version.group(1), r'^\d{8}\.\d{4}\.\d{4}$')
        description = re.search(r'^description: (.+)$', text, re.M)
        self.assertTrue(100 <= len(description.group(1)) <= 150)
        for required in ('不可信数据', '任务工作目录', '真实退出码',
                         'archive_status', 'cleanup_incomplete', '自然分页',
                         '不自动读取归档历史', 'run_docx_task.py', 'read_docx.py'):
            self.assertIn(required, text)

    def test_common_tasks_only_load_entry(self):
        for name in ('simple','simple-list','simple-table','read','read-summary',
                     'read-headings','read-section'):
            with self.subTest(task=name):
                files, byte_count = self.plan(name)
                self.assertEqual(files, ['SKILL.md'])
                self.assertEqual(byte_count, (ROOT/'SKILL.md').stat().st_size)

    def test_advanced_tasks_not_preloaded_unrelated(self):
        expected = {
            'table': ['SKILL.md', 'references/generation-tables.md'],
            'styles': ['SKILL.md', 'references/generation-styles.md'],
            'sections': ['SKILL.md', 'references/p2-layout.md'],
            'edit-text': ['SKILL.md','references/ooxml-editing.md'],
            'edit-table': ['SKILL.md','references/table-media-editing.md'],
            'full-report': ['SKILL.md','references/generation-core.md'],
        }
        for task, paths in expected.items():
            with self.subTest(task=task):
                actual, _ = self.plan(task)
                self.assertEqual(actual, paths)
                self.assertNotIn('references/generation-runtime.md', actual)

    def test_scripts_not_required_to_be_loaded_for_simple_work(self):
        text = (ROOT/'SKILL.md').read_text('utf-8')
        self.assertIn('不经 `generation.md` 索引', text)
        self.assertIn('成功交付不额外读取 `generation-runtime.md`', text)
        self.assertIn('按需', text)

if __name__ == '__main__':
    unittest.main()
