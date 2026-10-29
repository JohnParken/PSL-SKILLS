"""Execute the documented Python templates, including external script closeout."""
import ast
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(os.environ.get('TEMPLATE_SKILLS_ROOT', Path(__file__).resolve().parents[1] / 'skills'))


class ComplexJsonTemplatesTests(unittest.TestCase):
    def source(self, name, mode='success'):
        document = (ROOT / name / 'references/complex-json.md').read_text()
        code, expression = re.findall(r'```python\n(.*?)\n```', document, re.S)
        call = ast.parse(expression).body[0].value
        self.assertEqual(call.func.id, 'execute')
        self.assertEqual(call.keywords[0].arg, 'command')
        shell_command = ast.literal_eval(call.keywords[0].value)
        self.assertIn("<<'PY_SPEC'\n" + code + '\nPY_SPEC\n', shell_command)
        compile(code, '<documented-template>', 'exec')
        if name == 'docx':
            assignment = next(node for node in ast.parse(code).body
                              if isinstance(node, ast.Assign)
                              and any(isinstance(target, ast.Name) and target.id == 'spec'
                                      for target in node.targets))
            self.assertEqual(ast.literal_eval(assignment.value)['theme'], 'official')
        if mode == 'business_failure':
            replacement = 'request = {"blocks": [{"type": "unsupported"}]}' if name == 'docx' else 'request["configs"]["spec"] = {"invalid": True}'
            code = code.replace('\ntry:\n', '\n' + replacement + '\ntry:\n', 1)
        elif mode == 'serialization_failure':
            code = code.replace('\ntry:\n', '\nrequest = {"invalid": float("nan")}\ntry:\n', 1)
        elif mode == 'syntax_failure':
            code = 'invalid Python syntax !\n' + code
        return code

    def child(self, args, task, source=None):
        env = dict(os.environ)
        env.pop('ROUTER_CONFIG_VARIABLES', None)
        env['DOCX_DEBUG_ROUTER_CONFIG'] = '0'
        return subprocess.run([sys.executable, '-I', '-S', '-B', *map(str, args)],
                              cwd=task, input=source, text=True, encoding='utf-8',
                              capture_output=True, env=env, timeout=60)

    def assert_result(self, process, mode, name, task):
        expected = {'success': 0, 'business_failure': 1, 'serialization_failure': 2, 'syntax_failure': 1}[mode]
        self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        output = task / ('result.' + name)
        if mode == 'success':
            report = json.loads(process.stdout)
            self.assertTrue(report['cleanup']['cleanup_complete'])
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                xml = ''.join(archive.read(n).decode('utf-8') for n in archive.namelist() if n.endswith('.xml'))
                self.assertIn('已确认', xml)
                self.assertIn('$(echo 未执行)', xml)
                self.assertIn('C:\\资料', xml)
        else:
            self.assertFalse(output.exists())
            if mode == 'business_failure':
                report = json.loads(process.stdout)
                self.assertTrue(report['cleanup']['cleanup_complete'])
            if mode == 'serialization_failure':
                self.assertEqual(process.stdout, '')
                self.assertIn('spec_serialization_failed', process.stderr)

    def run_inline(self, mode):
        for name in ('docx', 'xlsx'):
            with self.subTest(name=name, mode=mode), tempfile.TemporaryDirectory(prefix='json-template-') as directory:
                task = Path(directory)
                (task / 'original.txt').write_text('protected original')
                process = self.child(['-', ROOT / name, 'result.' + name], task, self.source(name, mode))
                self.assert_result(process, mode, name, task)
                expected = {'original.txt', 'result.' + name} if mode == 'success' else {'original.txt'}
                self.assertEqual({p.name for p in task.iterdir()}, expected)
                self.assertEqual((task / 'original.txt').read_text(), 'protected original')

    def test_inline_success(self):
        self.run_inline('success')

    def test_inline_business_failure(self):
        self.run_inline('business_failure')

    def test_inline_serialization_failure(self):
        self.run_inline('serialization_failure')

    def test_inline_python_syntax_failure(self):
        self.run_inline('syntax_failure')

    def test_complete_documented_shell_call(self):
        for name in ('docx', 'xlsx'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='shell-json-template-') as directory:
                task = Path(directory)
                document = (ROOT / name / 'references/complex-json.md').read_text()
                expression = re.findall(r'```python\n(.*?)\n```', document, re.S)[1]
                command = ast.literal_eval(ast.parse(expression).body[0].value.keywords[0].value)
                self.assertTrue(command.startswith('python '))
                command = shlex.quote(sys.executable) + command[len('python'):]
                command = command.replace('<skill-root>', str(ROOT / name)).replace('报告.' + name, 'result.' + name)
                env = dict(os.environ)
                env.pop('ROUTER_CONFIG_VARIABLES', None)
                process = subprocess.run(['/bin/sh', '-c', command], cwd=task, env=env,
                                         capture_output=True, text=True, encoding='utf-8', timeout=60)
                self.assert_result(process, 'success', name, task)
                self.assertEqual({p.name for p in task.iterdir()}, {'result.' + name})

    def test_nonzero_result_and_both_channels_are_forwarded(self):
        for name in ('docx', 'xlsx'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='channel-json-template-') as directory:
                task = Path(directory)
                scripts = task / 'fixture-skill' / 'scripts'
                scripts.mkdir(parents=True)
                filename = 'run_docx_task.py' if name == 'docx' else 'run_xlsx_task.py'
                (scripts / filename).write_text('import sys\nprint(\'{"task_status":"cleanup_incomplete"}\')\nprint("cleanup-warning", file=sys.stderr)\nraise SystemExit(3)\n')
                process = self.child(['-', scripts.parent, 'result.' + name], task, self.source(name))
                self.assertEqual(process.returncode, 3)
                self.assertEqual(json.loads(process.stdout), {'task_status': 'cleanup_incomplete'})
                self.assertEqual(process.stderr.strip(), 'cleanup-warning')
                self.assertEqual({p.name for p in task.iterdir()}, {'fixture-skill'})

    def test_saved_script_external_closeout_on_every_exit(self):
        for name in ('docx', 'xlsx'):
            for mode in ('success', 'business_failure', 'serialization_failure', 'syntax_failure'):
                with self.subTest(name=name, mode=mode), tempfile.TemporaryDirectory(prefix='saved-json-template-') as directory:
                    task = Path(directory)
                    (task / 'original.txt').write_text('protected original')
                    lifecycle = ROOT / name / 'scripts' / ('cleanup_task.py' if name == 'docx' else 'xlsx_lifecycle.py')
                    plan = self.child([lifecycle, 'plan', 'outer.json', '--file', 'builder.py',
                                       '--protect', 'original.txt', '--deliverable', 'result.' + name], task)
                    self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
                    with (task / 'builder.py').open('x', encoding='utf-8') as handle:
                        handle.write(self.source(name, mode))
                    process = self.child(['builder.py', ROOT / name, 'result.' + name], task)
                    # The outer executor waits for exit, then closes out independently
                    # of Python syntax, serialization or business success.
                    seal_args = [lifecycle, 'seal', 'outer.json', '--outcome', 'failed']
                    if process.returncode == 0:
                        summary = json.loads(process.stdout)
                        archive_status = summary.get('delivery', summary)['archive_status']
                        seal_args = [lifecycle, 'seal', 'outer.json', '--outcome', 'success',
                                     '--verified', '--archive-status', archive_status]
                    sealed = self.child(seal_args, task)
                    self.assertEqual(sealed.returncode, 0, sealed.stdout + sealed.stderr)
                    cleaned = self.child([lifecycle, 'clean', 'outer.json'], task)
                    self.assertEqual(cleaned.returncode, 0, cleaned.stdout + cleaned.stderr)
                    self.assertTrue(json.loads(cleaned.stdout)['manifest_removed'])
                    self.assert_result(process, mode, name, task)
                    expected = {'original.txt', 'result.' + name} if mode == 'success' else {'original.txt'}
                    self.assertEqual({p.name for p in task.iterdir()}, expected)
                    self.assertEqual((task / 'original.txt').read_text(), 'protected original')


if __name__ == '__main__':
    unittest.main()
