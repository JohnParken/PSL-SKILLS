"""Synthetic public lifecycle tests; never delete real task residuals."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lifecycle
import controlled_cleanup as cleanup
from router_config import get_common_parameter, RouterConfigError


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temp = tempfile.TemporaryDirectory(prefix='artifact-test-')
        os.chdir(self.temp.name)
        self.environment = patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': json.dumps({'commonParam': [{'name': 'questionId', 'value': 'case'}]}), 'DOCX_DEBUG_ROUTER_CONFIG': '0'}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        os.chdir(self.previous)
        self.temp.cleanup()

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = lifecycle.main(args)
        return code, json.loads(output.getvalue())

    def prepare(self):
        Path('user.json').write_text('original', encoding='utf-8')
        cleanup.plan_cleanup('manifest.json', files=['worker.py', 'spec.json'], protected=['user.json'], deliverables=['report.txt'])
        Path('worker.py').write_text('# owned', encoding='utf-8')
        Path('spec.json').write_text('{}', encoding='utf-8')
        Path('report.txt').write_text('验收结果', encoding='utf-8')

    def test_check_only_removes_own_probe(self):
        Path('keep').write_text('x')
        code, result = self.call('check')
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'environment-ok')
        self.assertEqual(list(Path('.').iterdir()), [Path('keep')])

    def test_complete_cli_flow_keeps_input_and_final(self):
        self.prepare()
        code, archive = self.call('archive', 'report.txt', '--protect-input', 'user.json')
        self.assertEqual((code, archive['archive_message']), (0, '归档完成'))
        self.assertNotIn('output/', archive['archive_message'])
        self.assertEqual(self.call('seal', 'manifest.json', '--outcome', 'success', '--verified', '--archive-status', archive['archive_status'])[0], 0)
        code, result = self.call('clean', 'manifest.json')
        self.assertEqual(code, 0)
        self.assertTrue(result['cleanup_complete'])
        self.assertTrue(result['manifest_removed'])
        self.assertFalse(Path('manifest.json').exists())
        self.assertEqual(self.call('clean', 'manifest.json')[0], 1)
        self.assertFalse(Path('worker.py').exists())
        self.assertFalse(Path('spec.json').exists())
        self.assertEqual(Path('user.json').read_text(), 'original')
        self.assertEqual(Path('report.txt').read_bytes(), Path('output/case/report.txt').read_bytes())

    def test_archive_trims_string_parameter_edges_before_selecting_destination(self):
        self.prepare()
        raw = json.dumps([{'name': 'commonParam', 'value': json.dumps([
            {'name': 'questionId', 'value': ' \tcase\n '}])}])
        with patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': raw}):
            code, archived = self.call('archive', 'report.txt', '--protect-input', 'user.json')
            self.assertEqual(os.environ['ROUTER_CONFIG_VARIABLES'], raw)
        self.assertEqual(code, 0)
        self.assertEqual(archived['archive_status'], 'copied')
        self.assertEqual(Path('output/case/report.txt').read_bytes(), Path('report.txt').read_bytes())

    def test_retry_preserves_then_success_cleans(self):
        self.prepare()
        cleanup.seal_cleanup('manifest.json', outcome='retry')
        self.assertEqual(self.call('clean', 'manifest.json')[0], 3)
        self.assertTrue(Path('worker.py').exists())
        cleanup.seal_cleanup('manifest.json', outcome='success', verified=True, archive_status='skipped')
        self.assertEqual(self.call('clean', 'manifest.json')[0], 0)

    def test_preexisting_file_cannot_be_adopted(self):
        Path('old.py').write_text('old')
        code, result = self.call('plan', 'manifest.json', '--file', 'old.py')
        self.assertEqual(code, 1)
        self.assertEqual(Path('old.py').read_text(), 'old')
        self.assertFalse(Path('manifest.json').exists())

    def test_changed_fingerprint_retained(self):
        self.prepare()
        cleanup.seal_cleanup('manifest.json', outcome='success', verified=True, archive_status='skipped')
        Path('spec.json').write_text('changed')
        code, report = self.call('clean', 'manifest.json')
        self.assertNotEqual(code, 0)
        self.assertFalse(report['cleanup_complete'])
        self.assertEqual(Path('spec.json').read_text(), 'changed')

    def test_symlink_and_hardlink_inputs_protected(self):
        Path('user.txt').write_text('original')
        Path('alias.txt').symlink_to('user.txt')
        self.assertEqual(self.call('archive', 'alias.txt')[0], 1)
        os.link('user.txt', 'hard.txt')
        self.assertEqual(self.call('archive', 'hard.txt', '--protect-input', 'user.txt')[0], 1)
        self.assertEqual(Path('user.txt').read_text(), 'original')

    def test_invalid_or_missing_config_skips_archive(self):
        Path('report.txt').write_text('x')
        for value in ('bad', ''):
            os.environ['ROUTER_CONFIG_VARIABLES'] = value
            code, result = self.call('archive', 'report.txt')
            self.assertEqual((code, result['archive_status']), (3, 'skipped'))
            self.assertNotEqual(result['archive_message'], '归档完成')
        self.assertFalse(Path('output').exists())

    def test_conflict_requires_explicit_replacement_and_preserves_inputs(self):
        Path('report.txt').write_bytes(b'first')
        self.assertEqual(self.call('archive', 'report.txt')[0], 0)
        Path('report.txt').write_bytes(b'second')
        code, result = self.call('archive', 'report.txt')
        self.assertEqual(code, 1)
        self.assertEqual(result['files'][0]['error_code'], 'archive_conflict')
        self.assertEqual(Path('output/case/report.txt').read_bytes(), b'first')
        self.assertEqual(self.call('archive', 'report.txt', '--replace-existing',
                                  '--protect-input', 'output/case/report.txt')[0], 1)
        self.assertEqual(self.call('archive', 'report.txt', '--replace-existing')[0], 0)
        self.assertEqual(Path('output/case/report.txt').read_bytes(), b'second')
        self.assertFalse(list(Path('.').glob('.docx-*.tmp')))

    def test_archive_failure_preserves_primary_and_allows_truthful_cleanup(self):
        self.prepare()
        Path('output').write_text('blocked')
        code, result = self.call('archive', 'report.txt')
        self.assertEqual((code, result['archive_status']), (1, 'failed'))
        cleanup.seal_cleanup('manifest.json', outcome='success', verified=True, archive_status='failed')
        self.assertTrue(cleanup.cleanup_task('manifest.json')['cleanup_complete'])
        self.assertTrue(Path('report.txt').exists())

    def test_duplicate_parameters_and_json_constants_rejected(self):
        for value in ('{"commonParam":[{"name":"x","value":1},{"name":"x","value":2}]}', '{"commonParam":NaN}'):
            with self.assertRaises(RouterConfigError): get_common_parameter(value, 'x')

    def test_parent_path_and_unverified_seal_do_not_delete(self):
        self.assertEqual(self.call('plan', 'manifest.json', '--file', '../foreign')[0], 1)
        self.prepare()
        self.assertEqual(self.call('seal', 'manifest.json', '--outcome', 'success', '--archive-status', 'copied')[0], 1)
        self.assertTrue(Path('worker.py').exists())

    def test_missing_platform_capability_fails_without_deletion(self):
        self.prepare()
        with patch('controlled_cleanup.os.supports_dir_fd', set()):
            self.assertEqual(self.call('check')[0], 1)
        self.assertTrue(Path('worker.py').exists())

    def test_corrupt_manifest_never_deletes(self):
        Path('worker.py').write_text('keep')
        Path('manifest.json').write_text('[]')
        code, result = self.call('clean', 'manifest.json')
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(Path('worker.py').read_text(), 'keep')

    def test_changed_final_prevents_cleanup(self):
        self.prepare()
        cleanup.seal_cleanup('manifest.json', outcome='success', verified=True, archive_status='skipped')
        Path('report.txt').write_text('changed final')
        code, result = self.call('clean', 'manifest.json')
        self.assertNotEqual(code, 0)
        self.assertTrue(Path('worker.py').exists())
        self.assertFalse(result.get('cleanup_complete', False))

    def test_process_cli_stdout_is_single_json(self):
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('lifecycle.py')), 'check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'environment-ok')
