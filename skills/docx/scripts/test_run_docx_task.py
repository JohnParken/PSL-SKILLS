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


def task_file(workspace, name):
    return Path(runner.helper_path({'schema': runner.RUNNER_SCHEMA, 'workspace': workspace}, name))


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
        # Build an interrupted pre-closeout fixture for legacy recovery safety tests.
        result = runner._create_task(BAD_SPEC, '本轮报告.docx', workspace=workspace)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['cleanup']['cleanup_complete'])
        self.assertTrue(task_file(workspace, 'worker.py').is_file())
        self.assertTrue(task_file(workspace, 'spec.json').is_file())
        return result['task_manifest']

    def test_create_runs_environment_gate_without_prior_check(self):
        with patch.object(runner, 'task_environment', wraps=runner.task_environment) as environment:
            result = self.create(workspace='automatic-gate')
        environment.assert_called_once_with()
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['environment']['status'], 'ready')
        self.assertEqual(result['environment']['errors'], [])

    def test_each_required_environment_gate_failure_stops_before_planning(self):
        original_flock = runner.fcntl.flock

        def fail_probe_lock(fd, operation):
            fail_probe_lock.calls += 1
            if fail_probe_lock.calls == 3:
                raise OSError('probe lock unavailable')
            return original_flock(fd, operation)

        cases = (
            ('python_expat_runtime', patch.object(runner, 'check_runtime', return_value=False)),
            ('file_locks_or_safe_file_operations', patch.object(
                runner, '_runner_environment', side_effect=RuntimeError('safe file operations unavailable'))),
            ('file_locks', patch.object(runner.fcntl, 'flock', side_effect=OSError('root lock unavailable'))),
            ('task_root_write_or_probe_lock', patch.object(runner.fcntl, 'flock', side_effect=fail_probe_lock)),
            ('task_root_write_or_probe_lock', patch.object(
                runner.tempfile, 'TemporaryFile', side_effect=OSError('task root is not writable'))),
        )
        for index, (check, injected_failure) in enumerate(cases):
            with self.subTest(check=check, index=index):
                fail_probe_lock.calls = 0
                workspace = f'environment-failure-{index}'
                output = f'environment-failure-{index}.docx'
                with injected_failure:
                    result = self.create(output=output, workspace=workspace)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['task_status'], 'environment_failed')
                self.assertEqual(result['environment']['status'], 'failed')
                self.assertEqual(result['environment']['errors'][0]['check'], check)
                self.assertTrue(result['environment']['errors'][0]['reason'])
                self.assertFalse(Path(output).exists())
                self.assertFalse(Path(workspace + '.cleanup.json').exists())
                for name in ('worker.py', 'spec.json', 'receipt.json', 'task.lock', 'retry-spec.json'):
                    self.assertFalse(task_file(workspace, name).exists())

    def test_probe_close_error_after_successful_checks_is_warning_only(self):
        real_temporary_file = runner.tempfile.TemporaryFile

        class CloseWarningProbe:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.handle.close()
                if exc_type is None:
                    raise OSError('probe close unavailable')
                return False

            def __getattr__(self, name):
                return getattr(self.handle, name)

        def temporary_file(**kwargs):
            return CloseWarningProbe(real_temporary_file(**kwargs))

        with patch.object(runner.tempfile, 'TemporaryFile', side_effect=temporary_file):
            result = self.create(output='probe-close.docx', workspace='probe-close')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['environment']['status'], 'ready')
        self.assertIn('probe cleanup failed after successful checks', ' '.join(result['warnings']))
        self.assertIn('中文正文123.45。', read_docx(Path(result['output'])))

    def test_success_removes_executed_python_and_json_after_real_document_delivery(self):
        result = self.create(workspace='owned')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['task_status'], 'completed')
        self.assertEqual(result['archive_message'], '归档完成')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertTrue(result['cleanup']['deliverables_unchanged'])
        self.assertFalse(any(task_file('owned', name).exists()
                             for name in ('worker.py', 'spec.json', 'receipt.json', 'task.lock', 'retry-spec.json')))
        self.assertIn('中文正文123.45。', read_docx(Path(result['output'])))
        self.assertIsNone(result['task_manifest'])
        self.assertTrue(result['cleanup']['manifest_removed'])
        self.assertFalse(Path('owned.cleanup.json').exists())
        removed = {entry['path'] for entry in result['cleanup']['deleted']}
        self.assertIn(task_file('owned', 'worker.py').as_posix(), removed)
        self.assertIn(task_file('owned', 'spec.json').as_posix(), removed)
        self.assertEqual(result['attempts'], 1)

    def test_invalid_spec_retains_helpers_then_corrected_retry_finishes(self):
        manifest = self.failed_task()
        before = task_file('owned', 'worker.py').read_bytes()
        with redirect_stderr(io.StringIO()):
            result = runner.retry_task(manifest, SPEC)
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertFalse(task_file('owned', 'spec.json').exists())
        self.assertFalse(task_file('owned', 'worker.py').exists())
        self.assertFalse(Path(manifest).exists())
        self.assertEqual(result['attempts'], 2)
        self.assertTrue(before)
        self.assertIn('本轮报告', read_docx(Path(result['output'])))

    def test_existing_workspace_manifest_and_final_file_are_never_adopted(self):
        task_file('old', 'worker.py').write_bytes(b'user original script')
        task_file('old', 'spec.json').write_bytes(b'user original JSON')
        with self.assertRaises(ValueError):
            self.create(workspace='old')
        self.assertEqual(task_file('old', 'worker.py').read_bytes(), b'user original script')
        self.assertEqual(task_file('old', 'spec.json').read_bytes(), b'user original JSON')
        Path('other.cleanup.json').write_bytes(b'user original manifest')
        with self.assertRaises(FileExistsError):
            self.create(workspace='other')
        self.assertFalse(task_file('other', 'worker.py').exists())
        self.assertEqual(Path('other.cleanup.json').read_bytes(), b'user original manifest')
        Path('existing.docx').write_bytes(b'user original final')
        with self.assertRaises(ValueError):
            self.create(output='existing.docx', workspace='new')
        self.assertEqual(Path('existing.docx').read_bytes(), b'user original final')
        self.assertFalse(task_file('new', 'worker.py').exists())

    def test_v2_manifest_cannot_be_renamed_or_rebound(self):
        manifest = self.failed_task()
        original_state = Path(manifest).read_bytes()
        worker_before = task_file('owned', 'worker.py').read_bytes()
        spec_before = task_file('owned', 'spec.json').read_bytes()
        rebound = 'renamed.cleanup.json'
        data = json.loads(original_state)
        data['manifest'] = rebound
        Path(rebound).write_text(json.dumps(data), encoding='utf-8')
        rebound_before = Path(rebound).read_bytes()

        result = runner.resume_task(rebound)

        self.assertEqual(result['status'], 'failed')
        self.assertIn('manifest name differs', result['message'])
        self.assertEqual(Path(manifest).read_bytes(), original_state)
        self.assertEqual(Path(rebound).read_bytes(), rebound_before)
        self.assertEqual(task_file('owned', 'worker.py').read_bytes(), worker_before)
        self.assertEqual(task_file('owned', 'spec.json').read_bytes(), spec_before)
        self.assertFalse(task_file('renamed', 'worker.py').exists())

    def test_v1_internal_generation_entrypoints_refuse_to_create_files(self):
        data = {'runner': {'schema': runner.LEGACY_RUNNER_SCHEMA}}
        with self.assertRaisesRegex(ValueError, 'only be resumed'):
            runner._execute('legacy.cleanup.json', data, None)
        output = io.StringIO()
        with patch.object(runner, '_load', return_value=data), redirect_stdout(output):
            self.assertEqual(runner.worker_main('legacy.cleanup.json'), 1)
        self.assertIn('worker generation is unsupported', output.getvalue())
        self.assertEqual(list(Path('.').iterdir()), [])

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
                result = runner.resume_task('owned.cleanup.json')
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(Path('owned.cleanup.json').exists())
        self.assertEqual(Path(first['output']).read_bytes(), original)

    def test_archive_failure_and_cleanup_success_remain_separate(self):
        Path('output').write_bytes(b'archive directory blocked by existing user file')
        result = self.create(workspace='owned')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertEqual(result['archive_message'], '归档未完成')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertFalse(task_file('owned', 'worker.py').exists())
        self.assertIn('中文正文', read_docx(Path(result['output'])))
        self.assertEqual(Path('output').read_bytes(), b'archive directory blocked by existing user file')

    def test_missing_and_invalid_router_configuration_do_not_block_document(self):
        configurations = (
            ('missing-router', {'DOCX_DEBUG_ROUTER_CONFIG': '0'}),
            ('invalid-router', {'DOCX_DEBUG_ROUTER_CONFIG': '0', 'ROUTER_CONFIG_VARIABLES': '{bad json'}),
        )
        for workspace, environment in configurations:
            with self.subTest(workspace=workspace), patch.dict(os.environ, environment, clear=True):
                result = self.create(output=workspace + '.docx', workspace=workspace)
                self.assertEqual(result['status'], 'verified')
                self.assertIn('中文正文123.45。', read_docx(Path(result['output'])))
                self.assertIn(result['archive_status'], ('skipped', 'failed'))

    def test_archive_copy_failure_does_not_block_verified_document(self):
        from types import SimpleNamespace

        def run_worker(command, **kwargs):
            return SimpleNamespace(returncode=runner.worker_main(command[-1]), stdout=b'', stderr=b'')

        with patch('archive_deliverables._copy_main_to_archive',
                   side_effect=PermissionError('injected archive copy failure')), \
                patch.object(runner.subprocess, 'run', side_effect=run_worker):
            result = self.create(workspace='archive-copy-failure')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertIn('injected archive copy failure', ' '.join(result['warnings']))
        self.assertIn('中文正文123.45。', read_docx(Path(result['output'])))

    def test_changed_helper_is_retained_and_never_recaptured_by_retry(self):
        manifest = self.failed_task()
        task_file('owned', 'spec.json').write_bytes(b'changed after runner creation')
        with redirect_stderr(io.StringIO()):
            result = runner.retry_task(manifest, SPEC)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('changed', result['message'])
        self.assertEqual(task_file('owned', 'spec.json').read_bytes(), b'changed after runner creation')
        self.assertTrue(task_file('owned', 'worker.py').exists())
        self.assertFalse(Path('本轮报告.docx').exists())

    def test_hardlinked_and_symlinked_helpers_are_retained(self):
        manifest = self.failed_task('hard')
        os.link(task_file('hard', 'worker.py'), 'user-copy.py')
        result = runner.resume_task(manifest)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(task_file('hard', 'worker.py').exists())
        self.assertEqual(task_file('hard', 'worker.py').read_bytes(), Path('user-copy.py').read_bytes())
        second = self.failed_task('symbolic')
        task_file('symbolic', 'spec.json').unlink()
        Path('outside.json').write_bytes(b'user data')
        task_file('symbolic', 'spec.json').symlink_to('outside.json')
        result = runner.resume_task(second)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(task_file('symbolic', 'spec.json').is_symlink())
        self.assertEqual(Path('outside.json').read_bytes(), b'user data')

    def test_active_subprocess_lock_blocks_resume_without_deleting_helpers(self):
        manifest = self.failed_task()
        code = ('import fcntl,sys; f=open(sys.argv[1],"rb"); '
                'fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()')
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', code,
                                    task_file('owned', 'task.lock').as_posix()],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            result = runner.resume_task(manifest)
            self.assertEqual(result['status'], 'failed')
            self.assertIn('still active', result['message'])
            self.assertTrue(task_file('owned', 'worker.py').exists())
            self.assertTrue(task_file('owned', 'spec.json').exists())
        finally:
            process.communicate('', timeout=5)

    def test_seal_interruption_can_resume_receipt_without_regeneration(self):
        with patch.object(runner, 'seal_cleanup', side_effect=OSError('injected seal interruption')):
            result = self.create(workspace='owned')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertIn('injected seal interruption', ' '.join(result['warnings']))
        self.assertTrue(task_file('owned', 'receipt.json').is_file())
        self.assertTrue(task_file('owned', 'worker.py').is_file())
        final_before = Path('本轮报告.docx').read_bytes()
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('do not regenerate')):
            resumed = runner.resume_task(result['task_manifest'])
        self.assertEqual(resumed['status'], 'verified')
        self.assertTrue(resumed['cleanup']['cleanup_complete'])
        self.assertEqual(Path('本轮报告.docx').read_bytes(), final_before)
        self.assertFalse(task_file('owned', 'worker.py').exists())

    def test_cleanup_exception_after_verified_receipt_is_warning_and_resumable(self):
        with patch.object(runner, 'cleanup_task', side_effect=OSError('injected cleanup outage')):
            result = self.create(output='cleanup-outage.docx', workspace='cleanup-outage')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertIn('injected cleanup outage', ' '.join(result['warnings']))
        final_before = Path(result['output']).read_bytes()
        self.assertTrue(task_file('cleanup-outage', 'worker.py').exists())

        resumed = runner.resume_task(result['task_manifest'])

        self.assertEqual(resumed['status'], 'verified')
        self.assertTrue(resumed['cleanup']['cleanup_complete'])
        self.assertEqual(Path(result['output']).read_bytes(), final_before)
        self.assertFalse(task_file('cleanup-outage', 'worker.py').exists())

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
        self.assertTrue(task_file('interrupted', 'worker.py').is_file())
        self.assertTrue(task_file('interrupted', 'spec.json').is_file())
        before = Path('interrupted.docx').read_bytes()
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('no regeneration')):
            result = runner.resume_task('interrupted.cleanup.json')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertFalse(task_file('interrupted', 'worker.py').exists())
        self.assertEqual(Path('interrupted.docx').read_bytes(), before)
        self.assertEqual(read_docx('interrupted.docx'), '本轮报告\n中文正文123.45。')

    def test_partial_cleanup_failure_can_resume_original_sealed_plan(self):
        original_unlink = os.unlink
        def fail_spec(path, *args, **kwargs):
            if str(path).endswith('.spec.json'):
                raise PermissionError('injected cleanup refusal')
            return original_unlink(path, *args, **kwargs)
        with patch('cleanup_task.os.unlink', side_effect=fail_spec) as mocked_unlink, \
                patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {mocked_unlink}):
            result = self.create(workspace='owned')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertFalse(result['cleanup']['cleanup_complete'])
        self.assertTrue(result['warnings'])
        self.assertTrue(task_file('owned', 'spec.json').exists())
        self.assertFalse(task_file('owned', 'worker.py').exists())
        final_before = Path('本轮报告.docx').read_bytes()
        resumed = runner.resume_task(result['task_manifest'])
        self.assertEqual(resumed['status'], 'verified')
        self.assertTrue(resumed['cleanup']['cleanup_complete'])
        self.assertFalse(task_file('owned', 'worker.py').exists())
        self.assertEqual(Path('本轮报告.docx').read_bytes(), final_before)

    def test_check_rejects_missing_descriptor_capability_before_success_marker(self):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(os, 'supports_dir_fd', set()), \
                redirect_stdout(output), redirect_stderr(error):
            self.assertFalse(runner.check_task_runtime())
            result = runner.create_task(SPEC, 'never.docx', workspace='never')
        self.assertNotIn('check-ok', output.getvalue())
        self.assertNotIn('auto-cleanup=available', output.getvalue())
        self.assertIn('auto-cleanup-check-failed', error.getvalue())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['task_status'], 'environment_failed')
        self.assertEqual(result['environment']['errors'][0]['check'], 'file_locks_or_safe_file_operations')
        self.assertFalse(task_file('never', 'worker.py').exists())
        self.assertFalse(Path('never.cleanup.json').exists())

    def test_check_rejects_missing_fcntl_and_create_does_not_plan_files(self):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(runner, 'fcntl', None), \
                redirect_stdout(output), redirect_stderr(error):
            self.assertFalse(runner.check_task_runtime())
            result = runner.create_task(SPEC, 'never.docx', workspace='never')
        self.assertNotIn('check-ok', output.getvalue())
        self.assertIn('file locks', error.getvalue())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['task_status'], 'environment_failed')
        self.assertEqual(result['environment']['errors'][0]['check'], 'file_locks_or_safe_file_operations')
        self.assertFalse(Path('never.cleanup.json').exists())
        self.assertFalse(task_file('never', 'worker.py').exists())

    def test_check_requires_actual_lock_operation_not_only_import(self):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(runner.fcntl, 'flock', side_effect=OSError('lock operation unavailable')), \
                redirect_stdout(output), redirect_stderr(error):
            self.assertFalse(runner.check_task_runtime())
            result = runner.create_task(SPEC, 'never.docx', workspace='never')
        self.assertNotIn('check-ok', output.getvalue())
        self.assertIn('lock operation unavailable', error.getvalue())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['task_status'], 'environment_failed')
        self.assertEqual(result['environment']['errors'][0]['check'], 'file_locks')
        self.assertFalse(Path('never.cleanup.json').exists())
        self.assertFalse(task_file('never', 'worker.py').exists())

    def test_failed_generation_has_at_most_three_attempts_and_retains_original_plan(self):
        manifest = self.failed_task()
        with redirect_stderr(io.StringIO()):
            for index in range(2):
                changed = {'blocks': [{'type': 'invalid_' + str(index)}]}
                result = runner.retry_task(manifest, changed)
                self.assertEqual(result['status'], 'failed')
            exhausted = runner.retry_task(manifest, SPEC)
        self.assertEqual(exhausted['status'], 'failed')
        self.assertIn('exhausted', exhausted['message'])
        self.assertEqual(json.loads(Path(manifest).read_text())['runner']['attempts'], 3)
        self.assertTrue(task_file('owned', 'worker.py').is_file())
        self.assertTrue(task_file('owned', 'spec.json').is_file())
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
        self.assertIsNone(report['task_manifest'])
        self.assertTrue(report['cleanup']['cleanup_complete'])
        self.assertFalse(list(Path('.').glob('docx-task-*')))
        self.assertFalse(Path('失败.docx').exists())


if __name__ == '__main__':
    unittest.main()
