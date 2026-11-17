"""Historical hidden-root/visible-native-table publication regression.

Fixed upstream 7e0df670 and target converter were compared independently:
visibility: b71e21747a093a89fa02a0504d7f6b7e327010c99a7347e0e00bdca78df36da8;
display:none: 24678658d6dd18b49744f574f46eaf88f8d6c190cc912064b25f691d007aaf95.
The public build path must preserve this behavior, not reject valid content.
"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_pptx import build

NS = 'http://www.w3.org/2000/svg'
DRAWING = 'http://schemas.openxmlformats.org/drawingml/2006/main'


class VisibilityTableRegressionTests(unittest.TestCase):
    """visibility can be overridden; display:none suppresses the whole subtree."""

    def test_publication_preserves_visible_descendant(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                for hidden_display, expected in ((False, 1), (True, 0)):
                    with self.subTest(display_none=hidden_display):
                        root = ET.Element(f'{{{NS}}}svg', viewBox='0 0 1280 720', visibility='hidden')
                        if hidden_display:
                            root.set('display', 'none')
                        group = ET.SubElement(root, f'{{{NS}}}g', {
                            'visibility': 'visible', 'data-pptx-replace-with': 'table',
                            'data-pptx-native-authority': 'json',
                        })
                        metadata = ET.SubElement(group, f'{{{NS}}}metadata', type='application/json')
                        metadata.text = json.dumps({'schema': 'ppt-master.semantic-table.v2',
                                                    'rows': [['Item', 'Value'], ['A', '10']],
                                                    'x': 50, 'y': 50, 'width': 500, 'height': 300})
                        Path('page.svg').write_bytes(ET.tostring(root))
                        Path('manifest.json').write_text(json.dumps({'slides': [{'svg': 'page.svg'}]}))
                        output = f'visibility-{int(hidden_display)}.pptx'
                        with patch('archive_delivery.archive_delivery', return_value={'archive_status': 'success', 'files': [{'output': output, 'archive_status': 'copied'}]}):
                            result, code = build('manifest.json', output)
                        self.assertEqual(code, 0)
                        self.assertTrue(result['publication']['published'])
                        with zipfile.ZipFile(io.BytesIO(Path(output).read_bytes())) as archive:
                            slide = ET.fromstring(archive.read('ppt/slides/slide1.xml'))
                        self.assertEqual(len(slide.findall(f'.//{{{DRAWING}}}tbl')), expected)
                        texts = [node.text for node in slide.findall(f'.//{{{DRAWING}}}t')]
                        self.assertEqual(texts, ['Item', 'Value', 'A', '10'] if expected else [])
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
