"""Compatibility checks for the document generator's responsibility boundaries."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import create_docx
import docx_create_common
import docx_create_validation
import docx_create_xml


class GeneratorModuleBoundaries(unittest.TestCase):
    def test_original_imports_export_the_single_implementation(self):
        for module, symbols in (
            (docx_create_common, ('_validate_style_fields', '_cell_borders', '_twips')),
            (docx_create_validation, ('_validate_spec', '_image', 'ImageResources')),
            (docx_create_xml, ('_document_xml', '_styles_xml', '_package_parts')),
        ):
            for symbol in symbols:
                with self.subTest(symbol=symbol):
                    self.assertIs(getattr(create_docx, symbol), getattr(module, symbol))

    def test_validation_render_and_preflight_without_writing(self):
        spec = {'blocks': [{'type': 'paragraph', 'text': '保留正文'},
                           {'type': 'table', 'rows': [['编号', '内容'], ['001', '甲']]}]}
        result = create_docx.preflight_document(spec)
        self.assertEqual(result['status'], 'preflight-ok')
        self.assertEqual(result['blocks'], 2)
        validated = create_docx._validate_spec(spec, Path.cwd())
        parts = create_docx._package_parts(validated)
        self.assertIn('保留正文'.encode(), parts['word/document.xml'])
        self.assertIn(b'001', parts['word/document.xml'])


if __name__ == '__main__':
    unittest.main()
