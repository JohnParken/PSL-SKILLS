"""Explicit turn-local archival never discovers historical or intermediate files."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive_deliverables as delivery


class ArchiveOutputsTests(unittest.TestCase):
    def setUp(self):
        self.original = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory(prefix='archive-turn-', dir='.')
        self.work = Path(self.temporary.name).resolve()
        os.chdir(self.work)
        self.environment = patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': json.dumps(
            {'commonParam': [{'name': 'questionId', 'value': 'record'}]})})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        os.chdir(self.original)
        self.temporary.cleanup()

    def run_cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = delivery.main([str(argument) for argument in arguments])
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_only_explicit_current_files_across_turns_and_record_changes(self):
        for name in ('report.docx', 'summary.txt', 'old.docx', 'scratch.json'):
            Path(name).write_bytes(name.encode())
        first = delivery.archive_outputs([Path('report.docx'), Path('summary.txt')], [])
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(first['archive_status'], 'copied')
        self.assertEqual(first['warnings'], [])
        self.assertEqual(sorted(p.name for p in Path('output/record').iterdir()),
                         ['report.docx', 'summary.txt'])
        Path('report.docx').write_bytes(b'new turn')
        delivery.archive_outputs([Path('report.docx')], [])
        self.assertEqual(Path('output/record/report.docx').read_bytes(), b'new turn')
        self.assertEqual(Path('output/record/summary.txt').read_bytes(), b'summary.txt')
        os.environ['ROUTER_CONFIG_VARIABLES'] = json.dumps({'commonParam': [{'name': 'questionId', 'value': 'next'}]})
        delivery.archive_outputs([Path('report.docx')], [])
        self.assertEqual([p.name for p in Path('output/next').iterdir()], ['report.docx'])

    def test_invalid_config_skips_without_creating_archive(self):
        Path('result.txt').write_bytes(b'result')
        os.environ['ROUTER_CONFIG_VARIABLES'] = 'invalid'
        result = delivery.archive_outputs([Path('result.txt')], [])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['archive_status'], 'skipped')
        self.assertIsInstance(result['warnings'], list)
        self.assertIsNone(result['files'][0]['archive_output'])
        self.assertFalse(Path('output').exists())

    def test_colliding_basenames_and_cross_output_conflicts_fail_before_copy(self):
        Path('nested').mkdir()
        Path('result.txt').write_bytes(b'one')
        Path('nested/result.txt').write_bytes(b'two')
        result = delivery.archive_outputs([Path('result.txt'), Path('nested/result.txt')], [])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertTrue(result['warnings'])
        self.assertFalse(Path('output').exists())
        code, payload, stderr = self.run_cli('result.txt', 'nested/result.txt')
        self.assertEqual(code, 0)
        self.assertEqual(payload['status'], 'completed')
        self.assertEqual(payload['archive_status'], 'failed')
        self.assertTrue(payload['warnings'])
        self.assertIn('archive-warning:', stderr)
        self.assertFalse(Path('output').exists())

    def test_protected_input_hardlink_and_source_symlink_are_refused(self):
        Path('result.txt').write_bytes(b'delivery')
        Path('input.txt').write_bytes(b'input')
        Path('output/record').mkdir(parents=True)
        os.link('input.txt', 'output/record/result.txt')
        result = delivery.archive_outputs([Path('result.txt')], [Path('input.txt')])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertTrue(result['warnings'])
        self.assertEqual(Path('input.txt').read_bytes(), b'input')
        Path('alias.txt').symlink_to('result.txt')
        symlink_result = delivery.archive_outputs([Path('alias.txt')], [])
        self.assertEqual(symlink_result['status'], 'completed')
        self.assertEqual(symlink_result['archive_status'], 'failed')
        self.assertTrue(symlink_result['warnings'])
        with self.assertRaises(ValueError):
            delivery.validate_output_path(Path('output/record/result.txt'), [Path('input.txt')])

    def test_existing_archive_is_noop_and_partial_failure_keeps_other_results(self):
        Path('first.txt').write_bytes(b'first')
        Path('second.txt').write_bytes(b'second')
        real_copy = delivery._copy_main_to_archive

        def fail_second(*args):
            if args[1].name == 'second.txt':
                raise OSError('second copy denied')
            return real_copy(*args)

        with patch.object(delivery, '_copy_main_to_archive', side_effect=fail_second):
            result = delivery.archive_outputs([Path('first.txt'), Path('second.txt')], [])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertTrue(result['warnings'])
        self.assertIn('second copy denied', '\n'.join(result['warnings']))
        self.assertEqual([p['archive_status'] for p in result['files']], ['copied', 'failed'])
        self.assertIn('second copy denied', result['files'][1]['error'])
        self.assertEqual(Path('output/record/first.txt').read_bytes(), b'first')
        self.assertEqual(Path('second.txt').read_bytes(), b'second')
        again = delivery.archive_outputs([Path('output/record/first.txt')], [])
        self.assertEqual(again['files'][0]['archive_status'], 'already_archived')

    def test_resolver_failure_is_a_warning_and_standalone_cli_succeeds(self):
        Path('result.txt').write_bytes(b'result')
        with patch.object(delivery, 'resolve_output_location',
                          side_effect=RuntimeError('injected resolver failure')):
            code, payload, stderr = self.run_cli('result.txt')
        self.assertEqual(code, 0)
        self.assertEqual(payload['status'], 'completed')
        self.assertEqual(payload['archive_status'], 'failed')
        self.assertTrue(payload['warnings'])
        self.assertIn('injected resolver failure', '\n'.join(payload['warnings']))
        self.assertIn('archive-warning:', stderr)
        self.assertEqual(Path('result.txt').read_bytes(), b'result')
        self.assertFalse(Path('output').exists())

    def test_absolute_parent_and_directory_outputs_are_refused(self):
        for path in (self.work / 'result.txt', Path('../outside.txt'), Path('.')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                delivery.validate_output_path(path)


if __name__ == '__main__':
    unittest.main()
