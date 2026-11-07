"""Regression contract: simple official docs paginate naturally unless requested.

Only model-facing rules and tests change in this release. These tests protect
existing OOXML defaults from unintentional future regressions; they cannot infer
whether a user's prompt asked for an explicit break.
"""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from fixture_delivery import create_fixture

import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import create_docx
from validate_layout import validate_layout

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
ROOT = Path(__file__).resolve().parents[2]


class OfficialNaturalPaginationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.file = Path(temp.name) / 'official-natural.docx'

    def generate(self, blocks, **kwargs):
        create_fixture(create_docx.create_document,{'theme': 'official', 'blocks': blocks, **kwargs}, self.file, verify=True)
        with zipfile.ZipFile(self.file) as z:
            document = ET.fromstring(z.read('word/document.xml'))
            styles = ET.fromstring(z.read('word/styles.xml'))
        self.assertTrue(validate_layout(self.file)['structure_valid'])
        return document, styles

    @staticmethod
    def para_style(xml, style_id):
        for style in xml.findall(W+'style'):
            if style.get(W+'styleId') == style_id:
                return style
        raise AssertionError(f'Missing Word style {style_id}')

    @staticmethod
    def manual_breaks(root):
        return [br for br in root.findall('.//' + W+'br') if br.get(W+'type') == 'page']

    def test_default_automatic_pagination_with_multiple_chapters(self):
        blocks = [
            {'type':'title','text':'年度工作总结'},
            {'type':'heading','level':1,'text':'第一章'},
            {'type':'paragraph','text':'第一章正文'},
            {'type':'heading','level':2,'text':'工作举措'},
            {'type':'paragraph','text':'工作举措正文'},
            {'type':'heading','level':1,'text':'第二章'},
            {'type':'paragraph','text':'第二章正文'},
        ]
        document, styles = self.generate(blocks)
        self.assertFalse(self.manual_breaks(document))
        self.assertFalse(document.findall('.//' + W+'pageBreakBefore'))
        self.assertEqual(len(document.findall('.//' + W+'body/' + W+'sectPr')), 1)
        self.assertFalse(document.findall('.//' + W+'pPr/' + W+'sectPr'))
        for name in ['Title','Subtitle','Heading1','Heading2','Heading3','Heading4']:
            with self.subTest(style=name):
                ppr = self.para_style(styles, name).find(W+'pPr')
                self.assertIsNotNone(ppr)
                keep = ppr.find(W+'keepNext')
                self.assertIsNotNone(keep)
                self.assertNotEqual(keep.get(W+'val'), '0')
                self.assertIsNone(ppr.find(W+'pageBreakBefore'))
        body = self.para_style(styles, 'BodyText').find(W+'pPr')
        self.assertIsNone(body.find(W+'pageBreakBefore'))

    def test_default_theme_omitted_has_same_natural_pagination(self):
        blocks = [
            {'type':'heading','level':1,'text':'概述'},
            {'type':'paragraph','text':'内容'},
        ]
        self.generate(blocks)
        with zipfile.ZipFile(self.file) as z:
            explicit = {name:z.read(name) for name in z.namelist()}
        create_fixture(create_docx.create_document,{'blocks':blocks}, self.file, verify=True)
        with zipfile.ZipFile(self.file) as z:
            implicit = {name:z.read(name) for name in z.namelist()}
        self.assertEqual(explicit, implicit)

    def test_explicit_heading_page_break_before_is_honored(self):
        document, _ = self.generate([
            {'type':'paragraph','text':'前文'},
            {'type':'heading','level':1,'text':'新章', 'format':{'page_break_before': True}},
            {'type':'paragraph','text':'新章正文'},
        ])
        self.assertFalse(self.manual_breaks(document))
        self.assertEqual(len(document.findall('.//' + W+'pageBreakBefore')), 1)
        self.assertTrue(validate_layout(self.file)['structure_valid'])

    def test_explicit_single_page_break_is_preserved(self):
        document, _ = self.generate([
            {'type':'paragraph','text':'封面'},
            {'type':'page_break'},
            {'type':'heading','level':1,'text':'正文'},
            {'type':'paragraph','text':'正文内容'},
        ])
        self.assertEqual(len(self.manual_breaks(document)), 1)
        self.assertFalse(document.findall('.//' + W+'pageBreakBefore'))
        warnings = {i['code'] for i in validate_layout(self.file)['issues'] if i['level']=='warning'}
        self.assertNotIn('REPEATED_PAGE_BREAK', warnings)
        self.assertNotIn('PAGE_BREAK_SECTION_CONFLICT', warnings)

    def test_explicit_landscape_section_is_preserved(self):
        document, _ = self.generate([
            {'type':'paragraph','text':'纵向正文'},
            {'type':'section_break','break_type':'next_page','page':{'orientation':'landscape'}},
            {'type':'heading','level':1,'text':'横向附录'},
            {'type':'paragraph','text':'附录正文'},
        ])
        self.assertFalse(self.manual_breaks(document))
        self.assertEqual(len(document.findall('.//' + W+'sectPr')), 2)
        self.assertFalse(document.findall('.//' + W+'pageBreakBefore'))
        warnings = {i['code'] for i in validate_layout(self.file)['issues'] if i['level']=='warning'}
        self.assertNotIn('PAGE_BREAK_SECTION_CONFLICT', warnings)

    def test_model_facing_guidance_stays_compact_and_explicit(self):
        skill = (ROOT/'SKILL.md').read_text(encoding='utf-8')
        self.assertLess(len(skill.encode('utf-8')), 9000)
        for phrase in ('默认公文分页铁律','未明确要求','正文交给 Word/WPS 自然分页',
                       'keep_with_next','page_break_before:true','同一位置只选一种换页机制'):
            self.assertIn(phrase, skill)
        reference = (ROOT/'references/pagination-governance.md').read_text(encoding='utf-8')
        for phrase in ('默认公文自然分页策略','明确要求章节另起页','不额外加','不静默删除'):
            self.assertIn(phrase, reference)


if __name__ == '__main__':
    unittest.main()
