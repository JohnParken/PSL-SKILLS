"""Public outputs are raw filenames; nested inputs and private staging still work."""
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from run_wps_task import create_document
from run_wps_edit import execute_edit_task
from run_wps_job import _command
from docx_to_wps_rtf import convert_docx_to_rtf_wps
import export_wps
from wps_rtf_builder import RtfDocument
from test_wps_ooxml import package

SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = {'page_numbering': False, 'blocks': [{'type': 'paragraph', 'text': 'Source'}]}


@contextmanager
def task_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class DeliveryFilenameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(task_directory(self.root))
        Path('inputs').mkdir()
        Path('inputs/source.rtf').write_bytes(br'{\rtf1 Source\par}')
        Path('inputs/source.docx').write_bytes(package('<w:p><w:r><w:t>Source</w:t></w:r></w:p>'))

    def invalid_names(self):
        return ('./new.wps', 'sub/new.wps', '../new.wps', 'sub/../new.wps', str(self.root / 'new.wps'),
                r'C:\temp\new.wps', 'C:new.wps', r'\\server\new.wps', '', '.', '..')

    def test_public_api_rejects_raw_paths_before_writing(self):
        document = RtfDocument()
        for name in self.invalid_names():
            for function in (lambda: create_document(SPEC, name),
                             lambda: execute_edit_task(Path('inputs/source.rtf'), name, []),
                             lambda: convert_docx_to_rtf_wps('inputs/source.docx', name),
                             lambda: document.save(name)):
                with self.subTest(name=name, function=function), self.assertRaises(ValueError):
                    function()
            for function in (export_wps.convert_docx_to_wps, export_wps.convert_docx_to_wps_rtf,
                             export_wps.convert_docx_to_wps_libreoffice):
                with self.subTest(name=name, function=function.__name__):
                    result = function('inputs/source.docx', name)
                    self.assertEqual(result['status'], 'error', result)
                    self.assertIn('bare filename', result['error'])
        self.assertEqual(set(Path('.').iterdir()), {Path('inputs')})

    def test_cli_create_edit_export_and_job_reject_raw_paths(self):
        for name in self.invalid_names():
            commands = (('run_wps_task.py', ['create', name]),
                        ('run_wps_edit.py', ['text', 'inputs/source.rtf', name, '--find', 'Source', '--replace', 'New']),
                        ('export_wps.py', ['convert', 'inputs/source.docx', '--backend', 'rtf', '--output', name]))
            for script, arguments in commands:
                with self.subTest(name=name, script=script):
                    process = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / script), *arguments],
                                             input=json.dumps(SPEC), text=True, capture_output=True, check=False)
                    self.assertNotEqual(process.returncode, 0)
            for declared, actual in ((name, 'new.wps'), ('new.wps', name), (name, name)):
                with self.assertRaises(ValueError):
                    _command({'script': 'run_wps_task.py', 'arguments': ['create', actual, '--spec', '@spec'],
                              'output': declared, 'configs': {'spec': SPEC}})
        self.assertEqual(set(Path('.').iterdir()), {Path('inputs')})

    def test_default_export_uses_cwd_not_source_parent(self):
        result = export_wps.convert_docx_to_wps('inputs/source.docx', backend='rtf')
        self.assertEqual(result['status'], 'success', result)
        self.assertTrue(Path('source.wps').is_file())
        self.assertFalse(Path('inputs/source.wps').exists())
        self.assertFalse(list(Path('.').glob('.tmp_wps_*')))

    def test_cli_default_export_uses_cwd(self):
        process = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'export_wps.py'),
                                  'convert', 'inputs/source.docx', '--backend', 'rtf'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(Path('source.wps').is_file())
        self.assertFalse(Path('inputs/source.wps').exists())

    def test_builder_save_is_exclusive_even_after_validation(self):
        import wps_task_paths
        document = RtfDocument()
        Path('existing.wps').write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            document.save('existing.wps')
        self.assertEqual(Path('existing.wps').read_bytes(), b'original')
        write = wps_task_paths.write_candidate
        def race(candidate, data):
            write(candidate, data)
            Path('raced.wps').symlink_to('inputs/source.rtf')
        with patch('wps_task_paths.write_candidate', side_effect=race), self.assertRaises(FileExistsError):
            document.save('raced.wps')
        self.assertEqual(Path('inputs/source.rtf').read_bytes(), br'{\rtf1 Source\par}')
        self.assertFalse(list(Path('.').glob('.tmp_wps_*')))

    def test_valid_filenames_and_link_protection(self):
        create_document(SPEC, '中文 空格.wps')
        execute_edit_task(Path('inputs/source.rtf'), 'edited.rtf', [])
        Path('symlink.wps').symlink_to('inputs/source.rtf')
        os.link('inputs/source.rtf', 'hardlink.wps')
        for name in ('symlink.wps', 'hardlink.wps'):
            with self.assertRaises(ValueError):
                create_document(SPEC, name)
        self.assertEqual(Path('inputs/source.rtf').read_bytes(), br'{\rtf1 Source\par}')


if __name__ == '__main__':
    unittest.main()
