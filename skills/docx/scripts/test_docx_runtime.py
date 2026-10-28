"""Separate read-only runtime readiness from delivery-directory write readiness."""
from contextlib import redirect_stderr, redirect_stdout
import io
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
import read_docx
from office import runtime


class RuntimeTests(unittest.TestCase):
    def test_read_check_never_resolves_output_or_writes_probe(self):
        with patch.dict(os.environ, {'ROUTER_CONFIG_VARIABLES': 'private-invalid-config'}), \
                patch('create_docx._delivery_location', side_effect=AssertionError('no output routing')), \
                patch('pathlib.Path.mkdir', side_effect=AssertionError('no directory writes')), \
                patch('tempfile.TemporaryFile', side_effect=AssertionError('no write probe')), \
                redirect_stdout(io.StringIO()) as output:
            code = read_docx.main(['--check'])
        expected = 0 if expat.version_info >= (2, 6, 0) else 1
        self.assertEqual(code, expected)
        if code == 0:
            self.assertIn('read-check-ok', output.getvalue())
        self.assertNotIn('output_dir=', output.getvalue())
        self.assertNotIn('private-invalid-config', output.getvalue())

    def test_read_check_rejects_input_or_output_options(self):
        for args in (['input.docx'], ['-o', 'output.txt'], ['--include-related'],
                     ['--revisions', 'all'], ['--format', 'json']):
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    read_docx.main(['--check', *args])
                self.assertEqual(raised.exception.code, 2)

    def test_old_expat_fails_both_checks_without_write_probe(self):
        with patch.object(runtime.expat, 'EXPAT_VERSION', 'expat_2.2.8'), \
                patch('create_docx._prepare_delivery_directory', side_effect=AssertionError('no probe after failure')), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.assertEqual(read_docx.main(['--check']), 1)
            location = create_docx.OutputLocation(Path('.'), True, 'test')
            self.assertFalse(create_docx._runtime_check(Path.cwd(), location))
        self.assertNotIn('read-check-ok', output.getvalue())
        self.assertNotIn('\ncheck-ok\n', output.getvalue())

    def test_zlib_failure_is_reported(self):
        with patch.object(runtime.zlib, 'compress', side_effect=RuntimeError('unavailable')), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.assertEqual(read_docx.main(['--check']), 1)
        self.assertIn('zlib=failed', output.getvalue())
        self.assertNotIn('read-check-ok', output.getvalue())

    @unittest.skipUnless(expat.version_info >= (2, 6, 0), 'DOCX requires Expat >=2.6')
    def test_read_cli_in_nonwritable_task_directory_does_not_create_outputs(self):
        with tempfile.TemporaryDirectory(prefix='docx-read-runtime-', dir='.') as temp:
            directory = Path(temp).resolve()
            source = directory / 'input.docx'
            create_docx.create_document({'blocks': [{'type': 'paragraph', 'text': '只读内容 & 中文'}]}, source, verify=True)
            before = source.read_bytes()
            directory.chmod(0o555)
            try:
                for args in (['--check'], ['input.docx']):
                    process = subprocess.run([sys.executable, '-I', '-S', '-B',
                                              str(Path(read_docx.__file__)), *args],
                                             cwd=directory, env={**os.environ, 'PATH': '',
                                                'ROUTER_CONFIG_VARIABLES': 'private-invalid-config'},
                                             capture_output=True, encoding='utf-8')
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertNotIn('private-invalid-config', process.stdout + process.stderr)
                self.assertEqual(process.stdout, '只读内容 & 中文')
                self.assertEqual([p.name for p in directory.iterdir()], ['input.docx'])
                self.assertEqual(source.read_bytes(), before)
            finally:
                directory.chmod(0o755)


if __name__ == '__main__':
    unittest.main()
