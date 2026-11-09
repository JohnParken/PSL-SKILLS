"""Repository-only acceptance of ten independently built runtime packages.

Run with Python >=3.12.11 / Expat >=2.6, -I -S -B, as a non-root user.
Every runtime is extracted separately, chmod read-only, and invoked from a fresh
outside task directory with an empty PATH. Runtime Python is never transformed.
Synthetic fixtures are not real WPS/ET samples or desktop visual acceptance.
The printed platform is the only tested platform; Linux/Windows, client rendering,
network isolation at OS level and host/model delivery are not certified here.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import xml.parsers.expat as expat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('artifact-lifecycle', 'bank-writing', 'docx', 'et', 'ofd', 'pptx',
         'skill-development-standards', 'table-workflow', 'wps', 'xlsx')


# A Python audit guard makes accidental repository reads and sibling-package
# imports fail in each directly tested runtime. This is not an OS security
# sandbox and is not inherited by workflow's explicitly addressed subprocesses.
RUNTIME_GUARD = """import json, os, runpy, sys
entry, denied_json = sys.argv[1:3]
denied = json.loads(denied_json)
sys.argv = [entry] + sys.argv[3:]
def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.path.realpath(os.fsdecode(args[0]))
        if any(path == root or path.startswith(root + os.sep) for root in denied):
            raise PermissionError('Independent runtime attempted undeclared source/package access: ' + path)
sys.addaudithook(audit)
runpy.run_path(entry, run_name='__main__')
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprints(root):
    # Private installation fonts must not be inspected or redistributed.
    return {p.relative_to(root).as_posix(): digest(p.read_bytes())
            for p in root.rglob('*') if p.is_file()
            and ('user_fonts' not in p.parts or p.name == 'NOTICE.txt') and '__pycache__' not in p.parts}


def builder():
    spec = importlib.util.spec_from_file_location('all_runtime_packager', ROOT / 'tools/package_skills.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillRuntimePackages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info < (3, 12, 11) or expat.version_info < (2, 6, 0):
            raise RuntimeError('Acceptance requires Python >=3.12.11 and Expat >=2.6; not a skipped pass')
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            raise RuntimeError('Actual read-only enforcement requires a non-root user')
        print(json.dumps({'platform': platform.platform(), 'python': platform.python_version(),
                          'expat': expat.EXPAT_VERSION, 'fixtures': 'synthetic only',
                          'not_verified': ['other operating systems', 'Office/WPS rendering', 'real native ET',
                                           'OS-level network/read sandbox', 'host/model result delivery']}, ensure_ascii=False))
        cls.temp = tempfile.TemporaryDirectory(prefix='all-skills-runtime-')
        cls.root = Path(cls.temp.name)
        cls.installs = {}
        cls.before = {}
        cls.sources = {}
        cls.archives = {}
        cls.packager = builder()
        for number, name in enumerate(NAMES):
            source = ROOT / 'skills' / name
            cls.sources[name] = fingerprints(source)
            archive = cls.root / (name + '.zip')
            cls.packager.build_package(source, archive)
            cls.archives[archive] = digest(archive.read_bytes())
            installation = cls.root / ('isolated-install-' + str(number))
            with zipfile.ZipFile(archive) as package:
                package.extractall(installation)
            skill = installation / name
            cls.installs[name] = skill
            cls.before[name] = fingerprints(skill)
            for path in installation.rglob('*'):
                path.chmod(0o555 if path.is_dir() else 0o444)
            installation.chmod(0o555)

    @classmethod
    def tearDownClass(cls):
        try:
            for name, installed in cls.installs.items():
                if fingerprints(installed) != cls.before[name]:
                    raise AssertionError('Runtime modified: ' + name)
                if fingerprints(ROOT / 'skills' / name) != cls.sources[name]:
                    raise AssertionError('Source changed while tests ran: ' + name)
            for archive, initial in cls.archives.items():
                if digest(archive.read_bytes()) != initial:
                    raise AssertionError('Archive changed: ' + archive.name)
        finally:
            for path in cls.root.rglob('*'):
                path.chmod(0o755 if path.is_dir() else 0o644)
            cls.temp.cleanup()

    def setUp(self):
        self.task = tempfile.TemporaryDirectory(prefix='task-', dir=self.root)
        self.addCleanup(self.task.cleanup)
        self.work = Path(self.task.name)
        self.env = {'PATH': '', 'HOME': str(self.work), 'TMPDIR': str(self.work),
                    'DOCX_DEBUG_ROUTER_CONFIG': '0', 'PYTHONIOENCODING': 'utf-8',
                    'ROUTER_CONFIG_VARIABLES': json.dumps({'commonParam': [{'name': 'questionId', 'value': 'package-test'}]})}

    def invoke(self, name, script, *args, payload=None, expected=0):
        allowed = {name}
        if name == 'table-workflow':
            allowed.update(('xlsx', 'et'))
        if name == 'skill-development-standards':
            allowed.update(NAMES)
        denied = [str(ROOT.resolve())] + [str(path.resolve()) for key, path in self.installs.items() if key not in allowed]
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', RUNTIME_GUARD,
                                 str(self.installs[name] / 'scripts' / script), json.dumps(denied),
                                 *map(str, args)], cwd=self.work, env=self.env, text=True,
                                input=None if payload is None else json.dumps(payload, ensure_ascii=False),
                                capture_output=True, timeout=90)
        self.assertIn(result.returncode, expected if isinstance(expected, tuple) else (expected,),
                      f'{name}/{script}: exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}')
        return result

    def write_json(self, name, value):
        (self.work / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def workbook(self, output='source.xlsx'):
        spec = {'sheets': [{'name': 'Data', 'cells': {'A1': {'value': '机构'}, 'B1': {'value': '金额'},
                'A2': {'value': '000123'}, 'B2': {'value': 12}, 'A3': {'value': '乙'}, 'B3': {'value': 8}}}]}
        self.write_json('spec.json', spec)
        self.invoke('xlsx', 'xlsx_cli.py', 'run', 'create', 'spec.json', output)
        return spec

    def test_all_packages_exact_untransformed_and_readonly(self):
        for name, skill in self.installs.items():
            with self.subTest(skill=name):
                selected = self.packager.collect_files(ROOT / 'skills' / name)
                self.assertEqual({p: digest(data) for p, data in selected.items()}, self.before[name])
                if name == 'skill-development-standards':
                    self.assertTrue((skill / 'scripts/test/test_check_skill.py').is_file())
                else:
                    self.assertFalse((skill / 'scripts/test').exists())
                self.assertFalse(list(skill.rglob('__pycache__')))
                for path in skill.rglob('*'):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o222, 0)
                with self.assertRaises(PermissionError):
                    (skill / 'read-only-probe').write_bytes(b'not permitted')

    def test_docx_create_read_edit(self):
        self.invoke('docx', 'run_docx_task.py', 'check')
        self.invoke('docx', 'read_docx.py', '--check')
        created = self.invoke('docx', 'run_docx_task.py', 'create', 'source.docx',
                              payload={'blocks': [{'type': 'paragraph', 'text': '合同 ABC'}]})
        self.assertEqual(json.loads(created.stdout)['status'], 'verified')
        original = (self.work / 'source.docx').read_bytes()
        self.assertIn('合同 ABC', self.invoke('docx', 'read_docx.py', 'source.docx').stdout)
        self.invoke('docx', 'run_docx_edit.py', 'text', 'source.docx', 'edited.docx',
                    payload={'replacements': [{'old': 'ABC', 'new': 'XYZ', 'count': 1}]})
        self.assertIn('合同 XYZ', self.invoke('docx', 'read_docx.py', 'edited.docx').stdout)
        self.assertEqual((self.work / 'source.docx').read_bytes(), original)

    def test_xlsx_create_read_edit(self):
        self.invoke('xlsx', 'xlsx_cli.py', 'run', 'check')
        self.workbook()
        original = (self.work / 'source.xlsx').read_bytes()
        self.assertIn('000123', self.invoke('xlsx', 'xlsx_cli.py', 'run', 'read', 'source.xlsx').stdout)
        self.write_json('patch.json', {'sheets': [{'name': 'Data', 'cells': {'B2': {'value': 77}}}]})
        self.invoke('xlsx', 'xlsx_cli.py', 'run', 'edit', 'source.xlsx', 'patch.json', 'edited.xlsx')
        with zipfile.ZipFile(self.work / 'edited.xlsx') as package:
            self.assertIn(b'<v>77</v>', package.read('xl/worksheets/sheet1.xml'))
        self.assertIn('000123', self.invoke('xlsx', 'xlsx_cli.py', 'run', 'read', 'edited.xlsx').stdout)
        self.assertEqual((self.work / 'source.xlsx').read_bytes(), original)

    def test_wps_create_read_edit(self):
        self.invoke('wps', 'run_wps_task.py', 'check')
        self.invoke('wps', 'read_wps.py', '--check')
        self.invoke('wps', 'run_wps_task.py', 'create', 'source.wps',
                    payload={'blocks': [{'type': 'paragraph', 'text': '合同 ABC'}]})
        original = (self.work / 'source.wps').read_bytes()
        self.assertIn('合同 ABC', self.invoke('wps', 'read_wps.py', 'source.wps').stdout)
        self.invoke('wps', 'run_wps_edit.py', 'text', 'source.wps', 'edited.wps', '--find', 'ABC', '--replace', 'XYZ')
        self.assertIn('合同 XYZ', self.invoke('wps', 'read_wps.py', 'edited.wps').stdout)
        self.assertEqual((self.work / 'source.wps').read_bytes(), original)

    def test_et_synthetic_ooxml_read_and_analysis(self):
        self.invoke('et', 'et_cli.py', 'run', '--check')
        self.invoke('et', 'analyze_et.py', '--check')
        self.workbook()
        source = (self.work / 'source.xlsx').read_bytes()
        (self.work / 'synthetic.et').write_bytes(source)
        read = self.invoke('et', 'et_cli.py', 'run', 'synthetic.et', expected=(0, 3))
        self.assertIn('000123', read.stdout)
        result = self.invoke('et', 'analyze_et.py', 'synthetic.et', '--sheet', 'Data', '--range', 'A1:B3', '--header-rows', '1', expected=(0, 3))
        data = json.loads(result.stdout)
        self.assertEqual(data['schema_version'], 'et_table_analysis_v1')
        numeric = [value for value in data['profiles'].values() if value['numeric_count']]
        self.assertEqual(len(numeric), 1)
        self.assertEqual(str(numeric[0]['sum']), '20')
        self.assertEqual(numeric[0]['numeric_count'], 2)
        self.assertEqual((self.work / 'synthetic.et').read_bytes(), source)

    def test_ofd_bundled_font_generation_readback(self):
        self.invoke('ofd', 'generate_ofd.py', '--check')
        self.invoke('ofd', 'read_ofd.py', '--check')
        spec = {'schema_version': 'ofd-generate-1', 'fonts': {'example': {'bundled': 'zhuque-fangsong-v0.212'}},
                'pages': [{'width': 210, 'height': 297, 'margins': [15, 15, 15, 15], 'items': [
                    {'kind': 'text', 'x': 25, 'y': 35, 'font': 'example', 'size_mm': 5, 'text': '独立运行验收'}]}]}
        self.write_json('layout.json', spec)
        self.invoke('ofd', 'generate_ofd.py', 'layout.json', 'result.ofd')
        read = self.invoke('ofd', 'read_ofd.py', 'result.ofd', expected=3)
        self.assertIn('独立运行验收', read.stdout)
        self.assertEqual(json.loads(read.stdout)['status'], 'partial')
        with zipfile.ZipFile(self.work / 'result.ofd') as package:
            self.assertEqual(digest(package.read('Doc_0/Res/Font_1.ttf')),
                             '558c62730844fe54ba220146ed62f859d4e2880188d92d985f8921c6e3743bc4')

    def test_pptx_svg_generation_readback(self):
        self.invoke('pptx', 'pptx.py', 'check')
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="80" y="100" font-size="32">独立演示验收</text></svg>'
        self.invoke('pptx', 'pptx.py', 'create', '-', 'deck.pptx', payload={
            'schema': 'psl.pptx.v1', 'width': 1280, 'height': 720, 'slides': [{'svg': svg, 'notes': '验收备注'}]})
        self.invoke('pptx', 'pptx.py', 'validate', 'deck.pptx')
        self.assertIn('独立演示验收', self.invoke('pptx', 'pptx.py', 'inspect', 'deck.pptx').stdout)

    def test_artifact_archive_and_registered_cleanup(self):
        self.invoke('artifact-lifecycle', 'lifecycle.py', 'check')
        (self.work / 'input.txt').write_bytes(b'protected input')
        self.invoke('artifact-lifecycle', 'lifecycle.py', 'plan', 'task.cleanup.json', '--file', 'temporary.txt',
                    '--protect', 'input.txt', '--deliverable', 'final.txt')
        (self.work / 'temporary.txt').write_bytes(b'owned temporary')
        (self.work / 'final.txt').write_bytes(b'verified result')
        archived = self.invoke('artifact-lifecycle', 'lifecycle.py', 'archive', 'final.txt', '--protect-input', 'input.txt')
        self.assertEqual(json.loads(archived.stdout)['archive_status'], 'copied')
        self.invoke('artifact-lifecycle', 'lifecycle.py', 'seal', 'task.cleanup.json', '--outcome', 'success', '--verified', '--archive-status', 'copied')
        cleaned = json.loads(self.invoke('artifact-lifecycle', 'lifecycle.py', 'clean', 'task.cleanup.json').stdout)
        self.assertTrue(cleaned['cleanup_complete'])
        self.assertTrue(cleaned['manifest_removed'])
        self.assertFalse((self.work / 'temporary.txt').exists())
        self.assertEqual((self.work / 'input.txt').read_bytes(), b'protected input')
        self.assertEqual((self.work / 'output/package-test/final.txt').read_bytes(), b'verified result')

    def test_standards_checks_all_independent_packages(self):
        for name, installed in self.installs.items():
            with self.subTest(skill=name):
                self.invoke('skill-development-standards', 'check_skill.py', installed, '--strict')

    def test_bank_writing_complete_reference_closure(self):
        skill = self.installs['bank-writing']
        source = ROOT / 'skills/bank-writing'
        expected = {p.relative_to(source).as_posix() for p in (source / 'references').rglob('*') if p.is_file()}
        self.assertTrue(expected)
        self.assertTrue(expected.issubset(self.before['bank-writing']))
        for path in skill.rglob('*.md'):
            for relative in re.findall(r'<skill-root>/([A-Za-z_][A-Za-z0-9_./-]*)', path.read_text(encoding='utf-8')):
                self.assertTrue((skill / relative.rstrip('.')).exists(), f'{path.name}: missing {relative}')

    def test_workflow_explicit_independent_providers_and_missing_refusal(self):
        workflow = 'table_workflow.py'
        roots = ['--xlsx-root', self.installs['xlsx'], '--et-root', self.installs['et']]
        self.invoke('table-workflow', workflow, 'check', *roots)
        self.workbook()
        (self.work / 'synthetic.et').write_bytes((self.work / 'source.xlsx').read_bytes())
        before = fingerprints(self.work)
        result = self.invoke('table-workflow', workflow, 'inspect', *roots,
                             payload={'files': [{'path': 'source.xlsx'}, {'path': 'synthetic.et'}]}, expected=3)
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(data['source_partial_count'], 1)
        self.assertEqual(data['files'][1]['source_status'], 'partial')
        self.assertTrue(data['files'][1]['source_issues'])
        self.assertEqual(len(data['files']), 2)
        self.assertEqual([item['sheets'][0]['name'] for item in data['files']], ['Data', 'Data'])
        missing = self.invoke('table-workflow', workflow, 'check', '--xlsx-root', self.work / 'missing-provider', expected=1)
        self.assertNotEqual(json.loads(missing.stdout)['status'], 'environment-ok')
        self.invoke('table-workflow', workflow, 'check', '--xlsx-root', self.installs['xlsx'],
                    '--et-root', self.work / 'missing-et-provider', expected=1)
        self.assertEqual(fingerprints(self.work), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
