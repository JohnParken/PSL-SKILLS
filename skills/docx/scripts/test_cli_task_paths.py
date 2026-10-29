"""DOCX task CLI path checks precede file parsing and publication."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from create_docx import validate_task_resources


class CliTaskPathTests(unittest.TestCase):
    def test_image_resources_reject_external_and_link_paths_before_read(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory(prefix='docx-image-paths-') as folder:
            root = Path(folder)
            task = root / 'task'
            task.mkdir()
            (root / 'image.png').write_bytes(b'not decoded')
            (task / 'link.png').symlink_to(root / 'image.png')
            (task / 'inputs').mkdir()
            (task / 'inputs/image.png').write_bytes(b'not decoded')
            try:
                os.chdir(task)
                for value in ('../image.png', str(root / 'image.png'), 'link.png'):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        validate_task_resources({'blocks': [{'type': 'image', 'path': value}]})
                validate_task_resources({'blocks': [{'path': 'image.png'}]}, 'inputs')
            finally:
                os.chdir(previous)

    def test_read_and_legacy_create_reject_external_inputs(self):
        with tempfile.TemporaryDirectory(prefix='docx-paths-') as folder:
            root = Path(folder)
            task = root / 'task'
            task.mkdir()
            for name in ('input.docx', 'spec.json'):
                (root / name).write_bytes(b'not parsed')
                (task / ('link-' + name)).symlink_to(root / name)
            for script, name in (('read_docx.py', 'input.docx'), ('create_docx.py', 'spec.json')):
                for source in ('../' + name, str(root / name), 'link-' + name):
                    args = [source] if script == 'read_docx.py' else [source, 'result.docx']
                    child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / script), *args],
                                           cwd=task, capture_output=True, text=True)
                    self.assertNotEqual(child.returncode, 0)
                    self.assertTrue('path' in child.stderr or 'links' in child.stderr)
                    self.assertFalse((task / 'result.docx').exists())

    def test_plain_read_output_is_checked_without_delivery_flag(self):
        with tempfile.TemporaryDirectory(prefix='docx-output-paths-') as folder:
            root = Path(folder)
            task = root / 'task'
            task.mkdir()
            (task / 'input.docx').write_bytes(b'not parsed')
            for output in ('../result.txt', str(root / 'result.txt')):
                child = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'read_docx.py'),
                                        'input.docx', '-o', output], cwd=task, capture_output=True, text=True)
                self.assertNotEqual(child.returncode, 0)
                self.assertIn('path', child.stderr)
                self.assertFalse((root / 'result.txt').exists())


if __name__ == '__main__':
    unittest.main()
