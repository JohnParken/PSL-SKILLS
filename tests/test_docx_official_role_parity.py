"""Compare shared official role semantics in two independently generated packages."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
ROLES = ('Normal', 'Title', 'Subtitle', 'BodyText', 'Heading1', 'Heading2',
         'Heading3', 'Heading4', 'TableHeader', 'TableBody', 'TableCaption')
RUN = ('rFonts', 'sz', 'szCs', 'b', 'i', 'spacing', 'kern')
PARA = ('spacing', 'jc', 'ind', 'keepNext', 'keepLines', 'widowControl',
        'pageBreakBefore', 'outlineLvl', 'pBdr')


def effective(styles, role, group, tags):
    """Resolve selected style attributes; omitted booleans have OOXML defaults."""
    result = {}
    for name in dict.fromkeys(('Normal', role)):
        for tag in tags:
            node = styles[name].find(W + group + '/' + W + tag)
            if node is None:
                continue
            attrs = {key.removeprefix(W): value for key, value in node.attrib.items()}
            if tag in ('b', 'i', 'keepNext', 'keepLines', 'widowControl', 'pageBreakBefore'):
                attrs = {'val': attrs.get('val', '1')}
            for key, value in attrs.items():
                if tag == 'ind' and value == '0':
                    continue
                result[tag, key] = value
    return result


def snapshot(path):
    with ZipFile(path) as archive:
        styles = {node.get(W + 'styleId'): node for node in
                  ET.fromstring(archive.read('word/styles.xml')).findall(W + 'style')}
        main = ET.fromstring(archive.read('word/document.xml'))
        section = main.find('.//' + W + 'sectPr')
        table = main.find('.//' + W + 'tbl')
        props = table.find(W + 'tblPr')
        def side_values(tag, edges, fields):
            node = props.find(W + tag)
            return [(side, tuple(node.find(W + side).get(W + key) for key in fields)) for side in edges]
        table_format = (side_values('tblBorders', ('top', 'bottom', 'left', 'right', 'insideH', 'insideV'),
                                   ('val', 'sz', 'color')),
                        side_values('tblCellMar', ('top', 'bottom', 'left', 'right'), ('w', 'type')),
                        [(x.get(W + 'val')) for x in table.iter(W + 'vAlign')])
        return ({role: (effective(styles, role, 'pPr', PARA),
                        effective(styles, role, 'rPr', RUN)) for role in ROLES},
                table_format, [section.find(W + tag).attrib for tag in ('pgSz', 'pgMar')],
                [p.get(W + 'val') for p in main.iter(W + 'pStyle')],
                [len(list(main.iter(W + tag))) for tag in ('tblHeader', 'cantSplit', 'pageBreakBefore', 'gridSpan')],
                [(node.tag, node.get(W+'val'), node.get(W+'fldCharType'), node.get(W+'dirty'), node.text)
                 for name in ('word/header1.xml','word/footer1.xml') for node in
                 ET.fromstring(archive.read(name)).iter() if node.tag in {W+'t', W+'instrText', W+'fldChar'}])


class OfficialRoleParityTests(unittest.TestCase):
    def test_shared_roles(self):
        for options in ({}, {'profile': 'official-basic'}, {'theme': 'official',
                        'table_format': {'border_color': '008080', 'cell_margin_left_mm': 2},
                        'styles': {'body': {'size_pt': 20}, 'title': {'keep_with_next': False}}}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as work:
                spec = dict(options, header='单位', footer='页', page_numbers=True, blocks=[{'type': 'title', 'text': '通知'},
                            {'type': 'subtitle', 'text': '副标题'},
                            {'type': 'paragraph', 'text': '正文'},
                            *[{'type': 'heading', 'level': n, 'text': '标题'} for n in range(1, 5)],
                            {'type': 'table', 'caption': '表一', 'header': True, 'rows': [[{'text': '项目', 'col_span': 2}, None], ['内容', '值']]}])
                for skill, code in [('docx', "from create_docx import create_document; create_document(spec, 'docx.docx', verify=True)"),
                                    ('tl-docx', "from tl_create import build_document; build_document(spec, lambda _: b'').save('tl-docx.docx')")]:
                    subprocess.run([sys.executable, '-B', '-c',
                                    'import sys,json;sys.path.insert(0,sys.argv[1]);spec=json.load(sys.stdin);' + code,
                                    str(ROOT / 'skills' / skill / 'scripts')], input=json.dumps(spec),
                                   text=True, cwd=work, check=True, capture_output=True)
                expected, actual = (snapshot(Path(work) / name) for name in ('docx.docx', 'tl-docx.docx'))
                for role in ROLES:
                    self.assertEqual(expected[0][role], actual[0][role], role)
                self.assertEqual(expected[1:], actual[1:])
