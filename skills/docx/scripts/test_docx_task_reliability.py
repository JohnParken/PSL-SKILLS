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


class ReliabilityTests(unittest.TestCase):
    setUp = baseline.AutomaticTaskTests.setUp
    tearDown = baseline.AutomaticTaskTests.tearDown
    create = baseline.AutomaticTaskTests.create
    failed_task = baseline.AutomaticTaskTests.failed_task
    def test_manifest_shapes_fail_without_execution_or_deletion(self):
        manifest = self.failed_task()
        original = json.loads(Path(manifest).read_text())
        corruptions = [[], None, 7, 'bad']
        for key in ('runner', 'entries', 'deliverables'):
            for value in (None, 7, 'bad', [None]):
                data = copy.deepcopy(original)
                data[key] = value
                corruptions.append(data)
        for key in ('created', 'attempts', 'workspace_identity'):
            for value in (None, [], 'bad', True):
                data = copy.deepcopy(original)
                data['runner'][key] = value
                corruptions.append(data)
        Path('original.py').write_bytes(b'user source')
        Path('input.json').write_bytes(b'user input')
        Path('final.docx').write_bytes(b'user final')
        helpers = {str(p): p.read_bytes() for p in Path('owned').iterdir()}
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
            if path == manifest and not fired and json.loads(Path('owned/spec.json').read_text())['spec'] == SPEC:
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
original_save=runner.TaskFiles.save
original_replace=os.replace
def save(task,path,data,**kwargs):
    result=original_save(task,path,data,**kwargs)
    if path==sys.argv[2] and data.get('runner',{}).get('spec_update',{}).get('phase')==boundary:
        os._exit(23)
    return result
def replace(source,target,*args,**kwargs):
    result=original_replace(source,target,*args,**kwargs)
    if boundary=='replaced' and source=='retry-spec.json' and target=='spec.json':
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
                target = Path(workspace, leaf)
                target.write_bytes(b'unknown user edit')
                before = {str(p): p.read_bytes() for p in Path(workspace).iterdir()}
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
        receipt_path = Path('receipt/receipt.json')
        original = json.loads(receipt_path.read_text())
        delivery = Path('本轮报告.docx').read_bytes()
        for value in (None, [], 1, 'bad', {'result': None}, {'result': []},
                      {**original, 'result': 7}, {**original, 'result': 'bad'}):
            with self.subTest(value=value):
                receipt_path.write_text(json.dumps(value))
                before = {str(p): p.read_bytes() for p in Path('receipt').iterdir()}
                with patch.object(runner.subprocess, 'run', side_effect=AssertionError('receipt cannot run')):
                    result = runner.resume_task(manifest)
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['cleanup']['cleanup_complete'])
                self.assertEqual(Path('本轮报告.docx').read_bytes(), delivery)
                for name, contents in before.items():
                    self.assertEqual(Path(name).read_bytes(), contents)

    def legacy_manifest(self, workspace):
        manifest = self.failed_task(workspace)
        data = json.loads(Path(manifest).read_text())
        stage = data['runner'].pop('retry_stage')
        data['entries'] = [entry for entry in data['entries'] if entry['path'] != stage]
        Path(manifest).write_text(json.dumps(data))
        return manifest, stage

    def test_legacy_manifest_registers_only_absent_stage(self):
        manifest, stage = self.legacy_manifest('legacy-blocked')
        Path(stage).write_bytes(b'user staging filename')
        before = Path(manifest).read_bytes()
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('must not execute')):
            rejected = runner.retry_task(manifest, SPEC)
        self.assertEqual(rejected['status'], 'failed')
        self.assertEqual(Path(stage).read_bytes(), b'user staging filename')
        self.assertEqual(Path(manifest).read_bytes(), before)
        manifest, stage = self.legacy_manifest('legacy-valid')
        result = runner.retry_task(manifest, SPEC)
        self.assertEqual(result['status'], 'verified', result)
        persisted = json.loads(Path(manifest).read_text())
        self.assertEqual(persisted['runner']['retry_stage'], stage)
        self.assertIn(stage, [entry['path'] for entry in persisted['entries']])
        self.assertTrue(result['cleanup']['cleanup_complete'])

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
        self.assertTrue(Path('owned/retry-spec.json').is_file())
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('resume must not generate')):
            resumed = runner.resume_task(manifest)
        self.assertEqual(resumed['status'], 'failed')
        result = runner.retry_task(manifest, SPEC)
        self.assertEqual(result['status'], 'verified', result)
        self.assertTrue(result['cleanup']['cleanup_complete'])

    def test_sealed_resume_obeys_controller_directory_lock(self):
        with patch.object(runner, 'cleanup_task', side_effect=OSError('retain sealed helpers')):
            first = self.create(workspace='sealed')
        manifest = first['task_manifest']
        self.assertEqual(json.loads(Path(manifest).read_text())['phase'], 'sealed')
        before = {str(p): p.read_bytes() for p in Path('sealed').iterdir()}
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
