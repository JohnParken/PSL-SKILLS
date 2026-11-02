"""P2 OOXML contracts: sections, variant headers, fields, and table captions.

No dependency on MS Word, python-docx, WPS, or LibreOffice.
"""
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_docx
from validate_layout import validate_layout
from office.formatting.audit import audit_roots

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'


class P2LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'p2.docx'

    def parts(self, spec):
        create_docx.create_document(spec, self.output, verify=True)
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            return {name: ET.fromstring(archive.read(name)) for name in archive.namelist()
                    if name.endswith(('.xml', '.rels'))}

    @staticmethod
    def section_nodes(document):
        body = document.find(W + 'body')
        return ([p.find('./' + W + 'pPr/' + W + 'sectPr') for p in body.findall(W + 'p')
                 if p.find('./' + W + 'pPr/' + W + 'sectPr') is not None] +
                [body.find(W + 'sectPr')])

    def test_three_sections_orientation_and_inherited_margins(self):
        roots = self.parts({'page': {'margin_mm': 18}, 'blocks': [
            {'type': 'paragraph', 'text': '正文'},
            {'type': 'section_break', 'page': {'orientation': 'landscape'}, 'break_type': 'next_page'},
            {'type': 'table', 'rows': [['一','二'], ['3', '4']]},
            {'type': 'section_break', 'page': {'orientation': 'portrait', 'margin_left_mm': 24},
             'break_type': 'odd_page'},
            {'type': 'paragraph', 'text': '结论'}]})
        nodes = self.section_nodes(roots['word/document.xml'])
        self.assertEqual(len(nodes), 3)
        self.assertEqual([s.find(W + 'pgSz').get(W + 'orient') for s in nodes], [None, 'landscape', None])
        self.assertEqual([s.find(W + 'type').get(W + 'val') if s.find(W + 'type') is not None else None for s in nodes],
                         [None, 'nextPage', 'oddPage'])
        self.assertEqual([s.find(W + 'pgMar').get(W + 'left') for s in nodes],
                         [str(create_docx._twips(v)) for v in (18, 18, 24)])
        self.assertTrue(audit_roots(roots)['structure_valid'])
        self.assertFalse(audit_roots(roots)['issues'])

    def test_header_footer_variants_and_section_restart(self):
        roots = self.parts({
            'header': '正文页眉', 'footer': '中心', 'page_numbers': True,
            'odd_even_headers': True, 'different_first_page': True,
            'first_page_numbers': True, 'page_number_start': 5,
            'headers': {'first': '首页', 'even': '偶数页'},
            'footers': {'first': '封面', 'even': '偶数脚'},
            'blocks': [{'type': 'paragraph', 'text': '前言'},
                       {'type': 'section_break', 'page_number_start': 1,
                        'page_number_format': 'lowerRoman',
                        'headers': {'default': '附录', 'first': '附录首页'},
                        'footers': {'even': None}},
                       {'type': 'paragraph', 'text': '附录'}]})
        nodes = self.section_nodes(roots['word/document.xml'])
        for section in nodes:
            self.assertEqual({r.get(W + 'type') for r in section.findall(W + 'headerReference')},
                             {'default', 'even', 'first'})
            self.assertEqual({r.get(W + 'type') for r in section.findall(W + 'footerReference')},
                             {'default', 'even', 'first'})
            self.assertIsNotNone(section.find(W + 'titlePg'))
        self.assertEqual(nodes[0].find(W + 'pgNumType').get(W + 'start'), '5')
        self.assertEqual(nodes[1].find(W + 'pgNumType').get(W + 'start'), '1')
        self.assertEqual(nodes[1].find(W + 'pgNumType').get(W + 'fmt'), 'lowerRoman')
        self.assertIsNotNone(roots['word/settings.xml'].find(W + 'evenAndOddHeaders'))
        self.assertIsNone(roots['word/settings.xml'].find(W + 'updateFields'))
        self.assertEqual(len(roots['word/header1.xml'].findall('.//' + W + 't')), 1)
        self.assertEqual([x.text for x in roots['word/header1.xml'].iter(W+'t')], ['正文页眉'])
        # Footer page fields exist only in the intended variant parts.
        with_fields = [name for name, root in roots.items()
                       if name.startswith('word/footer') and root.find('.//' + W + 'instrText') is not None]
        self.assertGreaterEqual(len(with_fields), 2)
        for part in with_fields:
            self.assertEqual(roots[part].find('.//' + W + 'instrText').text, ' PAGE ')

    def test_first_page_is_blank_by_default(self):
        roots = self.parts({'header': '正文', 'page_numbers': True,
                            'different_first_page': True, 'blocks': [{'type':'paragraph','text':'甲'}]})
        section = self.section_nodes(roots['word/document.xml'])[0]
        refs = {x.get(W+'type'): x.get(R+'id') for x in section.findall(W+'headerReference')}
        self.assertEqual(set(refs), {'default', 'first'})
        rels = {r.get('Id'): r.get('Target') for r in roots['word/_rels/document.xml.rels'].findall(REL+'Relationship')}
        self.assertEqual([t.text for t in roots['word/'+rels[refs['first']]].iter(W+'t')], [])
        self.assertEqual([t.text for t in roots['word/'+rels[refs['default']]].iter(W+'t')], ['正文'])
        first_footer = [x for x in section.findall(W+'footerReference') if x.get(W+'type') == 'first'][0]
        self.assertEqual(list(roots['word/'+rels[first_footer.get(R+'id')]].iter(W+'instrText')), [])

    def test_disable_first_page_in_later_section(self):
        roots = self.parts({'header': '正文', 'different_first_page': True,
                            'headers': {'first': '首页'}, 'blocks': [
                                {'type':'paragraph', 'text':'封面'},
                                {'type':'section_break', 'different_first_page': False},
                                {'type':'paragraph', 'text':'正文'}]})
        nodes = self.section_nodes(roots['word/document.xml'])
        self.assertIsNotNone(nodes[0].find(W+'titlePg'))
        self.assertIsNone(nodes[1].find(W+'titlePg'))
        self.assertEqual([r.get(W+'type') for r in nodes[1].findall(W+'headerReference')], ['default'])

    def test_page_numbering_continues_if_no_restart(self):
        roots = self.parts({'page_numbers': True, 'page_number_start': 8, 'blocks': [
            {'type':'paragraph','text':'甲'}, {'type':'section_break'},
            {'type':'paragraph','text':'乙'}]})
        nodes = self.section_nodes(roots['word/document.xml'])
        self.assertEqual(nodes[0].find(W+'pgNumType').get(W+'start'),'8')
        self.assertIsNone(nodes[1].find(W+'pgNumType'))
        self.assertEqual([n for n in roots if n.startswith('word/footer')], ['word/footer1.xml'])

    def test_disabled_page_numbers_break_footer_inheritance_and_can_resume(self):
        roots = self.parts({'page_numbers': True, 'blocks': [
            {'type': 'paragraph', 'text': '有页码'},
            {'type': 'section_break', 'page_numbers': False},
            {'type': 'paragraph', 'text': '无页码'},
            {'type': 'section_break', 'page_numbers': True},
            {'type': 'paragraph', 'text': '恢复页码'}]})
        nodes = self.section_nodes(roots['word/document.xml'])
        relations = {node.get('Id'): node.get('Target')
                     for node in roots['word/_rels/document.xml.rels']}
        reference_ids = []
        for index, section in enumerate(nodes):
            reference = section.find(W + 'footerReference')
            self.assertIsNotNone(reference, f'section {index} must explicitly select its footer')
            reference_ids.append(reference.get(R + 'id'))
            footer = roots['word/' + relations[reference_ids[-1]]]
            fields = [node.text for node in footer.iter(W + 'instrText')]
            self.assertEqual(fields, [] if index == 1 else [' PAGE '])
            if index == 1:
                self.assertEqual(list(footer.iter(W + 't')), [])
        self.assertEqual(reference_ids[0], reference_ids[2])
        self.assertNotEqual(reference_ids[0], reference_ids[1])

    def test_page_number_disable_preserves_footer_text_in_all_variants(self):
        for footer_text in (None, '保留页脚'):
            with self.subTest(footer_text=footer_text):
                spec = {'page_numbers': True, 'odd_even_headers': True, 'different_first_page': True,
                        'first_page_numbers': True, 'blocks': [
                            {'type': 'paragraph', 'text': '有页码'},
                            {'type': 'section_break', 'page_numbers': False},
                            {'type': 'paragraph', 'text': '无页码'}]}
                if footer_text is not None:
                    spec['footers'] = {key: footer_text for key in ('default', 'first', 'even')}
                roots = self.parts(spec)
                nodes = self.section_nodes(roots['word/document.xml'])
                relations = {node.get('Id'): node.get('Target')
                             for node in roots['word/_rels/document.xml.rels']}
                for index, section in enumerate(nodes):
                    references = section.findall(W + 'footerReference')
                    self.assertEqual({ref.get(W + 'type') for ref in references}, {'default', 'first', 'even'})
                    for reference in references:
                        footer = roots['word/' + relations[reference.get(R + 'id')]]
                        self.assertEqual([node.text for node in footer.iter(W + 'instrText')],
                                         [' PAGE '] if index == 0 else [])
                        if index == 1:
                            self.assertEqual([node.text for node in footer.iter(W + 't')],
                                             [footer_text] if footer_text else [])

    def test_documents_without_page_numbers_or_footer_keep_no_footer_parts(self):
        roots = self.parts({'blocks': [{'type': 'paragraph', 'text': '甲'},
                                      {'type': 'section_break', 'page_numbers': False},
                                      {'type': 'paragraph', 'text': '乙'}]})
        self.assertFalse([part for part in roots if part.startswith('word/footer')])
        for section in self.section_nodes(roots['word/document.xml']):
            self.assertEqual(section.findall(W + 'footerReference'), [])

    def test_forward_refs_and_toc_use_updatefields(self):
        roots = self.parts({'heading_numbering':'decimal', 'blocks':[
            {'type':'toc','depth':2},
            {'type':'reference','bookmark':'future', 'kind':'page','prefix':'详见第','suffix':'页'},
            {'type':'reference','bookmark':'Future', 'kind':'text'},
            {'type':'heading','level':1,'text':'附录','bookmark':'future'}]})
        document = roots['word/document.xml']
        fields = [node.text for node in document.iter(W+'instrText')]
        self.assertEqual(fields[0], ' TOC \\o "1-2" \\u ')
        self.assertEqual(fields[1], ' PAGEREF future \\h ')
        self.assertEqual(fields[2], ' REF Future \\h ')
        self.assertEqual(roots['word/settings.xml'].find(W+'updateFields').get(W+'val'), 'true')
        self.assertTrue(audit_roots(roots)['structure_valid'])

    def test_bad_cross_reference_is_rejected(self):
        bad = {'blocks':[{'type':'reference','bookmark':'missing','kind':'text'}]}
        with self.assertRaisesRegex(ValueError, 'undefined'):
            create_docx.create_document(bad, self.output, verify=True)
        self.assertFalse(self.output.exists())
        bad['blocks'][0]['bookmark'] = '_invalid'
        with self.assertRaisesRegex(ValueError, 'bookmark'):
            create_docx.create_document(bad, self.output, verify=True)

    def test_audit_detects_missing_cross_reference(self):
        roots = self.parts({'blocks':[{'type':'heading','level':1,'text':'标题','bookmark':'book'},
                                     {'type':'reference','bookmark':'book'}]})
        start = roots['word/document.xml'].find('.//' + W + 'bookmarkStart')
        start.set(W + 'name', 'other')
        report = audit_roots(roots)
        self.assertFalse(report['structure_valid'])
        self.assertIn('UNRESOLVED_CROSS_REFERENCE', {i['code'] for i in report['issues']})

    def test_table_caption_affinity_and_complex_merge(self):
        roots = self.parts({'blocks':[{'type':'table','caption':'表1 存款经营', 'header_rows':2, 'rows':[
            [{'text':'机构','row_span':2}, {'text':'余额','col_span':2}, None],
            [None, '上期', '本期'], ['A', '7', '8']]}]})
        body = roots['word/document.xml'].find(W + 'body')
        self.assertEqual([x.tag for x in list(body)[:2]], [W+'p', W+'tbl'])
        self.assertEqual(body[0].find('./'+W+'pPr/'+W+'pStyle').get(W+'val'), 'TableCaption')
        style = next(x for x in roots['word/styles.xml'].findall(W+'style') if x.get(W+'styleId') == 'TableCaption')
        self.assertEqual(style.find('./'+W+'pPr/'+W+'keepNext').get(W+'val'), '1')
        self.assertTrue(validate_layout(self.output)['structure_valid'])

    def test_multi_section_width_audit_finds_overwide_table(self):
        roots = self.parts({'blocks':[{'type':'paragraph','text':'A'},
            {'type':'section_break','page':{'orientation':'landscape'}},
            {'type':'table','rows':[['1','2'],['3','4']]}]})
        self.assertNotIn('MULTI_SECTION_UNCHECKED', {x['code'] for x in audit_roots(roots)['issues']})
        grid = roots['word/document.xml'].find('.//'+W+'tblGrid')
        for col in grid.findall(W+'gridCol'):
            col.set(W+'w','40000')
        self.assertIn('TABLE_WIDTH_RISK', {x['code'] for x in audit_roots(roots)['issues']})

    def test_reject_invalid_section_specs_without_file(self):
        invalid = [
            {'type':'section_break','page':{'margin_mm':250}},
            {'type':'section_break','page':{'orientation':'sideways'}},
            {'type':'section_break','break_type':'blank'},
            {'type':'section_break','page_number_start':0},
            {'type':'section_break','page_number_start':True},
            {'type':'section_break','headers':{'even':'other'}},
            {'type':'section_break','page':{'height_mm':0}},
            {'type':'section_break','page_number_format':'not-a-format'},
            {'type':'section_break','first_page_numbers':True},
        ]
        for entry in invalid:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                create_docx.create_document({'blocks':[{'type':'paragraph','text':'A'},entry,
                                                         {'type':'paragraph','text':'B'}]},self.output,verify=True)
            self.assertFalse(self.output.exists())
        for blocks in [[{'type':'section_break'},{'type':'paragraph','text':'A'}],
                       [{'type':'paragraph','text':'A'},{'type':'section_break'}],
                       [{'type':'paragraph','text':'A'},{'type':'section_break'},
                        {'type':'section_break'},{'type':'paragraph','text':'B'}]]:
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                create_docx.create_document({'blocks':blocks},self.output,verify=True)
            self.assertFalse(self.output.exists())

    def test_profile_and_headings_survive_sections(self):
        roots = self.parts({'profile':'business-report', 'heading_numbering':'chinese','blocks':[
            {'type':'heading','level':1,'text':'总部'},
            {'type':'section_break','page':{'orientation':'landscape'}},
            {'type':'heading','level':2,'text':'附录'},
            {'type':'table','caption':'表一', 'rows':[['名称','值'],['甲','1']]}]})
        doc = roots['word/document.xml']
        self.assertEqual(len(doc.findall('.//'+W+'sectPr')),2)
        self.assertEqual(len(doc.findall('.//'+W+'numId')), 2)
        self.assertTrue(validate_layout(self.output)['structure_valid'])


if __name__ == '__main__':
    unittest.main()
