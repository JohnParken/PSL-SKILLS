"""Golden OOXML contracts for the stdlib-only typography P0 extension."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_docx
from read_docx import read_docx
from validate_layout import validate_layout
from office.formatting.audit import audit_roots
from office.formatting.profiles import apply_profile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class TypographyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _make(self, spec):
        file = self.root / 'output.docx'
        create_docx.create_document(spec, file, verify=True)
        with ZipFile(file) as archive:
            xml = {k: ET.fromstring(archive.read(k)) for k in ('word/document.xml', 'word/styles.xml')}
        return file, xml

    def _assert_page_margins(self, roots, **expected_mm):
        margins = roots['word/document.xml'].find('.//' + W + 'pgMar')
        for side, millimeters in expected_mm.items():
            self.assertEqual(margins.get(W + side), str(create_docx._twips(millimeters)))

    def test_profile_applies_defaults_and_explicit_overrides(self):
        spec = {'profile': 'business-report', 'styles': {'body': {'size_pt': 13}},
                'page': {'margin_left_mm': 30},
                'blocks': [{'type': 'heading', 'level': 1, 'text': '年度经营分析'},
                           {'type': 'paragraph', 'text': '正文'}]}
        file, roots = self._make(spec)
        styles = roots['word/styles.xml']
        body = next(x for x in styles.findall(W + 'style') if x.get(W + 'styleId') == 'BodyText')
        self.assertEqual(body.find(W + 'rPr/' + W + 'sz').get(W + 'val'), '26')
        self.assertEqual(body.find(W + 'pPr/' + W + 'widowControl').get(W + 'val'), '1')
        self._assert_page_margins(roots, top=22, bottom=22, left=30, right=24)
        report = validate_layout(file)
        self.assertTrue(report['structure_valid'], report)
        self.assertEqual(report['visual_validation'], 'not_run')
        self.assertFalse([i for i in report['issues'] if i['level'] == 'error'])

    def test_uniform_profile_margin_applies_to_every_side_without_mutating_source(self):
        spec = {'profile': 'business-report', 'page': {'margin_mm': 20},
                'blocks': [{'type': 'paragraph', 'text': '正文'}]}
        original = deepcopy(spec)
        file, roots = self._make(spec)
        self.assertEqual(spec, original)
        self.assertEqual(apply_profile(spec)['page'], {'margin_mm': 20})
        self._assert_page_margins(roots, top=20, bottom=20, left=20, right=20)

    def test_uniform_profile_margin_allows_explicit_side_override(self):
        spec = {'profile': 'business-report',
                'page': {'margin_mm': 20, 'margin_left_mm': 30},
                'blocks': [{'type': 'paragraph', 'text': '正文'}]}
        _, roots = self._make(spec)
        self._assert_page_margins(roots, top=20, bottom=20, left=30, right=20)

    def test_uniform_profile_margin_accepts_zero(self):
        spec = {'profile': 'business-report', 'page': {'margin_mm': 0},
                'blocks': [{'type': 'paragraph', 'text': '正文'}]}
        _, roots = self._make(spec)
        self._assert_page_margins(roots, top=0, bottom=0, left=0, right=0)

    def test_profile_margin_values_are_validated_even_when_overridden(self):
        invalid_pages = (
            {'margin_mm': -1, 'margin_left_mm': 30},
            {'margin_mm': 20, 'margin_left_mm': -1},
        )
        output = self.root / 'invalid.docx'
        for page in invalid_pages:
            with self.subTest(page=page):
                with self.assertRaises(ValueError):
                    create_docx.create_document(
                        {'profile': 'business-report', 'page': page,
                         'blocks': [{'type': 'paragraph', 'text': '正文'}]},
                        output, verify=True)
                self.assertFalse(output.exists())

    def test_all_profiles_generate_with_tables(self):
        for name in ('official-basic', 'business-report', 'academic', 'presentation-material'):
            with self.subTest(name=name):
                spec = {'profile': name, 'blocks': [
                    {'type': 'heading', 'level': 2, 'text': '风险概况'},
                    {'type': 'paragraph', 'text': '已完成阶段检查。'},
                    {'type': 'table', 'header': True, 'rows': [['项目', '值'], ['甲', '12']]},
                ]}
                file, roots = self._make(spec)
                self.assertEqual(read_docx(file), '风险概况\n已完成阶段检查。\n项目\t值\n甲\t12')
                self.assertTrue(validate_layout(file)['structure_valid'])

    def test_paragraph_order_tabs_pagination_and_preserved_tab_reading(self):
        spec = {'blocks': [
            {'type': 'paragraph', 'runs': [{'text': '项目'}, {'tab': True}, {'text': '金额'}],
             'format': {'keep_with_next': True, 'keep_together': True,
                        'widow_control': False, 'page_break_before': True,
                        'left_indent_pt': 12, 'right_indent_pt': 4,
                        'hanging_indent_pt': 8, 'space_after_pt': 6,
                        'tab_stops': [{'position_pt': 144, 'align': 'right'}]}}
        ]}
        file, roots = self._make(spec)
        paragraph = roots['word/document.xml'].find('.//' + W + 'body/' + W + 'p')
        ppr = paragraph.find(W + 'pPr')
        self.assertEqual([node.tag.rsplit('}', 1)[-1] for node in ppr],
                         ['pStyle', 'keepNext', 'keepLines', 'pageBreakBefore', 'widowControl',
                          'tabs', 'spacing', 'ind'])
        self.assertEqual(ppr.find(W + 'ind').get(W + 'hanging'), '160')
        self.assertEqual(ppr.find(W + 'tabs/' + W + 'tab').get(W + 'pos'), '2880')
        self.assertEqual([x.tag for x in paragraph.findall(W + 'r')[1]], [W + 'tab'])
        self.assertEqual(read_docx(file), '项目\t金额')
        self.assertTrue(validate_layout(file)['structure_valid'])

    def test_indent_override_displaces_profile_first_line_chars(self):
        spec = {'profile': 'academic', 'styles': {'body': {'hanging_indent_pt': 10}},
                'blocks': [{'type': 'paragraph', 'text': '合规检查'}]}
        _, roots = self._make(spec)
        body = next(x for x in roots['word/styles.xml'].findall(W + 'style')
                    if x.get(W + 'styleId') == 'BodyText')
        ind = body.find(W + 'pPr/' + W + 'ind')
        self.assertIsNone(ind.get(W + 'firstLineChars'))
        self.assertEqual(ind.get(W + 'hanging'), '200')

    def test_legacy_specs_are_compatible(self):
        _, roots = self._make({'blocks': [
            {'type': 'heading', 'level': 2, 'text': '原标题'},
            {'type': 'paragraph', 'text': '正文'}]})
        self.assertEqual(roots['word/document.xml'].find('.//' + W + 'pgSz').get(W + 'w'), '11906')
        self._assert_page_margins(roots, top=25.4, bottom=25.4, left=31.8, right=31.8)
        heading = next(x for x in roots['word/styles.xml'].findall(W + 'style')
                       if x.get(W + 'styleId') == 'Heading2')
        self.assertEqual(len(heading.findall(W + 'pPr/' + W + 'keepNext')), 1)

    def test_malformed_fields_fail_before_publish(self):
        examples = [
            {'profile': '../../bad'},
            {'profile': 42},
            {'blocks': [{'type': 'paragraph', 'text': 'a',
                         'format': {'tab_stops': [{'position_pt': 12, 'align': 'wrong'}]}}]},
            {'blocks': [{'type': 'paragraph', 'text': 'a',
                         'format': {'tab_stops': [{'position_pt': 12, 'align': 'left'},
                                                  {'position_pt': 12, 'align': 'right'}]}}]},
            {'blocks': [{'type': 'paragraph', 'text': 'a',
                         'format': {'first_line_chars': 2, 'hanging_indent_pt': 8}}]},
            {'blocks': [{'type': 'paragraph', 'runs': [{'tab': False}]}]},
        ]
        for example in examples:
            with self.subTest(example=example):
                spec = {'blocks': [{'type': 'paragraph', 'text': 'x'}], **example}
                (self.root / 'should-not-exist.docx').unlink(missing_ok=True)
                with self.assertRaises(ValueError):
                    create_docx.create_document(spec, self.root / 'should-not-exist.docx', verify=True)
                self.assertFalse((self.root / 'should-not-exist.docx').exists())

    def test_auditor_flags_duplicate_and_wrong_property_order(self):
        _, roots = self._make({'blocks': [{'type': 'paragraph', 'text': 'x'}]})
        paragraph = roots['word/document.xml'].find('.//' + W + 'body/' + W + 'p')
        ppr = paragraph.find(W + 'pPr')
        ET.SubElement(ppr, W + 'spacing')
        ET.SubElement(ppr, W + 'spacing')
        ET.SubElement(ppr, W + 'pageBreakBefore')
        report = audit_roots(roots)
        self.assertFalse(report['structure_valid'])
        codes = {issue['code'] for issue in report['issues']}
        self.assertIn('DUPLICATE_PARAGRAPH_PROPERTY', codes)
        self.assertIn('PARAGRAPH_PROPERTY_ORDER', codes)

    def test_audit_cli_produces_machine_readable_json(self):
        file, _ = self._make({'profile': 'official-basic',
                              'blocks': [{'type': 'paragraph', 'text': 'x'}]})
        command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).parent / 'validate_layout.py'),
                   file.name]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['structure_valid'])


if __name__ == '__main__':
    unittest.main()
