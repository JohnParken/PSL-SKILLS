"""Exercise task-root publication and optional DOCX archive delivery."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
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
import archive_deliverables
from read_docx import read_docx
from test_docx_editing import png_pixel


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
                      {'name': 'questionId', 'value': record}]
        return json.dumps([{'name': 'commonParam', 'value': json.dumps(parameters) if encoded else parameters},
                           {'name': 'otherParam', 'value': 'something'}])

    def run_cli(self, *arguments, config=None):
        environment = dict(os.environ, PATH='')
        # Keep no-log assertions meaningful under explicit debug disablement.
        environment["DOCX_DEBUG_ROUTER_CONFIG"] = "0"
        environment.pop('ROUTER_CONFIG_VARIABLES', None)
        if config is not None:
            environment['ROUTER_CONFIG_VARIABLES'] = config
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(Path(create_docx.__file__)), *arguments],
            cwd=self.work, env=environment, capture_output=True, encoding='utf-8',
        )

    def run_main(self, *arguments, config=None):
        original_directory = Path.cwd()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ROUTER_CONFIG_VARIABLES', None)
            if config is not None:
                os.environ['ROUTER_CONFIG_VARIABLES'] = config
            output = io.StringIO()
            error = io.StringIO()
            try:
                os.chdir(self.work)
                with redirect_stdout(output), redirect_stderr(error):
                    code = create_docx.main(list(arguments))
            finally:
                os.chdir(original_directory)
        return code, output.getvalue(), error.getvalue()

    def archive_path(self, name='report.docx'):
        return self.work / 'output/record-123' / name

    def test_question_id_creates_missing_archive_directory_without_prior_check(self):
        self.assertFalse((self.work / 'output').exists())
        input_bytes = (self.work / 'input.json').read_bytes()
        process = self.run_cli('input.json', 'report.docx', '--verify',
                               config=self.config('new-request'))
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['archive_status'], 'copied')
        self.assertEqual(result['archive_output'], 'output/new-request/report.docx')
        self.assertEqual((self.work / 'report.docx').read_bytes(),
                         (self.work / result['archive_output']).read_bytes())
        self.assertEqual((self.work / 'input.json').read_bytes(), input_bytes)

    def test_legacy_identifier_skips_archive_but_keeps_primary_success(self):
        config = json.dumps({'commonParam': [{'name': 'idRecord', 'value': 'legacy'}]})
        process = self.run_cli('input.json', 'report.docx', '--verify', config=config)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['archive_status'], 'skipped')
        self.assertEqual(result['fallback_reason'], 'missing_question_id')
        self.assertFalse((self.work / 'output').exists())
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')

    def test_generation_archive_reads_debug_config_with_question_id(self):
        config = self.config('abc123')
        with patch.dict(os.environ, {'DOCX_DEBUG_ROUTER_CONFIG': '1'}):
            code, stdout, stderr = self.run_main(
                'input.json', 'report.docx', '--verify', config=config)
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertEqual(result['archive_status'], 'copied')
        self.assertEqual(result['archive_output'], 'output/abc123/report.docx')
        prefix = 'router-config-debug: ROUTER_CONFIG_VARIABLES='
        lines = [line for line in stderr.splitlines() if line.startswith(prefix)]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0][len(prefix):]), config)
        self.assertIn('questionId', json.loads(lines[0][len(prefix):]))

    def test_old_request_id_logs_new_missing_question_id_reason_and_keeps_main(self):
        config = json.dumps([{"name":"commonParam", "value":json.dumps([
            {"name":"requestId", "value":"legacy"}])}])
        process = self.run_cli('input.json', 'report.docx', '--verify', config=config)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['archive_status'], 'skipped')
        self.assertEqual(result['fallback_reason'], 'missing_question_id')
        self.assertIn('reason=missing_question_id', process.stderr)
        self.assertNotIn('reason=missing_request_id', process.stderr)
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
        self.assertFalse((self.work / 'output').exists())

    def test_archive_mkdir_failure_only_warns_and_preserves_inputs_and_old_archive(self):
        archive = self.archive_path()
        input_bytes = (self.work / 'input.json').read_bytes()
        real_mkdir = Path.mkdir

        def deny_archive_mkdir(path, *args, **kwargs):
            if path == archive.parent:
                raise PermissionError('archive directory creation denied')
            return real_mkdir(path, *args, **kwargs)

        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    archive.parent.mkdir(parents=True)
                    archive.write_bytes(b'old archive')
                with patch.object(Path, 'mkdir', new=deny_archive_mkdir):
                    code, stdout, stderr = self.run_main('--check', config=self.config())
                    self.assertEqual(code, 0)
                    self.assertIn('check-ok', stdout)
                    self.assertIn('archive_dir=output/record-123 writable=False', stdout)
                    self.assertIn('archive-warning:', stderr)
                    code, stdout, stderr = self.run_main('input.json', 'report.docx', '--verify',
                                                        config=self.config())
                result = json.loads(stdout)
                self.assertEqual(code, 0)
                self.assertEqual(result['status'], 'verified')
                self.assertEqual(result['archive_status'], 'failed')
                self.assertTrue(result['warnings'])
                self.assertIn('archive directory creation denied', stderr)
                self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
                self.assertEqual((self.work / 'input.json').read_bytes(), input_bytes)
                if existing:
                    self.assertEqual(archive.read_bytes(), b'old archive')
                    self.assertEqual([p.name for p in archive.parent.iterdir()], ['report.docx'])
                else:
                    self.assertFalse(archive.parent.exists())

    def assert_archive_warning(self, process, *, status='verified', detail=None):
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['status'], status)
        self.assertEqual(result['archive_status'], 'failed')
        self.assertTrue(result['warnings'])
        self.assertTrue(all(isinstance(warning, str) and warning
                            for warning in result['warnings']))
        self.assertIn('archive-warning:', process.stderr)
        if detail is not None:
            self.assertIn(detail, '\n'.join(result['warnings']))
            self.assertIn(detail, process.stderr)
        return result

    def test_main_and_archive_are_identical_verified_and_readable(self):
        process = self.run_cli('input.json', '报告.docx', '--verify',
                               '--require-no-placeholders', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['output'], '报告.docx')
        self.assertEqual(result['output_dir'], '.')
        self.assertEqual(result['archive_output'], 'output/record-123/报告.docx')
        self.assertEqual(result['archive_status'], 'copied')
        self.assertFalse(result['used_fallback'])
        self.assertIsNone(result['fallback_reason'])
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(result['zip_crc'])
        main_output = self.work / result['output']
        archive_output = self.work / result['archive_output']
        self.assertEqual(main_output.read_bytes(), archive_output.read_bytes())
        self.assertEqual(read_docx(main_output), '交付内容 & 原样保留')
        self.assertEqual(read_docx(archive_output), '交付内容 & 原样保留')
        self.assertNotIn('do-not-log-this-secret', process.stdout + process.stderr)
        self.assertEqual([path.name for path in archive_output.parent.iterdir()], ['报告.docx'])

    def test_list_parameters_and_integer_zero_archive_without_verification(self):
        process = self.run_cli('input.json', 'report.docx',
                               config=self.config(0, encoded=False))
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['output'], 'report.docx')
        self.assertEqual(result['output_dir'], '.')
        self.assertEqual(result['archive_output'], 'output/0/report.docx')
        self.assertEqual(result['archive_status'], 'copied')
        self.assertEqual(result['status'], 'created')
        self.assertFalse(result['used_fallback'])
        self.assertIsNone(result['fallback_reason'])
        self.assertNotIn('zip_crc', result)
        self.assertEqual((self.work / 'report.docx').read_bytes(),
                         (self.work / 'output/0/report.docx').read_bytes())
        self.assertEqual(read_docx(self.work / result['archive_output']), '交付内容 & 原样保留')

    def test_new_archive_layout_leaves_existing_old_layout_untouched(self):
        old_archive = self.work / 'record-123/output/report.docx'
        old_archive.parent.mkdir(parents=True)
        old_archive.write_bytes(b'keep-old-layout')
        process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['archive_output'], 'output/record-123/report.docx')
        self.assertEqual(result['archive_status'], 'copied')
        self.assertEqual((self.work / 'report.docx').read_bytes(),
                         (self.work / 'output/record-123/report.docx').read_bytes())
        self.assertEqual(old_archive.read_bytes(), b'keep-old-layout')

    def test_configuration_failures_skip_archive_and_keep_existing_fallback_reasons(self):
        cases = (None, '', 'malformed-secret-json', '{}', self.config('../escape'),
                 json.dumps({'commonParam': [{'name': 'questionId', 'value': 'a'},
                                            {'name': 'questionId', 'value': 'b'}]}))
        for config in cases:
            with self.subTest(config=config):
                process = self.run_cli('input.json', 'report.docx', '--verify', config=config)
                self.assertEqual(process.returncode, 0, process.stderr)
                result = json.loads(process.stdout)
                self.assertEqual(result['output'], 'report.docx')
                self.assertEqual(result['output_dir'], '.')
                self.assertIsNone(result['archive_output'])
                self.assertEqual(result['archive_status'], 'skipped')
                self.assertTrue(result['used_fallback'])
                self.assertTrue(result['fallback_reason'])
                self.assertIn('archive-location: skipped; reason=', process.stderr)
                self.assertNotIn('malformed-secret-json', process.stdout + process.stderr)
                self.assertEqual(read_docx(self.work / result['output']), '交付内容 & 原样保留')
        self.assertFalse((self.work / 'output').exists())

    def test_runtime_check_probes_task_root_and_valid_archive_directory(self):
        process = self.run_cli('--check', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('output_dir=. writable=True', process.stdout)
        self.assertIn('archive_dir=output/record-123 writable=True', process.stdout)
        self.assertIn('check-ok', process.stdout)
        self.assertEqual(list(self.archive_path().parent.iterdir()), [])

        process = self.run_cli('--check')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('output_dir=. writable=True', process.stdout)
        self.assertNotIn('archive_dir=', process.stdout)
        self.assertIn('check-ok', process.stdout)

    def test_preflight_does_not_parse_configuration_or_create_directories(self):
        for config in ('bad-secret-config', self.config()):
            process = self.run_cli('input.json', '--preflight', config=config)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stderr, '')
        self.assertEqual([path.name for path in self.work.iterdir()], ['input.json'])

    def test_filename_rejects_directories_and_non_docx_names(self):
        names = ('./report.docx', '../report.docx', 'nested/report.docx',
                 '/report.docx', r'C:\report.docx', r'nested\report.docx',
                 'report.txt', 'report.docx ', 'CON.docx', 'COM¹.docx',
                 'CONOUT$.docx', 'report\n.docx', 'a' * 251 + '.docx')
        for name in names:
            with self.subTest(name=name):
                process = self.run_cli('input.json', name, '--verify', config=self.config())
                self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual([path.name for path in self.work.iterdir()], ['input.json'])

    def test_maximum_filename_length_works_with_short_archive_stage(self):
        name = 'a' * 250 + '.docx'
        process = self.run_cli('input.json', name, '--verify', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        main_output = self.work / name
        archive_output = self.archive_path(name)
        self.assertEqual(main_output.read_bytes(), archive_output.read_bytes())
        self.assertEqual(read_docx(archive_output), '交付内容 & 原样保留')
        self.assertEqual([path.name for path in archive_output.parent.iterdir()], [name])

    def test_archive_directory_failure_warns_but_keeps_verified_main(self):
        (self.work / 'output').write_bytes(b'keep-blocker')
        process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
        result = self.assert_archive_warning(process)
        self.assertFalse(result['used_fallback'])
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
        self.assertEqual((self.work / 'output').read_bytes(), b'keep-blocker')

    def test_archive_resolver_failure_warns_without_failing_verified_generation(self):
        with patch.object(archive_deliverables, 'resolve_output_location',
                          side_effect=RuntimeError('injected archive resolver failure')):
            code, stdout, stderr = self.run_main(
                'input.json', 'report.docx', '--verify', config=self.config())
        result = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertTrue(result['warnings'])
        self.assertIn('injected archive resolver failure', '\n'.join(result['warnings']))
        self.assertIn('archive-warning:', stderr)
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')

    def test_main_output_symlinks_are_rejected(self):
        (self.work / 'elsewhere').mkdir()
        sentinel = self.work / 'elsewhere/sentinel.docx'
        sentinel.write_bytes(b'keep-target')
        target = self.work / 'report.docx'
        target.symlink_to(sentinel)
        process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertTrue(target.is_symlink())
        self.assertEqual(sentinel.read_bytes(), b'keep-target')
        self.assertFalse(self.archive_path().parent.exists())

        target.unlink()
        target.symlink_to(self.work / 'missing.docx')
        process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertTrue(target.is_symlink())
        self.assertFalse((self.work / 'missing.docx').exists())

    def test_archive_directory_component_symlinks_are_rejected_after_main_publication(self):
        elsewhere = self.work / 'elsewhere'
        elsewhere.mkdir()
        sentinel = elsewhere / 'sentinel.docx'
        sentinel.write_bytes(b'keep-target')
        cases = ((Path('output'), elsewhere),
                 (Path('output'), self.work / 'missing-record'),
                 (Path('output/record-123'), elsewhere),
                 (Path('output/record-123'), self.work / 'missing-output'))
        for relative, destination in cases:
            with self.subTest(relative=relative, destination=destination):
                link = self.work / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(destination, target_is_directory=True)
                process = self.run_cli('input.json', 'report.docx', '--verify',
                                       config=self.config())
                result = self.assert_archive_warning(process)
                self.assertTrue(link.is_symlink())
                self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
                self.assertEqual(sentinel.read_bytes(), b'keep-target')
                link.unlink()
        self.assertEqual([path.name for path in elsewhere.iterdir()], ['sentinel.docx'])

    def test_archive_target_symlinks_including_dangling_are_rejected(self):
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        elsewhere = self.work / 'elsewhere.docx'
        elsewhere.write_bytes(b'keep-target')
        for destination in (elsewhere, self.work / 'missing.docx'):
            with self.subTest(destination=destination):
                archive_output.symlink_to(destination)
                process = self.run_cli('input.json', 'report.docx', '--verify',
                                       config=self.config())
                result = self.assert_archive_warning(process)
                self.assertTrue(archive_output.is_symlink())
                self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
                if destination == elsewhere:
                    self.assertEqual(elsewhere.read_bytes(), b'keep-target')
                else:
                    self.assertFalse(destination.exists())
                archive_output.unlink()
        self.assertEqual([path.name for path in archive_output.parent.iterdir()], [])

    def test_verification_failure_preserves_existing_main_and_archive(self):
        main_output = self.work / 'report.docx'
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        main_output.write_bytes(b'keep-main')
        archive_output.write_bytes(b'keep-archive')
        (self.work / 'input.json').write_text(json.dumps({
            'blocks': [{'type': 'paragraph', 'text': '{{ company }}'}],
        }), encoding='utf-8')
        process = self.run_cli('input.json', 'report.docx', '--require-no-placeholders',
                               config=self.config())
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(main_output.read_bytes(), b'keep-main')
        self.assertEqual(archive_output.read_bytes(), b'keep-archive')
        self.assertEqual(sorted(path.name for path in main_output.parent.iterdir()),
                         ['input.json', 'output', 'report.docx'])
        self.assertEqual([path.name for path in archive_output.parent.iterdir()], ['report.docx'])

    def test_copy_failure_preserves_published_main_and_old_archive_and_cleans_stage(self):
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        archive_output.write_bytes(b'keep-archive')
        input_bytes = (self.work / 'input.json').read_bytes()

        def partial_copy_failure(source, destination):
            destination.write(source.read(64))
            raise OSError('injected archive copy failure')

        with patch.object(create_docx.shutil, 'copyfileobj',
                          side_effect=partial_copy_failure):
            code, stdout, stderr = self.run_main('input.json', 'report.docx', '--verify',
                                                 config=self.config())
        result = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertTrue(result['warnings'])
        self.assertEqual(result['output'], 'report.docx')
        self.assertEqual(result['archive_output'], 'output/record-123/report.docx')
        self.assertIn('archive-warning:', stderr)
        self.assertIn('injected archive copy failure', stderr)
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
        self.assertEqual(archive_output.read_bytes(), b'keep-archive')
        self.assertEqual((self.work / 'input.json').read_bytes(), input_bytes)
        self.assertEqual([path.name for path in archive_output.parent.iterdir()], ['report.docx'])

    def test_replace_failure_preserves_published_main_and_old_archive_and_cleans_stage(self):
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        archive_output.write_bytes(b'keep-archive')
        real_replace = os.replace

        def fail_archive_replace(source, destination):
            if Path(destination) == archive_output:
                raise OSError('injected archive replace failure')
            return real_replace(source, destination)

        with patch.object(create_docx.os, 'replace', side_effect=fail_archive_replace):
            code, stdout, stderr = self.run_main('input.json', 'report.docx', '--verify',
                                                 config=self.config())
        result = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['archive_status'], 'failed')
        self.assertTrue(result['warnings'])
        self.assertIn('archive-warning:', stderr)
        self.assertIn('injected archive replace failure', stderr)
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
        self.assertEqual(archive_output.read_bytes(), b'keep-archive')
        self.assertEqual([path.name for path in archive_output.parent.iterdir()], ['report.docx'])

    def test_repeated_replacement_keeps_root_and_archive_in_sync(self):
        for text in ('first document', 'second document'):
            (self.work / 'input.json').write_text(json.dumps({
                'blocks': [{'type': 'paragraph', 'text': text}],
            }), encoding='utf-8')
            process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual((self.work / 'report.docx').read_bytes(), self.archive_path().read_bytes())
            self.assertIn(text, read_docx(self.work / 'report.docx'))
            self.assertIn(text, read_docx(self.archive_path()))
        self.assertEqual([path.name for path in self.archive_path().parent.iterdir()], ['report.docx'])

    def test_main_output_hardlink_to_input_is_rejected_before_generation(self):
        main_output = self.work / 'report.docx'
        os.link(self.work / 'input.json', main_output)
        input_bytes = (self.work / 'input.json').read_bytes()
        process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(main_output.read_bytes(), input_bytes)
        self.assertEqual((self.work / 'input.json').read_bytes(), input_bytes)
        self.assertFalse(self.archive_path().parent.exists())

    def test_archive_path_equal_to_input_is_rejected_after_main_publication(self):
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        raw_input = json.dumps(self.spec).encode('utf-8')
        archive_output.write_bytes(raw_input)
        process = self.run_cli(archive_output.relative_to(self.work).as_posix(),
                               'report.docx', '--verify', config=self.config())
        result = self.assert_archive_warning(
            process, detail='archive target conflicts with an input or another deliverable')
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
        self.assertNotEqual((self.work / 'report.docx').read_bytes(), raw_input)
        self.assertEqual(archive_output.read_bytes(), raw_input)

    def test_archive_hardlink_to_input_is_rejected_without_replacing_either_link(self):
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        input_bytes = (self.work / 'input.json').read_bytes()
        os.link(self.work / 'input.json', archive_output)
        process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
        result = self.assert_archive_warning(
            process, detail='archive target conflicts with an input or another deliverable')
        self.assertEqual((self.work / 'input.json').read_bytes(), input_bytes)
        self.assertEqual(archive_output.read_bytes(), input_bytes)
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')

    def test_selected_existing_archive_is_replaced_and_readable(self):
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        archive_output.write_bytes(b'old-package')
        process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
        self.assertEqual(read_docx(archive_output), '交付内容 & 原样保留')
        self.assertEqual((self.work / 'report.docx').read_bytes(), archive_output.read_bytes())
        self.assertEqual([path.name for path in archive_output.parent.iterdir()], ['report.docx'])

    def test_archive_cannot_replace_an_input_image_or_its_alias(self):
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        image_bytes = png_pixel(12, 34, 56)
        for alias in ('direct', 'symlink', 'hardlink'):
            for in_table in (False, True):
                with self.subTest(alias=alias, in_table=in_table):
                    image_alias = self.work / f'source-{alias}-{in_table}.png'
                    archive_output.write_bytes(image_bytes)
                    if alias == 'symlink':
                        image_alias.symlink_to(archive_output)
                    elif alias == 'hardlink':
                        os.link(archive_output, image_alias)
                    image_path = archive_output if alias == 'direct' else image_alias
                    image = {'path': image_path.relative_to(self.work).as_posix(),
                             'width_mm': 5, 'height_mm': 5, 'alt': '输入图片'}
                    block = ({'type': 'table', 'rows': [[{'image': image}]]} if in_table
                             else {'type': 'image', **image})
                    (self.work / 'input.json').write_text(json.dumps({'blocks': [block]}),
                                                         encoding='utf-8')
                    process = self.run_cli('input.json', 'report.docx', '--verify',
                                           config=self.config())
                    result = self.assert_archive_warning(process)
                    self.assertTrue(any('archive target conflicts with an input or another deliverable' in warning
                                        for warning in result['warnings']))
                    self.assertEqual(archive_output.read_bytes(), image_bytes)
                    self.assertEqual(image_path.read_bytes(), image_bytes)
                    self.assertTrue((self.work / 'report.docx').read_bytes().startswith(b'PK'))
                    self.assertEqual([p.name for p in archive_output.parent.iterdir()], ['report.docx'])
                    if alias != 'direct':
                        image_alias.unlink()

    def test_archive_cleanup_failure_reports_original_error_and_residual_path(self):
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        archive_output.write_bytes(b'keep-archive')
        real_unlink = os.unlink

        def fail_partial_copy(source, destination):
            destination.write(b'partial-copy')
            raise OSError('original copy failure')

        def fail_archive_unlink(path, *args, **kwargs):
            if Path(path).parent == archive_output.parent:
                raise PermissionError('cleanup denied')
            return real_unlink(path, *args, **kwargs)

        with patch.object(create_docx.shutil, 'copyfileobj', side_effect=fail_partial_copy), \
                patch.object(create_docx.os, 'unlink', side_effect=fail_archive_unlink):
            code, stdout, stderr = self.run_main('input.json', 'report.docx', '--verify',
                                                config=self.config())
        payload = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(payload['status'], 'verified')
        self.assertEqual(payload['archive_status'], 'failed')
        self.assertTrue(payload['warnings'])
        self.assertIn('archive-warning:', stderr)
        self.assertIn('original copy failure', stderr)
        self.assertIn('cleanup denied', stderr)
        residuals = [p for p in archive_output.parent.iterdir() if p != archive_output]
        self.assertEqual(len(residuals), 1)
        self.assertIn(residuals[0].relative_to(self.work).as_posix(), stderr)
        self.assertEqual(residuals[0].read_bytes(), b'partial-copy')
        self.assertEqual(archive_output.read_bytes(), b'keep-archive')
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')

    def test_main_output_cannot_replace_an_input_image_or_hardlink(self):
        main_output = self.work / 'report.docx'
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        archive_output.write_bytes(b'keep-archive')
        image_bytes = png_pixel(12, 34, 56)
        for alias in (False, True):
            with self.subTest(alias=alias):
                main_output.write_bytes(image_bytes)
                image_path = self.work / 'source.png' if alias else main_output
                if alias:
                    os.link(main_output, image_path)
                spec = {'blocks': [{'type': 'image', 'path': image_path.name,
                                    'width_mm': 5, 'height_mm': 5, 'alt': '输入图片'}]}
                (self.work / 'input.json').write_text(json.dumps(spec), encoding='utf-8')
                process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertIn('must not overwrite an image', process.stderr)
                self.assertEqual(main_output.read_bytes(), image_bytes)
                self.assertEqual(image_path.read_bytes(), image_bytes)
                self.assertEqual(archive_output.read_bytes(), b'keep-archive')

    def test_archive_sync_and_permission_failures_preserve_files_and_clean_stage(self):
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        real_copy = create_docx.shutil.copyfileobj
        for operation in ('fsync', 'chmod'):
            with self.subTest(operation=operation):
                archive_output.write_bytes(b'keep-archive')
                copying_archive = False
                real_operation = getattr(create_docx.os, operation)

                def start_archive_copy(source, destination):
                    nonlocal copying_archive
                    copying_archive = True
                    return real_copy(source, destination)

                def fail_archive_operation(*args, **kwargs):
                    if copying_archive:
                        raise OSError(f'archive {operation} denied')
                    return real_operation(*args, **kwargs)

                with patch.object(create_docx.shutil, 'copyfileobj', side_effect=start_archive_copy), \
                        patch.object(create_docx.os, operation, side_effect=fail_archive_operation):
                    code, stdout, stderr = self.run_main('input.json', 'report.docx', '--verify',
                                                        config=self.config())
                payload = json.loads(stdout)
                self.assertEqual(code, 0)
                self.assertEqual(payload['status'], 'verified')
                self.assertEqual(payload['archive_status'], 'failed')
                self.assertTrue(payload['warnings'])
                self.assertIn('archive-warning:', stderr)
                self.assertIn(f'archive {operation} denied', stderr)
                self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
                self.assertEqual(archive_output.read_bytes(), b'keep-archive')
                self.assertEqual([p.name for p in archive_output.parent.iterdir()], ['report.docx'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX permission bits')
    def test_archive_preserves_existing_mode_and_new_copy_uses_main_mode(self):
        main_output = self.work / 'report.docx'
        archive_output = self.archive_path()
        archive_output.parent.mkdir(parents=True)
        main_output.write_bytes(b'old-main')
        main_output.chmod(0o600)
        archive_output.write_bytes(b'old-archive')
        archive_output.chmod(0o640)
        for expected_mode in (0o640, 0o600):
            with self.subTest(expected_mode=expected_mode):
                process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(main_output.stat().st_mode & 0o777, 0o600)
                self.assertEqual(archive_output.stat().st_mode & 0o777, expected_mode)
                self.assertEqual(main_output.read_bytes(), archive_output.read_bytes())
                archive_output.unlink()

    def test_probe_close_failure_only_fails_for_task_root(self):
        real_temporary_file = tempfile.TemporaryFile
        location = create_docx.resolve_output_location(self.config())
        for failed_dir, failed_label in ((self.work, 'output_dir=.'),
                                         (self.archive_path().parent, 'archive_dir=output/record-123')):
            with self.subTest(failed_label=failed_label):
                @contextmanager
                def fail_probe_close(*args, **kwargs):
                    with real_temporary_file(*args, **kwargs) as probe:
                        yield probe
                    if Path(kwargs['dir']) == failed_dir:
                        raise OSError('probe close/cleanup failed')

                output, error = io.StringIO(), io.StringIO()
                with patch.object(create_docx.tempfile, 'TemporaryFile', side_effect=fail_probe_close), \
                        redirect_stdout(output), redirect_stderr(error):
                    result = create_docx._runtime_check(self.work, location)
                self.assertIn(f'{failed_label} writable=False', output.getvalue())
                self.assertIn('probe close/cleanup failed', error.getvalue())
                if failed_label == 'output_dir=.':
                    self.assertFalse(result)
                    self.assertNotIn('check-ok', output.getvalue())
                else:
                    self.assertTrue(result)
                    self.assertIn('check-ok', output.getvalue())
                    self.assertIn('archive-warning:', error.getvalue())

    def test_root_probe_failure_does_not_report_check_ok(self):
        original_directory = Path.cwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, original_directory)
        location = create_docx.resolve_output_location(self.config())
        diagnostic = io.StringIO()
        with patch.object(create_docx.tempfile, 'TemporaryFile',
                          side_effect=PermissionError('test write denied')) as probe, \
                redirect_stdout(diagnostic), redirect_stderr(io.StringIO()):
            self.assertFalse(create_docx._runtime_check(self.work, location))
        self.assertIn('output_dir=. writable=False', diagnostic.getvalue())
        self.assertIn('archive_dir=output/record-123 writable=False', diagnostic.getvalue())
        self.assertNotIn('check-ok', diagnostic.getvalue())
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(list((self.work / 'output/record-123').iterdir()), [])

        with patch.object(create_docx.tempfile, 'TemporaryFile',
                          side_effect=PermissionError('test write denied')):
            code, stdout, stderr = self.run_main('--check', config=self.config())
        self.assertEqual(code, 1)
        self.assertIn('output_dir=. writable=False', stdout)
        self.assertNotIn('check-ok', stdout)
        self.assertIn('check-failed:', stderr)

    def test_archive_probe_failure_keeps_check_and_generation_successful(self):
        archive_blocker = self.work / 'output'
        archive_blocker.write_bytes(b'keep-blocker')
        process = self.run_cli('--check', config=self.config())
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('output_dir=. writable=True', process.stdout)
        self.assertIn('archive_dir=output/record-123 writable=False', process.stdout)
        self.assertIn('check-ok', process.stdout)
        self.assertIn('archive-warning:', process.stderr)
        self.assertEqual(archive_blocker.read_bytes(), b'keep-blocker')

        process = self.run_cli('input.json', 'report.docx', '--verify', config=self.config())
        result = self.assert_archive_warning(process)
        self.assertEqual(result['output'], 'report.docx')
        self.assertEqual(read_docx(self.work / 'report.docx'), '交付内容 & 原样保留')
        self.assertEqual(archive_blocker.read_bytes(), b'keep-blocker')

        archive_blocker.unlink()
        original_directory = Path.cwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, original_directory)
        location = create_docx.resolve_output_location(self.config())
        real_temporary_file = tempfile.TemporaryFile
        diagnostic = io.StringIO()

        def reject_archive_probe(*args, **kwargs):
            if Path(kwargs['dir']) == self.work / location.output_dir:
                raise PermissionError('archive probe denied')
            return real_temporary_file(*args, **kwargs)

        with patch.object(create_docx.tempfile, 'TemporaryFile',
                          side_effect=reject_archive_probe), \
                redirect_stdout(diagnostic), redirect_stderr(io.StringIO()) as error:
            self.assertTrue(create_docx._runtime_check(self.work, location))
        self.assertIn('output_dir=. writable=True', diagnostic.getvalue())
        self.assertIn('archive_dir=output/record-123 writable=False', diagnostic.getvalue())
        self.assertIn('check-ok', diagnostic.getvalue())
        self.assertIn('archive-warning:', error.getvalue())
        self.assertEqual(list((self.work / 'output/record-123').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
