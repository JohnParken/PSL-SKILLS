"""Boundary tests independent of the upstream converter."""
import base64
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_guard import ContractError, bare_name, publish_exclusive, read_local, validate_svg


@contextmanager
def task_directory():
    """Keep fixtures out of the repository and restore the process directory."""
    original = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            yield Path(directory)
        finally:
            os.chdir(original)


class GuardTests(unittest.TestCase):
    """Test hostile XML, references, file aliases and exclusive output."""

    def test_plain_svg(self):
        result = validate_svg(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="30" y="40">Hello</text></svg>')
        self.assertEqual(result['nodes'], 2)

    def test_dtd_utf8_utf16(self):
        text = '<!DOCTYPE svg [<!ENTITY x "unsafe">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>'
        for data in (text.encode(), text.encode('utf-16')):
            with self.assertRaises(ContractError):
                validate_svg(data)

    def test_external_and_active(self):
        elements = ['<image href="file:///etc/passwd"/>', '<image href="https://example.com/a.png"/>',
                    '<rect fill="url(../x)"/>', '<script/>', '<foreignObject/>', '<rect onclick="bad"/>',
                    '<use data-icon="local-secret"/>', '<rect data-pptx-native-ref="abc"/>', '<style>bad</style>']
        for element in elements:
            with self.subTest(element=element), self.assertRaises(ContractError):
                validate_svg(('<svg xmlns="http://www.w3.org/2000/svg">' + element + '</svg>').encode())

    def test_passive_hyperlinks_are_data_not_fetches(self):
        for target in ('https://example.com/page', 'mailto:person@example.com', '#slide-2'):
            for element in (f'<a href="{target}"><text>link</text></a>',
                            f'<rect data-pptx-shape-hyperlink="{target}"/>'):
                with self.subTest(element=element):
                    validate_svg(('<svg xmlns="http://www.w3.org/2000/svg">' + element + '</svg>').encode())
        for target in ('file:///secret', 'javascript:alert(1)', 'data:text/plain,secret'):
            with self.subTest(target=target), self.assertRaises(ContractError):
                validate_svg(f'<svg xmlns="http://www.w3.org/2000/svg"><a href="{target}"/></svg>'.encode())

    def test_embedded_svg_external_reference(self):
        inner = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///secret"/></svg>'
        encoded = base64.b64encode(inner).decode()
        with self.assertRaises(ContractError):
            validate_svg(f'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/svg+xml;base64,{encoded}"/></svg>'.encode())

    def test_processing_instruction(self):
        with self.assertRaises(ContractError):
            validate_svg(b'<?xml-stylesheet href="http://example.com/x"?><svg xmlns="http://www.w3.org/2000/svg"/>')

    def test_names(self):
        for value in (None, 42, ['name.pptx'], {}, '../a.pptx', './a.pptx', '/a.pptx', 'a/b.pptx', 'a\\b.pptx', 'a\nb.pptx', '.a.pptx'):
            with self.subTest(value=value), self.assertRaises(ContractError):
                bare_name(value, '.pptx')
        self.assertEqual(bare_name('报告.pptx', '.pptx'), '报告.pptx')

    def test_symlink_read_and_output(self):
        with task_directory():
            Path('original.svg').write_bytes(b'data')
            Path('link.svg').symlink_to('original.svg')
            with self.assertRaises(OSError):
                read_local('link.svg', '.svg')
            Path('link.pptx').symlink_to('original.svg')
            with self.assertRaises(FileExistsError):
                publish_exclusive('link.pptx', b'changed')
            self.assertEqual(Path('original.svg').read_bytes(), b'data')

    def test_exclusive_publication(self):
        with task_directory():
            result = publish_exclusive('result.pptx', b'first')
            self.assertTrue(result['published'])
            with self.assertRaises(FileExistsError):
                publish_exclusive('result.pptx', b'second')
            self.assertEqual(Path('result.pptx').read_bytes(), b'first')


if __name__ == '__main__':
    unittest.main()
