"""Regression checks for runtime skill execute examples; repository maintenance only."""
import ast
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / 'skills'
BUSINESS = ('artifact-lifecycle', 'docx', 'et', 'ofd', 'pptx', 'table-workflow', 'wps', 'xlsx')


class ExecuteExampleTests(unittest.TestCase):
    def test_runtime_command_fences_use_execute(self):
        for name in BUSINESS:
            for path in (SKILLS / name).rglob('*.md'):
                text = path.read_text(encoding='utf-8')
                self.assertNotRegex(text, r'(?m)^\s*```(?:console|sh|bash|shell)\s*$', str(path))
                for call in re.findall(r'(?m)^\s*(execute\(command=.*\))\s*$', text):
                    expr = ast.parse(call, mode='eval').body
                    self.assertIsInstance(expr, ast.Call, str(path))
                    self.assertEqual([arg.arg for arg in expr.keywords], ['command'], str(path))
                    command = ast.literal_eval(expr.keywords[0].value)
                    self.assertTrue(command.strip(), str(path))
                    result = subprocess.run(['/bin/sh', '-n'], input=command, text=True,
                                            capture_output=True, check=False)
                    self.assertEqual(result.returncode, 0, f'{path}: {result.stderr}')

    def test_production_python_uses_own_deployment_slot(self):
        """Check shell payloads and inline examples; maintenance commands use the local Python."""
        slot = '"<skill-root>/scripts/.venv/bin/python"'
        for name in (*BUSINESS, 'tl-docx', 'tl-pptx'):
            directory = SKILLS / name
            flags = ' -I -B' if name.startswith('tl-') else ' -I -S -B'
            seen = 0
            for path in directory.rglob('*.md'):
                text = path.read_text(encoding='utf-8').replace('\\"', '"')
                for line in text.splitlines():
                    self.assertNotRegex(line, r'\bpython3?\s+-I[^"`]*"<skill-root>/scripts/[^"]+\.py"', str(path))
                    for match in re.finditer(re.escape(slot) + r'( -[A-Z](?: -[A-Z])*) (- )?"<skill-root>/([^"]+)"', line):
                        self.assertEqual(match[1], flags, str(path))
                        self.assertTrue((directory / match[3]).exists(), f'{path}: {match[3]}')
                        seen += 1
            self.assertGreater(seen, 0, name)

    def test_complete_stdin_and_safe_subshell(self):
        main = (SKILLS / 'table-workflow/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('inspect --xlsx-root', main)
        self.assertIn('<<\'JSON\'\\n', main)
        plan = (SKILLS / 'table-workflow/references/plan-contract.md').read_text(encoding='utf-8')
        self.assertIn('不能提交不含 stdin 的命令', plan)
        for name in ('customer-structure', 'deposit-analysis', 'management-policy',
                     'management-reconciliation', 'maturity-distribution', 'rank-share', 'result-export'):
            text = (SKILLS / f'xlsx/references/{name}.md').read_text(encoding='utf-8')
            self.assertIn('set -eC', text, name)
        assessment = (SKILLS / 'et/references/reading-assessment.md').read_text(encoding='utf-8')
        self.assertNotIn('Path(__file__).resolve()', assessment)
        self.assertIn('Path(sys.argv[1])', assessment)
        pptx = (SKILLS / 'pptx/SKILL.md').read_text(encoding='utf-8')
        self.assertNotRegex(pptx, r'execute\(command=.*create - report\.pptx"\)')

    def test_execution_instructions_preserve_paths_and_result_checks(self):
        for name in BUSINESS:
            text = (SKILLS / name / 'SKILL.md').read_text(encoding='utf-8')
            self.assertIn('execute(command=', text, name)
            self.assertIn('<skill-root>', text, name)
            self.assertIn('退出', text, name)
        for name in ('docx', 'et', 'wps', 'xlsx'):
            text = (SKILLS / name / 'SKILL.md').read_text(encoding='utf-8')
            self.assertIn('顶层', text, name)


if __name__ == '__main__':
    unittest.main()
