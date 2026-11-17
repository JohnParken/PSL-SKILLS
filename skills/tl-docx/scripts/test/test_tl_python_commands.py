"""Static command-contract regression; does not certify a deployed environment."""
import re
from pathlib import Path
import unittest

SKILL_ROOT = Path(__file__).resolve().parents[2]
PYTHON = '"<skill-root>/scripts/.venv/bin/python"'


def command_lines(text):
    """Select complete TL CLI invocation examples, including inline commands."""
    return [line for line in text.splitlines() if ' -I -B ' in line and 'scripts/tl_docx.py' in line]


class PythonCommandTests(unittest.TestCase):
    """Keep public commands explicit without relying on shell activation."""

    def test_all_cli_examples_choose_skill_python(self):
        count = 0
        for path in [SKILL_ROOT / 'SKILL.md', *sorted((SKILL_ROOT / 'references').glob('*.md'))]:
            for line in command_lines(path.read_text(encoding='utf-8')):
                count += 1
                self.assertIn(PYTHON + ' -I -B ', line, str(path))
                self.assertNotRegex(line, r'(?<![/\w])python3? -I', str(path))
        self.assertGreaterEqual(count, 20)

    def test_entry_documents_fail_closed_deployment(self):
        text = (SKILL_ROOT / 'SKILL.md').read_text(encoding='utf-8')
        for fragment in ('缺失时停止并报告', '不自动创建环境或安装组件', '不回退', '禁止 `-S`', '不是源码或运行 ZIP'):
            self.assertIn(fragment, text)

    def test_reject_bare_example(self):
        line = 'python -I -B "<skill-root>/scripts/tl_docx.py" check'
        self.assertEqual(command_lines(line), [line])
        self.assertNotIn(PYTHON + ' -I -B ', line)

    def test_keep_dependency_name_out_of_command_scan(self):
        self.assertEqual(command_lines('python-docx==1.2.0'), [])

    def test_benchmark_example_uses_explicit_interpreter(self):
        text = Path(__file__).with_name('benchmark_page_budget.py').read_text(encoding='utf-8')
        self.assertIn(PYTHON + ' -I -B ', text)
        self.assertIsNone(re.search(r'Usage: python3? ', text))


if __name__ == '__main__':
    unittest.main()
