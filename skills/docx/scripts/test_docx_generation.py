"""Small native DOCX generation contracts with independently fixed expectations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from xml.parsers import expat
import zipfile

if sys.version_info < (3, 10):
    raise unittest.SkipTest('DOCX requires Python >=3.10')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_docx
from read_docx import read_docx

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'DOCX requires Expat >=2.6')
class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=".")
        self.work = Path(self.temporary.name)
        self.original_directory = Path.cwd()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self.original_directory)
        self.temporary.cleanup()

    def spec(self, text='中文 & <literal> “引号”'):
        return {'blocks': [{'type': 'heading', 'level': 1, 'text': '项目报告'},
                           {'type': 'paragraph', 'text': text},
                           {'type': 'table', 'header': True,
                            'rows': [['名称', '金额'], ['甲', '12.50']]}]}

    def run_cli(self, *args, ascii_locale=False):
        command = [sys.executable, '-I', '-S', '-B']
        if ascii_locale:
            command += ['-X', 'utf8=0']
        command += [str(Path(create_docx.__file__)), *args]
        return subprocess.run(command, cwd=self.work,
                              env=dict(os.environ, PATH='', LC_ALL='C'),
                              capture_output=True, encoding='utf-8')

    def test_chinese_xml_literal_table_and_related_roundtrip(self):
        spec = self.spec()
        spec.update(header='页眉', footer='页脚')
        create_docx.create_document(spec, 'native.docx', verify=True)
        self.assertEqual(read_docx('native.docx'),
                         '项目报告\n中文 & <literal> “引号”\n名称\t金额\n甲\t12.50')
        related = read_docx('native.docx', include_related=True)
        self.assertIn('页眉', related)
        self.assertIn('页脚', related)
        with zipfile.ZipFile('native.docx') as archive:
            self.assertIsNone(archive.testzip())
            root = ET.fromstring(archive.read('word/document.xml'))
            self.assertEqual(root.findall('.//' + W + 'literal'), [])
            self.assertEqual(len(root.findall('.//' + W + 'tbl')), 1)
            self.assertEqual([''.join(t.itertext()) for t in root.findall('.//' + W + 'tr')],
                             ['名称金额', '甲12.50'])

    def test_default_style_and_page_intent_have_fixed_values(self):
        create_docx.create_document(self.spec(), 'native.docx', verify=True)
        with zipfile.ZipFile('native.docx') as archive:
            document = ET.fromstring(archive.read('word/document.xml'))
            styles = ET.fromstring(archive.read('word/styles.xml'))
        size = document.find('.//' + W + 'pgSz')
        self.assertEqual((size.get(W + 'w'), size.get(W + 'h')), ('11906', '16838'))
        style = next(node for node in styles.findall(W + 'style')
                     if node.get(W + 'styleId') == 'Heading1')
        self.assertEqual(style.find(W + 'pPr/' + W + 'outlineLvl').get(W + 'val'), '0')
        self.assertEqual(style.find(W + 'rPr/' + W + 'rFonts').get(W + 'eastAsia'), '方正小标宋简体')
        self.assertEqual(style.find(W + 'rPr/' + W + 'sz').get(W + 'val'), '36')

    def test_placeholder_refusal_preserves_old_output_and_cleans_stage(self):
        output = Path('existing.docx')
        output.write_bytes(b'keep-existing-output')
        with self.assertRaises(ValueError):
            create_docx.create_document(self.spec('{{ company }}'), output,
                                        require_no_placeholders=True)
        self.assertEqual(output.read_bytes(), b'keep-existing-output')
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), ['existing.docx'])

    def test_verification_fault_preserves_old_output_and_cleans_stage(self):
        output = Path('existing.docx')
        output.write_bytes(b'keep-existing-output')
        with patch.object(create_docx, '_verify_content', side_effect=ValueError('injected verification fault')):
            with self.assertRaisesRegex(ValueError, 'injected verification fault'):
                create_docx.create_document(self.spec(), output, verify=True)
        self.assertEqual(output.read_bytes(), b'keep-existing-output')
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), ['existing.docx'])

    def test_successful_replacement_is_a_complete_readable_package(self):
        Path('existing.docx').write_bytes(b'old output')
        create_docx.create_document(self.spec('new saved content'), 'existing.docx', verify=True)
        self.assertIn('new saved content', read_docx('existing.docx'))
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), ['existing.docx'])

    def test_invalid_shape_and_field_types_do_not_create_output(self):
        invalid = [
            {'blocks': [{'type': 'table', 'rows': [['A'], ['B', 'C']]}]},
            {'page': {'width_mm': True}, 'blocks': [{'type': 'paragraph', 'text': 'x'}]},
            {'blocks': [{'type': 'paragraph', 'text': 'x', 'unknown': 1}]},
            {'blocks': [{'type': 'paragraph', 'text': 'line one\nline two'}]},
        ]
        for spec in invalid:
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    create_docx.create_document(spec, 'not-created.docx', verify=True)
                self.assertFalse(Path('not-created.docx').exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_preflight_is_readonly(self):
        before = list(self.work.iterdir())
        result = create_docx.preflight_document(self.spec())
        self.assertIsInstance(result, dict)
        self.assertEqual(list(self.work.iterdir()), before)

    def test_cli_explicit_utf8_input_under_ascii_locale(self):
        Path('input.json').write_text(json.dumps(self.spec(), ensure_ascii=False), encoding='utf-8')
        process = self.run_cli('input.json', 'native.docx', '--verify', ascii_locale=True)
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        self.assertIn('中文 & <literal> “引号”', read_docx('native.docx'))

    def test_cli_duplicate_json_keys_and_same_source_output_refused(self):
        raw = '{"blocks": [], "blocks": [{"type":"paragraph","text":"x"}]}'
        Path('input.json').write_text(raw, encoding='utf-8')
        Path('existing.docx').write_bytes(b'old output')
        process = self.run_cli('input.json', 'existing.docx', '--verify')
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(Path('existing.docx').read_bytes(), b'old output')
        process = self.run_cli('input.json', 'input.json')
        self.assertEqual(process.returncode, 2)
        self.assertEqual(Path('input.json').read_text(encoding='utf-8'), raw)


if __name__ == '__main__':
    unittest.main()
