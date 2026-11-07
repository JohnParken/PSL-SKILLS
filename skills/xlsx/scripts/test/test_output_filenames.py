"""Filename-only publication contracts, using an independent task cwd."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_directory import TaskDirectory
from stdlib_xlsx import Workbook
from xlsx_tabular import export_table
from xlsx_cli import Session
from run_xlsx_task import run_task
from xlsx_pack import pack
from libreoffice_recalc import recalculate

SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = {'sheets': [{'name': 'Data', 'cells': {'A1': {'value': 7}}}]}

class OutputFilenameTests(unittest.TestCase):
    def setUp(self):
        self.task = TaskDirectory()
        self.addCleanup(self.task.cleanup)
        Path('nested').mkdir()
        Workbook.create(SPEC).save('source.xlsx')

    def invalid_names(self, suffix='xlsx'):
        name = 'result.' + suffix
        return ('./' + name, 'nested/' + name, 'nested/../' + name,
                '../' + name, str(Path(name).resolve()), 'C:\\' + name,
                'C:' + name, '\\\\server\\share\\' + name)

    def test_workbook_api_rejects_raw_paths_and_keeps_inputs(self):
        before = Path('source.xlsx').read_bytes()
        for name in self.invalid_names():
            with self.subTest(name=name), self.assertRaises(ValueError):
                Workbook.create(SPEC).save(name)
        for alias in ('symbolic.xlsx', 'hard.xlsx'):
            if alias.startswith('symbolic'):
                Path(alias).symlink_to('source.xlsx')
            else:
                os.link('source.xlsx', alias)
            with self.assertRaises(ValueError):
                Workbook.load('source.xlsx').save(alias)
        self.assertEqual(Path('source.xlsx').read_bytes(), before)
        self.assertFalse(Path('result.xlsx').exists())
        self.assertEqual(list(Path('nested').iterdir()), [])

    def test_csv_public_api_rejects_paths(self):
        for name in self.invalid_names('csv'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                export_table('source.xlsx', name, 'Data')
        export_table('source.xlsx', '表 格.csv', 'Data')
        self.assertTrue(Path('表 格.csv').is_file())

    def test_runner_rejects_declared_and_command_paths_before_configs(self):
        for name in self.invalid_names():
            for declared, argument in ((name, name), ('result.xlsx', name), (name, 'result.xlsx')):
                with self.subTest(declared=declared, argument=argument), self.assertRaises(ValueError):
                    run_task({'script': 'stdlib_xlsx.py', 'arguments': ['create', '@spec', argument],
                              'output': declared, 'configs': {'spec': SPEC}})
        self.assertFalse(any(Path('.').glob('xlsx-task-*')))
        self.assertFalse(Path('result.xlsx').exists())

    def test_json_cli_publish_rejects_paths(self):
        from xlsx_cli_protocol import PROTOCOL, dumps, loads
        for name in self.invalid_names():
            requests = [('create', {'spec': SPEC}), ('publish', {'handle': 0, 'output': name})]
            raw = b''.join(dumps({'protocol': PROTOCOL, 'id': i, 'operation': op, 'parameters': p}) + b'\n'
                           for i, (op, p) in enumerate(requests))
            child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'xlsx_cli.py'), 'serve'],
                                   input=raw, capture_output=True)
            replies = [loads(line) for line in child.stdout.splitlines()]
            self.assertTrue(replies[0]['ok'])
            self.assertFalse(replies[1]['ok'])
        self.assertFalse(Path('result.xlsx').exists())

    def test_legacy_report_pack_and_recalc_reject_paths_without_external_tools(self):
        for name in self.invalid_names('json'):
            child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'formula_check.py'),
                                    'source.xlsx', '--report', '-o', name], capture_output=True)
            self.assertNotEqual(child.returncode, 0)
            self.assertTrue(b'filename' in child.stderr or b'path' in child.stderr, child.stderr)
        for name in self.invalid_names():
            with self.assertRaises(ValueError):
                pack('missing-input-directory', name)
            with patch('libreoffice_recalc.find_soffice', side_effect=AssertionError('must not discover external software')):
                self.assertFalse(recalculate('source.xlsx', name)[0])
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'formula_check.py'),
                                'source.xlsx', '--report', '-o', 'report.json'], capture_output=True)
        self.assertEqual(child.returncode, 0, child.stderr)
        before = Path('report.json').read_bytes()
        again = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'formula_check.py'),
                                'source.xlsx', '--report', '-o', 'report.json'], capture_output=True)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(Path('report.json').read_bytes(), before)
