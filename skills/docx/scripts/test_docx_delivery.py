"""Exercise configured delivery locations through the actual DOCX CLI."""
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

if sys.version_info < (3, 10):
    raise unittest.SkipTest('DOCX requires Python >=3.10')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_docx
from read_docx import read_docx


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'DOCX requires Expat >=2.6')
class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='docx-delivery-', dir='.')
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name).resolve()
        self.spec = {'blocks': [{'type': 'paragraph', 'text': '交付内容 & 原样保留'}]}
        (self.work / 'input.json').write_text(json.dumps(self.spec), encoding='utf-8')

    @staticmethod
    def config(record='record-123', *, encoded=True):
        parameters = [{'name': 'unrelated', 'value': 'do-not-log-this-secret'},
                      {'name': 'idRecord', 'value': record}]
        return json.dumps({'commonParam': json.dumps(parameters) if encoded else parameters})

    def run_cli(self, *arguments, config=None):
        environment = dict(os.environ, PATH='')
        environment.pop('ROUTER_CONFIG_VARIABLES', None)
        if config is not None:
            environment['ROUTER_CONFIG_VARIABLES'] = config
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(Path(create_docx.__file__)), *arguments],
            cwd=self.work, env=environment, capture_output=True, encoding='utf-8',
        )

    def test_configured_chinese_filename_is_generated_verified_and_reported(self):
        process = self.run_cli('input.json', '报告.docx', '--verify',
                               '--require-no-placeholders', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['output'], 'record-123/output/报告.docx')
        self.assertEqual(result['output_dir'], 'record-123/output')
        self.assertFalse(result['used_fallback'])
        self.assertIsNone(result['fallback_reason'])
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(result['zip_crc'])
        self.assertEqual(read_docx(self.work / result['output']), '交付内容 & 原样保留')
        self.assertFalse((self.work / '报告.docx').exists())
        self.assertNotIn('do-not-log-this-secret', process.stdout + process.stderr)
        self.assertEqual([path.name for path in (self.work / 'record-123/output').iterdir()],
                         ['报告.docx'])

    def test_list_parameters_and_integer_zero_route_without_verification(self):
        process = self.run_cli('input.json', 'report.docx', config=self.config(0, encoded=False))
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['output'], '0/output/report.docx')
        self.assertEqual(result['status'], 'created')
        self.assertNotIn('zip_crc', result)
        self.assertEqual(read_docx(self.work / result['output']), '交付内容 & 原样保留')

    def test_configuration_failures_fall_back_to_task_root_with_diagnostic(self):
        for config in (None, '', 'malformed-secret-json', '{}', self.config('../escape'),
                       json.dumps({'commonParam': [{'name': 'idRecord', 'value': 'a'},
                                                 {'name': 'idRecord', 'value': 'b'}]})):
            with self.subTest(config=config):
                process = self.run_cli('input.json', 'report.docx', '--verify', config=config)
                self.assertEqual(process.returncode, 0, process.stderr)
                result = json.loads(process.stdout)
                self.assertEqual(result['output'], 'report.docx')
                self.assertEqual(result['output_dir'], '.')
                self.assertTrue(result['used_fallback'])
                self.assertTrue(result['fallback_reason'])
                self.assertIn('using default ./', process.stderr)
                self.assertNotIn('malformed-secret-json', process.stdout + process.stderr)
                self.assertEqual(read_docx(self.work / result['output']), '交付内容 & 原样保留')
        self.assertFalse((self.work / 'output').exists())

    def test_self_check_probes_selected_directory_and_cleans_probe(self):
        process = self.run_cli('--check', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('check-ok', process.stdout)
        self.assertIn('output_dir=record-123/output writable=True', process.stdout)
        self.assertEqual(list((self.work / 'record-123/output').iterdir()), [])
        process = self.run_cli('--check')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('output_dir=. writable=True', process.stdout)

    def test_preflight_does_not_parse_configuration_or_create_output_directory(self):
        for config in ('bad-secret-config', self.config()):
            process = self.run_cli('input.json', '--preflight', config=config)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stderr, '')
        self.assertEqual([path.name for path in self.work.iterdir()], ['input.json'])

    def test_filename_rejects_directories_and_non_docx_names(self):
        names = ('./report.docx', '../report.docx', 'nested/report.docx',
                 '/report.docx', r'C:\report.docx', r'nested\report.docx',
                 'report.txt', 'report.docx ', 'CON.docx', 'COM¹.docx',
                 'CONOUT$.docx', 'report\n.docx',
                 'a' * 251 + '.docx')
        for name in names:
            with self.subTest(name=name):
                process = self.run_cli('input.json', name, '--verify', config=self.config())
                self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual([path.name for path in self.work.iterdir()], ['input.json'])

    def test_maximum_filename_length_can_be_saved_without_long_stage_name(self):
        name = 'a' * 250 + '.docx'
        process = self.run_cli('input.json', name, '--verify', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(read_docx(self.work / 'record-123/output' / name),
                         '交付内容 & 原样保留')
        self.assertEqual([path.name for path in (self.work / 'record-123/output').iterdir()],
                         [name])

    def test_blocked_directory_fails_without_default_output(self):
        (self.work / 'record-123').write_bytes(b'keep-blocker')
        (self.work / 'report.docx').write_bytes(b'keep-default-file')
        for arguments, expected in ((('--check',), 1),
                                    (('input.json', 'report.docx', '--verify'), 2)):
            process = self.run_cli(*arguments, config=self.config())
            self.assertEqual(process.returncode, expected, process.stderr)
            self.assertNotIn('using default ./', process.stderr)
        self.assertEqual((self.work / 'record-123').read_bytes(), b'keep-blocker')
        self.assertEqual((self.work / 'report.docx').read_bytes(), b'keep-default-file')

    def test_directory_and_target_symlinks_are_rejected_without_fallback(self):
        (self.work / 'elsewhere').mkdir()
        (self.work / 'elsewhere/sentinel.docx').write_bytes(b'keep-target')
        for relative in ('record-123', 'record-123/output',
                         'record-123/output/report.docx'):
            with self.subTest(relative=relative):
                link = self.work / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                destination = (self.work / 'elsewhere/sentinel.docx'
                               if link.suffix else self.work / 'elsewhere')
                link.symlink_to(destination, target_is_directory=not bool(link.suffix))
                process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertNotIn('using default ./', process.stderr)
                self.assertTrue(link.is_symlink())
                self.assertFalse((self.work / 'report.docx').exists())
                link.unlink()
        self.assertEqual((self.work / 'elsewhere/sentinel.docx').read_bytes(), b'keep-target')

    def test_failed_verification_keeps_old_selected_output_and_removes_stage(self):
        target = self.work / 'record-123/output/report.docx'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'keep-existing')
        (self.work / 'input.json').write_text(json.dumps({
            'blocks': [{'type': 'paragraph', 'text': '{{ company }}'}],
        }), encoding='utf-8')
        process = self.run_cli('input.json', 'report.docx', '--require-no-placeholders',
                               config=self.config())
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(target.read_bytes(), b'keep-existing')
        self.assertEqual([path.name for path in target.parent.iterdir()], ['report.docx'])
        self.assertFalse((self.work / 'report.docx').exists())

    def test_dangling_output_links_are_rejected_for_both_directory_choices(self):
        cases = ((None, Path('report.docx')),
                 (self.config(), Path('record-123/output/report.docx')))
        for config, relative in cases:
            with self.subTest(relative=relative):
                target = self.work / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(self.work / 'missing.docx')
                process = self.run_cli('input.json', 'report.docx', '--verify', config=config)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertTrue(target.is_symlink())
                self.assertFalse((self.work / 'missing.docx').exists())
                target.unlink()

    def test_selected_existing_file_is_replaced_after_success(self):
        target = self.work / 'record-123/output/report.docx'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'old-package')
        process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(read_docx(target), '交付内容 & 原样保留')
        self.assertEqual([path.name for path in target.parent.iterdir()], ['report.docx'])

    def test_probe_permission_failure_does_not_attempt_fallback_directory(self):
        original = Path.cwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, original)
        location = create_docx.resolve_output_location(self.config())
        diagnostic = io.StringIO()
        with patch.object(create_docx.tempfile, 'TemporaryFile',
                          side_effect=PermissionError('test write denied')) as probe, \
                redirect_stdout(diagnostic), redirect_stderr(diagnostic):
            self.assertFalse(create_docx._runtime_check(self.work, location))
        self.assertIn('writable=False', diagnostic.getvalue())
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(probe.call_args.kwargs['dir'], self.work / 'record-123/output')
        self.assertFalse((self.work / 'report.docx').exists())


if __name__ == '__main__':
    unittest.main()
