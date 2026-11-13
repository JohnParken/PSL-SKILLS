"""Archive collision and version naming contracts on isolated synthetic files."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import concurrent.futures
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archive_delivery as delivery
import artifact_files
from run_docx_task import create_task


class ArchiveCollisionTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory(prefix='archive-collision-')
        os.chdir(self.temporary.name)
        self.environment = patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': json.dumps(
            {'commonParam': [{'name': 'questionId', 'value': 'test'}]})}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        os.chdir(self.previous)
        self.temporary.cleanup()

    def test_conflict_preserves_history_and_primary_and_cleans_stage(self):
        Path('report.txt').write_bytes(b'new report')
        target = Path('output/test/report.txt')
        target.parent.mkdir(parents=True)
        target.write_bytes(b'history')
        original = target.stat()
        result = delivery.archive_delivery([Path('report.txt')], [])
        self.assertEqual(result['archive_status'], 'failed')
        self.assertEqual(result['files'][0]['error_code'], 'archive_conflict')
        self.assertEqual(target.read_bytes(), b'history')
        self.assertEqual(target.stat().st_ino, original.st_ino)
        self.assertEqual(Path('report.txt').read_bytes(), b'new report')
        self.assertEqual({p.name for p in Path('.').iterdir()}, {'report.txt', 'output'})

    def test_archive_cli_refuses_replacement(self):
        Path('report.txt').write_bytes(b'new report')
        target = Path('output/test/report.txt')
        target.parent.mkdir(parents=True)
        target.write_bytes(b'history')
        command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).parents[1] / 'archive_delivery.py'), 'report.txt']
        refused = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(json.loads(refused.stdout)['files'][0]['error_code'], 'archive_conflict')
        rejected = subprocess.run(command + ['--replace-existing'], text=True, capture_output=True, check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(target.read_bytes(), b'history')
        self.assertRaises(TypeError, delivery.archive_delivery, [Path('report.txt')], [], replace_existing=True)

    def test_target_appearing_at_publication_is_not_replaced(self):
        Path('report.txt').write_bytes(b'new report')
        real_link = os.link
        def competing_link(source, target, **kwargs):
            Path(target).write_bytes(b'other task')
            return real_link(source, target, **kwargs)
        with patch.object(artifact_files.os, 'link', side_effect=competing_link):
            result = delivery.archive_delivery([Path('report.txt')], [])
        self.assertEqual(result['files'][0]['error_code'], 'archive_conflict')
        self.assertEqual(Path('output/test/report.txt').read_bytes(), b'other task')
        self.assertFalse(list(Path('.').glob('.docx-*.tmp')))

    def test_two_publishers_have_one_complete_winner(self):
        Path('report.txt').write_bytes(b'A' * 4096)
        sources = [Path('report.txt'), Path('report.txt')]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda source: delivery.archive_delivery([source], []), sources))
        self.assertEqual(sorted(r['archive_status'] for r in results), ['success', 'success'])
        winner = next(i for i, r in enumerate(results) if r['archive_status'] == 'success')
        self.assertEqual(Path('output/test/report.txt').read_bytes(), sources[winner].read_bytes())
        self.assertFalse(list(Path('.').glob('.docx-*.tmp')))

    def test_unavailable_exclusive_publication_never_falls_back_to_replace(self):
        Path('report.txt').write_bytes(b'new report')
        with patch.object(artifact_files.os, 'link', side_effect=OSError('hard links unavailable')), \
                patch.object(artifact_files.os, 'replace', side_effect=AssertionError('unsafe fallback')):
            result = delivery.archive_delivery([Path('report.txt')], [])
        self.assertEqual(result['archive_status'], 'failed')
        self.assertFalse(Path('output/test/report.txt').exists())
        self.assertFalse(list(Path('.').glob('.docx-*.tmp')))

    def test_successful_publication_with_unlink_failure_reports_residual(self):
        Path('report.txt').write_bytes(b'new report')
        with patch.object(artifact_files.os, 'unlink', side_effect=PermissionError('cleanup denied')):
            result = delivery.archive_delivery([Path('report.txt')], [])
        self.assertEqual(result['archive_status'], 'failed')
        self.assertEqual(Path('output/test/report.txt').read_bytes(), b'new report')
        self.assertEqual(len(result['temporary_residuals']), 1)
        self.assertTrue(Path(result['temporary_residuals'][0]).exists())

    def test_version_selection_checks_main_and_archive_without_stacking_suffixes(self):
        Path('报告.docx').write_bytes(b'original')
        Path('output/test').mkdir(parents=True)
        Path('output/test/报告_第2版.docx').write_bytes(b'archive')
        self.assertEqual(delivery.choose_delivery_output(Path('报告.docx')), Path('报告_第3版.docx'))
        Path('报告_第3版.docx').write_bytes(b'original third')
        self.assertEqual(delivery.choose_delivery_output(Path('报告_第3版.docx')), Path('报告_第4版.docx'))

    def test_version_selection_keeps_boundary_and_filename_byte_limit(self):
        long_name = '文' * 83 + '.docx'
        Path(long_name).write_bytes(b'original')
        selected = delivery.choose_delivery_output(Path(long_name))
        self.assertLessEqual(len(selected.name.encode('utf-8')), 255)
        self.assertTrue(selected.name.endswith('_第2版.docx'))
        for invalid in ('../report.docx', '/report.docx'):
            with self.assertRaises(ValueError):
                delivery.choose_delivery_output(Path(invalid))
        with self.assertRaises(ValueError):
            delivery.choose_delivery_output(Path(long_name), [Path(long_name)])

    def test_docx_runner_versions_archive_only_collision_and_cleans_helpers(self):
        Path('output/test').mkdir(parents=True)
        Path('output/test/报告.docx').write_bytes(b'historical archive')
        spec = {'blocks': [{'type': 'paragraph', 'text': 'new content'}]}
        first = create_task(spec, '报告.docx')
        second = create_task(spec, '报告.docx')
        self.assertEqual(first['output'], '报告_第2版.docx')
        self.assertEqual(second['output'], '报告_第3版.docx')
        self.assertEqual(Path('output/test/报告.docx').read_bytes(), b'historical archive')
        for result in (first, second):
            self.assertEqual(result['archive_status'], 'success')
            self.assertTrue(result['cleanup']['manifest_removed'])
            self.assertEqual(Path(result['output']).read_bytes(), Path('output/test', result['output']).read_bytes())
        self.assertEqual({p.name for p in Path('.').iterdir()},
                         {'output', '报告_第2版.docx', '报告_第3版.docx'})

    def test_versioned_generation_failure_cleans_helpers_and_keeps_history(self):
        Path('报告.docx').write_bytes(b'previous main')
        target = Path('output/test/报告_第2版.docx')
        target.parent.mkdir(parents=True)
        target.write_bytes(b'previous archive')
        result = create_task({'blocks': [{'type': 'unsupported', 'text': 'rejected'}]}, '报告.docx')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['cleanup']['manifest_removed'])
        self.assertEqual(Path('报告.docx').read_bytes(), b'previous main')
        self.assertEqual(target.read_bytes(), b'previous archive')
        self.assertEqual({p.name for p in Path('.').iterdir()}, {'报告.docx', 'output'})


if __name__ == '__main__':
    unittest.main()
