"""Public CLI boundary regressions with synthetic, exclusively owned task files."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_xlsx_task import run_task

SCRIPTS = Path(__file__).resolve().parent


class CliBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.folder = tempfile.TemporaryDirectory(prefix='xlsx-boundary-')
        self.root = Path(self.folder.name)
        self.task = self.root / 'task'
        self.task.mkdir()
        (self.root / 'outside').mkdir()
        os.chdir(self.task)
        Path('input.json').write_text('{}')
        self.environment = patch.dict(os.environ, {'DOCX_DEBUG_ROUTER_CONFIG': '0'}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        os.chdir(self.previous)
        self.folder.cleanup()

    def invoke(self, script, args):
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / script), *args],
                               capture_output=True, text=True)
        return child.returncode, json.loads(child.stdout)

    def commands(self, output):
        yield 'stdlib_xlsx.py', ['create', 'input.json', output]
        yield 'xlsx_tabular.py', ['import', 'input.json', output]
        yield 'xlsx_result_export.py', ['input.json', output]
        yield 'xlsx_management.py', ['input.json', '--sheet', 'Data', '--query', 'input.json', '--xlsx', output]
        for script in ('xlsx_dashboard.py', 'xlsx_deposit_structure.py', 'xlsx_management_policy.py'):
            yield script, ['input.json', '--query', 'input.json', '--xlsx', output]
        for script in ('xlsx_deposits.py', 'xlsx_customers.py', 'xlsx_reconcile.py'):
            yield script, ['input.json', 'input.json', '--query', 'input.json', '--xlsx', output]

    def test_every_write_cli_rejects_paths_before_business_processing(self):
        Path('link').symlink_to(self.root / 'outside', target_is_directory=True)
        for output in ('../outside/result.xlsx', str(self.root / 'outside/result.xlsx'),
                       'link/result.xlsx', 'C:\\outside\\result.xlsx'):
            for script, args in self.commands(output):
                with self.subTest(script=script, output=output):
                    code, result = self.invoke(script, args)
                    self.assertEqual(code, 1)
                    self.assertFalse(result['output_written'])
                    self.assertTrue('path' in result['message'] or 'links' in result['message'])
                    self.assertFalse((self.root / 'outside/result.xlsx').exists())
                    self.assertFalse(Path('output').exists())

    def test_inputs_and_aliases_are_rejected_without_output(self):
        (self.root / 'outside/spec.json').write_text('{}')
        Path('spec-link.json').symlink_to(self.root / 'outside/spec.json')
        for source in ('../outside/spec.json', 'spec-link.json', str(self.root / 'outside/spec.json')):
            code, result = self.invoke('stdlib_xlsx.py', ['create', source, 'report.xlsx'])
            self.assertEqual(code, 1)
            self.assertFalse(result['output_written'])
            self.assertFalse(Path('report.xlsx').exists())
        os.link('input.json', 'alias.xlsx')
        code, result = self.invoke('stdlib_xlsx.py', ['create', 'input.json', 'alias.xlsx'])
        self.assertEqual(code, 1)
        self.assertIn('input', result['message'])
        self.assertEqual(Path('input.json').read_text(), '{}')

    def test_relative_subdirectory_success_and_no_overwrite(self):
        Path('reports').mkdir()
        Path('input.json').write_text(json.dumps({'sheets': [{'name': 'Data', 'cells': {'A1': {'value': 42}}}]}))
        code, result = self.invoke('stdlib_xlsx.py', ['create', 'input.json', 'reports/report.xlsx'])
        self.assertEqual(code, 0)
        self.assertTrue(result['output_written'])
        before = Path('reports/report.xlsx').read_bytes()
        code, result = self.invoke('stdlib_xlsx.py', ['create', 'input.json', 'reports/report.xlsx'])
        self.assertEqual(code, 1)
        self.assertEqual(Path('reports/report.xlsx').read_bytes(), before)

    def test_runner_argument_error_preserves_exit_and_redacts_streams(self):
        code, result = run_task({'script': 'stdlib_xlsx.py', 'arguments': ['create', 'input.json', 'out.xlsx', '--private-secret'], 'output': 'out.xlsx'})
        self.assertEqual(code, 2)
        self.assertEqual(result['error_code'], 'child_argument_error')
        evidence = result['child_execution']
        self.assertEqual(evidence['exit_code'], 2)
        self.assertTrue(evidence['stderr']['omitted'])
        self.assertGreater(evidence['stderr']['original_characters'], 0)
        self.assertNotIn('private-secret', json.dumps(result))
        self.assertFalse(Path('out.xlsx').exists())

    def test_timeout_cleans_manifest_and_reports_unknown_exit(self):
        spec = {'script': 'stdlib_xlsx.py', 'arguments': ['create', '@spec', 'out.xlsx'],
                'output': 'out.xlsx', 'configs': {'spec': {}}}
        with patch('run_xlsx_task.subprocess.run', side_effect=subprocess.TimeoutExpired('child', 120, output=b'sensitive', stderr=b'secret')):
            code, result = run_task(spec)
        self.assertEqual(code, 1)
        self.assertEqual(result['task_status'], 'failed_cleaned')
        self.assertIsNone(result['child_execution']['exit_code'])
        self.assertIsNone(result['manifest'])
        self.assertEqual({p.name for p in Path('.').iterdir()}, {'input.json'})
        self.assertEqual(Path('input.json').read_text(), '{}')
        self.assertNotIn('secret', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
