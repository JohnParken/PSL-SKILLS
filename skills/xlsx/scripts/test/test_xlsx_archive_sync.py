"""Public archive contract in the bundled copy; synthetic isolated files only."""
import errno
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from _artifact_runtime import archive_deliverables as archive
from xlsx_delivery import finish_delivery


class ArchiveSyncTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.directory = tempfile.TemporaryDirectory(prefix='xlsx-archive-sync-')
        os.chdir(self.directory.name)
        self.environment = patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': json.dumps(
            {'commonParam': [{'name': 'questionId', 'value': 'test'}]})})
        self.environment.start()
        Path('report.csv').write_bytes(b'original')

    def tearDown(self):
        self.environment.stop()
        os.chdir(self.previous)
        self.directory.cleanup()

    def test_success_preserves_old_fields_and_adds_verified_notice(self):
        result = archive.archive_outputs([Path('report.csv')], [])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['archive_status'], 'copied')
        self.assertEqual(result['archive_message'], '归档完成')
        self.assertTrue(result['files'][0]['archive_verified'])
        self.assertEqual(Path('report.csv').read_bytes(), Path('output/test/report.csv').read_bytes())
        self.assertIn('云空间', result['archive_user_notice'])
        self.assertNotIn('output/test', result['archive_user_notice'])

    def test_changed_source_or_corrupt_archive_is_failed_without_rollback(self):
        real_copy = archive._copy_main_to_archive
        for kind in ('source', 'archive'):
            with self.subTest(kind=kind):
                name = kind + '.csv'
                Path(name).write_bytes(b'original')
                def change_after_copy(*args, **kwargs):
                    target = real_copy(*args, **kwargs)
                    Path(name if kind == 'source' else target).write_bytes(b'changed')
                    return target
                with patch.object(archive, '_copy_main_to_archive', side_effect=change_after_copy):
                    result = finish_delivery({'status': 'calculated', 'sentinel': 42}, name)
                self.assertEqual(result['status'], 'calculated')
                self.assertEqual(result['sentinel'], 42)
                delivery = result['delivery']
                self.assertEqual(delivery['archive_status'], 'failed')
                self.assertEqual(delivery['files'][0]['error_code'], 'archive_copy_or_verification_failed')
                self.assertNotIn('archive_verified', delivery['files'][0])
                self.assertEqual(Path(name).read_bytes(), b'changed' if kind == 'source' else b'original')

    def test_os_errors_are_classified_without_claiming_archive_success(self):
        for code, expected in [(errno.EXDEV, 'archive_cross_device'), (errno.EACCES, 'archive_permission_denied'),
                               (errno.EPERM, 'archive_permission_denied'), (errno.ENOSPC, 'archive_storage_full')]:
            with self.subTest(code=code), patch.object(archive, '_copy_main_to_archive', side_effect=OSError(code, 'injected')):
                result = archive.archive_outputs([Path('report.csv')], [])
                self.assertEqual(result['files'][0]['error_code'], expected)
                self.assertEqual(result['archive_status'], 'failed')
                self.assertNotIn('archive_verified', result['files'][0])
                self.assertEqual(Path('report.csv').read_bytes(), b'original')

    def test_wrapper_exception_and_skip_keep_business_result_and_notice(self):
        with patch('xlsx_delivery.archive_outputs', side_effect=RuntimeError('injected')):
            result = finish_delivery({'status': 'success'}, 'report.csv')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['delivery']['archive_status'], 'failed')
        self.assertEqual(result['delivery']['archive_message'], '归档未完成')
        self.assertIn('archive_user_notice', result['delivery'])
        with patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': 'invalid'}):
            result = finish_delivery({'status': 'success'}, 'report.csv')
        self.assertEqual(result['delivery']['archive_status'], 'skipped')
        self.assertNotIn('archive_verified', result['delivery']['files'][0])

    def test_readonly_standalone_install_needs_no_neighbor_skill(self):
        source = Path(__file__).resolve().parents[2]
        install = Path('install').resolve()
        shutil.copytree(source, install, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        files = [path for path in install.rglob('*') if path.is_file()]
        before = {path.relative_to(install).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        directories = [path for path in install.rglob('*') if path.is_dir()] + [install]
        try:
            for path in files:
                path.chmod(0o444)
            for path in directories:
                path.chmod(0o555)
            code = ('import sys,json; sys.path.insert(0,sys.argv[1]); '
                    'from xlsx_delivery import finish_delivery; '
                    'print(json.dumps(finish_delivery({"status":"success"},"report.csv")))')
            result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code, str(install / 'scripts')],
                                    capture_output=True, text=True, env={**os.environ, 'PATH': ''}, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['delivery']['files'][0]['archive_verified'])
            after = {path.relative_to(install).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
            self.assertEqual(before, after)
            self.assertFalse(list(install.rglob('__pycache__')))
        finally:
            for path in directories:
                path.chmod(0o755)
            for path in files:
                path.chmod(0o644)


if __name__ == '__main__':
    unittest.main()
