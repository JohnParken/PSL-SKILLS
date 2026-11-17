"""Synthetic independent creation-gate regression and fault-injection tests."""

import base64
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from tl_create import build_document
from tl_create_styles import normalize_spec
from tl_create_verify import CreationMismatch, verify_created
from tl_package import Package

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5WQAAAAASUVORK5CYII=')


def candidate(spec, images=None):
    """Create only synthetic test content in memory."""
    stream = BytesIO()
    build_document(deepcopy(spec), (images or {}).__getitem__).save(stream)
    return Package(stream.getvalue())


def mutated(package, part, action):
    """Serialize an injected defect to avoid testing only a mutable XML cache."""
    root = deepcopy(package.xml(part))
    action(root)
    return Package(package.overlay({part: etree.tostring(root)}))


class CreationVerificationTests(unittest.TestCase):
    """Test independent rejection of well-formed but wrong documents."""

    def setUp(self):
        self.images = {'pixel.png': PNG}
        self.spec = {
            'profile': 'business-report',
            'header': '页眉 A', 'footer': '页脚', 'page_numbers': True,
            'odd_even_headers': True, 'different_first_page': True,
            'headers': {'first': '首页', 'even': '偶数页'},
            'footers': {'first': [{'text': '首页'}, {'field': 'NUMPAGES', 'display': '7'}]},
            'first_page_numbers': True,
            'blocks': [
                {'type': 'title', 'text': '验证 & <报告>'},
                {'type': 'heading', 'level': 1, 'text': '节一', 'bookmark': 'section_one'},
                {'type': 'paragraph', 'runs': [{'text': '甲', 'bold': True}, {'text': '乙'}, {'tab': True}, {'field': 'REF section_one', 'display': '节一'}]},
                {'type': 'list', 'ordered': True, 'items': ['第一', '第二']},
                {'type': 'table', 'caption': '合并表', 'header_rows': 1,
                 'rows': [[{'text': '总表', 'col_span': 2}, None],
                          [{'text': '纵向', 'row_span': 2}, {'text': '图', 'image': {'path': 'pixel.png', 'width_mm': 5, 'height_mm': 5, 'alt': '表内图'}}],
                          [None, '结束']]},
                {'type': 'image', 'path': 'pixel.png', 'alt': '正文图'},
                {'type': 'page_break'},
                {'type': 'toc', 'levels': 2},
                {'type': 'reference', 'bookmark': 'section_one', 'kind': 'page', 'prefix': '见第', 'suffix': '页'},
                {'type': 'section_break', 'header': '页眉 B', 'footer': None, 'first_page_numbers': False},
                {'type': 'paragraph', 'text': '尾声'},
                {'type': 'section_break', 'different_first_page': False, 'page_numbers': False},
                {'type': 'section_break', 'different_first_page': True},
                {'type': 'paragraph', 'text': ''},
            ],
        }

    def check(self, package=None, spec=None, images=None):
        """Run the gate against the original plan, never candidate-derived input."""
        spec = self.spec if spec is None else spec
        return verify_created(package or candidate(spec, self.images), normalize_spec(spec), self.images if images is None else images)

    def test_complex_supported_content(self):
        result = self.check()
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['sections'], 4)
        self.assertIn('formatting_fidelity', result['not_checked'])

    def test_empty_and_legacy_merges(self):
        for spec in ({'blocks': []}, {'blocks': [{'type': 'table', 'rows': [['a', '', 'b'], ['', '', 'c']],
                                                'merges': [{'row': 0, 'col': 0, 'rowspan': 2, 'colspan': 2}]}]}):
            with self.subTest(spec=spec):
                self.assertEqual(self.check(spec=spec)['status'], 'verified')

    def test_rich_footer_display_has_separator_space(self):
        spec = {'footer': [{'field': 'NUMPAGES', 'display': '7'}], 'page_numbers': True, 'blocks': []}
        self.assertEqual(self.check(spec=spec)['status'], 'verified')

    def test_run_boundaries_do_not_change_content(self):
        spec = {'blocks': [{'type': 'paragraph', 'runs': [{'text': '甲'}, {'text': '乙'}]}]}
        package = candidate(spec)
        def combine(root):
            paragraph = root.find(qn('w:body'))[0]
            runs = paragraph.findall(qn('w:r'))
            runs[0].find(qn('w:t')).text = '甲乙'
            paragraph.remove(runs[1])
        self.assertEqual(self.check(mutated(package, package.main, combine), spec)['status'], 'verified')

    def test_body_missing_extra_order_and_text(self):
        package = candidate(self.spec, self.images)
        def drop(root):
            body = root.find(qn('w:body'))
            body.remove(body[0])
        def extra(root):
            body = root.find(qn('w:body'))
            body.insert(0, deepcopy(body[0]))
        def reorder(root):
            body = root.find(qn('w:body'))
            first = body[0]
            body.remove(first)
            body.insert(1, first)
        def wrong_text(root):
            next(root.iter(qn('w:t'))).text = '错误'
        for action in (drop, extra, reorder, wrong_text):
            with self.subTest(action=action.__name__), self.assertRaises(CreationMismatch):
                self.check(mutated(package, package.main, action))

    def test_table_content_geometry_and_extra_empty_paragraph(self):
        package = candidate(self.spec, self.images)
        def cell_text(root):
            table = next(root.iter(qn('w:tbl')))
            next(table.iter(qn('w:t'))).text = '错表'
        def span(root):
            next(root.iter(qn('w:gridSpan'))).set(qn('w:val'), '1')
        def vertical(root):
            next(root.iter(qn('w:vMerge'))).set(qn('w:val'), 'continue')
        def empty_paragraph(root):
            next(root.iter(qn('w:tc'))).append(OxmlElement('w:p'))
        for action in (cell_text, span, vertical, empty_paragraph):
            with self.subTest(action=action.__name__), self.assertRaises(CreationMismatch):
                self.check(mutated(package, package.main, action))

    def test_image_bytes_alt_and_relationship(self):
        package = candidate(self.spec, self.images)
        image_part = next(name for name in package.names if name.startswith('word/media/'))
        with self.assertRaises(CreationMismatch):
            self.check(Package(package.overlay({image_part: PNG + b'changed'})))
        def alt(root):
            next(root.iter(qn('wp:docPr'))).set('descr', 'wrong')
        def relation(root):
            next(root.iter(qn('a:blip'))).set(qn('r:embed'), 'missing')
        for action in (alt, relation):
            with self.subTest(action=action.__name__), self.assertRaises(CreationMismatch):
                self.check(mutated(package, package.main, action))
        with self.assertRaises(CreationMismatch):
            self.check(package, images={})

    def test_header_footer_content_and_inheritance(self):
        package = candidate(self.spec, self.images)
        # The fixture includes dormant empty headers; choose a real text target
        # deterministically rather than depending on randomized set iteration.
        header = next(name for name in sorted(package.names)
                      if name.startswith('word/header') and name.endswith('.xml')
                      and next(package.xml(name).iter(qn('w:t')), None) is not None)
        def wrong(root):
            next(root.iter(qn('w:t'))).text = 'wrong'
        with self.assertRaises(CreationMismatch):
            self.check(mutated(package, header, wrong))
        def inherited(root):
            section = list(root.iter(qn('w:sectPr')))[1]
            for item in list(section.findall(qn('w:headerReference'))):
                section.remove(item)
        with self.assertRaises(CreationMismatch):
            self.check(mutated(package, package.main, inherited))

    def test_fields_bookmarks_and_page_break(self):
        package = candidate(self.spec, self.images)
        def field(root):
            next(root.iter(qn('w:instrText'))).text = ' PAGE '
        def bookmark(root):
            next(root.iter(qn('w:bookmarkStart'))).set(qn('w:name'), 'wrong')
        def page(root):
            next(root.iter(qn('w:br'))).set(qn('w:type'), 'textWrapping')
        def end(root):
            next(root.iter(qn('w:fldChar'))).set(qn('w:fldCharType'), 'end')
        for action in (field, bookmark, page, end):
            with self.subTest(action=action.__name__), self.assertRaises(CreationMismatch):
                self.check(mutated(package, package.main, action))

    def test_unknown_content_fails_closed(self):
        package = candidate(self.spec, self.images)
        def injected(root):
            root.find(qn('w:body')).insert(0, OxmlElement('w:altChunk'))
        with self.assertRaises(CreationMismatch):
            self.check(mutated(package, package.main, injected))


    def test_omitted_list_item_is_rejected(self):
        """List text is content even though computed numbering labels are not verified."""
        spec = {'blocks': [{'type': 'list', 'items': ['one', 'two'], 'ordered': True}]}
        package = candidate(spec)
        def omit(root):
            body = root.find(qn('w:body'))
            body.remove(body[1])
        with self.assertRaises(CreationMismatch):
            self.check(mutated(package, package.main, omit), spec)

    def test_header_mode_switch_is_content_relevant(self):
        """A hidden first/even header is not equivalent to the requested visible one."""
        package = candidate(self.spec, self.images)
        def first(root):
            next(root.iter(qn('w:titlePg'))).set(qn('w:val'), 'false')
        with self.assertRaises(CreationMismatch):
            self.check(mutated(package, package.main, first))
        def even(root):
            next(root.iter(qn('w:evenAndOddHeaders'))).set(qn('w:val'), 'false')
        with self.assertRaises(CreationMismatch):
            self.check(mutated(package, 'word/settings.xml', even))

    def test_requested_metadata_is_checked(self):
        """Requested author, title and other metadata are checked without guessing defaults."""
        spec = {'metadata': {'title': '标题', 'subject': '主题', 'author': '作者', 'comments': '说明', 'identifier': '001',
                             'language': 'zh-CN', 'category': '分类', 'keywords': '词', 'last_modified_by': '编辑者'}, 'blocks': []}
        package = candidate(spec)
        self.assertEqual(self.check(package, spec)['status'], 'verified')
        def wrong(root):
            root.find('{http://purl.org/dc/elements/1.1/}title').text = 'wrong'
        with self.assertRaises(CreationMismatch):
            self.check(mutated(package, 'docProps/core.xml', wrong), spec)

    def test_verifier_does_not_call_generator_or_normalizer(self):
        package = candidate(self.spec, self.images)
        normalized = normalize_spec(self.spec)
        with patch('tl_create.build_document', side_effect=AssertionError('generator called')), patch('tl_create_styles.normalize_spec', side_effect=AssertionError('normalizer called')):
            self.assertEqual(verify_created(package, normalized, self.images)['status'], 'verified')


if __name__ == '__main__':
    unittest.main()
