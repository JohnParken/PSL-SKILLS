"""Final closeout leaves deliveries, not execution receipts, in owned task space."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import controlled_cleanup as cleanup
import run_docx_task as runner


class TerminalCleanupTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temp = tempfile.TemporaryDirectory(prefix='terminal-cleanup-')
        os.chdir(self.temp.name)
        self.environment = patch.dict(os.environ, {'DOCX_DEBUG_ROUTER_CONFIG': '0'}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        os.chdir(self.previous)
        self.temp.cleanup()

    def test_successive_generations_leave_only_current_and_historical_deliveries(self):
        spec = {'blocks': [{'type': 'paragraph', 'text': '交付内容'}]}
        first = runner.create_task(spec, 'historical.docx')
        before = Path('historical.docx').read_bytes()
        second = runner.create_task(spec, 'current.docx')
        for result in (first, second):
            self.assertTrue(result['cleanup']['cleanup_complete'])
            self.assertTrue(result['cleanup']['manifest_removed'])
            self.assertIsNone(result['task_manifest'])
        self.assertEqual({p.name for p in Path('.').iterdir()}, {'historical.docx', 'current.docx'})
        self.assertEqual(Path('historical.docx').read_bytes(), before)

    def test_invalid_generation_cleans_all_helpers_and_preserves_originals(self):
        from test_run_docx_task import BAD_SPEC
        Path('original.txt').write_text('user input')
        result = runner.create_task(BAD_SPEC, 'failed.docx', protected_inputs=['original.txt'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['task_status'], 'failed_cleaned')
        self.assertIsNone(result['task_manifest'])
        self.assertEqual({p.name for p in Path('.').iterdir()}, {'original.txt'})
        self.assertEqual(Path('original.txt').read_text(), 'user input')

    def test_worker_timeout_cleans_all_helpers(self):
        import subprocess
        with patch.object(runner.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 120)):
            result = runner.create_task({'blocks': [{'type': 'paragraph', 'text': 'test'}]}, 'failed.docx')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertEqual(list(Path('.').iterdir()), [])

    def test_stopped_task_removes_plan_and_helpers_but_preserves_inputs_and_history(self):
        Path('original.json').write_text('original')
        Path('history.docx').write_bytes(b'historical delivery')
        cleanup.plan_cleanup('task.json', files=['worker.py', 'spec.json'], protected=['original.json'])
        Path('worker.py').write_text('owned')
        Path('spec.json').write_text('owned config')
        cleanup.seal_cleanup('task.json', outcome='stopped')
        report = cleanup.finalize_cleanup('task.json')
        self.assertTrue(report['cleanup_complete'])
        self.assertTrue(report['manifest_removed'])
        self.assertEqual({p.name for p in Path('.').iterdir()}, {'original.json', 'history.docx'})

    def test_manifest_removal_failure_is_incomplete_and_recoverable(self):
        cleanup.plan_cleanup('task.json', files=['helper.json'])
        Path('helper.json').write_text('owned')
        cleanup.seal_cleanup('task.json', outcome='failed')
        real_unlink = os.unlink
        def deny_manifest(path, *args, **kwargs):
            if str(path) == 'task.json':
                raise PermissionError('manifest deletion blocked')
            return real_unlink(path, *args, **kwargs)
        with patch.object(os, 'unlink', side_effect=deny_manifest) as patched, \
                patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {patched}):
            result = cleanup.finalize_cleanup('task.json')
        self.assertFalse(result['cleanup_complete'])
        self.assertTrue(Path('task.json').exists())
        self.assertTrue(cleanup.finalize_cleanup('task.json')['manifest_removed'])
        self.assertEqual(list(Path('.').iterdir()), [])

    def test_recreated_entry_blocks_manifest_retirement(self):
        cleanup.plan_cleanup('task.json', files=['helper.json'])
        Path('helper.json').write_text('owned')
        cleanup.seal_cleanup('task.json', outcome='stopped')
        result = cleanup.cleanup_task('task.json')
        Path('helper.json').write_text('foreign new file')
        result = cleanup.retire_cleanup_manifest('task.json', result)
        self.assertFalse(result['cleanup_complete'])
        self.assertEqual(Path('helper.json').read_text(), 'foreign new file')
        self.assertTrue(Path('task.json').exists())


if __name__ == '__main__':
    unittest.main()
