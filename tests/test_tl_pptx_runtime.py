"""Repository-only acceptance of the independent TL PPTX runtime package.

Set TL_PPTX_TEST_VENV to a task-specific, already provisioned virtual environment.
This test never installs packages or edits that environment. It builds a new ZIP,
extracts runtime code to an isolated read-only installation and links only its
scripts/.venv deployment slot to that prepared environment. Every invocation uses
that installation's explicit scripts/.venv/bin/python with -I -B.

Run as a non-root user. Missing deployment configuration is an error, not a pass.
The audit hook is not an OS sandbox. This validates only the actual local platform,
not PowerPoint rendering, other OSes, real DeepAgents or cloud delivery.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'skills' / 'tl-pptx'
AUDIT_RUNNER = '''import json, os, runpy, sys
entry, denied = sys.argv[1:3]
denied = os.path.realpath(denied)
sys.argv = [entry] + sys.argv[3:]
def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        target = os.path.realpath(os.fsdecode(args[0]))
        if target == denied or target.startswith(denied + os.sep):
            raise PermissionError('Undeclared repository read by independent runtime')
sys.addaudithook(audit)
runpy.run_path(entry, run_name='__main__')
'''
SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
<rect x="50" y="50" width="500" height="120" fill="#123456"/>
<text x="70" y="130" font-family="Arial" font-size="36" fill="#FFFFFF">{title}</text>
</svg>'''


def digest(data):
    """Return the exact byte fingerprint used by source/package parity checks."""
    return hashlib.sha256(data).hexdigest()


def snapshots(directory):
    """Read only this skill's non-cache files, never deployment environment files."""
    result = {}
    for parent, directories, files in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories if name not in {'.venv', '__pycache__'}]
        for name in files:
            path = Path(parent) / name
            if path.is_symlink():
                raise AssertionError('Unexpected runtime/source symlink')
            if path.suffix not in {'.pyc', '.pyo'} and name != '.DS_Store':
                result[path.relative_to(directory).as_posix()] = digest(path.read_bytes())
    return result


def load_packager():
    """Load the repository maintenance builder, not a runtime dependency."""
    spec = importlib.util.spec_from_file_location('tl_pptx_acceptance_packager', ROOT / 'tools' / 'package_skills.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('Package builder unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def owned_paths(directory):
    """Enumerate the test-owned installation without following deployment links."""
    paths = [directory]
    for parent, directories, files in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories if name != '.venv']
        paths.extend(Path(parent) / name for name in directories + files)
    return paths


class TlPptxRuntimeAcceptance(unittest.TestCase):
    """Exercise the packaged public CLI and preserve source/runtime fingerprints."""

    @classmethod
    def setUpClass(cls):
        """Build and unpack an exact runtime snapshot without provisioning packages."""
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            raise RuntimeError('Read-only enforcement requires a non-root user')
        value = os.environ.get('TL_PPTX_TEST_VENV')
        if not value:
            raise RuntimeError('Set TL_PPTX_TEST_VENV to the prepared task-specific deployment venv')
        deployment = Path(value).resolve(strict=True)
        if not (deployment / 'pyvenv.cfg').is_file() or not os.access(deployment / 'bin' / 'python', os.X_OK):
            raise RuntimeError('A pre-existing usable deployment venv is required; no installation is attempted')
        if deployment.is_relative_to(ROOT):
            raise RuntimeError('Use a separate test deployment, not a repository or sibling skill environment')
        cls.temporary = tempfile.TemporaryDirectory(prefix='tl-pptx-runtime-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.workspace = Path(cls.temporary.name)
        cls.source_before = snapshots(SOURCE)
        packager = load_packager()
        selected = packager.collect_files(SOURCE)
        cls.archive = cls.workspace / 'tl-pptx.zip'
        cls.build_report = packager.build_package(SOURCE, cls.archive)
        cls.archive_before = digest(cls.archive.read_bytes())
        destination = cls.workspace / 'install'
        with zipfile.ZipFile(cls.archive) as package:
            expected = {'tl-pptx/' + name for name in selected}
            if set(package.namelist()) != expected:
                raise AssertionError('Runtime ZIP members differ from the explicit manifest')
            for member in package.infolist():
                path = PurePosixPath(member.filename)
                if path.is_absolute() or '..' in path.parts or member.is_dir():
                    raise AssertionError('Unexpected package entry')
            package.extractall(destination)
        cls.installation = destination / 'tl-pptx'
        cls.runtime_before = snapshots(cls.installation)
        if cls.runtime_before != {name: digest(data) for name, data in selected.items()}:
            raise AssertionError('Extracted runtime differs from the selected source bytes')
        (cls.installation / 'scripts' / '.venv').symlink_to(deployment, target_is_directory=True)
        cls.interpreter = cls.installation / 'scripts' / '.venv' / 'bin' / 'python'
        cls.paths = owned_paths(cls.installation)
        cls.addClassCleanup(cls.restore_owned_permissions)
        for path in reversed(cls.paths):
            path.chmod(0o555 if path.is_dir() else 0o444)
        print(json.dumps({'platform': sys.platform, 'maintenance_python': sys.version.split()[0],
                          'fixture_kind': 'synthetic', 'deployment': 'preprovisioned test venv link',
                          'not_verified': ['other OSes', 'PowerPoint/LibreOffice rendering',
                                           'OS-level isolation', 'DeepAgents delivery', 'cloud upload']}, ensure_ascii=False))

    @classmethod
    def restore_owned_permissions(cls):
        """Restore only files created by this test; never chmod the linked venv."""
        for path in cls.paths:
            if path.exists() and not path.is_symlink():
                path.chmod(0o755 if path.is_dir() else 0o644)

    @classmethod
    def tearDownClass(cls):
        """Verify archive, source and runtime files are unchanged by execution."""
        if snapshots(SOURCE) != cls.source_before:
            raise AssertionError('TL PPTX source changed while acceptance ran')
        if snapshots(cls.installation) != cls.runtime_before:
            raise AssertionError('Installed runtime changed during execution')
        if digest(cls.archive.read_bytes()) != cls.archive_before:
            raise AssertionError('Runtime archive changed during execution')

    def setUp(self):
        """Give every test a clean task directory outside both source and install."""
        task = tempfile.TemporaryDirectory(prefix='task-', dir=self.workspace)
        self.addCleanup(task.cleanup)
        self.task = Path(task.name)
        self.environment = {'PATH': '', 'HOME': str(self.task), 'TMPDIR': str(self.task),
                            'PYTHONIOENCODING': 'utf-8',
                            'ROUTER_CONFIG_VARIABLES': json.dumps({'commonParam': [
                                {'name': 'questionId', 'value': 'tl-pptx-package-acceptance'}]})}

    def invoke(self, *args, expected=0):
        """Execute the installed interpreter and retain stdout, stderr and exit code."""
        completed = subprocess.run([str(self.interpreter), '-I', '-B', '-c', AUDIT_RUNNER,
                                    str(self.installation / 'scripts' / 'tl_pptx.py'), str(ROOT), *args],
                                   cwd=self.task, env=self.environment, capture_output=True,
                                   text=True, timeout=120, check=False)
        self.assertEqual(completed.returncode, expected,
                         f'exit={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}')
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1, completed.stdout)
        return json.loads(completed.stdout)

    def write_svg(self, name='page.svg', title='Independent native slide'):
        """Write a synthetic fixture into this test's registered temporary directory."""
        data = SVG.format(title=title).encode('utf-8')
        (self.task / name).write_bytes(data)
        return data

    def test_runtime_is_readonly_and_contains_no_tests_or_environment(self):
        """The archive ships exact reviewed runtime bytes, not tests or deployment files."""
        with zipfile.ZipFile(self.archive) as package:
            self.assertFalse(any('/test/' in name or '/.venv/' in name or '__pycache__' in name
                                 for name in package.namelist()))
        for path in self.paths:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o222, 0)
        with self.assertRaises(PermissionError):
            (self.installation / 'forbidden-probe').write_bytes(b'no write')

    def test_checks_leave_no_task_files(self):
        """Read-only and write-capability checks return markers without residue."""
        report = self.invoke('check')
        self.assertEqual(report['status'], 'environment-ok')
        self.assertEqual(report['task_write_check'], 'not_requested')
        report = self.invoke('check', '--write')
        self.assertEqual(report['task_write_check'], 'passed')
        self.assertEqual(list(self.task.iterdir()), [])

    def test_native_two_page_conversion_archive_and_source_preservation(self):
        """Convert ordered pages from an isolated package and inspect native output text."""
        original = {name: self.write_svg(name, title) for name, title in
                    [('first.svg', 'First page'), ('second.svg', 'Second page')]}
        report = self.invoke('convert', 'result.pptx', 'first.svg', 'second.svg')
        self.assertEqual(report['status'], 'verified')
        self.assertTrue(report['publication']['published'])
        self.assertTrue(report['cleanup']['cleanup_complete'])
        self.assertEqual(report['archive']['archive_status'], 'success')
        self.assertTrue(report['archive']['files'])
        self.assertEqual(report['source_count'], 2)
        payload = (self.task / 'result.pptx').read_bytes()
        self.assertEqual(report['sha256'], digest(payload))
        with zipfile.ZipFile(self.task / 'result.pptx') as package:
            for number, title in [(1, b'First page'), (2, b'Second page')]:
                slide = package.read(f'ppt/slides/slide{number}.xml')
                self.assertIn(title, slide)
                self.assertIn(b'<p:sp', slide)
                self.assertNotIn(b'<p:pic', slide)
        for name, data in original.items():
            self.assertEqual((self.task / name).read_bytes(), data)
        self.assertFalse(list(self.task.glob('tl-pptx-source-*')))
        self.assertFalse(list(self.task.glob('tl-pptx-probe-*')))
        self.invoke('convert', 'result.pptx', 'first.svg', expected=1)
        self.assertEqual((self.task / 'result.pptx').read_bytes(), payload)

    def test_full_builder_analysis_import_and_preserve_rebuild(self):
        """All core stage-two commands work with repository reads denied."""
        original = self.write_svg(title='Original source text')
        manifest = {'slides': [{'svg': 'page.svg', 'notes': 'Native note'}],
                    'options': {'transition': 'fade'}}
        (self.task / 'build.json').write_text(json.dumps(manifest), encoding='utf-8')
        report = self.invoke('build', 'built.pptx', 'build.json')
        self.assertEqual(report['validation']['builder'], 'full-upstream')
        self.assertTrue(report['cleanup']['cleanup_complete'])
        self.invoke('analyze', 'analysis.json', 'built.pptx')
        analysis = json.loads((self.task / 'analysis.json').read_text(encoding='utf-8'))
        self.assertEqual(analysis['slide_count'], 1)
        self.assertIn('Original source text', json.dumps(analysis['content']))
        self.assertIn('Native note', json.dumps(analysis['notes']))
        self.invoke('import', 'workspace.zip', 'built.pptx')
        (self.task / 'rebuild.json').write_text(json.dumps({'workspace': 'workspace.zip'}), encoding='utf-8')
        rebuilt = self.invoke('rebuild', 'rebuilt.pptx', 'rebuild.json')
        self.assertTrue(rebuilt['publication']['published'])
        with zipfile.ZipFile(self.task / 'built.pptx') as source, zipfile.ZipFile(self.task / 'rebuilt.pptx') as target:
            self.assertEqual(source.read('ppt/slides/slide1.xml'), target.read('ppt/slides/slide1.xml'))
            def linked_note(package):
                # Preserve mode may rename copied parts to avoid collisions.
                # Resolve the slide's real notes relationship, never guess a filename.
                relationships = ET.fromstring(package.read('ppt/slides/_rels/slide1.xml.rels'))
                notes = [node for node in relationships
                         if node.get('Type') == 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide']
                self.assertEqual(len(notes), 1, 'Slide must retain exactly one notes relationship')
                self.assertNotEqual(notes[0].get('TargetMode'), 'External')
                target_name = notes[0].get('Target')
                self.assertTrue(target_name)
                resolved = (posixpath.normpath(target_name.lstrip('/')) if target_name.startswith('/')
                            else posixpath.normpath(posixpath.join('ppt/slides', target_name)))
                self.assertIn(resolved, package.namelist(), 'Notes relationship must resolve to an existing part')
                payload = package.read(resolved)
                note_xml = ET.fromstring(payload)
                texts = note_xml.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}t')
                self.assertIn('Native note', ''.join(node.text or '' for node in texts))
                return payload
            self.assertEqual(linked_note(source), linked_note(target))
        self.invoke('analyze', 'rebuilt-analysis.json', 'rebuilt.pptx')
        rebuilt_analysis = json.loads((self.task / 'rebuilt-analysis.json').read_text(encoding='utf-8'))
        self.assertIn('Native note', json.dumps(rebuilt_analysis['notes']))
        self.assertEqual((self.task / 'page.svg').read_bytes(), original)
        for prefix in ('tl-pptx-build-', 'tl-pptx-analysis-', 'tl-pptx-import-', 'tl-pptx-roundtrip-'):
            self.assertFalse(list(self.task.glob(prefix + '*')))

    def test_explicit_text_outline_publishes_real_shapes_not_pictures(self):
        """Use library-supplied sample font bytes only; no installed-font discovery."""
        import fitz
        data = fitz.Font('china-s').buffer
        (self.task / 'sample.ttf').write_bytes(data)
        spec = {'text': 'OB8', 'font': 'sample.ttf', 'font_authorized': True}
        (self.task / 'letters.json').write_text(json.dumps(spec), encoding='utf-8')
        self.invoke('text-outline', 'letters.svg', 'letters.json')
        self.invoke('convert', 'letters.pptx', 'letters.svg')
        with zipfile.ZipFile(self.task / 'letters.pptx') as package:
            slide = package.read('ppt/slides/slide1.xml')
            self.assertEqual(slide.count(b'<a:custGeom'), 3)
            self.assertNotIn(b'<p:pic', slide)
        self.assertEqual((self.task / 'sample.ttf').read_bytes(), data)
        spec['text'] = 'a' + chr(0x0301)
        (self.task / 'unsupported.json').write_text(json.dumps(spec), encoding='utf-8')
        self.invoke('text-outline', 'refused.svg', 'unsupported.json', expected=1)
        self.assertFalse((self.task / 'refused.svg').exists())

    def test_template_import_is_not_an_authored_template(self):
        """Imported reference material alone must not qualify for registration."""
        self.write_svg()
        self.invoke('convert', 'source.pptx', 'page.svg')
        self.invoke('template-import', 'template.zip', 'source.pptx')
        report = self.invoke('template-register', 'registered.zip', 'template.zip',
                             '--kind', 'deck', '--template-id', 'fixture', expected=1)
        self.assertIn('has no templates/ directory', report['message'])
        self.assertFalse((self.task / 'registered.zip').exists())

    def test_authored_style_create_register_and_apply(self):
        """Use explicit authored template files; do not mock the integrity or quality gate."""
        style = (SOURCE / 'scripts/test/template_style_fixture.txt').read_bytes()
        def write_bundle(name, kind, entries):
            meta = {'schema': 'tl-pptx.workspace.v1', 'kind': kind,
                    'source_sha256': '', 'files': {k: digest(v) for k, v in entries.items()}}
            with zipfile.ZipFile(self.task / name, 'w') as package:
                for key, value in entries.items():
                    package.writestr(key, value)
                package.writestr('tl-workspace.json', json.dumps(meta))
        write_bundle('style.zip', 'template-source', {'templates/design_spec.md': style})
        write_bundle('project.zip', 'project-workspace', {'seed.txt': b'original'})
        for action in ('create', 'register'):
            self.invoke('template-' + action, action + '.zip', 'style.zip', '--kind', 'style',
                        '--template-id', 'incident-postmortem')
        self.invoke('template-apply', 'applied.zip', 'project.zip', 'register.zip')
        with zipfile.ZipFile(self.task / 'applied.zip') as package:
            installed = package.read('templates/design_spec.style.incident-postmortem.md')
            self.assertIn(b'## II. Communication Method', installed)
            self.assertIn(b'blameless', installed.lower())
            receipt = json.loads(package.read('template_install.json'))
            self.assertEqual(receipt['installed_specs'], ['design_spec.style.incident-postmortem.md'])
            self.assertEqual(package.read('seed.txt'), b'original')

    def test_optional_deck_preview(self):
        """Exercise a reviewed optional deck pack with the public selector and preview."""
        asset = ROOT / 'dist/runtime/tl-pptx-assets-decks-7e0df670-20261117.zip'
        if not asset.is_file():
            self.skipTest('Optional offline deck pack is not installed')
        (self.task / 'decks.zip').write_bytes(asset.read_bytes())
        selection = {'requests': [{'pack': 'decks.zip', 'ids': ['中国电信']}]}
        (self.task / 'selection.json').write_text(json.dumps(selection), encoding='utf-8')
        self.invoke('asset-select', 'selected.zip', 'selection.json')
        self.invoke('template-preview', 'preview.pptx', 'selected.zip')
        with zipfile.ZipFile(self.task / 'preview.pptx') as package:
            self.assertIsNone(package.testzip())
            self.assertIn('ppt/slides/slide1.xml', package.namelist())

    def test_tampered_release_fails_closed(self):
        """An extra script, modified license, missing runtime or edited index rejects a copied install."""
        with zipfile.ZipFile(self.archive) as bundle:
            bundle.extractall(self.task)
        root = self.task / 'tl-pptx'
        command = [str(self.interpreter), '-B', str(root / 'scripts/attribution_guard.py')]
        def check(code):
            self.assertEqual(subprocess.run(command, capture_output=True, check=False).returncode, code)
        check(0)
        extra = root / 'scripts/unlisted.py'
        extra.write_bytes(b'# not in release')
        check(78)
        extra.unlink()
        for name in ('references/LICENSE-PPT-MASTER.txt', 'references/tl-release-integrity.json',
                     'scripts/tl_templates.py'):
            path = root / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            check(78)
            path.unlink()
            check(78)
            path.write_bytes(original)
        check(0)

    def test_missing_archive_configuration_preserves_main_file(self):
        """A missing current questionId is reported as incomplete delivery, not lost work."""
        self.environment.pop('ROUTER_CONFIG_VARIABLES')
        self.write_svg()
        report = self.invoke('convert', 'result.pptx', 'page.svg', expected=3)
        self.assertEqual(report['status'], 'verified')
        self.assertTrue(report['publication']['published'])
        self.assertEqual(report['archive']['archive_status'], 'failed')
        self.assertTrue(report['cleanup']['cleanup_complete'])
        self.assertEqual({path.name for path in self.task.iterdir()}, {'page.svg', 'result.pptx'})

    def test_external_resource_rejected_without_output_or_snapshot(self):
        """External data references cannot make the converter access another file."""
        text = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><image href="file:///private/input.png"/></svg>'
        (self.task / 'page.svg').write_text(text, encoding='utf-8')
        report = self.invoke('convert', 'result.pptx', 'page.svg', expected=1)
        self.assertEqual(report['status'], 'error')
        self.assertEqual({path.name for path in self.task.iterdir()}, {'page.svg'})

    def test_argument_errors_remain_machine_readable(self):
        """Unknown commands return one JSON error and the actual argument-error exit."""
        report = self.invoke('unsupported', expected=2)
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['error_type'], 'ArgumentError')


if __name__ == '__main__':
    unittest.main()
