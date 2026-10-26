"""Synthetic routing regressions and native generate/edit/read smoke test."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.parsers import expat
sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_docx import read_docx, read_docx_structure
from office.binary_doc import read_ms_doc


class RoutingTests(unittest.TestCase):
    def test_wps_suffixes_rejected_before_open(self):
        for name in ('missing.wps', 'missing.ET', 'missing.Ett'):
            for reader in (read_docx, read_docx_structure, read_ms_doc):
                with self.subTest(name=name, reader=reader.__name__):
                    with self.assertRaisesRegex(ValueError, 'wps skill'):
                        reader(name)

    def test_cli_explicit_unsupported(self):
        script = str(Path(__file__).with_name('read_docx.py'))
        for suffix in ('.wps', '.WPS', '.et', '.ET', '.ett', '.ETT'):
            for output_format in ('json', 'text'):
                run = subprocess.run([sys.executable, '-I', '-S', '-B', script,
                                      'missing' + suffix, '--format', output_format],
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 1, run.stderr)
                if output_format == 'json':
                    result = json.loads(run.stdout)
                    self.assertEqual(result['status'], 'unsupported')
                    self.assertNotIn('text', result)
                    message = result['message']
                else:
                    self.assertEqual(run.stdout, '')
                    message = run.stderr
                self.assertIn('不支持读取 WPS 格式', message)
                self.assertIn('wps skill', message)

    def test_unknown_native_is_not_wps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'unknown.docx'
            path.write_bytes(b'unknown input')
            with self.assertRaises(ValueError) as caught:
                read_docx(path)
            self.assertNotIn('wps skill', str(caught.exception))

    def test_cfb_disguised_as_native(self):
        with tempfile.TemporaryDirectory() as folder:
            for suffix in ('.docx', '.dotx'):
                path = Path(folder) / ('fake' + suffix)
                path.write_bytes(bytes.fromhex('d0cf11e0a1b11ae1') + b'not a valid container')
                for reader in (read_docx, read_docx_structure):
                    with self.assertRaisesRegex(ValueError, 'wps skill'):
                        reader(path)

    @unittest.skipUnless(sys.version_info >= (3, 10) and expat.version_info >= (2, 6, 0),
                         'native docx requires Python >=3.10 and Expat >=2.6')
    def test_native_generate_edit_read(self):
        scripts = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            spec = {'blocks': [{'type': 'heading', 'level': 1, 'text': '验证'},
                               {'type': 'paragraph', 'text': '中文 ABC'},
                               {'type': 'table', 'rows': [['甲', '乙'], ['1', '2']], 'header': True}]}
            (work / 'input.json').write_text(json.dumps(spec, ensure_ascii=False))
            (work / 'replacements.json').write_text(json.dumps({'replacements': [{'old': '中文 ABC', 'new': '修改 XYZ', 'count': 1}]}))
            env = dict(os.environ, PATH='')
            for script, args in (
                ('create_docx.py', ['input.json', 'native.docx', '--verify']),
                ('edit_docx.py', ['native.docx', 'replacements.json', 'edited.docx'])):
                proc = subprocess.run([sys.executable, '-I', '-S', '-B', str(scripts / script)] + args,
                                      cwd=folder, env=env, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual(read_docx(work / 'edited.docx'), '验证\n修改 XYZ\n甲\t乙\n1\t2')
            self.assertEqual(read_docx_structure(work / 'edited.docx')['format'], 'docx-structure')
            # Identical OOXML bytes with WPS ownership still route explicitly.
            (work / 'same.WPS').write_bytes((work / 'edited.docx').read_bytes())
            with self.assertRaisesRegex(ValueError, 'wps skill'):
                read_docx(work / 'same.WPS')


if __name__ == '__main__':
    unittest.main()
