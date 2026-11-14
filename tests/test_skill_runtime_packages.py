"""Repository-only acceptance of independently built runtime packages.

Run with Python >=3.12.11 / Expat >=2.6, -I -S -B, as a non-root user.
Every runtime is extracted separately, chmod read-only, and invoked from a fresh
outside task directory with an empty PATH. Runtime Python is never transformed.
Synthetic fixtures are not real WPS/ET samples or desktop visual acceptance.
The printed platform is the only tested platform; Linux/Windows, client rendering,
network isolation at OS level and host/model delivery are not certified here.
"""
import hashlib
from base64 import b64decode
import importlib.util
import json
import os
from io import BytesIO
from pathlib import Path
import platform
import re
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.parsers.expat as expat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('artifact-lifecycle', 'bank-writing', 'document-navigation', 'docx', 'et', 'ofd', 'pptx',
         'skill-development-standards', 'table-workflow', 'tl-docx', 'wps', 'xlsx')


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

    def invoke(self, name, script, *args, payload=None, expected=0, site_packages=False):
        allowed = {name}
        if name == 'table-workflow':
            allowed.update(('xlsx', 'et'))
        if name == 'skill-development-standards':
            allowed.update(NAMES)
        denied = [str(ROOT.resolve())] + [str(path.resolve()) for key, path in self.installs.items() if key not in allowed]
        flags = ['-I', '-B'] if site_packages else ['-I', '-S', '-B']
        result = subprocess.run([sys.executable, *flags, '-c', RUNTIME_GUARD,
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
        report = json.loads(self.invoke('docx', 'read_docx.py', 'source.docx').stdout)
        self.assertEqual(report['format'], 'docx-report')
        self.assertEqual([block['text'] for block in report['blocks'] if block['type'] == 'paragraph'], ['合同 ABC'])
        self.assertFalse(report['has_more'])
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

    def test_wps_reads_stored_png_from_readonly_package(self):
        # Fixture builder belongs to the source tests. The runtime process
        # reads the resulting bytes from its task directory with no repository access.
        fixture_dir = str(ROOT / 'skills/wps/scripts/test')
        script_dir = str(ROOT / 'skills/wps/scripts')
        fixture_code = (
            'from base64 import b64encode\n'
            'from pathlib import Path\n'
            'import sys\n'
            'sys.path[:0] = sys.argv[1:3]\n'
            'from test_wps_binary_images import inline_fixture, png_bytes\n'
            "Path('binary.wps').write_bytes(inline_fixture())\n"
            'print(b64encode(png_bytes()).decode("ascii"))\n'
        )
        expected = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', fixture_code, fixture_dir, script_dir],
                                  cwd=self.work, env=self.env, capture_output=True, text=True, check=True, timeout=10)
        original = (self.work / 'binary.wps').read_bytes()
        response = self.invoke('wps', 'read_wps.py', 'binary.wps', '--reading-mode', 'maximum',
                               '--include-image-bytes', expected=3)
        images = json.loads(response.stdout)['binary_images']
        self.assertEqual((images['image_count'], images['unresolved_reference_count']), (1, 0))
        self.assertEqual(b64decode(images['images'][0]['data_base64']), b64decode(expected.stdout))
        self.assertEqual((self.work / 'binary.wps').read_bytes(), original)

    def test_wps_reads_grouped_merges_from_readonly_package(self):
        fixture_code = (
            'from pathlib import Path\n'
            'import sys\n'
            'sys.path[:0] = sys.argv[1:3]\n'
            'from test_wps_msdoc_table_groups import grouped_fixture\n'
            "Path('tables.doc').write_bytes(grouped_fixture())\n"
        )
        scripts = ROOT / 'skills/wps/scripts'
        subprocess.run([sys.executable, '-I', '-S', '-B', '-c', fixture_code, str(scripts / 'test'), str(scripts)],
                       cwd=self.work, env=self.env, capture_output=True, check=True, timeout=10)
        original = (self.work / 'tables.doc').read_bytes()
        response = self.invoke('wps', 'read_wps.py', 'tables.doc', '--reading-mode', 'maximum', expected=3)
        structure = json.loads(response.stdout)['binary_table_structure']
        self.assertEqual((structure['table_grouping'], structure['grid_and_merges']), ('stored', 'stored'))
        self.assertEqual([table['row_count'] for table in structure['tables']], [2, 1])
        self.assertEqual(structure['tables'][0]['vertical_merges'][0]['row_count'], 2)
        self.assertEqual(structure['tables'][0]['horizontal_merges'][0]['cell_count'], 2)
        self.assertEqual((self.work / 'tables.doc').read_bytes(), original)

    def test_wps_reads_chart_caches_from_readonly_package(self):
        fixture_code = (
            'from pathlib import Path\n'
            'import sys\n'
            'sys.path[:0] = sys.argv[1:3]\n'
            'from test_wps_binary_charts import chart_fixture, chart_package\n'
            "Path('charts.wps').write_bytes(chart_fixture(floating=chart_package(), inline=chart_package(values='')))\n"
        )
        scripts = ROOT / 'skills/wps/scripts'
        subprocess.run([sys.executable, '-I', '-S', '-B', '-c', fixture_code, str(scripts / 'test'), str(scripts)],
                       cwd=self.work, env=self.env, capture_output=True, check=True, timeout=10)
        path = self.work / 'charts.wps'
        original = path.read_bytes()
        response = self.invoke('wps', 'read_wps.py', path.name, '--reading-mode', 'maximum', expected=3)
        report = json.loads(response.stdout)['binary_charts']
        self.assertEqual((report['status'], report['chart_count']), ('partial', 2))
        floating, inline = report['charts']
        self.assertEqual([chart['references'][0]['kind'] for chart in (floating, inline)], ['floating', 'inline'])
        self.assertEqual(floating['series'][0]['values']['points'], [{'index': 0, 'value': '9.1250'}, {'index': 1, 'value': '2.50E-2'}])
        self.assertEqual(inline['series'][0]['values']['status'], 'unavailable')
        self.assertEqual(path.read_bytes(), original)

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

    def test_et_synthetic_biff8_compatibility_in_readonly_package(self):
        fixture_root = ROOT / 'skills/et/scripts/test'
        with patch.object(sys, 'path', [str(fixture_root), *sys.path]):
            from test_wps_biff8 import cell, rec, workbook
            from test_wps_biff8_links import external_book
            from test_wps_cfb import synthetic_cfb
            from test_wps_embedded_charts import CHART, DRAWING, GROUP, OBJECT
        malformed_chart = CHART.replace(rec(0x1001, bytes(2)), rec(0x1001, b'x'))
        sheet = rec(0xEC, DRAWING) + rec(0x5D, OBJECT) + malformed_chart
        sheet += rec(0x0203, cell(1, 0) + struct.pack('<d', 42))
        xti = rec(0x0017, struct.pack('<HHhh', 1, 0, 0, 0))
        stream = workbook([sheet], extra_global=external_book() + xti + rec(0xEB, GROUP))
        source = synthetic_cfb({'Workbook': stream}) + b'\0' * 3
        path = self.work / 'synthetic.et'
        path.write_bytes(source)
        response = self.invoke('et', 'et_cli.py', 'run', path.name, expected=3)
        result = json.loads(response.stdout)
        cell_result = result['sheets'][0]['cells']['A2']
        self.assertEqual((cell_result['type'], cell_result['value']), ('number', '42'))
        self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')
        codes = {item['code'] for item in result['issues']}
        self.assertTrue({'cfb_zero_trailing_padding_ignored', 'external_links_retained_opaque', 'embedded_chart_retained_opaque'} <= codes)
        self.assertEqual(path.read_bytes(), source)

    def test_ofd_read_only_package(self):
        self.invoke('ofd', 'read_ofd.py', '--check')
        installed = self.installs['ofd']
        for name in ('scripts/generate_ofd.py', 'scripts/check_generated_ofd.py',
                     'scripts/ofd_generation_assets.py', 'references/generation.md',
                     'references/bundled-fonts.md', 'assets/fonts/zhuque_fangsong/ZhuqueFangsong-Regular.ttf'):
            self.assertFalse((installed / name).exists(), name)
        ns = 'http://www.ofdspec.org/2016'
        def xml(tag, content, attrs=''):
            return f'<ofd:{tag} xmlns:ofd="{ns}" {attrs}>{content}</ofd:{tag}>'
        with zipfile.ZipFile(self.work / 'input.ofd', 'w') as package:
            package.writestr('OFD.xml', xml('OFD', '<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>', 'Version="1.0" DocType="OFD"'))
            package.writestr('Doc/Document.xml', xml('Document', '<ofd:CommonData/><ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/P.xml"/></ofd:Pages>'))
            page = ('<ofd:Content><ofd:Layer ID="2"><ofd:TextObject ID="3" Boundary="1 2 30 4" Size="3">'
                    '<ofd:TextCode X="0" Y="3">独立运行验收</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>')
            package.writestr('Doc/Pages/P.xml', xml('Page', page))
        response = self.invoke('ofd', 'read_ofd.py', 'input.ofd', expected=0)
        result = json.loads(response.stdout)
        self.assertEqual(result['status'], 'complete')
        self.assertIn('独立运行验收', response.stdout)

    def test_pptx_svg_generation_readback(self):
        self.invoke('pptx', 'pptx.py', 'check')
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="80" y="100" font-size="32">独立演示验收</text></svg>'
        self.invoke('pptx', 'pptx.py', 'create', '-', 'deck.pptx', payload={
            'schema': 'psl.pptx.v1', 'width': 1280, 'height': 720, 'slides': [{'svg': svg, 'notes': '验收备注'}]})
        self.invoke('pptx', 'pptx.py', 'validate', 'deck.pptx')
        self.assertIn('独立演示验收', self.invoke('pptx', 'pptx.py', 'inspect', 'deck.pptx').stdout)
        with zipfile.ZipFile(self.work / 'deck.pptx') as package:
            members = {name: package.read(name) for name in package.namelist()}
        name = 'ppt/notesSlides/notesSlide1.xml'
        frame = (
            '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Table"/>'
            '<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
            '<p:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            '<a:tbl><a:tblPr/><a:tblGrid><a:gridCol w="914400"/></a:tblGrid><a:tr h="914400">'
            '<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>独立备注表格</a:t></a:r></a:p>'
            '</a:txBody><a:tcPr/></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
        )
        members[name] = members[name].replace(b'</p:spTree>', frame.encode('utf-8') + b'</p:spTree>')
        with zipfile.ZipFile(self.work / 'notes-table.pptx', 'w', zipfile.ZIP_DEFLATED) as package:
            for member, data in members.items():
                package.writestr(member, data)
        result = json.loads(self.invoke('pptx', 'pptx.py', 'inspect', 'notes-table.pptx', '--max-output-bytes', '1500').stdout)
        self.assertEqual(result['slides'][0]['notes'], ['验收备注', '独立备注表格'])
        self.assertTrue(result['pagination']['complete'])
        # Literal third-party-style OMML, independent of the SVG converter.
        fraction = (
            '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
            '<m:f><m:num><m:r><m:t>12</m:t></m:r></m:num>'
            '<m:den><m:r><m:t>15</m:t></m:r></m:den></m:f></m:oMath>'
        )
        members[name] = members[name].replace('<a:r><a:t>独立备注表格</a:t></a:r>'.encode('utf-8'), fraction.encode('utf-8'))
        with zipfile.ZipFile(self.work / 'notes-math.pptx', 'w', zipfile.ZIP_DEFLATED) as package:
            for member, data in members.items():
                package.writestr(member, data)
        full = json.loads(self.invoke('pptx', 'pptx.py', 'inspect', 'notes-math.pptx').stdout)
        self.assertEqual(full['slides'][0]['notes'], ['验收备注', '1215'])
        for options in (('--summary',), ('--notes', 'omit', '--max-output-bytes', '1500')):
            result = json.loads(self.invoke('pptx', 'pptx.py', 'inspect', 'notes-math.pptx', *options).stdout)
            self.assertEqual(result['structural_validation'], 'passed')
            assessment = result['slides'][0]['text_assessment']
            self.assertEqual(assessment, full['slides'][0]['text_assessment'])
            self.assertEqual(assessment['status'], 'math_structure_not_preserved')
            self.assertEqual(assessment['math_paragraphs'], {'text': 0, 'notes': 1})

    def test_artifact_archive_and_registered_cleanup(self):
        self.invoke('artifact-lifecycle', 'lifecycle.py', 'check')
        (self.work / 'input.txt').write_bytes(b'protected input')
        self.invoke('artifact-lifecycle', 'lifecycle.py', 'plan', 'task.cleanup.json', '--file', 'temporary.txt',
                    '--protect', 'input.txt', '--deliverable', 'final.txt')
        (self.work / 'temporary.txt').write_bytes(b'owned temporary')
        (self.work / 'final.txt').write_bytes(b'verified result')
        archived = self.invoke('artifact-lifecycle', 'archive_delivery.py', 'final.txt', '--protect-input', 'input.txt')
        self.assertEqual(json.loads(archived.stdout)['archive_status'], 'success')
        self.invoke('artifact-lifecycle', 'lifecycle.py', 'seal', 'task.cleanup.json', '--outcome', 'success', '--verified', '--archive-status', 'success')
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

    def test_document_navigation_single_file_repeated_read(self):
        """The complete guidance package is readable without a runtime or peers."""
        skill = self.installs['document-navigation']
        self.assertEqual(set(self.before['document-navigation']), {'SKILL.md'})
        initial_work = fingerprints(self.work)
        entry = skill / 'SKILL.md'
        for _ in range(2):
            self.assertEqual(digest(entry.read_bytes()), self.before['document-navigation']['SKILL.md'])
        self.assertEqual(fingerprints(self.work), initial_work)

    def test_tl_docx_reads_creates_and_edits_from_isolated_install(self):
        entry = 'tl_docx.py'
        without_dependency = self.invoke('tl-docx', entry, 'check', expected=1)
        self.assertEqual('error', json.loads(without_dependency.stdout)['status'])
        available = subprocess.run([sys.executable, '-I', '-B', '-c', 'import docx; assert docx.__version__ == "1.2.0"'],
                                   env=self.env, capture_output=True, check=False, timeout=20)
        if available.returncode:
            self.skipTest('python-docx 1.2.0 not available to the selected interpreter with site enabled')

        def call(*args, **kwargs):
            response = self.invoke('tl-docx', entry, *args, site_packages=True, **kwargs)
            return json.loads(response.stdout)

        self.assertEqual('environment-ok', call('check', '--write')['status'])
        styled = call('create', 'styled.docx', payload={
            'profile': 'academic', 'styles': {'body': {'size_pt': 13}},
            'page': {'margin_mm': 20},
            'blocks': [{'type': 'heading', 'level': 1, 'text': 'Styled section'},
                       {'type': 'paragraph', 'text': 'Styled content'}]})
        self.assertEqual('verified', styled['status'])
        summary = call('read', 'styled.docx', '--summary')
        self.assertEqual('Styled section', summary['records'][0]['text'])
        created = call('create', 'source.docx', payload={'blocks': [{'type': 'paragraph', 'text': '月报原文'}]})
        self.assertEqual('verified', created['status'])
        self.assertEqual('success', created['archive_status'])
        original = (self.work / 'source.docx').read_bytes()
        result = call('read', 'source.docx')
        self.assertIn('月报原文', [item.get('text') for item in result['records']])
        content = call('read', 'source.docx', '--content')
        self.assertEqual('content', content['view'])
        self.assertIn('月报原文', [item.get('text') for item in content['records']])
        self.assertFalse(content['has_more'])
        spec = {'operations': [{'type': 'replace_run_text', 'paragraph_index': 0, 'run_index': 0,
                                'expected_text': '月报原文', 'expected_run_text': '月报原文', 'text': '月报修订'}]}
        edited = call('edit', 'source.docx', 'result.docx', payload=spec)
        self.assertEqual('edited', edited['status'])
        self.assertEqual('success', edited['archive_status'])
        self.assertTrue(edited['cleanup']['manifest_removed'])
        self.assertEqual(original, (self.work / 'source.docx').read_bytes())
        with zipfile.ZipFile(BytesIO(original)) as source, zipfile.ZipFile(self.work / 'result.docx') as target:
            self.assertEqual(source.namelist(), target.namelist())
            self.assertEqual(['word/document.xml'], [name for name in source.namelist() if source.read(name) != target.read(name)])
        comment = {'action': 'add', 'paragraph_index': 0, 'expected_text': '月报原文', 'anchor_text': '月报原文',
                   'text': '请核对', 'author': '复核人'}
        annotated = call('comments', 'source.docx', payload=comment)
        self.assertEqual('source_批注版.docx', annotated['output'])
        listed = call('comments', annotated['output'], payload={'action': 'list'})
        self.assertEqual('modern', listed['mode'])
        self.assertEqual('请核对', listed['comments'][0]['text'])
        risk = call('create', 'risks.docx', payload={'blocks': [{'type': 'page_break'}]})
        self.assertEqual('verified', risk['status'])
        audit = call('validate', 'risks.docx', '--strict-pagination', expected=2)
        self.assertTrue(audit['layout']['pagination_warnings'])
        structure = call('read', 'source.docx', '--format', 'structure', '--revisions', 'all', '--xml-part', 'word/document.xml')
        self.assertEqual(['word/document.xml'], [record['part'] for record in structure['records']])
        export = call('read', 'source.docx', '--report', '-o', 'report.json', '--delivery')
        self.assertEqual('exported', export['status'])
        self.assertEqual('success', export['archive_status'])
        self.assertEqual('report', json.loads((self.work / 'report.json').read_bytes())['view'])
        substituted = call('replace', 'source.docx', 'filled.docx', payload={'replacements': [{'old': '月报原文', 'new': '月报填写'}]})
        self.assertEqual([1], substituted['result']['rule_counts'])
        self.assertEqual(original, (self.work / 'source.docx').read_bytes())

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
