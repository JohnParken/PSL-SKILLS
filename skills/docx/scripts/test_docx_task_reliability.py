"""Independent failure and race regressions for the public task controller."""
import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_run_docx_task as baseline
from test_run_docx_task import BAD_SPEC, SPEC
import run_docx_task as runner


HELPER_NAMES = ('worker.py', 'spec.json', 'receipt.json', 'task.lock', 'retry-spec.json')


def task_state(manifest):
    return json.loads(Path(manifest).read_text())['runner']


def task_file(manifest, name):
    return Path(runner.helper_path(task_state(manifest), name))


def task_files(manifest):
    state = task_state(manifest)
    return [Path(runner.helper_path(state, name)) for name in HELPER_NAMES]


class ReliabilityTests(unittest.TestCase):
    setUp = baseline.AutomaticTaskTests.setUp
    tearDown = baseline.AutomaticTaskTests.tearDown
    create = baseline.AutomaticTaskTests.create
    failed_task = baseline.AutomaticTaskTests.failed_task

    def test_retry_runs_environment_gate_before_mutating_task(self):
        manifest = self.failed_task()
        original_manifest = Path(manifest).read_bytes()
        original_helpers = {str(path): path.read_bytes() for path in task_files(manifest) if path.exists()}
        environment = {'status': 'failed', 'errors': [
            {'check': 'task_root_write_or_probe_lock', 'reason': 'probe unavailable'}], 'diagnostics': ''}
        with patch.object(runner, 'task_environment', return_value=environment) as check, \
                patch.object(runner.subprocess, 'run', side_effect=AssertionError('retry must not execute')):
            result = runner.retry_task(manifest, SPEC)
        check.assert_called_once_with()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['task_status'], 'environment_failed')
        self.assertEqual(result['environment']['errors'][0]['check'], 'task_root_write_or_probe_lock')
        self.assertEqual(Path(manifest).read_bytes(), original_manifest)
        for name, content in original_helpers.items():
            self.assertEqual(Path(name).read_bytes(), content)
        self.assertFalse(Path('本轮报告.docx').exists())

    def test_recorded_receipt_proof_prevents_regeneration_when_files_go_missing(self):
        with patch.object(runner, 'seal_cleanup', side_effect=OSError('retain original proof')):
            result = self.create(output='lost-delivery.docx', workspace='lost-delivery')
        self.assertEqual(result['status'], 'verified')
        manifest = result['task_manifest']
        original_manifest = Path(manifest).read_bytes()
        self.assertIn('receipt_fingerprint', task_state(manifest))
        Path(result['output']).unlink()
        task_file(manifest, 'receipt.json').unlink()
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('Never regenerate a recorded delivery')):
            rejected = runner.retry_task(manifest, SPEC)
        self.assertEqual(rejected['status'], 'failed')
        self.assertEqual(Path(manifest).read_bytes(), original_manifest)
        self.assertFalse(Path(result['output']).exists())

    def test_delivery_save_exception_after_receipt_is_warning_and_resumable(self):
        original_save = runner._save

        def fail_delivery_save(manifest, data):
            if data['runner'].get('state') == 'delivered':
                raise OSError('injected delivery save outage')
            return original_save(manifest, data)

        with patch.object(runner, '_save', side_effect=fail_delivery_save):
            result = self.create(output='save-outage.docx', workspace='save-outage')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertIn('injected delivery save outage', ' '.join(result['warnings']))
        output = Path(result['output'])
        final_before = output.read_bytes()
        self.assertTrue(task_file(result['task_manifest'], 'receipt.json').is_file())

        resumed = runner.resume_task(result['task_manifest'])

        self.assertEqual(resumed['status'], 'verified')
        self.assertTrue(resumed['cleanup']['cleanup_complete'])
        self.assertEqual(output.read_bytes(), final_before)

    def test_output_tampered_during_cleanup_remains_hard_failure(self):
        output = Path('tamper-during-cleanup.docx')

        def tamper_then_fail(manifest):
            output.write_bytes(b'tampered during cleanup')
            raise OSError('injected cleanup outage after tampering')

        with patch.object(runner, 'cleanup_task', side_effect=tamper_then_fail):
            result = self.create(output=output.name, workspace='tamper-during-cleanup')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('changed or is missing after cleanup', result['message'])
        self.assertEqual(output.read_bytes(), b'tampered during cleanup')
        self.assertTrue(task_file(result['task_manifest'], 'worker.py').exists())
        self.assertTrue(task_file(result['task_manifest'], 'spec.json').exists())

    def test_create_and_resume_cli_exit_zero_for_cleanup_incomplete(self):
        script_dir = str(Path(runner.__file__).resolve().parent)
        cleanup_stub = """lambda manifest: {
    'status': 'retained', 'deleted': [], 'retained': [{'path': 'helper', 'reason': 'injected'}],
    'failed': [], 'manifest_retained': manifest, 'deliverables_unchanged': False,
    'cleanup_complete': False
}"""
        create_code = (
            'import sys; sys.path.insert(0, sys.argv[1]); import run_docx_task as runner; '
            f'runner.cleanup_task = {cleanup_stub}; '
            "raise SystemExit(runner.main(['create', 'cleanup-cli.docx']))"
        )
        created = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', create_code, script_dir],
                                  input=json.dumps(SPEC), capture_output=True, text=True, timeout=30)
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        create_report = json.loads(created.stdout)
        self.assertEqual(create_report['status'], 'verified')
        self.assertEqual(create_report['task_status'], 'cleanup_incomplete')
        self.assertTrue(create_report['warnings'])

        with patch.object(runner, 'cleanup_task', side_effect=OSError('retain for resume CLI')):
            pending = self.create(output='resume-cli.docx', workspace='resume-cli')
        self.assertEqual(pending['status'], 'verified')
        resume_code = (
            'import sys; sys.path.insert(0, sys.argv[1]); import run_docx_task as runner; '
            f'runner.cleanup_task = {cleanup_stub}; '
            "raise SystemExit(runner.main(['resume', sys.argv[2]]))"
        )
        resumed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', resume_code,
                                  script_dir, pending['task_manifest']],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        resume_report = json.loads(resumed.stdout)
        self.assertEqual(resume_report['status'], 'verified')
        self.assertEqual(resume_report['task_status'], 'cleanup_incomplete')
        self.assertTrue(resume_report['warnings'])
    def test_manifest_shapes_fail_without_execution_or_deletion(self):
        manifest = self.failed_task()
        original = json.loads(Path(manifest).read_text())
        corruptions = [[], None, 7, 'bad']
        for key in ('runner', 'entries', 'deliverables'):
            for value in (None, 7, 'bad', [None]):
                data = copy.deepcopy(original)
                data[key] = value
                corruptions.append(data)
        for key in ('created', 'attempts', 'workspace', 'schema'):
            for value in (None, [], 'bad', True):
                data = copy.deepcopy(original)
                data['runner'][key] = value
                corruptions.append(data)
        Path('original.py').write_bytes(b'user source')
        Path('input.json').write_bytes(b'user input')
        Path('final.docx').write_bytes(b'user final')
        helpers = {str(p): p.read_bytes() for p in task_files(manifest) if p.exists()}
        for data in corruptions:
            for name, content in helpers.items():
                Path(name).write_bytes(content)
            with self.subTest(data=data):
                Path(manifest).write_text(json.dumps(data))
                with patch.object(runner.subprocess, 'run', side_effect=AssertionError('must not execute')):
                    for operation in (lambda: runner.resume_task(manifest),
                                      lambda: runner.retry_task(manifest, SPEC)):
                        result = operation()
                        self.assertEqual(result['status'], 'failed')
                        self.assertFalse(result['cleanup']['cleanup_complete'])
                for name, content in helpers.items():
                    self.assertEqual(Path(name).read_bytes(), content)
                for name, content in [('original.py', b'user source'), ('input.json', b'user input'),
                                      ('final.docx', b'user final')]:
                    self.assertEqual(Path(name).read_bytes(), content)

    def test_failed_manifest_commit_leaves_retry_recoverable(self):
        manifest = self.failed_task()
        save = runner.TaskFiles.save
        fired = False
        def fail_manifest(task, path, data, **kwargs):
            nonlocal fired
            if path == manifest and not fired and json.loads(task_file(manifest, 'spec.json').read_text())['spec'] == SPEC:
                fired = True
                raise OSError('injected manifest commit refusal')
            return save(task, path, data, **kwargs)
        with patch.object(runner.TaskFiles, 'save', fail_manifest):
            failed = runner.retry_task(manifest, SPEC)
        self.assertTrue(fired)
        self.assertEqual(failed['status'], 'failed')
        runner.resume_task(manifest)
        recovered = runner.retry_task(manifest, SPEC)
        self.assertEqual(recovered['status'], 'verified', recovered)

    def test_controller_refreshes_snapshot_after_lock_acquisition(self):
        manifest = self.failed_task()
        code = '''import json,sys,fcntl
sys.path.insert(0,sys.argv[1])
import run_docx_task as runner
original=fcntl.flock
paused=False
def barrier(fd,operation):
    global paused
    if operation & fcntl.LOCK_EX and not paused:
        paused=True
        print('before-lock',flush=True)
        sys.stdin.readline()
    return original(fd,operation)
fcntl.flock=barrier
print(json.dumps(runner.retry_task(sys.argv[2],json.loads(sys.argv[3]))),flush=True)
'''
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', code,
                                    str(Path(runner.__file__).resolve().parent), manifest, json.dumps(BAD_SPEC)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'before-lock')
            self.assertEqual(runner.retry_task(manifest, BAD_SPEC)['status'], 'failed')
            stdout, stderr = process.communicate('\n', timeout=20)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(json.loads(Path(manifest).read_text())['runner']['attempts'], 3)
            with patch.object(runner.subprocess, 'run', side_effect=AssertionError('fourth attempt')):
                self.assertEqual(runner.retry_task(manifest, SPEC)['status'], 'failed')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def interrupt_retry(self, manifest, boundary):
        code = """import os,sys,json
sys.path.insert(0,sys.argv[1])
import run_docx_task as runner
boundary=sys.argv[3]
state=json.load(open(sys.argv[2]))['runner']
stage=runner.helper_path(state,'retry-spec.json')
spec=runner.helper_path(state,'spec.json')
original_save=runner.TaskFiles.save
original_replace=os.replace
def save(task,path,data,**kwargs):
    result=original_save(task,path,data,**kwargs)
    if path==sys.argv[2] and data.get('runner',{}).get('spec_update',{}).get('phase')==boundary:
        os._exit(23)
    return result
def replace(source,target,*args,**kwargs):
    result=original_replace(source,target,*args,**kwargs)
    if boundary=='replaced' and source==stage and target==spec:
        os._exit(23)
    return result
runner.TaskFiles.save=save
os.replace=replace
runner.retry_task(sys.argv[2],json.loads(sys.argv[4]))
"""
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code,
                                 str(Path(runner.__file__).resolve().parent), manifest, boundary,
                                 json.dumps(SPEC)], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)

    def test_process_exit_at_each_retry_boundary_recovers(self):
        for boundary in ('preparing', 'prepared', 'replaced'):
            with self.subTest(boundary=boundary):
                workspace = 'exit-' + boundary
                manifest = self.failed_task(workspace)
                self.interrupt_retry(manifest, boundary)
                with patch.object(runner.subprocess, 'run', side_effect=AssertionError('resume must not generate')):
                    resumed = runner.resume_task(manifest)
                self.assertEqual(resumed['status'], 'failed')
                result = runner.retry_task(manifest, SPEC)
                self.assertEqual(result['status'], 'verified', result)
                self.assertTrue(result['cleanup']['cleanup_complete'])
                # Each new synthetic task requires an unoccupied final filename.
                Path(result['output']).rename(workspace + '.docx')

    def test_unknown_pending_spec_or_stage_is_never_adopted(self):
        for boundary, leaf in (('prepared', 'retry-spec.json'), ('prepared', 'spec.json'),
                               ('replaced', 'spec.json')):
            with self.subTest(boundary=boundary, leaf=leaf):
                workspace = 'tamper-' + boundary + '-' + leaf.replace('.', '-')
                manifest = self.failed_task(workspace)
                self.interrupt_retry(manifest, boundary)
                target = task_file(manifest, leaf)
                target.write_bytes(b'unknown user edit')
                before = {str(p): p.read_bytes() for p in task_files(manifest) if p.exists()}
                with patch.object(runner.subprocess, 'run', side_effect=AssertionError('unknown file execution')):
                    for operation in (lambda: runner.resume_task(manifest),
                                      lambda: runner.retry_task(manifest, SPEC)):
                        result = operation()
                        self.assertEqual(result['status'], 'failed')
                        self.assertFalse(result['cleanup']['cleanup_complete'])
                for name, contents in before.items():
                    self.assertEqual(Path(name).read_bytes(), contents)
                self.assertFalse(Path('本轮报告.docx').exists())

    def test_corrupt_receipt_shapes_return_failure_and_preserve_delivery(self):
        with patch.object(runner, 'seal_cleanup', side_effect=OSError('retain receipt')):
            first = self.create(workspace='receipt')
        manifest = first['task_manifest']
        receipt_path = task_file(manifest, 'receipt.json')
        original = json.loads(receipt_path.read_text())
        delivery = Path('本轮报告.docx').read_bytes()
        for value in (None, [], 1, 'bad', {'result': None}, {'result': []},
                      {**original, 'result': 7}, {**original, 'result': 'bad'}):
            with self.subTest(value=value):
                receipt_path.write_text(json.dumps(value))
                before = {str(p): p.read_bytes() for p in task_files(manifest) if p.exists()}
                with patch.object(runner.subprocess, 'run', side_effect=AssertionError('receipt cannot run')):
                    result = runner.resume_task(manifest)
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['cleanup']['cleanup_complete'])
                self.assertEqual(Path('本轮报告.docx').read_bytes(), delivery)
                for name, contents in before.items():
                    self.assertEqual(Path(name).read_bytes(), contents)

    def convert_to_legacy_v1(self, manifest, *, omit_retry_stage=False):
        data = json.loads(Path(manifest).read_text())
        state = data['runner']
        self.assertEqual(state['schema'], runner.RUNNER_SCHEMA)
        workspace = state['workspace']
        self.assertFalse(Path(workspace).exists())
        Path(workspace).mkdir()
        legacy_state = copy.deepcopy(state)
        legacy_state['schema'] = runner.LEGACY_RUNNER_SCHEMA
        # Actual v1 records predate exclusive receipt ownership metadata.
        legacy_state.pop('receipt_mode', None)
        legacy_state.pop('receipt_fingerprint', None)
        legacy_created = {}
        for name in HELPER_NAMES:
            current = Path(runner.helper_path(state, name))
            legacy = Path(runner.helper_path(legacy_state, name))
            if current.exists():
                current.rename(legacy)
            if current.as_posix() in state['created']:
                legacy_created[legacy.as_posix()] = state['created'][current.as_posix()]
        legacy_state['created'] = legacy_created
        if omit_retry_stage:
            legacy_state.pop('retry_stage', None)
        else:
            legacy_state['retry_stage'] = runner.helper_path(legacy_state, 'retry-spec.json')
        with runner.TaskFiles() as task:
            legacy_state['workspace_identity'] = task.inspect(workspace)

        entries = [{'path': workspace, 'kind': 'directory', 'absent_at_plan': True}]
        retry_path = runner.helper_path(state, 'retry-spec.json')
        for entry in data['entries']:
            if omit_retry_stage and entry['path'] == retry_path:
                continue
            updated = dict(entry)
            if entry['kind'] == 'file':
                name = entry['path'].removeprefix(workspace + '.')
                updated['path'] = runner.helper_path(legacy_state, name)
            entries.append(updated)
        data['entries'] = entries
        data['runner'] = legacy_state

        receipt_path = Path(runner.helper_path(legacy_state, 'receipt.json'))
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            receipt['created'] = legacy_created
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False))
            if data['phase'] == 'sealed':
                with runner.TaskFiles() as task:
                    for entry in entries:
                        if entry['path'] == receipt_path.as_posix():
                            entry['fingerprint'] = task.inspect(entry['path'], contents=True)
        if data['phase'] == 'sealed':
            with runner.TaskFiles() as task:
                entries[0]['fingerprint'] = task.inspect(workspace)
        Path(manifest).write_text(json.dumps(data))
        return manifest

    def test_legacy_v1_rejects_retry_without_adding_files(self):
        result = self.create(BAD_SPEC, output='legacy-retry.docx', workspace='legacy-retry')
        self.assertEqual(result['status'], 'failed')
        manifest = self.convert_to_legacy_v1(result['task_manifest'], omit_retry_stage=True)
        stage = task_file(manifest, 'retry-spec.json')
        self.assertFalse(stage.exists())
        before_manifest = Path(manifest).read_bytes()
        before_helpers = {str(path): path.read_bytes() for path in task_files(manifest) if path.exists()}
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('legacy retry cannot execute')):
            rejected = runner.retry_task(manifest, SPEC)
        self.assertEqual(rejected['status'], 'failed')
        self.assertIn('Legacy v1 task retry is unsupported', rejected['message'])
        self.assertFalse(stage.exists())
        self.assertEqual(Path(manifest).read_bytes(), before_manifest)
        for name, content in before_helpers.items():
            self.assertEqual(Path(name).read_bytes(), content)

    def test_legacy_v1_sealed_task_resumes_cleanup(self):
        with patch.object(runner, 'cleanup_task', side_effect=OSError('retain legacy helpers')):
            result = self.create(output='legacy-resume.docx', workspace='legacy-resume')
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertIn('retain legacy helpers', ' '.join(result['warnings']))
        manifest = self.convert_to_legacy_v1(result['task_manifest'])
        output = Path('legacy-resume.docx').read_bytes()
        resumed = runner.resume_task(manifest)
        self.assertEqual(resumed['status'], 'verified', resumed)
        self.assertTrue(resumed['cleanup']['cleanup_complete'])
        self.assertEqual(Path('legacy-resume.docx').read_bytes(), output)
        self.assertFalse(Path('legacy-resume').exists())

    def test_prepared_commit_then_error_remains_recoverable(self):
        manifest = self.failed_task()
        original_save = runner.TaskFiles.save
        fired = False
        def save_then_error(task, path, data, **kwargs):
            nonlocal fired
            result = original_save(task, path, data, **kwargs)
            if path == manifest and not fired and data.get('runner', {}).get('spec_update', {}).get('phase') == 'prepared':
                fired = True
                raise OSError('prepared commit succeeded but acknowledgement failed')
            return result
        with patch.object(runner.TaskFiles, 'save', save_then_error):
            failed = runner.retry_task(manifest, SPEC)
        self.assertTrue(fired)
        self.assertEqual(failed['status'], 'failed')
        self.assertTrue(task_file(manifest, 'retry-spec.json').is_file())
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('resume must not generate')):
            resumed = runner.resume_task(manifest)
        self.assertEqual(resumed['status'], 'failed')
        result = runner.retry_task(manifest, SPEC)
        self.assertEqual(result['status'], 'verified', result)
        self.assertTrue(result['cleanup']['cleanup_complete'])

    def test_sealed_resume_obeys_controller_root_lock(self):
        with patch.object(runner, 'cleanup_task', side_effect=OSError('retain sealed helpers')):
            first = self.create(workspace='sealed')
        manifest = first['task_manifest']
        self.assertEqual(json.loads(Path(manifest).read_text())['phase'], 'sealed')
        before = {str(p): p.read_bytes() for p in task_files(manifest) if p.exists()}
        delivery = Path('本轮报告.docx').read_bytes()
        code = ('import os,fcntl,sys; fd=os.open(".",os.O_RDONLY); '
                'fcntl.flock(fd,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.readline()')
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', code],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            with patch.object(runner.subprocess, 'run', side_effect=AssertionError('no execution')):
                result = runner.resume_task(manifest)
            self.assertEqual(result['status'], 'failed')
            self.assertFalse(result['cleanup']['cleanup_complete'])
            for name, content in before.items():
                self.assertEqual(Path(name).read_bytes(), content)
            self.assertEqual(Path('本轮报告.docx').read_bytes(), delivery)
        finally:
            process.communicate('release\n', timeout=5)
        self.assertTrue(runner.resume_task(manifest)['cleanup']['cleanup_complete'])


if __name__ == '__main__':
    unittest.main()
