"""Advisory pagination governance regressions; legacy output remains unchanged."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_docx
from validate_layout import validate_layout
from office.formatting.pagination import PAGINATION_CODES

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def paragraph(text, **formatting):
    block = {'type': 'paragraph', 'text': text}
    if formatting:
        block['format'] = formatting
    return block


def heading(text='标题', **formatting):
    block = {'type': 'heading', 'level': 1, 'text': text}
    if formatting:
        block['format'] = formatting
    return block


class PaginationGovernanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'pagination.docx'
        self.audit_script = Path(__file__).with_name('validate_layout.py')

    def create(self, blocks, **options):
        create_docx.create_document({'blocks': blocks, **options}, self.path, verify=True)
        report = validate_layout(self.path)
        self.assertTrue(report['structure_valid'], report)
        return report

    def warnings(self, blocks, **options):
        report = self.create(blocks, **options)
        return {item['code'] for item in report['issues'] if item['level'] == 'warning'}

    def test_simple_normal_pagination_no_warnings(self):
        warnings = self.warnings([heading(), paragraph('正文'), {'type': 'page_break'},
                                  heading('后续'), paragraph('后续正文')])
        self.assertFalse(warnings & PAGINATION_CODES, warnings)

    def test_leading_trailing_and_adjacent_breaks(self):
        codes = self.warnings([{'type': 'page_break'}, {'type': 'page_break'},
                               paragraph('中间'), {'type': 'page_break'}])
        self.assertIn('LEADING_PAGE_BREAK', codes)
        self.assertIn('REPEATED_PAGE_BREAK', codes)
        self.assertIn('TRAILING_PAGE_BREAK', codes)

    def test_redundant_break_before_heading_and_paragraph(self):
        for last, expected in ((heading(page_break_before=True), 'REDUNDANT_HEADING_PAGE_BREAK'),
                               (paragraph('正文', page_break_before=True), 'REDUNDANT_PARAGRAPH_PAGE_BREAK')):
            with self.subTest(expected=expected):
                codes = self.warnings([paragraph('前文'), {'type': 'page_break'}, last])
                self.assertIn(expected, codes)

    def test_section_break_adjacency_both_directions(self):
        section = {'type': 'section_break', 'page': {'orientation': 'landscape'}}
        for blocks in ([paragraph('前文'), {'type': 'page_break'}, section, paragraph('后文')],
                       [paragraph('前文'), section, {'type': 'page_break'}, paragraph('后文')]):
            with self.subTest(blocks=blocks):
                codes = self.warnings(blocks)
                self.assertIn('PAGE_BREAK_SECTION_CONFLICT', codes)

    def test_nextpage_section_followed_by_heading_break_before(self):
        codes = self.warnings([paragraph('前文'), {'type': 'section_break'},
                               heading('新节标题', page_break_before=True), paragraph('正文')])
        self.assertIn('SECTION_HEADING_PAGE_BREAK', codes)

    def test_continuous_section_manual_break_is_not_flagged_as_conflict(self):
        codes = self.warnings([paragraph('前文'), {'type': 'page_break'},
                               {'type': 'section_break', 'break_type': 'continuous'},
                               paragraph('后文')])
        self.assertNotIn('PAGE_BREAK_SECTION_CONFLICT', codes)

    def test_heading_separation_and_document_end(self):
        self.assertIn('HEADING_SEPARATED_FROM_CONTENT',
                      self.warnings([heading(), {'type': 'page_break'}, paragraph('正文')]))
        self.assertIn('HEADING_SEPARATED_FROM_CONTENT',
                      self.warnings([heading(), {'type': 'section_break'}, paragraph('正文')]))
        self.assertIn('HEADING_WITHOUT_CONTENT', self.warnings([heading()]))

    def test_explicit_disable_overrides_inherited_page_break_before(self):
        self.create([paragraph('前文'), {'type': 'page_break'}, heading('标题', page_break_before=False)])
        with zipfile.ZipFile(self.path) as archive:
            content = {key: archive.read(key) for key in archive.namelist()}
        styles = ET.fromstring(content['word/styles.xml'])
        h1 = next(s for s in styles.findall(W+'style') if s.get(W+'styleId') == 'Heading1')
        ET.SubElement(h1.find(W+'pPr'), W+'pageBreakBefore', {W+'val': '1'})
        content['word/styles.xml'] = ET.tostring(styles, encoding='utf-8', xml_declaration=True)
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, data in content.items():
                archive.writestr(name, data)
        codes = {i['code'] for i in validate_layout(self.path)['issues']}
        self.assertNotIn('REDUNDANT_HEADING_PAGE_BREAK', codes)

    def test_basedon_keepnext_style_is_respected(self):
        self.create([heading(), paragraph('内容')])
        with zipfile.ZipFile(self.path) as archive:
            content = {key: archive.read(key) for key in archive.namelist()}
        styles = ET.fromstring(content['word/styles.xml'])
        heading_style = next(s for s in styles.findall(W+'style') if s.get(W+'styleId') == 'Heading1')
        normal_style = next(s for s in styles.findall(W+'style') if s.get(W+'styleId') == 'Normal')
        keep = heading_style.find(W+'pPr/'+W+'keepNext')
        heading_style.find(W+'pPr').remove(keep)
        normal_ppr = normal_style.find(W+'pPr')
        if normal_ppr is None:
            normal_ppr = ET.SubElement(normal_style, W+'pPr')
        ET.SubElement(normal_ppr, W+'keepNext', {W+'val': '1'})
        # Heading1 basedOn Normal is part of the original style graph.
        content['word/styles.xml'] = ET.tostring(styles, encoding='utf-8', xml_declaration=True)
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, data in content.items():
                archive.writestr(name, data)
        codes = {i['code'] for i in validate_layout(self.path)['issues']}
        self.assertNotIn('HEADING_ORPHAN_RISK', codes)

    def test_mixed_page_break_warning_and_rendered_break_ignored(self):
        self.create([paragraph('测试')])
        with zipfile.ZipFile(self.path) as archive:
            content = {key: archive.read(key) for key in archive.namelist()}
        root = ET.fromstring(content['word/document.xml'])
        p = root.find('.//' + W + 'body/' + W + 'p')
        ET.SubElement(ET.SubElement(p, W + 'r'), W + 'br', {W + 'type': 'page'})
        ET.SubElement(ET.SubElement(p, W + 'r'), W + 'br', {W + 'type': 'page'})
        content['word/document.xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, data in content.items():
                archive.writestr(name, data)
        codes = {i['code'] for i in validate_layout(self.path)['issues']}
        self.assertIn('INLINE_PAGE_BREAK_RISK', codes)
        self.assertIn('MULTIPLE_PAGE_BREAKS_IN_PARAGRAPH', codes)

        # A cached w:lastRenderedPageBreak is not an authored break and must
        # not be flagged as a duplicate or manual page break.
        root = ET.fromstring(content['word/document.xml'])
        p = root.find('.//' + W + 'body/' + W + 'p')
        for br in list(p.iter(W + 'br')):
            parent = next(elem for elem in p.iter() if br in list(elem))
            parent.remove(br)
        ET.SubElement(ET.SubElement(p, W + 'r'), W + 'lastRenderedPageBreak')
        content['word/document.xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, data in content.items():
                archive.writestr(name, data)
        codes = {i['code'] for i in validate_layout(self.path)['issues']}
        self.assertNotIn('INLINE_PAGE_BREAK_RISK', codes)
        self.assertNotIn('MULTIPLE_PAGE_BREAKS_IN_PARAGRAPH', codes)

    def test_mixed_table_does_not_count_as_body_break(self):
        codes = self.warnings([paragraph('前文'), {'type': 'table', 'rows': [['字段'], ['值']]},
                               {'type': 'page_break'}, paragraph('后文')])
        self.assertNotIn('REPEATED_PAGE_BREAK', codes)
        self.assertNotIn('LEADING_PAGE_BREAK', codes)

    def test_advisories_are_nonblocking_and_do_not_rewrite_docx(self):
        report = self.create([paragraph('内容'), {'type': 'page_break'}])
        before = self.path.read_bytes()
        self.assertIn('TRAILING_PAGE_BREAK', {i['code'] for i in report['issues']})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(validate_layout(self.path)['structure_valid'], True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_opt_in_strict_pagination_exit_code(self):
        cases = [([paragraph('正文'), {'type': 'page_break'}], 2),
                 ([paragraph('正文')], 0)]
        for blocks, expected in cases:
            with self.subTest(expected=expected):
                self.create(blocks)
                p = subprocess.run([sys.executable, '-I', '-S', '-B', str(self.audit_script),
                                    str(self.path), '--strict-pagination'],
                                   text=True, encoding='utf8', capture_output=True)
                self.assertEqual(p.returncode, expected, p.stdout + p.stderr)
                parsed = json.loads(p.stdout)
                self.assertTrue(parsed['structure_valid'])

    def test_existing_independent_control_fixture_has_no_pagination_warnings(self):
        fixture = Path(__file__).resolve().parents[1] / 'assets' / 'compatibility' / 'libreoffice-control.docx'
        report = validate_layout(fixture)
        self.assertTrue(report['structure_valid'], report)
        self.assertFalse({item['code'] for item in report['issues']} & PAGINATION_CODES)


if __name__ == '__main__':
    unittest.main()
