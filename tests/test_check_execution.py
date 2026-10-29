"""Exercise the host-facing envelope without replacing any installed Skill files."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('check_execution', ROOT / 'tools/run_skill_check.py')
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='check-execution-', dir='.')
        self.directory = Path(self.temporary.name).resolve()
        self.old_cwd = Path.cwd()
        os.chdir(self.directory)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.temporary.cleanup()

    def fixture_check(self, source, **options):
        script = self.directory / 'skills/wps/scripts/read_wps.py'
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(source, encoding='utf-8')
        with patch.object(ADAPTER, 'ROOT', self.directory):
            return ADAPTER.run_check('wps', **options)

    def test_nonzero_exit_preserves_both_channels_and_json(self):
        report = self.fixture_check(
            'import sys\nprint(\'{"status":"failed","detail":"中文"}\')\n'
            'print("failure detail", file=sys.stderr)\nsys.exit(4)\n')
        self.assertEqual(report['execution_status'], 'completed')
        self.assertEqual(report['check_status'], 'failed')
        self.assertEqual(report['exit_code'], 4)
        self.assertEqual(report['stdout'], '{"status":"failed","detail":"中文"}\n')
        self.assertEqual(report['stderr'], 'failure detail\n')
        self.assertEqual(json.loads(report['stdout'])['detail'], '中文')
        self.assertFalse(report['stdout_truncated'])
        self.assertEqual(report['stdout_bytes'], len(report['stdout'].encode()))

    def test_exit_zero_without_success_marker_is_unconfirmed(self):
        report = self.fixture_check('print("imports done")\n')
        self.assertEqual(report['exit_code'], 0)
        self.assertEqual(report['check_status'], 'unconfirmed')
        self.assertEqual(report['artifact_validation'], 'not_run')

    def test_success_retains_warning_without_mixing_channels(self):
        report = self.fixture_check(
            'import sys\nprint("check_kind=environment artifact_validation=not_run")\n'
            'print("environment-ok")\nprint("optional warning", file=sys.stderr)\n')
        self.assertEqual(report['check_status'], 'passed')
        self.assertEqual(report['stderr'], 'optional warning\n')
        self.assertNotIn('optional warning', report['stdout'])

    def test_timeout_preserves_partial_channels_without_fake_exit(self):
        report = self.fixture_check(
            'import sys,time\nprint("started", flush=True)\n'
            'print("pending", file=sys.stderr, flush=True)\ntime.sleep(5)\n', timeout=0.3)
        self.assertEqual(report['execution_status'], 'timed_out')
        self.assertEqual(report['check_status'], 'unconfirmed')
        self.assertIsNone(report['exit_code'])
        self.assertIsNone(report['returncode'])
        self.assertEqual(report['stdout'], 'started\n')
        self.assertEqual(report['stderr'], 'pending\n')

    @unittest.skipUnless(os.name == 'posix', 'Signal return codes are POSIX-specific')
    def test_signal_is_not_reported_as_normal_exit(self):
        report = self.fixture_check('import os,signal\nos.kill(os.getpid(), signal.SIGTERM)\n')
        self.assertEqual(report['execution_status'], 'signaled')
        self.assertIsNone(report['exit_code'])
        self.assertEqual(report['returncode'], -15)
        self.assertEqual(report['signal'], 15)

    def test_launch_error_is_distinct_from_child_failure(self):
        with patch.object(ADAPTER.subprocess, 'run', side_effect=OSError('launch refused')):
            report = ADAPTER.run_check('wps')
        self.assertEqual(report['execution_status'], 'not_started')
        self.assertIsNone(report['exit_code'])
        self.assertEqual(report['stdout'], '')
        self.assertEqual(report['stderr'], '')
        self.assertEqual(report['message'], 'launch refused')

    def test_truncation_marks_channel_and_does_not_accept_marker(self):
        with patch.object(ADAPTER, 'OUTPUT_LIMIT', 80):
            report = self.fixture_check(
                'print("check_kind=environment artifact_validation=not_run")\n'
                'print("environment-ok")\nprint("x"*200)\n')
        self.assertEqual(report['check_status'], 'unconfirmed')
        self.assertTrue(report['stdout_truncated'])
        self.assertFalse(report['stderr_truncated'])
        self.assertGreater(report['stdout_bytes'], 80)
        self.assertEqual(len(report['stdout']), 80)

    def test_non_utf8_output_is_explicit_not_silently_replaced(self):
        report = self.fixture_check('import sys\nsys.stdout.buffer.write(b"\\xff")\n')
        self.assertTrue(report['stdout_decode_errors'])
        self.assertEqual(report['check_status'], 'unconfirmed')

    def test_invalid_arguments_never_start_child(self):
        for options in ({'timeout': 0}, {'timeout': float('nan')}, {'timeout': 61},
                        {'task_dir': '..'}, {'task_dir': str(self.directory)}, {'skill': 'docx'}):
            with self.subTest(options=options), patch.object(ADAPTER.subprocess, 'run') as child:
                report = ADAPTER.run_check('wps', **options)
                child.assert_not_called()
                self.assertEqual(report['execution_status'], 'not_started')
                self.assertIsNone(report['exit_code'])

    def test_parser_error_returns_envelope_without_faking_child_stderr(self):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            code = ADAPTER.main(['wps'])  # missing confirmed task directory
        report = json.loads(stream.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(report['execution_status'], 'not_started')
        self.assertIn('--task-dir', report['message'])
        self.assertEqual(report['stderr'], '')
        self.assertIsNone(report['exit_code'])

    def test_real_cli_envelope_can_be_returned_as_tool_result(self):
        for check_id in ('wps', 'xlsx'):
            with self.subTest(check_id=check_id):
                process = subprocess.run(
                    [sys.executable, '-I', '-S', '-B', str(ROOT / 'tools/run_skill_check.py'),
                     check_id, '--task-dir', '.'], capture_output=True, check=False)
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertEqual(process.stderr, b'')
                report = json.loads(process.stdout)
                self.assertEqual(report['exit_code'], 0)
                self.assertEqual(report['check_status'], 'passed')
                self.assertTrue(report['invocation_id'])
                self.assertEqual(report['artifact_validation'], 'not_run')
                if check_id == 'xlsx':
                    self.assertEqual(json.loads(report['stdout'])['status'], 'ready')

    def test_structure_check_does_not_claim_environment_or_artifact_acceptance(self):
        report = ADAPTER.run_check('skill-structure', skill='docx')
        self.assertEqual(report['check_kind'], 'skill_structure')
        self.assertEqual(report['check_status'], 'passed', report)
        self.assertEqual(report['artifact_validation'], 'not_run')


if __name__ == '__main__':
    unittest.main()
