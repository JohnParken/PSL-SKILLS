"""Repository release checks: build and exercise the self-contained DOCX runtime ZIP.

No test code is installed into the runtime. These are synthetic, non-visual tests;
the operating system and interpreter executing this suite are the tested platform.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from xml.parsers import expat
import zipfile
import zlib

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'skills' / 'docx'
TOOL = REPO / 'tools' / 'package_docx_runtime.py'
MANIFEST = REPO / 'packaging/runtime-manifests/docx.json'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
EXCLUDED = {'references/maintenance-validation.md', 'references/reference-loading.md',
            'references/token-optimization.md', 'scripts/reference_loading_audit.py',
            'assets/compatibility/libreoffice-control.docx',
            'scripts/test/reference_loading_audit.py', 'scripts/test/fixtures/libreoffice-control.docx'}


def packager():
    specification = importlib.util.spec_from_file_location('docx_runtime_builder', TOOL)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def fingerprints(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def png(red, green, blue):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes([0, red, green, blue]))) + chunk(b'IEND', b''))


class RuntimePackageBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='docx-runtime-build-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.builder = packager()

    def copy_source(self):
        return Path(shutil.copytree(SOURCE, self.work / 'source'))

    def test_manifest_covers_business_files_and_excludes_development(self):
        files = self.builder.collect_files(SOURCE)
        actual = {p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')
                  and p.name != '.DS_Store'}
        expected = {n for n in actual if not n.startswith('scripts/test/') and n not in EXCLUDED}
        self.assertEqual(set(files), expected)
        self.assertTrue(EXCLUDED.isdisjoint(files))
        self.assertEqual(len(files), 79)
        self.assertEqual(json.loads(MANIFEST.read_text(encoding='utf-8'))['exclude_files'], {})
        for name in ('maintenance-validation.md', 'reference-loading.md', 'token-optimization.md'):
            self.assertFalse((SOURCE / 'references' / name).exists())
            self.assertTrue((REPO / 'docs' / 'maintenance' / 'docx' / name).is_file())
        self.assertTrue((SOURCE / 'scripts/test/reference_loading_audit.py').is_file())
        self.assertTrue((SOURCE / 'scripts/test/fixtures/libreoffice-control.docx').is_file())
        for name, contents in files.items():
            self.assertEqual(contents, (SOURCE / name).read_bytes(), name)
        for name in ('scripts/create_docx.py', 'scripts/read_docx.py', 'scripts/run_docx_edit.py',
                     'scripts/manage_comments.py', 'scripts/comment.py', 'scripts/merge_runs.py',
                     'scripts/controlled_cleanup.py', 'scripts/office/validators/generation.py'):
            self.assertIn(name, files)

    def test_zip_is_deterministic_complete_and_source_unchanged(self):
        before = fingerprints(SOURCE)
        first, second = self.work / 'first.zip', self.work / 'second.zip'
        self.builder.build_package(SOURCE, first)
        self.builder.build_package(SOURCE, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        expected = self.builder.collect_files(SOURCE)
        with zipfile.ZipFile(first) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {'docx/' + n for n in expected})
            self.assertEqual(len(archive.namelist()), len(expected))
            for name, contents in expected.items():
                self.assertEqual(archive.read('docx/' + name), contents, name)
        self.assertEqual(fingerprints(SOURCE), before)

    def test_unknown_file_is_rejected(self):
        source = self.copy_source()
        (source / 'scripts' / 'unreviewed.py').write_text('print("new")\n')
        with self.assertRaises((ValueError, OSError)):
            self.builder.collect_files(source)

    def test_missing_required_file_is_rejected(self):
        source = self.copy_source()
        (source / 'scripts' / 'office' / 'xmlio.py').unlink()
        with self.assertRaises((ValueError, OSError)):
            self.builder.collect_files(source)

    def test_source_file_and_directory_symlinks_are_rejected(self):
        for relative in ('scripts/create_docx.py', 'scripts/test'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(dir=self.work) as temp:
                source = Path(shutil.copytree(SOURCE, Path(temp) / 'source'))
                target = source / relative
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                target.symlink_to(SOURCE / relative, target_is_directory=(SOURCE / relative).is_dir())
                with self.assertRaises((ValueError, OSError)):
                    self.builder.collect_files(source)

    def test_existing_archive_and_symlink_target_are_preserved(self):
        output = self.work / 'existing.zip'
        output.write_bytes(b'user-owned archive')
        with self.assertRaises((ValueError, OSError)):
            self.builder.build_package(SOURCE, output)
        self.assertEqual(output.read_bytes(), b'user-owned archive')
        link = self.work / 'linked.zip'
        link.symlink_to(output)
        with self.assertRaises((ValueError, OSError)):
            self.builder.build_package(SOURCE, link)
        self.assertTrue(link.is_symlink())
        self.assertEqual(output.read_bytes(), b'user-owned archive')

    def test_manifest_rejects_duplicate_unsafe_and_excluded_runtime_entries(self):
        original = json.loads(MANIFEST.read_text())
        for entry in (original['files'][0], '../outside.py', 'scripts/test/test_extra.py'):
            with self.subTest(entry=entry):
                changed = dict(original, files=original['files'] + [entry])
                manifest = self.work / 'changed-manifest.json'
                manifest.write_text(json.dumps(changed))
                with self.assertRaises((ValueError, OSError)):
                    self.builder.collect_files(SOURCE, manifest)

    def test_output_inside_source_is_rejected_without_modifying_source(self):
        source = self.copy_source()
        before = fingerprints(source)
        with self.assertRaises((ValueError, OSError)):
            self.builder.build_package(source, source / 'runtime.zip')
        self.assertEqual(fingerprints(source), before)

    def test_cli_reports_success_and_refuses_overwrite(self):
        output = self.work / 'cli.zip'
        command = [sys.executable, '-I', '-S', '-B', str(TOOL), '--source', str(SOURCE),
                   '--output', str(output)]
        first = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        report = json.loads(first.stdout)
        self.assertEqual(report['status'], 'packaged')
        self.assertEqual(report['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        content = output.read_bytes()
        second = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(second.returncode, 1)
        self.assertEqual(second.stdout, '')
        self.assertIn('Runtime packaging failed:', second.stderr)
        self.assertEqual(output.read_bytes(), content)

    def test_cache_files_are_not_packaged(self):
        source = self.copy_source()
        cache = source / 'scripts' / '__pycache__'
        cache.mkdir(exist_ok=True)
        (cache / 'create_docx.cpython-test.pyc').write_bytes(b'cache')
        (source / '.DS_Store').write_bytes(b'metadata')
        self.assertEqual(self.builder.collect_files(source), self.builder.collect_files(SOURCE))


class InstalledRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info < (3, 10) or expat.version_info < (2, 6, 0):
            raise RuntimeError('Behavior acceptance requires Python >=3.10 and Expat >=2.6.0; do not count skips as acceptance')
        cls.temp = tempfile.TemporaryDirectory(prefix='docx-runtime-installed-')
        cls.root = Path(cls.temp.name)
        archive = cls.root / 'runtime.zip'
        packager().build_package(SOURCE, archive)
        cls.install = cls.root / 'installation'
        with zipfile.ZipFile(archive) as package:
            package.extractall(cls.install)
        cls.skill = cls.install / 'docx'
        cls.before = fingerprints(cls.skill)
        for path in cls.install.rglob('*'):
            path.chmod(0o555 if path.is_dir() else 0o444)
        cls.install.chmod(0o555)

    @classmethod
    def tearDownClass(cls):
        try:
            if fingerprints(cls.skill) != cls.before:
                raise AssertionError('Installed runtime contents changed during acceptance')
        finally:
            cls.install.chmod(0o755)
            for path in cls.install.rglob('*'):
                path.chmod(0o755 if path.is_dir() else 0o644)
            cls.temp.cleanup()

    def setUp(self):
        self.task = tempfile.TemporaryDirectory(prefix='task-', dir=self.root)
        self.addCleanup(self.task.cleanup)
        self.work = Path(self.task.name)
        self.environment = dict(os.environ, PATH='', DOCX_DEBUG_ROUTER_CONFIG='0')
        self.environment.pop('PYTHONPATH', None)
        self.environment.pop('PYTHONHOME', None)
        self.environment['ROUTER_CONFIG_VARIABLES'] = json.dumps([
            {'name': 'commonParam', 'value': json.dumps([{'name': 'questionId', 'value': 'runtime-acceptance'}])}])

    def run_script(self, script, *args, payload=None, expected=0):
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(self.skill / 'scripts' / script),
                                 *map(str, args)], cwd=self.work, env=self.environment,
                                input=None if payload is None else json.dumps(payload, ensure_ascii=False),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stdout + '\n' + result.stderr)
        return result

    def create(self, name, blocks, **options):
        result = self.run_script('run_docx_task.py', 'create', name,
                                 payload={'theme': 'official', 'blocks': blocks, **options})
        data = json.loads(result.stdout)
        self.assert_delivery(data, 'verified')
        return self.work / data['output']

    def assert_delivery(self, data, status):
        self.assertEqual(data['status'], status, data)
        self.assertEqual(data['archive_status'], 'copied', data)
        self.assertEqual(data['task_status'], 'completed', data)
        self.assertTrue(data['cleanup']['cleanup_complete'], data)
        self.assertTrue(data['cleanup']['manifest_removed'], data)
        output = self.work / data['output']
        self.assertGreater(output.stat().st_size, 0)
        self.assertEqual(output.read_bytes(), (self.work / 'output/runtime-acceptance' / output.name).read_bytes())
        self.assertFalse(list(self.work.glob('docx-task-*')))
        self.assertFalse(list(self.work.glob('docx-edit-*')))

    def body(self, path):
        with zipfile.ZipFile(path) as package:
            return ET.fromstring(package.read('word/document.xml'))

    def test_installation_is_readonly_and_runtime_checks_pass(self):
        self.assertFalse((self.skill / 'scripts/test').exists())
        for path in self.install.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o222, 0)
        # Check actual enforcement, not merely mode bits; root cannot validate this property.
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            self.fail('Read-only installation acceptance must execute as a non-root user')
        with self.assertRaises(PermissionError):
            (self.skill / 'should-not-write').write_bytes(b'probe')
        self.assertIn('read-check-ok', self.run_script('read_docx.py', '--check').stdout)
        self.assertIn('check-ok', self.run_script('run_docx_task.py', 'check').stdout)

    def test_create_natural_pagination_and_collision_preserve_original(self):
        blocks = [{'type': 'title', 'text': '运行包验收'}, {'type': 'heading', 'level': 1, 'text': '工作情况'},
                  {'type': 'paragraph', 'text': '正文不强制换页。'},
                  {'type': 'table', 'header': True, 'rows': [['项目', '结果'], ['验收', '通过']]}]
        path = self.create('report.docx', blocks)
        initial = path.read_bytes()
        body = self.body(path)
        self.assertFalse(body.findall('.//' + W + 'pageBreakBefore'))
        self.assertFalse([node for node in body.iter(W + 'br') if node.get(W + 'type') == 'page'])
        self.assertEqual(len(body.findall('.//' + W + 'sectPr')), 1)
        self.assertTrue(body.findall('.//' + W + 'tblHeader'))
        repeated = self.create('report.docx', blocks)
        self.assertEqual(repeated.name, 'report_第2版.docx')
        self.assertEqual(path.read_bytes(), initial)
        self.assertIn('正文不强制换页。', self.run_script('read_docx.py', path.name).stdout)

    def test_all_five_edits_preserve_inputs_and_archive(self):
        source = self.create('source.docx', [{'type': 'paragraph', 'text': '合同 ABC'}])
        table = self.create('table.docx', [{'type': 'table', 'header': True,
                                          'rows': [['项目', '金额'], ['甲', '10']]}])
        (self.work / 'red.png').write_bytes(png(255, 0, 0))
        (self.work / 'blue.png').write_bytes(png(0, 0, 255))
        image = self.create('image.docx', [{'type': 'image', 'path': 'red.png', 'width_mm': 5,
                                          'height_mm': 5, 'alt': 'pixel'}])
        with zipfile.ZipFile(image) as package:
            media = next(n for n in package.namelist() if n.startswith('word/media/'))
        before = {p.name: p.read_bytes() for p in (source, table, image, self.work / 'red.png', self.work / 'blue.png')}
        cases = [
            ('text', source.name, {'replacements': [{'old': 'ABC', 'new': 'XYZ', 'count': 1}]}, []),
            ('structure', source.name, {'operations': [{'type': 'insert_paragraph', 'paragraph_index': 0,
              'expected_text': '合同 ABC', 'position': 'after', 'text': '补充'}]}, []),
            ('tables', table.name, {'operation': {'type': 'insert_row', 'table_index': 0, 'row_index': 1,
              'expected_rows': 2, 'expected_text': ['甲', '10'], 'position': 'after', 'cells': ['乙', '20']}}, []),
            ('image', image.name, {'media_part': media, 'expected_sha256': hashlib.sha256(before['red.png']).hexdigest(),
              'expected_relationships': 1, 'expected_references': 1}, ['--replacement', 'blue.png']),
            ('comments', source.name, {'action': 'add', 'paragraph_index': 0, 'expected_text': '合同 ABC',
              'anchor_text': '合同 ABC', 'text': '核对金额', 'author': '审阅者'}, []),
        ]
        for kind, name, spec, args in cases:
            with self.subTest(kind=kind):
                result = self.run_script('run_docx_edit.py', kind, name, 'edited-' + kind + '.docx',
                                         *args, payload=spec)
                self.assert_delivery(json.loads(result.stdout), 'edited')
        self.assertIn('合同 XYZ', self.run_script('read_docx.py', 'edited-text.docx').stdout)
        self.assertIn('补充', self.run_script('read_docx.py', 'edited-structure.docx').stdout)
        self.assertIn('乙\t20', self.run_script('read_docx.py', 'edited-tables.docx').stdout)
        with zipfile.ZipFile(self.work / 'edited-image.docx') as package:
            self.assertEqual(package.read(media), before['blue.png'])
        with zipfile.ZipFile(self.work / 'edited-comments.docx') as package:
            self.assertIn('核对金额', package.read('word/comments.xml').decode())
        self.assertEqual({n: (self.work / n).read_bytes() for n in before}, before)

    def test_bound_pages_collect_full_table_and_reject_changed_source(self):
        expected = [[str(i), '产品' + str(i)] for i in range(42)]
        source = self.create('pages.docx', [{'type': 'table', 'header': False, 'rows': expected}])
        rows, cursor, first_cursor = [], None, None
        for _ in range(100):
            args = ['--compact-json', '--tables', 'rows', '--max-output-bytes', '1700']
            if cursor:
                args += ['--cursor', cursor]
            result = self.run_script('read_docx.py', source.name, *args)
            self.assertLessEqual(len(result.stdout.encode()), 1700)
            data = json.loads(result.stdout)
            self.assertFalse(data['coverage']['document_complete'])
            rows.extend(block['cells'] for block in data['blocks'] if block['type'] == 'table_row')
            cursor = data['next_cursor']
            first_cursor = first_cursor or cursor
            if cursor is None:
                break
        else:
            self.fail('Bounded reading made no terminal progress')
        self.assertEqual(rows, expected)
        self.assertIsNotNone(first_cursor)
        with zipfile.ZipFile(source, 'a') as package:
            package.writestr('word/extra.xml', '<extra/>')
        failed = self.run_script('read_docx.py', source.name, '--compact-json', '--tables', 'rows',
                                 '--max-output-bytes', '1700', '--cursor', first_cursor, expected=2)
        self.assertIn('source bytes changed', failed.stderr)

    def test_legacy_comment_merge_and_package_validation(self):
        source = self.create('source.docx', [{'type': 'paragraph', 'text': 'original text'}])
        original = source.read_bytes()
        self.run_script('comment.py', source.name, 'note', '-o', 'commented.docx')
        self.run_script('merge_runs.py', source.name, '-o', 'merged.docx')
        self.run_script('office/validate.py', 'merged.docx', '--original', source.name)
        self.assertEqual(source.read_bytes(), original)
        with zipfile.ZipFile(self.work / 'commented.docx') as package:
            self.assertIn('note', package.read('word/comments.xml').decode())

    def test_invalid_generation_and_output_path_leave_no_owned_temporary_files(self):
        for name, spec in [('bad.docx', {'blocks': [{'type': 'unknown'}]}),
                           ('./bad-name.docx', {'blocks': [{'type': 'paragraph', 'text': 'test'}]})]:
            with self.subTest(name=name):
                self.run_script('run_docx_task.py', 'create', name, payload=spec, expected=1)
                self.assertFalse(list(self.work.glob('docx-task-*')))
                self.assertFalse(list(self.work.glob('*.docx')))

    def test_archive_failure_preserves_primary_and_cleans_temporary_files(self):
        (self.work / 'output').write_bytes(b'protected archive blocker')
        result = self.run_script('run_docx_task.py', 'create', 'survives.docx',
                                 payload={'blocks': [{'type': 'paragraph', 'text': '保留主文件'}]})
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'verified')
        self.assertEqual(data['archive_status'], 'failed')
        self.assertTrue(data['cleanup']['cleanup_complete'])
        self.assertTrue(data['cleanup']['manifest_removed'])
        self.assertEqual((self.work / 'output').read_bytes(), b'protected archive blocker')
        self.assertIn('保留主文件', self.run_script('read_docx.py', 'survives.docx').stdout)
        self.assertFalse(list(self.work.glob('docx-task-*')))

    def test_source_and_packaged_delivery_instructions_distinguish_statuses(self):
        for root in (SOURCE, self.skill):
            with self.subTest(root='source' if root == SOURCE else 'installed'):
                entry = (root / 'SKILL.md').read_text(encoding='utf-8')
                self.assertIn('创建核对 `status=verified`，编辑核对 `status=edited`', entry)
                self.assertIn('退出0不代表归档成功', entry)
                delivery = (root / 'references/delivery.md').read_text(encoding='utf-8')
                self.assertIn('退出0不代表归档成功', delivery)
                self.assertIn('`status=completed`', delivery)
                self.assertIn('`status=edited`', delivery)
                self.assertIn('`status=verified`', delivery)
                self.assertIn('`archive_status`', delivery)

    def test_explicit_archive_conflict_exits_zero_but_reports_failed_and_preserves_files(self):
        primary = self.create('primary.docx', [{'type': 'paragraph', 'text': '历史归档内容'}])
        historical = self.work / 'output/runtime-acceptance' / primary.name
        history_bytes = historical.read_bytes()
        replacement = self.create('replacement.docx', [{'type': 'paragraph', 'text': '当前主文件内容'}])
        primary.write_bytes(replacement.read_bytes())
        current_bytes = primary.read_bytes()
        self.assertNotEqual(current_bytes, history_bytes)
        process = self.run_script('archive_deliverables.py', primary.name, expected=0)
        data = json.loads(process.stdout)
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(data['archive_status'], 'failed')
        self.assertEqual(len(data['files']), 1)
        self.assertEqual(data['files'][0]['archive_status'], 'failed')
        self.assertTrue(data['warnings'])
        self.assertIn('archive-warning:', process.stderr)
        self.assertNotIn('文件归档成功', data.get('archive_user_notice') or '')
        self.assertEqual(primary.read_bytes(), current_bytes)
        self.assertEqual(historical.read_bytes(), history_bytes)
        self.assertIn('当前主文件内容', self.run_script('read_docx.py', primary.name).stdout)
        self.assertFalse(list(self.work.glob('docx-task-*')))

    def test_cleanup_failure_is_reported_and_preserves_edited_primary(self):
        self.create('source.docx', [{'type': 'paragraph', 'text': '合同 ABC'}])
        code = '''import json, os, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import run_docx_edit as editor
original = os.unlink
def deny_manifest(path, *args, **kwargs):
    if str(path).startswith('docx-edit-') and str(path).endswith('.cleanup.json'):
        raise PermissionError('injected manifest removal failure')
    return original(path, *args, **kwargs)
with patch.object(editor.os, 'unlink', side_effect=deny_manifest) as unlink:
    with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {unlink}):
        result = editor.edit_task('text', 'source.docx', 'edited.docx',
                                 {'replacements': [{'old': 'ABC', 'new': 'XYZ'}]})
print(json.dumps(result, ensure_ascii=False))
'''
        process = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, str(self.skill / 'scripts')],
                                 cwd=self.work, env=self.environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        data = json.loads(process.stdout)
        self.assertEqual(data['status'], 'edited')
        self.assertEqual(data['task_status'], 'cleanup_incomplete')
        self.assertFalse(data['cleanup']['cleanup_complete'])
        self.assertTrue((self.work / data['task_manifest']).is_file())
        self.assertIn('合同 XYZ', self.run_script('read_docx.py', 'edited.docx').stdout)


if __name__ == '__main__':
    unittest.main()
