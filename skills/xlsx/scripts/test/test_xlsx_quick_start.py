"""Execute the documented quick path in an isolated read-only Skill copy.

Synthetic maintenance fixtures, not live Deep Agents, Excel or business acceptance.
The copied production package contains no tests and no neighboring Skill.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def fingerprints(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob('*') if path.is_file()
    }


class QuickStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.install = tempfile.TemporaryDirectory(prefix='xlsx-independent-')
        cls.skill = Path(cls.install.name) / 'xlsx'
        shutil.copytree(ROOT, cls.skill, ignore=shutil.ignore_patterns('test', '__pycache__', '*.pyc'))
        cls.before = fingerprints(cls.skill)
        for path in cls.skill.rglob('*'):
            path.chmod(0o555 if path.is_dir() else 0o444)
        cls.skill.chmod(0o555)
        entry = (cls.skill / 'SKILL.md').read_text(encoding='utf-8')
        calls = re.findall(r'(?m)^\s*(execute\(command=.*\))\s*$', entry)
        commands = [ast.literal_eval(ast.parse(call, mode='eval').body.keywords[0].value) for call in calls]
        command = next((item for item in commands if 'run_xlsx_task.py' in item and "<<'JSON'" in item), '')
        match = re.search(r"<<'JSON'\n(.*?)\nJSON", command, re.S)
        if match is None:
            raise AssertionError('The entry must contain the runnable stdin example')
        cls.spec_text = match.group(1)

    @classmethod
    def tearDownClass(cls):
        try:
            if fingerprints(cls.skill) != cls.before:
                raise AssertionError('Read-only installation contents changed')
        finally:
            cls.skill.chmod(0o755)
            for path in cls.skill.rglob('*'):
                path.chmod(0o755 if path.is_dir() else 0o644)
            cls.install.cleanup()

    def setUp(self):
        self.task = tempfile.TemporaryDirectory(prefix='xlsx-quick-task-')
        self.addCleanup(self.task.cleanup)
        self.cwd = Path(self.task.name)
        self.env = dict(os.environ, ROUTER_CONFIG_VARIABLES='', DOCX_DEBUG_ROUTER_CONFIG='0')

    def command(self, script, *arguments, stdin=None):
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(self.skill / 'scripts' / script), *arguments],
            cwd=self.cwd, env=self.env, input=stdin, capture_output=True, text=True, timeout=120,
        )

    def create(self, spec=None):
        child = self.command('run_xlsx_task.py', stdin=self.spec_text if spec is None else json.dumps(spec, ensure_ascii=False))
        self.assertEqual(child.stderr, '')
        result = json.loads(child.stdout)
        self.assertEqual(result['child_execution']['state'], 'exited')
        self.assertEqual(result['child_execution']['exit_code'], child.returncode)
        return child.returncode, result

    def assert_clean(self, result):
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertTrue(result['cleanup']['manifest_removed'])
        self.assertIsNone(result['manifest'])
        self.assertEqual(list(self.cwd.glob('xlsx-task-*')), [])

    def test_documented_static_example_self_check_and_readback(self):
        check = self.command('xlsx_cli.py', 'run', 'check')
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(json.loads(check.stdout)['status'], 'ready')
        code, result = self.create()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'calculated_subset')
        self.assertEqual(result['formula_count'], 0)
        self.assertEqual(result['issues'], [])
        self.assertTrue(result['output_written'])
        self.assertEqual(result['task_status'], 'completed')
        self.assertEqual(result['delivery']['archive_status'], 'failed')
        self.assert_clean(result)
        read = self.command('xlsx_cli.py', 'run', 'read', result['output'])
        self.assertEqual(read.returncode, 0, read.stderr)
        data = json.loads(read.stdout)
        cells = data['sheets'][0]['cells']
        self.assertEqual(data['sheets'][0]['name'], '清单')
        self.assertEqual(cells['A2']['value'], '001')
        self.assertEqual(cells['A2']['type'], 'inlineStr')
        self.assertEqual(cells['B2']['value'], '12')
        self.assertEqual(cells['B2']['type'], 'n')
        self.assertEqual(len(cells), 4)

    def workbook_style(self, output, ref):
        namespace = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
        with zipfile.ZipFile(self.cwd / output) as package:
            styles = ET.fromstring(package.read('xl/styles.xml'))
            sheet = ET.fromstring(package.read('xl/worksheets/sheet1.xml'))
        cell = sheet.find('.//' + namespace + "c[@r='" + ref + "']")
        style = styles.find(namespace + 'cellXfs')[int(cell.get('s', '0'))]
        return namespace, styles, sheet, style

    def test_documented_example_default_header_style(self):
        code, result = self.create()
        self.assertEqual(code, 0)
        for ref in ('A1', 'B1'):
            ns, styles, sheet, style = self.workbook_style(result['output'], ref)
            fill = styles.find(ns + 'fills')[int(style.get('fillId'))]
            self.assertEqual(fill.find(ns + 'patternFill/' + ns + 'fgColor').get('rgb'), 'FFDCEAF7')
            font = styles.find(ns + 'fonts')[int(style.get('fontId'))]
            self.assertIsNotNone(font.find(ns + 'b'))
            border = styles.find(ns + 'borders')[int(style.get('borderId'))]
            self.assertEqual(border.find(ns + 'bottom').get('style'), 'thin')
            self.assertEqual(style.find(ns + 'alignment').get('horizontal'), 'center')
            self.assertIsNotNone(sheet.find(ns + 'cols'))
        self.assert_clean(result)

    def test_documented_entry_keeps_explicit_style_override(self):
        spec = json.loads(self.spec_text)
        cells = spec['configs']['spec']['sheets'][0]['cells']
        cells['A1']['style'] = 4
        cells['B1']['style'] = 0
        code, result = self.create(spec)
        self.assertEqual(code, 0)
        for ref, index in (('A1', 4), ('B1', 0)):
            ns, styles, _, style = self.workbook_style(result['output'], ref)
            self.assertEqual(ET.tostring(style), ET.tostring(styles.find(ns + 'cellXfs')[index]))
            self.assertEqual(style.get('fillId'), '0')
            self.assertEqual(style.get('borderId'), '0')
        self.assert_clean(result)

    def test_archived_and_repeated_tasks_keep_previous_file(self):
        self.env['ROUTER_CONFIG_VARIABLES'] = json.dumps({'commonParam': [{'name': 'questionId', 'value': 'quick'}]})
        code, first = self.create()
        self.assertEqual(code, 0)
        original = (self.cwd / first['output']).read_bytes()
        code, second = self.create()
        self.assertEqual(code, 0)
        self.assertNotEqual(first['output'], second['output'])
        self.assertIn('_第2版', second['output'])
        self.assertEqual((self.cwd / first['output']).read_bytes(), original)
        for result in (first, second):
            self.assertEqual(result['delivery']['archive_status'], 'success')
            self.assertTrue(result['delivery']['files'][0]['archive_verified'])
            self.assertEqual((self.cwd / 'output' / 'quick' / Path(result['output']).name).read_bytes(), (self.cwd / result['output']).read_bytes())
            self.assert_clean(result)

    def test_summary_reports_overview_not_values(self):
        _, result = self.create()
        child = self.command('stdlib_xlsx.py', 'read', result['output'], '--summary')
        self.assertEqual(child.returncode, 0, child.stderr)
        data = json.loads(child.stdout)
        self.assertEqual(data['coverage'], 'overview_only_no_cell_values')
        # Source fingerprints can contain the same digits as cell text.
        # Check the parsed worksheet projection, not arbitrary JSON substrings.
        self.assertEqual(data['sheets'], [{'name': '清单', 'stored_cells': 4}])
        for sheet in data['sheets']:
            self.assertNotIn('cells', sheet)
            self.assertNotIn('value', sheet)
            self.assertNotIn('001', json.dumps(sheet, ensure_ascii=False))

    def test_formula_partial_keeps_diagnostics_and_cleans(self):
        spec = json.loads(self.spec_text)
        spec['configs']['spec']['sheets'][0]['cells']['C2'] = {'formula': 'UNSUPPORTED(A2)'}
        code, result = self.create(spec)
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(result['issues'])
        self.assertTrue(result['output_written'])
        self.assertEqual(result['task_status'], 'failed_cleaned')
        self.assert_clean(result)

    def test_failed_spec_cleans_without_overwriting_input(self):
        original = self.cwd / 'original.txt'
        original.write_text('protected', encoding='utf-8')
        spec = json.loads(self.spec_text)
        spec['inputs'] = ['original.txt']
        spec['configs']['spec']['unknown'] = True
        code, result = self.create(spec)
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse((self.cwd / spec['output']).exists())
        self.assertEqual(original.read_text(encoding='utf-8'), 'protected')
        self.assert_clean(result)

    def test_literal_formula_and_text_identifier_stay_data(self):
        spec = json.loads(self.spec_text)
        cells = spec['configs']['spec']['sheets'][0]['cells']
        cells['C2'] = {'value': '=SUM(B2:B2)'}
        cells['D2'] = {'value': True}
        code, result = self.create(spec)
        self.assertEqual(code, 0)
        self.assertEqual(result['formula_count'], 0)
        self.assert_clean(result)
        read = self.command('xlsx_cli.py', 'run', 'read', result['output'])
        self.assertEqual(read.returncode, 0)
        cells = json.loads(read.stdout)['sheets'][0]['cells']
        self.assertEqual(cells['C2']['value'], '=SUM(B2:B2)')
        self.assertEqual(cells['C2']['type'], 'inlineStr')
        self.assertEqual(cells['D2']['value'], True)


if __name__ == '__main__':
    unittest.main()
