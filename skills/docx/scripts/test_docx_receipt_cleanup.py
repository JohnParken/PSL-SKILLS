"""Receipt ownership and worker-exit recovery regressions."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_run_docx_task as baseline
from test_run_docx_task import SPEC
import run_docx_task as runner
from read_docx import read_docx

def task_data(manifest):
    return json.loads(Path(manifest).read_text(encoding='utf-8'))


def task_file(manifest, name):
    return Path(runner.helper_path(task_data(manifest)['runner'], name))


@unittest.skipUnless(sys.version_info >= (3, 10) and expat.version_info >= (2, 6, 0),
                     'DOCX runtime requires Python>=3.10 and Expat>=2.6')
class ReceiptCleanupTests(unittest.TestCase):
    setUp = baseline.AutomaticTaskTests.setUp
    tearDown = baseline.AutomaticTaskTests.tearDown
    create = baseline.AutomaticTaskTests.create

    def invoke_worker(self, command):
        worker_stdout = io.StringIO()
        worker_stderr = io.StringIO()
        with redirect_stdout(worker_stdout), redirect_stderr(worker_stderr):
            status = runner.worker_main(command[-1])
        return status, worker_stdout.getvalue(), worker_stderr.getvalue()

    def run_with_late_worker_error(self, workspace, output, mutate):
        def run_worker(command, **_kwargs):
            status, worker_stdout, worker_stderr = self.invoke_worker(command)
            self.assertEqual(status, 0, worker_stdout + worker_stderr)
            manifest = command[-1]
            state = task_data(manifest)['runner']
            receipt_path = Path(runner.helper_path(state, 'receipt.json'))
            output_path = Path(state['output'])
            mutate(manifest, receipt_path, output_path)
            return SimpleNamespace(returncode=1, stdout=b'injected late worker failure',
                                   stderr=b'')

        with patch.object(runner.subprocess, 'run', side_effect=run_worker):
            result = self.create(output=output, workspace=workspace)
        return result

    def assert_helpers_retained(self, manifest):
        for name in ('worker.py', 'spec.json', 'task.lock'):
            self.assertTrue(task_file(manifest, name).is_file(), name)

    def test_receipt_publication_never_unlinks_a_staging_hard_link(self):
        evidence = {}

        def run_worker(command, **_kwargs):
            manifest = command[-1]
            state = task_data(manifest)['runner']
            receipt_path = Path(runner.helper_path(state, 'receipt.json'))
            receipt_name = receipt_path.as_posix()
            original_unlink = os.unlink

            def deny_receipt_alias(name, *args, **kwargs):
                candidate = os.fsdecode(name)
                if candidate.startswith('.docx-cleanup-') and candidate.endswith('.tmp'):
                    try:
                        temporary_info = os.stat(
                            name, dir_fd=kwargs.get('dir_fd'), follow_symlinks=False)
                        receipt_info = os.stat(receipt_name, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        if (temporary_info.st_dev, temporary_info.st_ino) == (
                                receipt_info.st_dev, receipt_info.st_ino):
                            evidence.setdefault('denied_aliases', []).append(candidate)
                            raise PermissionError('injected refusal to unlink receipt staging alias')
                return original_unlink(name, *args, **kwargs)

            worker_stdout = io.StringIO()
            worker_stderr = io.StringIO()
            with patch.object(runner.os, 'unlink', side_effect=deny_receipt_alias) as mocked_unlink, \
                    patch.object(runner.os, 'supports_dir_fd', os.supports_dir_fd | {mocked_unlink}):
                with redirect_stdout(worker_stdout), redirect_stderr(worker_stderr):
                    status = runner.worker_main(manifest)
                evidence['worker_status'] = status
                evidence['worker_stdout'] = worker_stdout.getvalue()
                evidence['worker_stderr'] = worker_stderr.getvalue()
                if receipt_path.is_file():
                    with runner.TaskFiles() as task:
                        evidence['receipt_fingerprint'] = task.inspect(receipt_name, contents=True)
                    evidence['receipt'] = json.loads(receipt_path.read_text(encoding='utf-8'))
                evidence['staging_residuals'] = sorted(
                    path.name for path in Path.cwd().glob('.docx-cleanup-*.tmp'))
            return SimpleNamespace(returncode=status, stdout=worker_stdout.getvalue().encode('utf-8'),
                                   stderr=worker_stderr.getvalue().encode('utf-8'))

        with patch.object(runner.subprocess, 'run', side_effect=run_worker):
            result = self.create(output='receipt-exclusive.docx', workspace='receipt-exclusive')

        self.assertEqual(evidence['worker_status'], 0,
                         evidence['worker_stdout'] + evidence['worker_stderr'])
        self.assertIn('receipt_fingerprint', evidence)
        self.assertEqual(result['status'], 'verified', result)
        self.assertEqual(result['task_status'], 'completed')
        self.assertEqual(evidence.get('denied_aliases', []), [])
        self.assertEqual(evidence['receipt_fingerprint']['links'], 1)
        self.assertEqual(evidence['staging_residuals'], [])
        self.assertEqual(evidence['receipt']['manifest'], result['task_manifest'])
        manifest_data = task_data(result['task_manifest'])
        self.assertEqual(manifest_data['runner']['receipt_mode'], 'exclusive_v1')
        self.assertEqual(manifest_data['runner']['receipt_fingerprint'],
                         evidence['receipt_fingerprint'])
        with runner.TaskFiles() as task:
            output_fingerprint = task.inspect(result['output'], contents=True)
        self.assertEqual(output_fingerprint, evidence['receipt']['output_fingerprint'])
        self.assertIn('中文正文123.45。', read_docx(Path(result['output'])))

    def test_post_commit_manifest_error_is_recovered_and_cli_exits_zero_once(self):
        original_save = runner.TaskFiles.save
        fired = False
        worker_calls = []

        def fail_after_proof_commit(task, path, data, *, new=False):
            nonlocal fired
            saved = original_save(task, path, data, new=new)
            if not fired and 'receipt_fingerprint' in data.get('runner', {}):
                fired = True
                raise OSError('injected acknowledgement failure after receipt proof commit')
            return saved

        def run_worker(command, **_kwargs):
            worker_calls.append(command[-1])
            status, worker_stdout, worker_stderr = self.invoke_worker(command)
            self.assertEqual(status, 1, worker_stdout + worker_stderr)
            return SimpleNamespace(returncode=status, stdout=worker_stdout.encode('utf-8'),
                                   stderr=worker_stderr.encode('utf-8'))

        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(SPEC, ensure_ascii=False).encode('utf-8')),
                                 encoding='utf-8')
        stdout = io.StringIO()
        with patch.object(runner.TaskFiles, 'save', fail_after_proof_commit), \
                patch.object(runner.subprocess, 'run', side_effect=run_worker), \
                patch.object(runner.sys, 'stdin', stdin), \
                redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            exit_code = runner.main(['create', 'late-save.docx'])

        self.assertTrue(fired)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(worker_calls), 1)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report['status'], 'verified')
        self.assertIn('worker-warning: verified receipt recovered', ' '.join(report['warnings']))
        self.assertTrue(report['cleanup']['cleanup_complete'])
        self.assertIn('中文正文123.45。', read_docx(Path(report['output'])))

    def test_receipt_or_output_tampering_after_proof_commit_is_retained(self):
        mutations = (
            ('receipt-bytes', lambda _manifest, receipt, _output:
             receipt.write_bytes(receipt.read_bytes() + b' ')),
            ('main-output', lambda _manifest, _receipt, output:
             output.write_bytes(b'tampered main output')),
        )
        for suffix, mutate in mutations:
            with self.subTest(suffix=suffix):
                workspace = 'tamper-' + suffix
                output = workspace + '.docx'
                result = self.run_with_late_worker_error(workspace, output, mutate)
                self.assertEqual(result['status'], 'failed', result)
                self.assertFalse(result['cleanup']['cleanup_complete'])
                manifest_data = task_data(result['task_manifest'])
                self.assertEqual(manifest_data['phase'], 'planned')
                self.assertIn('receipt_fingerprint', manifest_data['runner'])
                self.assert_helpers_retained(result['task_manifest'])
                self.assertTrue(task_file(result['task_manifest'], 'receipt.json').is_file())
                self.assertTrue(Path(output).is_file())
                if suffix == 'main-output':
                    self.assertEqual(Path(output).read_bytes(), b'tampered main output')
                else:
                    self.assertIn('中文正文123.45。', read_docx(Path(output)))

    def test_identical_receipt_bytes_replaced_with_another_inode_are_rejected(self):
        def replace_receipt(manifest, receipt_path, _output_path):
            original = receipt_path.read_bytes()
            replacement = Path('receipt-replacement.tmp')
            replacement.write_bytes(original)
            os.replace(replacement, receipt_path)
            self.assertEqual(receipt_path.read_bytes(), original)
            self.assertNotEqual(os.stat(receipt_path).st_ino,
                                task_data(manifest)['runner']['receipt_fingerprint']['identity'][1])

        result = self.run_with_late_worker_error(
            'receipt-replaced', 'receipt-replaced.docx', replace_receipt)
        self.assertEqual(result['status'], 'failed', result)
        self.assertFalse(result['cleanup']['cleanup_complete'])
        self.assert_helpers_retained(result['task_manifest'])
        self.assertTrue(task_file(result['task_manifest'], 'receipt.json').is_file())
        self.assertIn('中文正文123.45。', read_docx(Path('receipt-replaced.docx')))

    def test_extra_hard_link_to_valid_receipt_is_rejected(self):
        alias = Path('receipt-extra-link.json')

        def add_receipt_alias(_manifest, receipt_path, _output_path):
            os.link(receipt_path, alias)

        result = self.run_with_late_worker_error(
            'receipt-hardlink', 'receipt-hardlink.docx', add_receipt_alias)
        self.assertEqual(result['status'], 'failed', result)
        self.assertFalse(result['cleanup']['cleanup_complete'])
        with runner.TaskFiles() as task:
            receipt_fingerprint = task.inspect(task_file(result['task_manifest'], 'receipt.json').as_posix(),
                                               contents=True)
        self.assertEqual(receipt_fingerprint['links'], 2)
        self.assertTrue(alias.is_file())
        self.assert_helpers_retained(result['task_manifest'])
        self.assertIn('中文正文123.45。', read_docx(Path('receipt-hardlink.docx')))

    def test_receipt_preexisting_before_worker_is_never_adopted(self):
        receipt_bytes = b'user-owned receipt must not be replaced'

        def occupy_receipt(command, **_kwargs):
            manifest = command[-1]
            receipt_path = task_file(manifest, 'receipt.json')
            with receipt_path.open('xb') as receipt_file:
                receipt_file.write(receipt_bytes)
            status, worker_stdout, worker_stderr = self.invoke_worker(command)
            self.assertEqual(status, 1, worker_stdout + worker_stderr)
            return SimpleNamespace(returncode=status, stdout=worker_stdout.encode('utf-8'),
                                   stderr=worker_stderr.encode('utf-8'))

        with patch.object(runner.subprocess, 'run', side_effect=occupy_receipt):
            result = self.create(output='receipt-preexisting.docx', workspace='receipt-preexisting')

        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(task_file(result['task_manifest'], 'receipt.json').read_bytes(), receipt_bytes)
        self.assertNotIn('receipt_fingerprint', task_data(result['task_manifest'])['runner'])
        self.assert_helpers_retained(result['task_manifest'])
        self.assertTrue(Path('receipt-preexisting.docx').is_file())
        self.assertIn('中文正文123.45。', read_docx(Path('receipt-preexisting.docx')))

    def test_uncommitted_receipt_proof_is_not_recovered_or_deleted(self):
        original_save = runner.TaskFiles.save
        fired = False

        def fail_before_proof_commit(task, path, data, *, new=False):
            nonlocal fired
            if not fired and 'receipt_fingerprint' in data.get('runner', {}):
                fired = True
                raise OSError('injected refusal before receipt proof commit')
            return original_save(task, path, data, new=new)

        def run_worker(command, **_kwargs):
            status, worker_stdout, worker_stderr = self.invoke_worker(command)
            return SimpleNamespace(returncode=status, stdout=worker_stdout.encode('utf-8'),
                                   stderr=worker_stderr.encode('utf-8'))

        with patch.object(runner.TaskFiles, 'save', fail_before_proof_commit), \
                patch.object(runner.subprocess, 'run', side_effect=run_worker):
            result = self.create(output='uncommitted-proof.docx', workspace='uncommitted-proof')

        self.assertTrue(fired)
        self.assertEqual(result['status'], 'failed', result)
        manifest_data = task_data(result['task_manifest'])
        self.assertNotIn('receipt_fingerprint', manifest_data['runner'])
        self.assertTrue(task_file(result['task_manifest'], 'receipt.json').is_file())
        with runner.TaskFiles() as task:
            receipt_snapshot = task.inspect(task_file(result['task_manifest'], 'receipt.json').as_posix(),
                                            contents=True)
        self.assertEqual(receipt_snapshot['links'], 1)
        self.assert_helpers_retained(result['task_manifest'])
        self.assertTrue(Path('uncommitted-proof.docx').is_file())
        self.assertIn('中文正文123.45。', read_docx(Path('uncommitted-proof.docx')))
