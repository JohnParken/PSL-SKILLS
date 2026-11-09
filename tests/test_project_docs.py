"""Repository maintenance tests; not distributed with an individual Skill."""
import importlib.util
import shlex
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('project_docs', Path(__file__).resolve().parents[1] / 'tools/check_project_docs.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class DocumentationChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('skills/example/SKILL.md', '---\nversion: "20261107.1943.5370"\n---\n# Example\n')
        self.write('README.md', '| [example](skills/example/SKILL.md) | 20261107.1943.5370 |\n')
        self.write('AGENTS.md', '# Rules\n')
        self.write('docs/README.md', '| [example](skills/example.md) | 20261107.1943.5370 |\n')
        self.write('docs/skills/example.md', '> Skill 版本：`20261107.1943.5370`\n# 示例\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def codes(self):
        return {issue['code'] for issue in checker.check_project_docs(self.root)['issues']}

    def test_valid(self):
        self.assertTrue(checker.check_project_docs(self.root)['ok'])

    def test_nested_maintenance_links_are_checked(self):
        self.write('docs/maintenance/example/history.md', '[missing](missing.md)')
        self.assertIn('missing-link', self.codes())

    def test_version_drift(self):
        self.write('docs/skills/example.md', '> Skill 版本：`20261105.1556.4829`')
        self.assertIn('version-drift', self.codes())

    def test_coverage_omission(self):
        self.write('README.md', '# No inventory')
        self.assertIn('coverage', self.codes())

    def test_missing_link_and_anchor(self):
        self.write('AGENTS.md', '[missing](missing.md)\n[anchor](README.md#absent)')
        self.assertTrue({'missing-link', 'missing-anchor'} <= self.codes())

    def test_reference_and_unicode_anchor(self):
        self.write('AGENTS.md', '[one][ref]\n[ref]: docs/skills/example.md#示例\n')
        self.assertEqual(set(), self.codes())

    def test_escape(self):
        self.write('AGENTS.md', '[outside](../outside.md)')
        self.assertIn('outside-repository', self.codes())

    def test_fenced_examples_ignored(self):
        self.write('AGENTS.md', '```md\n[example](nonexistent.md)\n```\n')
        self.assertEqual(set(), self.codes())

    def test_plain_intro_and_later_history(self):
        self.write('docs/skills/example.md', (
            '# ET：只读提取\n\n'
            '核对日期：2026-11-07。本文对应仓库基线，Skill 版本为 `20261107.1943.5370`。\n\n'
            '## 历史\n\n> Skill 版本：`20261105.1556.4829`\n'
        ))
        self.assertEqual(set(), self.codes())

    def test_malformed_frontmatter(self):
        for source in ('---', '---\nversion: "20261107.1943.5370"', 'version: "20261107.1943.5370"'):
            with self.subTest(source=source):
                self.write('skills/example/SKILL.md', source)
                self.assertIn('skill-version', self.codes())

    def test_duplicate_versions(self):
        self.write('skills/example/SKILL.md', (
            '---\nversion: "20261107.1943.5370"\nversion: "20261107.1943.5370"\n---\n'
        ))
        self.assertIn('skill-version', self.codes())

    def test_duplicate_table_rows(self):
        row = '| [example](skills/example/SKILL.md) | 20261107.1943.5370 |\n'
        self.write('README.md', row * 2)
        self.assertIn('coverage', self.codes())

    def test_duplicate_intro_versions(self):
        self.write('docs/skills/example.md', (
            '> Skill 版本：`20261107.1943.5370`\n\n'
            'Skill 版本为 `20261107.1943.5370`。\n'
        ))
        self.assertIn('version-drift', self.codes())

    def test_duplicate_heading(self):
        self.assertEqual({'hello', 'hello-1'}, checker.anchors('# Hello\n# Hello'))


class RepositoryDeliveryExamples(unittest.TestCase):
    def test_current_delivery_examples_use_bare_filenames(self):
        root = Path(__file__).resolve().parents[1]
        examples = [
            ('README.md', 'stdlib_xlsx.py', 'create', 2),
            ('README.md', 'stdlib_xlsx.py', 'edit', 3),
            ('docs/skills/wps.md', 'run_wps_task.py', 'create', 1),
        ]
        for relative, script, operation, offset in examples:
            with self.subTest(page=relative, operation=operation):
                commands = [
                    shlex.split(line)
                    for line in (root / relative).read_text(encoding='utf-8').splitlines()
                    if line.startswith(('python ', 'python3 '))
                    and f'/scripts/{script}" {operation} ' in line
                ]
                self.assertEqual(len(commands), 1)
                command = commands[0]
                output = command[command.index(operation) + offset]
                self.assertTrue(output)
                self.assertNotIn('/', output)
                self.assertNotIn('\\', output)
                self.assertNotIn(':', output)
                self.assertNotIn(output, ('.', '..'))


if __name__ == '__main__':
    unittest.main()
