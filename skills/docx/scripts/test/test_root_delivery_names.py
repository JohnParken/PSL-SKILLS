"""Public delivery names must be checked before lexical Path normalization."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from archive_delivery import validate_delivery_output, validate_input_path, choose_delivery_output, archive_delivery


class RootDeliveryNames(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory()
        os.chdir(self.temporary.name)
        Path('inputs').mkdir()
        Path('inputs/input.docx').write_bytes(b'not parsed')
        Path('spec.json').write_text('{}')

    def tearDown(self):
        os.chdir(self.previous)
        self.temporary.cleanup()

    def test_filename_validation_and_internal_input_boundary(self):
        self.assertEqual(validate_delivery_output('中文报告.docx'), Path('中文报告.docx'))
        self.assertEqual(validate_input_path(Path('inputs/input.docx')), Path('inputs/input.docx'))
        for name in ('./report.docx', 'sub/report.docx', '../report.docx', '/report.docx',
                     'C:\\report.docx', 'C:report.docx', '\\\\host\\report.docx',
                     '.', '..', '', 'report.docx/', 'a/../report.docx'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_delivery_output(name)
        Path('report.docx').write_bytes(b'existing')
        self.assertEqual(choose_delivery_output('report.docx'), Path('report_第2版.docx'))
        self.assertEqual(Path('report.docx').read_bytes(), b'existing')
        self.assertEqual(archive_delivery(['inputs/input.docx'], [])['archive_status'], 'failed')

    def test_public_write_apis_reject_paths(self):
        from create_docx import create_document
        from edit_docx import replace_text
        from edit_structure import edit_structure
        from edit_tables import edit_tables
        from replace_image import replace_image
        from office.package import pack_document
        from office.helpers import rezip
        for name in ('./report.docx', 'sub/report.docx', str(Path.cwd()/'report.docx'), 'C:\\report.docx'):
            callbacks = [
                lambda: create_document({'blocks': []}, name),
                lambda: replace_text('inputs/input.docx', name, {}),
                lambda: edit_structure('inputs/input.docx', name, {}),
                lambda: edit_tables('inputs/input.docx', name, {}),
                lambda: replace_image('inputs/input.docx', 'spec.json', name, {}),
                lambda: pack_document('inputs', name),
                lambda: rezip(Path('inputs'), name),
            ]
            for callback in callbacks:
                with self.subTest(name=name, callback=callback), self.assertRaisesRegex(ValueError, 'bare filename'):
                    callback()

    def test_create_cannot_replace_image_or_its_hardlink(self):
        from create_docx import create_document
        from test_docx_editing import png_pixel
        original = png_pixel(1, 2, 3)
        Path('image.png').write_bytes(original)
        os.link('image.png', 'alias.docx')
        spec = {'blocks': [{'type': 'image', 'path': 'image.png', 'width_mm': 5, 'height_mm': 5, 'alt': 'pixel'}]}
        for output in ('alias.docx',):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'overwrite an image'):
                create_document(spec, output)
            self.assertEqual(Path('image.png').read_bytes(), original)
            self.assertEqual(Path('alias.docx').read_bytes(), original)

    def test_public_cli_rejects_directory_syntax_before_parsing(self):
        entries = [
            ('read_docx.py', ['inputs/input.docx', '-o']),
            ('validate_layout.py', ['inputs/input.docx', '-o']),
            ('edit_docx.py', ['inputs/input.docx', 'spec.json']),
            ('edit_structure.py', ['inputs/input.docx', 'spec.json']),
            ('edit_tables.py', ['inputs/input.docx', 'spec.json']),
            ('manage_comments.py', ['inputs/input.docx', 'spec.json']),
            ('comment.py', ['inputs/input.docx', 'comment', '-o']),
            ('merge_runs.py', ['inputs/input.docx', '-o']),
            ('office/package.py', ['pack', 'inputs']),
            ('archive_delivery.py', []),
        ]
        for script, args in entries:
            for name in ('./report.docx', 'sub/report.docx', '../report.docx', '/report.docx', 'C:\\report.docx'):
                with self.subTest(script=script, name=name):
                    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS/script), *args, name], capture_output=True, text=True)
                    if script != 'archive_delivery.py':
                        self.assertNotEqual(result.returncode, 0)
                    else:
                        self.assertIn('failed', result.stdout)
                    self.assertIn('bare filename', result.stdout + result.stderr)
                    self.assertFalse(Path('report.docx').exists())
                    self.assertFalse(Path('sub').exists())


if __name__ == '__main__':
    unittest.main()
