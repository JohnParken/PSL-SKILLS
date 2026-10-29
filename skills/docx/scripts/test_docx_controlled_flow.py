"""Four workflow changes; isolated synthetic input and explicit ownership only."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import subprocess
from contextlib import redirect_stdout
from unittest.mock import patch
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_run_docx_task as baseline
from test_run_docx_task import SPEC, BAD_SPEC
import run_docx_task as runner
import run_docx_edit as editor
from run_docx_edit import edit_task
from create_docx import create_document
from read_docx import read_docx, main as read_main


class ControlledTests(unittest.TestCase):
    setUp = baseline.AutomaticTaskTests.setUp
    tearDown = baseline.AutomaticTaskTests.tearDown

    def source(self, blocks=None):
        create_document({'blocks': blocks or [{'type':'paragraph','text':'合同 ABC'}]}, 'source.docx')
        return Path('source.docx')

    def test_deterministic_unchanged_retry_keeps_attempts(self):
        # Exercise compatibility recovery on a pre-closeout interrupted fixture.
        result = runner._create_task(BAD_SPEC, 'report.docx', workspace='owned')
        self.assertEqual(result['error_code'], 'spec_validation_failed')
        self.assertEqual(result['next_action'], 'correct_spec')
        manifest = result['task_manifest']
        before = Path(manifest).read_bytes()
        repeated = runner.retry_task(manifest, copy.deepcopy(BAD_SPEC))
        self.assertEqual(repeated['error_code'], 'unchanged_invalid_spec')
        self.assertEqual(Path(manifest).read_bytes(), before)
        self.assertEqual(runner.retry_task(manifest, SPEC)['status'], 'verified')

    def test_text_edit_cleans_owned_json_and_preserves_input(self):
        source = self.source(); original = source.read_bytes()
        result = edit_task('text', source, 'edited.docx', {'replacements':[{'old':'ABC','new':'XYZ','count':1}]})
        self.assertEqual(result['status'], 'edited', result)
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(read_docx('edited.docx'), '合同 XYZ')
        self.assertFalse(list(Path('.').glob('docx-edit-*.spec.json')))
        self.assertFalse(list(Path('.').glob('docx-edit-*.cleanup.json')))
        self.assertIsNone(result['task_manifest'])
        self.assertIsNone(result['cleanup']['manifest_retained'])
        self.assertTrue(result['cleanup']['manifest_removed'])
        with self.assertRaises(ValueError): edit_task('text', source, 'edited.docx', {})

    def test_structure_tables_comments_dispatch(self):
        self.source()
        requests = [('structure', {'operations':[{'type':'insert_paragraph','paragraph_index':0,'expected_text':'合同 ABC','position':'after','text':'补充'}]}),
                    ('comments', {'action':'add','paragraph_index':0,'expected_text':'合同 ABC','anchor_text':'合同 ABC','text':'核对','author':'审阅者'})]
        for kind, spec in requests:
            result = edit_task(kind, 'source.docx', kind+'.docx', spec)
            self.assertEqual(result['status'], 'edited', result)
        create_document({'blocks':[{'type':'table','rows':[['项目','金额'],['甲','10']], 'header':True}]}, 'table.docx')
        result = edit_task('tables', 'table.docx', 'tables.docx', {'operation':{'type':'insert_row','table_index':0,'row_index':1,'expected_rows':2,'expected_text':['甲','10'],'position':'after','cells':['乙','20']}})
        self.assertEqual(result['status'], 'edited', result)

    def test_failed_edit_cleans_registered_spec(self):
        self.source()
        result = edit_task('text','source.docx','edited.docx',{'replacements':[{'old':'missing','new':'X'}]})
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(list(Path('.').glob('docx-edit-*.spec.json')))
        self.assertIsNone(result['task_manifest'])
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertFalse(Path('edited.docx').exists())

    def test_success_does_not_touch_history_or_pending_retry(self):
        self.source()
        failed = edit_task('text', 'source.docx', 'retry.docx',
                           {'replacements': [{'old': 'missing', 'new': 'X'}]})
        history = {p: p.read_bytes() for p in Path('.').glob('docx-edit-*.json')}
        self.assertIsNone(failed['task_manifest'])
        self.assertEqual(history, {})
        unknown = Path('docx-edit-history.cleanup.json')
        unknown.write_text('unknown historical data')
        history[unknown] = unknown.read_bytes()
        for index in range(2):
            result = edit_task('text', 'source.docx', f'edited{index}.docx',
                               {'replacements': [{'old': 'ABC', 'new': 'XYZ'}]})
            self.assertTrue(result['cleanup']['manifest_removed'])
        self.assertEqual({p: p.read_bytes() for p in Path('.').glob('docx-edit-*.json')}, history)

    def test_cli_reports_success_without_stale_manifest_path(self):
        self.source()
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(Path(editor.__file__).resolve()),
             'text', 'source.docx', 'edited.docx'],
            input=json.dumps({'replacements': [{'old': 'ABC', 'new': 'XYZ'}]}),
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'edited')
        self.assertEqual(report['task_status'], 'completed')
        self.assertEqual(report['archive_status'], 'copied')
        self.assertTrue(report['cleanup']['manifest_removed'])
        self.assertIsNone(report['task_manifest'])
        self.assertFalse(list(Path('.').glob('docx-edit-*.json')))
        self.assertEqual(read_docx('edited.docx'), '合同 XYZ')

    def test_archive_failure_or_skip_cleans_all_intermediates(self):
        self.source()
        for status in ('failed', 'skipped'):
            with self.subTest(status=status), patch('archive_deliverables.archive_outputs', return_value={
                    'archive_status': status, 'archive_message': None, 'warnings': []}):
                result = edit_task('text', 'source.docx', status + '.docx',
                                   {'replacements': [{'old': 'ABC', 'new': 'XYZ'}]})
            self.assertEqual(result['status'], 'edited', result)
            self.assertEqual(result['task_status'], 'completed')
            self.assertIsNone(result['task_manifest'])
            self.assertTrue(result['cleanup']['manifest_removed'])
            self.assertFalse(list(Path('.').glob('docx-edit-*')))
            self.assertEqual(read_docx(status + '.docx'), '合同 XYZ')

    def test_manifest_unlink_failure_preserves_receipt_and_primary(self):
        self.source()
        original = os.unlink
        def deny_manifest(path, *args, **kwargs):
            if str(path).startswith('docx-edit-') and str(path).endswith('.cleanup.json'):
                raise PermissionError('injected manifest removal failure')
            return original(path, *args, **kwargs)
        with patch.object(editor.os, 'unlink', side_effect=deny_manifest) as unlink:
            # TaskFiles checks function identity against the platform capability set.
            with patch.object(os, 'supports_dir_fd', os.supports_dir_fd | {unlink}):
                result = edit_task('text', 'source.docx', 'edited.docx',
                                   {'replacements': [{'old': 'ABC', 'new': 'XYZ'}]})
        self.assertEqual(result['status'], 'edited', result)
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertFalse(result['cleanup']['cleanup_complete'])
        self.assertTrue(Path(result['task_manifest']).is_file())
        self.assertEqual(read_docx('edited.docx'), '合同 XYZ')
        self.assertIn('injected manifest removal failure', str(result['warnings']))

    def test_changed_manifest_is_not_removed(self):
        self.source()
        original = editor._retire_manifest
        def replace_receipt(task, manifest, expected, payload):
            Path(manifest).write_text('replacement must survive')
            return original(task, manifest, expected, payload)
        with patch.object(editor, '_retire_manifest', side_effect=replace_receipt):
            result = edit_task('text', 'source.docx', 'edited.docx',
                               {'replacements': [{'old': 'ABC', 'new': 'XYZ'}]})
        self.assertEqual(result['task_status'], 'cleanup_incomplete')
        self.assertEqual(Path(result['task_manifest']).read_text(), 'replacement must survive')

    def test_changed_output_keeps_manifest(self):
        self.source()
        original = editor.cleanup_task
        def change_output(manifest):
            report = original(manifest)
            Path('edited.docx').write_bytes(b'changed')
            return report
        with patch.object(editor, 'cleanup_task', side_effect=change_output):
            result = edit_task('text', 'source.docx', 'edited.docx',
                               {'replacements': [{'old': 'ABC', 'new': 'XYZ'}]})
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(Path(result['task_manifest']).is_file())

    def test_manifest_link_replacement_preserves_target(self):
        self.source()
        target = Path('protected.json')
        target.write_text('user data')
        original = editor._retire_manifest
        for link_kind in ('symlink', 'hardlink'):
            def replace_with_link(task, manifest, expected, payload):
                Path(manifest).unlink()
                if link_kind == 'symlink':
                    Path(manifest).symlink_to(target)
                else:
                    os.link(target, manifest)
                return original(task, manifest, expected, payload)
            with self.subTest(link_kind=link_kind), patch.object(
                    editor, '_retire_manifest', side_effect=replace_with_link):
                result = edit_task('text', 'source.docx', link_kind + '.docx',
                                   {'replacements': [{'old': 'ABC', 'new': 'XYZ'}]})
                self.assertEqual(result['task_status'], 'cleanup_incomplete')
                self.assertTrue(Path(result['task_manifest']).exists())
                self.assertEqual(target.read_text(), 'user data')

    def test_read_delivery_is_explicit_and_legacy_stdout_stays(self):
        self.source()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(read_main(['source.docx']), 0)
        self.assertEqual(output.getvalue().strip(), '合同 ABC')
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(read_main(['source.docx','-o','read.txt','--delivery']), 0)
        result=json.loads(output.getvalue())
        self.assertEqual(result['archive_message'],'归档完成')
        self.assertEqual(Path('read.txt').read_text(), '合同 ABC')

    def test_comments_list_does_not_create_helpers(self):
        self.source(); before=set(Path('.').iterdir())
        result=edit_task('comments','source.docx','list.docx',{'action':'list'})
        self.assertEqual(result['error_code'],'edit_request_rejected')
        self.assertEqual(before,set(Path('.').iterdir()))

    def test_image_edit_uses_existing_selection_contract(self):
        import hashlib
        import zipfile
        from test_docx_editing import png_pixel
        Path('red.png').write_bytes(png_pixel(255,0,0))
        Path('blue.png').write_bytes(png_pixel(0,0,255))
        self.source([{'type':'image','path':'red.png','width_mm':5,'height_mm':5,'alt':'pixel'}])
        with zipfile.ZipFile('source.docx') as archive:
            media=next(n for n in archive.namelist() if n.startswith('word/media/'))
            selection={'media_part':media,'expected_sha256':hashlib.sha256(archive.read(media)).hexdigest(),'expected_relationships':1,'expected_references':1}
        result=edit_task('image','source.docx','image.docx',selection,replacement='blue.png')
        self.assertEqual(result['status'],'edited',result)
        self.assertTrue(result['cleanup']['cleanup_complete'])

    def test_busy_task_lock_preserves_config_and_does_not_edit(self):
        self.source()
        process=subprocess.Popen([sys.executable,'-I','-S','-B','-c','import os,fcntl,sys; f=os.open(".",os.O_RDONLY); fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.readline()'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'locked')
            result=edit_task('text','source.docx','edited.docx',{'replacements':[{'old':'ABC','new':'XYZ'}]})
            self.assertEqual(result['status'],'failed')
            self.assertFalse(Path('edited.docx').exists())
        finally:
            process.communicate('release\n',timeout=10)

    def test_cleanup_failure_retains_primary_success(self):
        self.source()
        with patch('run_docx_edit.cleanup_task',side_effect=OSError('injected cleanup failure')):
            result=edit_task('text','source.docx','edited.docx',{'replacements':[{'old':'ABC','new':'XYZ'}]})
        self.assertEqual(result['status'],'edited')
        self.assertEqual(result['task_status'],'cleanup_incomplete')
        self.assertTrue(Path('edited.docx').exists())
        self.assertTrue(Path(result['task_manifest']).is_file())
        self.assertTrue(list(Path('.').glob('docx-edit-*.spec.json')))
