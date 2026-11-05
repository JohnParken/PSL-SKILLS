"""CLI boundary and isolated path replacement regressions; no user files."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from read_wps import read_bytes
from run_wps_task import create_document
from test_wps_ooxml import package
from wps_task_paths import ACTIVE, TaskPaths
import wps_task_paths


@contextmanager
def scoped(directory):
    previous = Path.cwd()
    os.chdir(directory)
    access = TaskPaths()
    token = ACTIVE.set(access)
    try:
        yield access
    finally:
        ACTIVE.reset(token)
        os.close(access.descriptor)
        os.chdir(previous)


class TaskPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wps-boundary-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.task = self.base / 'task'
        self.task.mkdir()
        self.other = self.base / 'other'
        self.other.mkdir()
        self.source = self.task / 'in.rtf'
        self.source.write_bytes(br'{\rtf1 Source\par}')
        (self.other / 'outside.rtf').write_bytes(self.source.read_bytes())

    def cli(self, script, *arguments, stdin=None):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str((Path(__file__).parents[1] / script)), *map(str, arguments)],
                              cwd=self.task, input=stdin, text=True, capture_output=True, timeout=10)

    def test_reader_rejects_parent_absolute_symlink_and_hardlink(self):
        (self.task / 'alias.rtf').symlink_to(self.other / 'outside.rtf')
        os.link(self.other / 'outside.rtf', self.task / 'hard.rtf')
        for argument in ('../other/outside.rtf', str(self.source), 'alias.rtf', 'hard.rtf'):
            with self.subTest(argument=argument):
                result = self.cli('read_wps.py', argument)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('Source', result.stdout)
                self.assertNotIn(json.loads(result.stdout)['status'], ('ok', 'partial'))
        (self.task / '--check').symlink_to(self.other / 'outside.rtf')
        literal = self.cli('read_wps.py', '--', '--check')
        self.assertEqual(literal.returncode, 1)
        self.assertNotIn('Source', literal.stdout)

    def test_creation_rejects_escape_linked_parent_and_existing_target(self):
        (self.task / 'linked').symlink_to(self.other, target_is_directory=True)
        (self.task / 'existing.wps').write_bytes(b'original')
        spec = json.dumps({'blocks': [{'type': 'paragraph', 'text': 'Created'}]})
        for argument in ('../other/output.wps', str(self.other / 'output.wps'), 'linked/output.wps', 'existing.wps'):
            result = self.cli('run_wps_task.py', 'create', argument, stdin=spec)
            self.assertEqual(result.returncode, 1)
        self.assertEqual((self.task / 'existing.wps').read_bytes(), b'original')
        self.assertFalse((self.other / 'output.wps').exists())

    def test_relative_nested_create_read_and_edit(self):
        spec = json.dumps({'theme': 'general', 'page_numbering': False, 'blocks': [{'type': 'paragraph', 'text': 'Source'}]})
        self.assertEqual(self.cli('run_wps_task.py', 'create', 'nested/new.wps', stdin=spec).returncode, 0)
        self.assertEqual(self.cli('read_wps.py', 'nested/new.wps').returncode, 0)
        edited = self.cli('run_wps_edit.py', 'text', 'nested/new.wps', 'nested/edited.wps', '--find', 'Source', '--replace', 'Changed')
        self.assertEqual(edited.returncode, 0, edited.stderr)
        self.assertEqual(read_bytes((self.task / 'nested/edited.wps').read_bytes())['text'], 'Changed')

    def test_conversion_and_inspect_confined(self):
        (self.task / 'in.docx').write_bytes(package('<w:p><w:r><w:t>Text</w:t></w:r></w:p>'))
        converted = self.cli('export_wps.py', 'convert', 'in.docx', '--backend', 'rtf', '--output', 'out.wps', '--verify-read')
        self.assertEqual(converted.returncode, 0, converted.stdout + converted.stderr)
        self.assertEqual(self.cli('export_wps.py', 'inspect', 'out.wps').returncode, 0)
        self.assertEqual(self.cli('export_wps.py', 'inspect', '../other/outside.rtf').returncode, 1)
        self.assertEqual(self.cli('export_wps.py', 'convert', 'in.docx', '--backend', 'rtf', '--output', '../other/out.wps').returncode, 1)

    def test_config_inputs_cannot_escape(self):
        (self.other / 'spec.json').write_text('{"blocks":[{"type":"paragraph","text":"X"}]}', encoding='utf-8')
        result = self.cli('run_wps_task.py', 'create', 'new.wps', '--spec', '../other/spec.json')
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.task / 'new.wps').exists())

    def test_parent_replacement_cannot_redirect_candidate_or_publication(self):
        (self.task / 'nested').mkdir()
        original = wps_task_paths.write_candidate
        with scoped(self.task):
            def swapped(path, data):
                original(path, data)
                (self.task / 'nested').rename(self.task / 'saved')
                (self.task / 'nested').symlink_to(self.other, target_is_directory=True)
            with patch('wps_task_paths.write_candidate', side_effect=swapped), self.assertRaises(OSError):
                create_document({'blocks': [{'type': 'paragraph', 'text': 'X'}]}, Path('nested/new.wps'))
        self.assertFalse((self.other / 'new.wps').exists())
        self.assertEqual(list((self.task / 'saved').iterdir()), [])

    def test_target_appearing_during_validation_is_never_replaced(self):
        original = wps_task_paths.write_candidate
        with scoped(self.task):
            def appeared(path, data):
                original(path, data)
                Path('new.wps').write_bytes(b'other task')
            with patch('wps_task_paths.write_candidate', side_effect=appeared), self.assertRaises(FileExistsError):
                create_document({'blocks': [{'type': 'paragraph', 'text': 'X'}]}, Path('new.wps'))
        self.assertEqual((self.task / 'new.wps').read_bytes(), b'other task')
        self.assertFalse(list(self.task.glob('.tmp_wps_*')))

    def test_library_absolute_path_remains_supported(self):
        output = self.other / 'library.wps'
        self.assertEqual(create_document({'blocks': [{'type': 'paragraph', 'text': 'API'}]}, output)['status'], 'success')
        self.assertEqual(read_bytes(output.read_bytes())['text'], 'API')

    def test_two_cli_publishers_do_not_overwrite_each_other(self):
        spec = json.dumps({'blocks': [{'type': 'paragraph', 'text': 'Winner'}]})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.cli('run_wps_task.py', 'create', 'shared.wps', stdin=spec), range(2)))
        self.assertEqual(sorted(result.returncode for result in results), [0, 1])
        self.assertEqual(read_bytes((self.task / 'shared.wps').read_bytes())['text'], 'Winner')
        self.assertFalse(list(self.task.glob('.tmp_wps_*')))

    def test_missing_capabilities_and_old_python_stop(self):
        with patch.object(os, 'supports_dir_fd', set()), self.assertRaisesRegex(ValueError, 'unavailable'):
            TaskPaths()
        with patch.object(sys, 'version_info', (3, 9, 20)), self.assertRaisesRegex(ValueError, '3.10'):
            TaskPaths()

    def test_replaced_candidate_is_retained_not_deleted(self):
        original = wps_task_paths.write_candidate
        candidates = []
        with scoped(self.task):
            def replaced(path, data):
                original(path, data)
                Path(path).unlink()
                Path(path).write_bytes(b'unknown replacement')
                candidates.append(Path(path))
            with patch('wps_task_paths.write_candidate', side_effect=replaced), self.assertRaises(ValueError):
                create_document({'blocks': [{'type': 'paragraph', 'text': 'X'}]}, Path('new.wps'))
            self.assertEqual(candidates[0].read_bytes(), b'unknown replacement')
        self.assertFalse((self.task / 'new.wps').exists())

    def test_candidate_byte_change_after_validation_is_not_published(self):
        from wps_content_validation import validate_format
        with scoped(self.task):
            def changed(document, candidate):
                report = validate_format(document, candidate)
                path = next(Path('.').glob('.tmp_wps_*.rtf'))
                path.write_bytes(path.read_bytes().replace(b'Reliable', b'Untrusted'))
                return report
            with patch('wps_content_validation.validate_format', side_effect=changed), self.assertRaisesRegex(ValueError, 'bytes changed'):
                create_document({'blocks': [{'type': 'paragraph', 'text': 'Reliable'}]}, Path('new.wps'))
        self.assertFalse((self.task / 'new.wps').exists())
        self.assertFalse(list(self.task.glob('.tmp_wps_*')))


if __name__ == '__main__':
    unittest.main()
