"""Synthetic task-directory archival and explicit owned-config lifecycle tests."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from run_wps_task import create_document
from run_wps_job import run_job
from wps_delivery import finish_delivery
from test_wps_ooxml import package
from _wps_artifact_runtime.controlled_cleanup import finalize_cleanup, finalize_failed_cleanup
from _wps_artifact_runtime import archive_deliverables

SCRIPTS = Path(__file__).resolve().parent
SPEC = {'blocks': [{'type': 'paragraph', 'text': 'Reliable text'}]}
CONFIG = json.dumps({'commonParam': [{'name': 'questionId', 'value': 'wps-test'}]})


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wps-delivery-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': CONFIG})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @contextlib.contextmanager
    def working(self):
        previous = Path.cwd()
        os.chdir(self.root)
        try:
            yield
        finally:
            os.chdir(previous)

    def cli(self, script, *arguments, stdin=None, scripts=SCRIPTS):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(scripts / script), *map(str, arguments)], cwd=self.root,
                              input=stdin, text=True, encoding='utf-8', capture_output=True, timeout=20)

    def job(self, spec=SPEC, output='job.wps'):
        return run_job({'script': 'run_wps_task.py', 'arguments': ['create', output, '--spec', '@spec'],
                        'output': output, 'configs': {'spec': spec}})

    def archived(self, result, name):
        self.assertEqual(result['delivery']['archive_status'], 'copied', result)
        self.assertTrue(result['delivery']['files'][0]['archive_verified'])
        self.assertEqual((self.root / name).read_bytes(), (self.root / 'output/wps-test' / Path(name).name).read_bytes())
        notice = result['delivery']['archive_user_notice']
        self.assertIn('云空间', notice)
        self.assertNotIn('wps-test', notice)
        self.assertNotIn('output/', notice)

    def test_three_clis_auto_archive_and_preserve_inputs(self):
        (self.root / 'spec.json').write_text(json.dumps(SPEC), encoding='utf-8')
        created = self.cli('run_wps_task.py', 'create', 'created.wps', '--spec', 'spec.json')
        self.assertEqual(created.returncode, 0, created.stderr)
        self.archived(json.loads(created.stdout), 'created.wps')
        original = (self.root / 'created.wps').read_bytes()
        edited = self.cli('run_wps_edit.py', 'text', 'created.wps', 'edited.wps', '--find', 'Reliable', '--replace', 'Edited')
        self.assertEqual(edited.returncode, 0, edited.stderr)
        self.archived(json.loads(edited.stdout), 'edited.wps')
        docx = package('<w:p><w:r><w:t>Exported text</w:t></w:r></w:p>')
        (self.root / 'input.docx').write_bytes(docx)
        exported = self.cli('export_wps.py', 'convert', 'input.docx', '--backend', 'rtf', '--output', 'exported.wps')
        self.assertEqual(exported.returncode, 0, exported.stdout + exported.stderr)
        self.archived(json.loads(exported.stdout), 'exported.wps')
        self.assertEqual((self.root / 'input.docx').read_bytes(), docx)
        self.assertEqual((self.root / 'created.wps').read_bytes(), original)
        self.assertEqual(json.loads((self.root / 'spec.json').read_text(encoding='utf-8')), SPEC)

    def test_missing_or_invalid_config_skips_but_keeps_main_and_cleans_owned_json(self):
        for value in (None, 'invalid JSON'):
            with self.subTest(value=value), self.working():
                if value is None:
                    os.environ.pop('ROUTER_CONFIG_VARIABLES', None)
                else:
                    os.environ['ROUTER_CONFIG_VARIABLES'] = value
                name = 'missing.wps' if value is None else 'invalid.wps'
                code, result = self.job(output=name)
                self.assertEqual(code, 0, result)
                self.assertEqual(result['delivery']['archive_status'], 'skipped')
                self.assertTrue(result['job_cleanup']['cleanup_complete'])
                self.assertTrue(Path(name).is_file())
                self.assertFalse(list(Path('.').glob('wps-job-*')))

    def test_archive_conflict_preserves_both_files_and_closes_job(self):
        existing = self.root / 'output/wps-test/job.wps'
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b'history')
        with self.working():
            code, result = self.job()
        self.assertEqual(code, 0)
        self.assertEqual(result['delivery']['archive_status'], 'failed')
        self.assertEqual(result['delivery']['files'][0]['error_code'], 'archive_conflict')
        self.assertTrue(result['job_cleanup']['cleanup_complete'])
        self.assertEqual(existing.read_bytes(), b'history')
        self.assertNotEqual((self.root / 'job.wps').read_bytes(), b'history')

    def test_copy_verification_failure_preserves_main_and_does_not_announce_success(self):
        with self.working():
            result = create_document(SPEC, Path('main.wps'))
            original = Path('main.wps').read_bytes()
            copy = archive_deliverables._copy_main_to_archive
            def corrupted(source, target, **kwargs):
                copy(source, target, **kwargs)
                target.write_bytes(b'corrupted copy')
            with patch.object(archive_deliverables, '_copy_main_to_archive', side_effect=corrupted):
                finish_delivery(result, 'main.wps')
            self.assertEqual(result['delivery']['archive_status'], 'failed')
            self.assertNotEqual(result['delivery']['archive_message'], '归档完成')
            self.assertEqual(Path('main.wps').read_bytes(), original)

    def test_bare_api_and_stdout_read_do_not_archive(self):
        from run_wps_edit import execute_edit_task
        from export_wps import convert_docx_to_wps_rtf
        with self.working():
            result = create_document(SPEC, Path('api.wps'))
            self.assertNotIn('delivery', result)
            edited = execute_edit_task(Path('api.wps'), Path('api-edit.wps'), [{'operation': 'replace_text', 'find': 'Reliable', 'replace': 'Edited'}])
            self.assertNotIn('delivery', edited)
            Path('input.docx').write_bytes(package('<w:p><w:r><w:t>API</w:t></w:r></w:p>'))
            exported = convert_docx_to_wps_rtf('input.docx', 'api-export.wps')
            self.assertNotIn('delivery', exported)
        read = self.cli('read_wps.py', 'api.wps')
        self.assertEqual(read.returncode, 0)
        self.assertFalse((self.root / 'output').exists())
        self.assertFalse(list(self.root.glob('wps-job-*')))

    def test_partial_export_archives_without_erasing_diagnostics(self):
        body = '<w:p><w:pPr><w:keepNext/></w:pPr><w:r><w:t>Partial text</w:t></w:r></w:p>'
        (self.root / 'input.docx').write_bytes(package(body))
        process = self.cli('export_wps.py', 'convert', 'input.docx', '--backend', 'rtf', '--output', 'partial.wps')
        self.assertEqual(process.returncode, 3)
        result = json.loads(process.stdout)
        self.archived(result, 'partial.wps')
        self.assertTrue(result['partial'])
        self.assertTrue(result['conversion_issues'])

    def test_partial_edit_job_keeps_exit3_and_cleans_owned_config(self):
        source = br'{\rtf1{\header omitted}Reliable text\par}'
        (self.root / 'source.rtf').write_bytes(source)
        with self.working():
            code, result = run_job({'script': 'run_wps_edit.py', 'arguments': ['batch', 'source.rtf', 'partial.wps', '--spec', '@ops', '--allow-lossy-reflow'],
                                    'output': 'partial.wps', 'configs': {'ops': [{'operation': 'replace_text', 'find': 'Reliable', 'replace': 'Changed'}]}})
        self.assertEqual(code, 3)
        self.assertTrue(result['partial'])
        self.assertTrue(result['job_cleanup']['cleanup_complete'])
        self.archived(result, 'partial.wps')
        self.assertEqual((self.root / 'source.rtf').read_bytes(), source)
        self.assertFalse(list(self.root.glob('wps-job-*')))

    def test_source_change_after_publication_blocks_archive(self):
        with self.working():
            result = create_document(SPEC, Path('main.wps'))
            Path('main.wps').write_bytes(b'changed')
            finish_delivery(result, 'main.wps')
        self.assertEqual(result['delivery']['archive_status'], 'failed')
        self.assertFalse((self.root / 'output').exists())
        self.assertEqual((self.root / 'main.wps').read_bytes(), b'changed')

    def test_invalid_generation_cleans_configs_but_not_user_or_unknown_files(self):
        (self.root / 'user.json').write_bytes(b'user input')
        (self.root / 'unknown.py').write_bytes(b'user helper')
        with self.working():
            code, result = self.job(spec={'blocks': 'invalid'})
        self.assertEqual(code, 1)
        self.assertTrue(result['job_cleanup']['cleanup_complete'])
        self.assertFalse((self.root / 'job.wps').exists())
        self.assertFalse((self.root / 'output').exists())
        self.assertEqual((self.root / 'user.json').read_bytes(), b'user input')
        self.assertEqual((self.root / 'unknown.py').read_bytes(), b'user helper')
        self.assertFalse(list(self.root.glob('wps-job-*')))

    def test_owned_cleanup_failure_is_reported_and_original_manifest_can_finish(self):
        with self.working(), patch('run_wps_job.finalize_cleanup', side_effect=PermissionError('injected cleanup failure')):
            code, result = self.job()
        self.assertEqual(code, 1)
        self.archived(result, 'job.wps')
        self.assertFalse(result['job_cleanup']['cleanup_complete'])
        self.assertEqual(result['job_status'], 'cleanup_incomplete')
        original = (self.root / 'job.wps').read_bytes()
        with self.working():
            report = finalize_cleanup(result['task_manifest'])
            self.assertTrue(report['cleanup_complete'])
        self.assertEqual((self.root / 'job.wps').read_bytes(), original)
        self.assertFalse(list(self.root.glob('wps-job-*')))

    def test_timeout_is_pending_and_does_not_guess_worker_has_stopped(self):
        with self.working(), patch('run_wps_job.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 120)):
            code, result = self.job()
        self.assertEqual(code, 1)
        self.assertEqual(result['job_status'], 'recovery_pending')
        self.assertIsNone(result['child_execution']['exit_code'])
        self.assertTrue((self.root / result['task_manifest']).exists())
        # This synthetic producer never started; only the test can confirm that.
        with self.working():
            self.assertTrue(finalize_failed_cleanup(result['task_manifest'])['cleanup_complete'])

    def test_no_configs_job_has_no_unnecessary_manifest(self):
        (self.root / 'user.json').write_text(json.dumps(SPEC), encoding='utf-8')
        with self.working():
            code, result = run_job({'script': 'run_wps_task.py', 'arguments': ['create', 'plain.wps', '--spec', 'user.json'],
                                    'output': 'plain.wps'})
        self.assertEqual(code, 0)
        self.assertEqual(result['job_cleanup']['cleanup_scope'], 'no_job_intermediates')
        self.assertTrue((self.root / 'user.json').exists())
        self.assertFalse(list(self.root.glob('wps-job-*')))

    def test_job_rejects_output_alias_that_would_redirect_child_output(self):
        (self.root / 'input.docx').write_bytes(package('<w:p><w:r><w:t>X</w:t></w:r></w:p>'))
        with self.working(), self.assertRaisesRegex(ValueError, 'unabbreviated'):
            run_job({'script': 'export_wps.py', 'arguments': ['convert', 'input.docx', '--output', 'declared.wps', '--o', 'other.wps'],
                     'output': 'declared.wps'})
        self.assertFalse((self.root / 'declared.wps').exists())
        self.assertFalse((self.root / 'other.wps').exists())

    def test_file_appearing_after_plan_is_not_claimed_or_deleted(self):
        original_open = os.open
        unexpected = []
        def raced(name, flags, *args, **kwargs):
            if str(name).startswith('wps-job-') and str(name).endswith('.spec.json') and flags & os.O_EXCL:
                descriptor = original_open(name, flags, *args, **kwargs)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(b'unknown concurrent file')
                unexpected.append(str(name))
                raise FileExistsError('injected concurrent creation')
            return original_open(name, flags, *args, **kwargs)
        with self.working(), patch.object(os, 'open', raced), patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {raced}):
            code, result = self.job()
        self.assertEqual(code, 1)
        self.assertFalse(result['job_cleanup']['cleanup_complete'])
        self.assertEqual((self.root / unexpected[0]).read_bytes(), b'unknown concurrent file')
        self.assertFalse((self.root / 'job.wps').exists())

    def test_config_changed_after_worker_is_retained(self):
        original_run = subprocess.run
        changed = []
        def worker(*args, **kwargs):
            result = original_run(*args, **kwargs)
            command = args[0]
            path = Path(command[command.index('--spec') + 1])
            path.write_bytes(b'changed after worker')
            changed.append(path)
            return result
        with self.working(), patch('run_wps_job.subprocess.run', side_effect=worker):
            code, result = self.job()
        self.assertEqual(code, 1)
        self.assertEqual(result['job_status'], 'cleanup_incomplete')
        self.assertEqual((self.root / changed[0]).read_bytes(), b'changed after worker')
        self.assertTrue((self.root / 'job.wps').exists())

    def test_readonly_standalone_install_needs_no_neighbor_skill(self):
        installed = self.root / 'installed'
        shutil.copytree(SCRIPTS.parent, installed, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        hashes = {p.relative_to(installed): hashlib.sha256(p.read_bytes()).hexdigest() for p in installed.rglob('*') if p.is_file()}
        for path in installed.rglob('*'):
            path.chmod(0o555 if path.is_dir() else 0o444)
        installed.chmod(0o555)
        try:
            check = self.cli('run_wps_job.py', '--check', scripts=installed / 'scripts')
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertEqual(json.loads(check.stdout)['status'], 'environment-ok')
            process = self.cli('run_wps_task.py', 'create', 'standalone.wps', stdin=json.dumps(SPEC), scripts=installed / 'scripts')
            self.assertEqual(process.returncode, 0, process.stderr)
            self.archived(json.loads(process.stdout), 'standalone.wps')
            (self.root / 'input.docx').write_bytes(package('<w:p><w:r><w:t>Standalone</w:t></w:r></w:p>'))
            process = self.cli('export_wps.py', 'convert', 'input.docx', '--backend', 'rtf', '--output', 'standalone-export.wps', scripts=installed / 'scripts')
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.archived(json.loads(process.stdout), 'standalone-export.wps')
            process = self.cli('run_wps_edit.py', 'text', 'standalone.wps', 'standalone-edit.wps', '--find', 'Reliable', '--replace', 'New', scripts=installed / 'scripts')
            self.assertEqual(process.returncode, 0, process.stderr)
            self.archived(json.loads(process.stdout), 'standalone-edit.wps')
            job = {'script': 'run_wps_task.py', 'arguments': ['create', 'standalone-job.wps', '--spec', '@spec'],
                   'output': 'standalone-job.wps', 'configs': {'spec': SPEC}}
            process = self.cli('run_wps_job.py', stdin=json.dumps(job), scripts=installed / 'scripts')
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertTrue(json.loads(process.stdout)['job_cleanup']['cleanup_complete'])
            self.archived(json.loads(process.stdout), 'standalone-job.wps')
            self.assertEqual(hashes, {p.relative_to(installed): hashlib.sha256(p.read_bytes()).hexdigest() for p in installed.rglob('*') if p.is_file()})
        finally:
            installed.chmod(0o700)
            for path in installed.rglob('*'):
                path.chmod(0o700 if path.is_dir() else 0o600)


if __name__ == '__main__':
    unittest.main()
