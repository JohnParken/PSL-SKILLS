"""Runtime ownership and outer-process cleanup, using only isolated synthetic data."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_docx_task as runner
from read_docx import read_docx

SPEC = {'theme': 'official', 'blocks': [
    {'type': 'heading', 'level': 1, 'text': '本轮报告'},
    {'type': 'paragraph', 'text': '中文正文123.45。'},
]}
BAD_SPEC = {'blocks': [{'type': 'not_a_block', 'text': 'invalid'}]}
SCRIPT = Path(runner.__file__).resolve()


@unittest.skipUnless(sys.version_info >= (3, 10) and expat.version_info >= (2, 6, 0),
                     'DOCX runtime requires Python>=3.10 and Expat>=2.6')
class AutomaticTaskTests(unittest.TestCase):
    def setUp(self):
        self.old_cwd = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory(prefix='docx-auto-test-', dir='.')
        self.work = Path(self.temporary.name).resolve()
        os.chdir(self.work)
        self.environment = patch.dict(os.environ, {
            'DOCX_DEBUG_ROUTER_CONFIG': '0',
            'ROUTER_CONFIG_VARIABLES': json.dumps({'commonParam': [
                {'name': 'questionId', 'value': 'synthetic-task'}]})})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        os.chdir(self.old_cwd)
        self.temporary.cleanup()

    def create(self, spec=SPEC, output='本轮报告.docx', **options):
        with redirect_stderr(io.StringIO()):
            return runner.create_task(spec, output, **options)

    def failed_task(self, workspace='owned'):
        result = self.create(BAD_SPEC, workspace=workspace)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['cleanup']['cleanup_complete'])
        self.assertTrue(Path(workspace, 'worker.py').is_file())
        self.assertTrue(Path(workspace, 'spec.json').is_file())
        return result['task_manifest']

    def test_success_removes_executed_python_and_json_after_real_document_delivery(self):
        result = self.create(workspace='owned')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['task_status'], 'completed')
        self.assertEqual(result['archive_message'], '归档完成')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertTrue(result['cleanup']['deliverables_unchanged'])
        self.assertFalse(Path('owned').exists())
        self.assertIn('中文正文123.45。', read_docx(Path(result['output'])))
        self.assertTrue(Path(result['task_manifest']).is_file())
        receipt = json.loads(Path(result['task_manifest']).read_text())
        self.assertEqual(receipt['phase'], 'sealed')
        self.assertEqual(receipt['outcome'], 'success')
        removed = {entry['path'] for entry in result['cleanup']['deleted']}
        self.assertIn('owned/worker.py', removed)
        self.assertIn('owned/spec.json', removed)
        self.assertEqual(receipt['runner']['attempts'], 1)

    def test_invalid_spec_retains_helpers_then_corrected_retry_finishes(self):
        manifest = self.failed_task()
        before = Path('owned/worker.py').read_bytes()
        with redirect_stderr(io.StringIO()):
            result = runner.retry_task(manifest, SPEC)
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertFalse(Path('owned/spec.json').exists())
        self.assertFalse(Path('owned/worker.py').exists())
        data = json.loads(Path(manifest).read_text())
        self.assertEqual(data['runner']['attempts'], 2)
        self.assertTrue(before)
        self.assertIn('本轮报告', read_docx(Path(result['output'])))

    def test_existing_workspace_manifest_and_final_file_are_never_adopted(self):
        Path('old').mkdir()
        Path('old/worker.py').write_bytes(b'user original script')
        Path('old/spec.json').write_bytes(b'user original JSON')
        with self.assertRaises(ValueError):
            self.create(workspace='old')
        self.assertEqual(Path('old/worker.py').read_bytes(), b'user original script')
        self.assertEqual(Path('old/spec.json').read_bytes(), b'user original JSON')
        Path('other.cleanup.json').write_bytes(b'user original manifest')
        with self.assertRaises(FileExistsError):
            self.create(workspace='other')
        self.assertFalse(Path('other').exists())
        self.assertEqual(Path('other.cleanup.json').read_bytes(), b'user original manifest')
        Path('existing.docx').write_bytes(b'user original final')
        with self.assertRaises(ValueError):
            self.create(output='existing.docx', workspace='new')
        self.assertEqual(Path('existing.docx').read_bytes(), b'user original final')
        self.assertFalse(Path('new').exists())

    def test_unregistered_scripts_json_and_protected_inputs_survive_success(self):
        originals = {'user.py': b'user code', 'user.json': b'{"user": true}',
                     'unregistered.py': b'other task', 'unregistered.json': b'other task JSON'}
        for name, content in originals.items():
            Path(name).write_bytes(content)
        result = self.create(protected_inputs=['user.py', 'user.json'], workspace='owned')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        for name, content in originals.items():
            self.assertEqual(Path(name).read_bytes(), content)

    def test_protected_output_and_traversal_fail_before_any_mutation(self):
        Path('input.json').write_bytes(b'user input')
        with self.assertRaises(ValueError):
            self.create(output='input.json', protected_inputs=['input.json'])
        self.assertEqual(Path('input.json').read_bytes(), b'user input')
        for value in ('../other', '/absolute', 'nested/child', '.', '..'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.create(workspace=value)
        self.assertEqual([p.name for p in Path('.').iterdir()], ['input.json'])

    def test_repeated_resume_does_not_regenerate_or_delete_final_document(self):
        first = self.create(workspace='owned')
        original = Path(first['output']).read_bytes()
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('must not rerun')):
            for _ in range(2):
                result = runner.resume_task(first['task_manifest'])
                self.assertEqual(result['status'], 'verified')
                self.assertTrue(result['cleanup']['cleanup_complete'])
                self.assertTrue(result['cleanup']['deliverables_unchanged'])
        self.assertEqual(Path(first['output']).read_bytes(), original)

    def test_archive_failure_and_cleanup_success_remain_separate(self):
        Path('output').write_bytes(b'archive directory blocked by existing user file')
        result = self.create(workspace='owned')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertEqual(result['archive_message'], '归档未完成')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertFalse(Path('owned').exists())
        self.assertIn('中文正文', read_docx(Path(result['output'])))
        self.assertEqual(Path('output').read_bytes(), b'archive directory blocked by existing user file')

    def test_changed_helper_is_retained_and_never_recaptured_by_retry(self):
        manifest = self.failed_task()
        Path('owned/spec.json').write_bytes(b'changed after runner creation')
        with redirect_stderr(io.StringIO()):
            result = runner.retry_task(manifest, SPEC)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('changed', result['message'])
        self.assertEqual(Path('owned/spec.json').read_bytes(), b'changed after runner creation')
        self.assertTrue(Path('owned/worker.py').exists())
        self.assertFalse(Path('本轮报告.docx').exists())

    def test_hardlinked_and_symlinked_helpers_are_retained(self):
        manifest = self.failed_task('hard')
        os.link('hard/worker.py', 'user-copy.py')
        result = runner.resume_task(manifest)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(Path('hard/worker.py').exists())
        self.assertEqual(Path('hard/worker.py').read_bytes(), Path('user-copy.py').read_bytes())
        second = self.failed_task('symbolic')
        Path('symbolic/spec.json').unlink()
        Path('outside.json').write_bytes(b'user data')
        Path('symbolic/spec.json').symlink_to('../outside.json')
        result = runner.resume_task(second)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(Path('symbolic/spec.json').is_symlink())
        self.assertEqual(Path('outside.json').read_bytes(), b'user data')

    def test_active_subprocess_lock_blocks_resume_without_deleting_helpers(self):
        manifest = self.failed_task()
        code = ('import fcntl,sys; f=open(sys.argv[1],"rb"); '
                'fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()')
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', code, 'owned/task.lock'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            result = runner.resume_task(manifest)
            self.assertEqual(result['status'], 'failed')
            self.assertIn('still active', result['message'])
            self.assertTrue(Path('owned/worker.py').exists())
            self.assertTrue(Path('owned/spec.json').exists())
        finally:
            process.communicate('', timeout=5)

    def test_seal_interruption_can_resume_receipt_without_regeneration(self):
        with patch.object(runner, 'seal_cleanup', side_effect=OSError('injected seal interruption')):
            result = self.create(workspace='owned')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(Path('owned/receipt.json').is_file())
        self.assertTrue(Path('owned/worker.py').is_file())
        final_before = Path('本轮报告.docx').read_bytes()
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('do not regenerate')):
            resumed = runner.resume_task(result['task_manifest'])
        self.assertEqual(resumed['status'], 'verified')
        self.assertTrue(resumed['cleanup']['cleanup_complete'])
        self.assertEqual(Path('本轮报告.docx').read_bytes(), final_before)
        self.assertFalse(Path('owned').exists())

    def test_controller_process_exit_after_seal_resumes_without_generation(self):
        code = """import json, os, sys
sys.path.insert(0, sys.argv[1])
import run_docx_task as runner
runner.cleanup_task = lambda manifest: os._exit(17)
runner.create_task(json.loads(sys.argv[2]), 'interrupted.docx', workspace='interrupted')
"""
        process = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code,
                                  str(SCRIPT.parent), json.dumps(SPEC)], capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 17)
        state = json.loads(Path('interrupted.cleanup.json').read_text(encoding='utf-8'))
        self.assertEqual(state['phase'], 'sealed')
        self.assertTrue(Path('interrupted/worker.py').is_file())
        self.assertTrue(Path('interrupted/spec.json').is_file())
        before = Path('interrupted.docx').read_bytes()
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('no regeneration')):
            result = runner.resume_task('interrupted.cleanup.json')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertFalse(Path('interrupted').exists())
        self.assertEqual(Path('interrupted.docx').read_bytes(), before)
        self.assertEqual(read_docx('interrupted.docx'), '本轮报告\n中文正文123.45。')

    def test_partial_cleanup_failure_can_resume_original_sealed_plan(self):
        original_unlink = os.unlink
        def fail_spec(path, *args, **kwargs):
            if str(path) == 'spec.json':
                raise PermissionError('injected cleanup refusal')
            return original_unlink(path, *args, **kwargs)
        with patch('cleanup_task.os.unlink', side_effect=fail_spec) as mocked_unlink, \
                patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {mocked_unlink}):
            result = self.create(workspace='owned')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertFalse(result['cleanup']['cleanup_complete'])
        self.assertTrue(Path('owned/spec.json').exists())
        self.assertFalse(Path('owned/worker.py').exists())
        final_before = Path('本轮报告.docx').read_bytes()
        resumed = runner.resume_task(result['task_manifest'])
        self.assertEqual(resumed['status'], 'verified')
        self.assertTrue(resumed['cleanup']['cleanup_complete'])
        self.assertFalse(Path('owned').exists())
        self.assertEqual(Path('本轮报告.docx').read_bytes(), final_before)

    def test_check_rejects_missing_descriptor_capability_before_success_marker(self):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(os, 'supports_dir_fd', set()), \
                patch.object(runner, '_check_cli_runtime', side_effect=AssertionError('must stop first')), \
                redirect_stdout(output), redirect_stderr(error):
            self.assertFalse(runner.check_task_runtime())
            with self.assertRaises(RuntimeError):
                runner.create_task(SPEC, 'never.docx', workspace='never')
        self.assertNotIn('check-ok', output.getvalue())
        self.assertNotIn('auto-cleanup=available', output.getvalue())
        self.assertIn('auto-cleanup-check-failed', error.getvalue())
        self.assertFalse(Path('never').exists())
        self.assertFalse(Path('never.cleanup.json').exists())

    def test_check_rejects_missing_fcntl_and_create_does_not_plan_files(self):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(runner, 'fcntl', None), \
                patch.object(runner, '_check_cli_runtime', side_effect=AssertionError('must stop first')), \
                redirect_stdout(output), redirect_stderr(error):
            self.assertFalse(runner.check_task_runtime())
            with self.assertRaises(RuntimeError):
                runner.create_task(SPEC, 'never.docx', workspace='never')
        self.assertNotIn('check-ok', output.getvalue())
        self.assertIn('file locks', error.getvalue())
        self.assertEqual(list(Path('.').iterdir()), [])

    def test_check_requires_actual_lock_operation_not_only_import(self):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(runner.fcntl, 'flock', side_effect=OSError('lock operation unavailable')), \
                patch.object(runner, '_check_cli_runtime', side_effect=AssertionError('must stop first')), \
                redirect_stdout(output), redirect_stderr(error):
            self.assertFalse(runner.check_task_runtime())
        self.assertNotIn('check-ok', output.getvalue())
        self.assertIn('lock operation unavailable', error.getvalue())
        self.assertEqual(list(Path('.').iterdir()), [])

    def test_failed_generation_has_at_most_three_attempts_and_retains_original_plan(self):
        manifest = self.failed_task()
        with redirect_stderr(io.StringIO()):
            for _ in range(2):
                result = runner.retry_task(manifest, BAD_SPEC)
                self.assertEqual(result['status'], 'failed')
            exhausted = runner.retry_task(manifest, SPEC)
        self.assertEqual(exhausted['status'], 'failed')
        self.assertIn('exhausted', exhausted['message'])
        self.assertEqual(json.loads(Path(manifest).read_text())['runner']['attempts'], 3)
        self.assertTrue(Path('owned/worker.py').is_file())
        self.assertTrue(Path('owned/spec.json').is_file())
        self.assertFalse(Path('本轮报告.docx').exists())

    def test_cli_stdin_success_and_failure_keep_stdout_machine_contract(self):
        success = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), 'create', 'CLI报告.docx'],
                                 input=json.dumps(SPEC), capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(success.returncode, 0, success.stdout + success.stderr)
        result = json.loads(success.stdout)
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertEqual(result['archive_message'], '归档完成')
        self.assertIn('中文正文', read_docx(Path(result['output'])))
        failure = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), 'create', '失败.docx'],
                                 input=json.dumps(BAD_SPEC), capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(failure.returncode, 1)
        report = json.loads(failure.stdout)
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(Path(report['task_manifest']).exists())
        state = json.loads(Path(report['task_manifest']).read_text())
        self.assertTrue(Path(state['runner']['workspace'], 'worker.py').exists())
        self.assertTrue(Path(state['runner']['workspace'], 'spec.json').exists())


if __name__ == '__main__':
    unittest.main()
