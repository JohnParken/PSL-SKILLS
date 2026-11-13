"""Fresh request context, safe retries and failure recovery for the archive API."""
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
import errno
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import archive_delivery as delivery
import artifact_files


class ArchiveV2Tests(unittest.TestCase):
    def setUp(self):
        self.cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory(prefix='archive-v2-')
        os.chdir(self.temp.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.context('one')

    def tearDown(self):
        self.env.stop()
        os.chdir(self.cwd)
        self.temp.cleanup()

    @staticmethod
    def context(question):
        os.environ['ROUTER_CONFIG_VARIABLES'] = json.dumps({'commonParam': [{'name': 'questionId', 'value': question}]})

    def test_empty_manifest_has_no_success_claim_or_filesystem_side_effect(self):
        result = delivery.archive_delivery([])
        self.assertEqual(result['archive_status'], 'not_required')
        self.assertEqual(result['completed_count'], 0)
        self.assertNotIn('归档成功', result['archive_user_notice'])
        self.assertEqual(list(Path('.').iterdir()), [])

    def test_retry_and_new_question_reverify_current_bytes(self):
        Path('final.txt').write_bytes(b'content')
        first = delivery.archive_delivery(['final.txt'])
        target = Path('output/one/final.txt')
        identity = target.stat().st_ino
        second = delivery.archive_delivery(['final.txt'])
        self.assertEqual(first['files'][0]['archive_status'], 'copied')
        self.assertEqual(second['files'][0]['archive_status'], 'already_archived')
        self.assertEqual(target.stat().st_ino, identity)
        self.context('two')
        third = delivery.archive_delivery(['final.txt'])
        self.assertEqual(third['files'][0]['archive_output'], 'output/two/final.txt')
        del os.environ['ROUTER_CONFIG_VARIABLES']
        missing = delivery.archive_delivery(['final.txt'])
        self.assertEqual(missing['archive_status'], 'failed')
        self.assertEqual(missing['reason_code'], 'current_question_id_unavailable')
        self.assertEqual(Path('final.txt').read_bytes(), b'content')

    def test_partial_retry_only_copies_missing_item(self):
        for name in ['first.txt', 'second.txt']:
            Path(name).write_text(name)
        real = delivery._copy_main_to_archive
        def fail_second(*args):
            if args[1].name == 'second.txt':
                raise OSError(errno.ENOSPC, 'full')
            return real(*args)
        with patch.object(delivery, '_copy_main_to_archive', side_effect=fail_second):
            first = delivery.archive_delivery(['first.txt', 'second.txt'])
        self.assertEqual(first['archive_status'], 'partial')
        self.assertEqual(first['files'][1]['reason_code'], 'archive_storage_full')
        self.assertEqual(delivery.archive_exit_code(first), 3)
        second = delivery.archive_delivery(['first.txt', 'second.txt'])
        self.assertEqual(second['archive_status'], 'success')
        self.assertEqual([item['archive_status'] for item in second['files']], ['already_archived', 'copied'])

    def test_context_change_mid_batch_never_uses_stale_context_for_next_copy(self):
        Path('a.txt').write_bytes(b'a')
        Path('b.txt').write_bytes(b'b')
        real = delivery._copy_main_to_archive
        def change_context(*args):
            result = real(*args)
            self.context('two')
            return result
        with patch.object(delivery, '_copy_main_to_archive', side_effect=change_context):
            result = delivery.archive_delivery(['a.txt', 'b.txt'])
        self.assertEqual(result['archive_status'], 'failed')
        self.assertFalse(Path('output/one/b.txt').exists())
        self.assertFalse(Path('output/two').exists())

    def test_changed_source_is_not_marked_verified(self):
        Path('a.txt').write_bytes(b'a')
        real = delivery._copy_main_to_archive
        def change_source(*args):
            result = real(*args)
            Path('a.txt').write_bytes(b'b')
            return result
        with patch.object(delivery, '_copy_main_to_archive', side_effect=change_source):
            result = delivery.archive_delivery(['a.txt'])
        self.assertEqual(result['archive_status'], 'failed')
        self.assertNotIn('archive_verified', result['files'][0])

    def test_stage_files_never_enter_archive_directory(self):
        Path('a.txt').write_bytes(b'a')
        real = artifact_files.os.open
        observed = []
        def track_stage(path, *args, **kwargs):
            if Path(path).name.startswith('.archive-'):
                observed.append(Path(path).parent.resolve())
            return real(path, *args, **kwargs)
        with patch.object(artifact_files.os, 'open', side_effect=track_stage):
            result = delivery.archive_delivery(['a.txt'])
        self.assertEqual(result['archive_status'], 'success')
        self.assertEqual(observed, [Path.cwd()])
        self.assertEqual(list(Path('output/one').iterdir()), [Path('output/one/a.txt')])
        self.assertFalse(list(Path('.').glob('.archive-*.tmp')))

    def test_archive_directory_symlink_and_source_alias_are_rejected(self):
        Path('a.txt').write_bytes(b'a')
        Path('elsewhere').mkdir()
        Path('output').symlink_to('elsewhere', target_is_directory=True)
        self.assertEqual(delivery.archive_delivery(['a.txt'])['archive_status'], 'failed')
        self.assertEqual(list(Path('elsewhere').iterdir()), [])

    def test_cli_empty_check_and_removed_flags(self):
        script = str(Path(delivery.__file__).resolve())
        for args, expected in [([], 0), (['--check'], 0), (['--replace-existing'], 2)]:
            result = subprocess.run([sys.executable, '-I', '-S', '-B', script, *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected == 0:
                self.assertEqual(json.loads(result.stdout)['archive_contract'], 'archive-delivery-v2')


if __name__ == '__main__':
    unittest.main()
