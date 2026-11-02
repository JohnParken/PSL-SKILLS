"""Native OOXML P1 contract tests. No office application or third-party dependency."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_docx

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


class P1LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'p1.docx'

    def parts(self, spec):
        create_docx.create_document(spec, self.output, verify=True)
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            return {n: ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith('.xml') or n.endswith('.rels')}

    def headings(self, scheme):
        return {'heading_numbering': scheme, 'blocks': [
            {'type': 'heading', 'level': 1, 'text': '总体情况'},
            {'type': 'heading', 'level': 2, 'text': '重点工作'},
            {'type': 'heading', 'level': 3, 'text': '完成情况'},
            {'type': 'list', 'ordered': True, 'items': ['第一项']},
            {'type': 'heading', 'level': 1, 'text': '下一阶段'},
            {'type': 'heading', 'level': 2, 'text': '计划安排'},
        ]}

    def test_chinese_numbering_and_list_isolation(self):
        roots = self.parts(self.headings('chinese'))
        numbering = roots['word/numbering.xml']
        abstract = next(x for x in numbering.findall(W + 'abstractNum') if x.get(W + 'abstractNumId') == '4')
        self.assertEqual([l.find(W + 'numFmt').get(W + 'val') for l in abstract.findall(W + 'lvl')],
                         ['chineseCounting', 'chineseCounting', 'decimal', 'decimal'])
        self.assertEqual([l.find(W + 'lvlText').get(W + 'val') for l in abstract.findall(W + 'lvl')],
                         ['%1、', '（%2）', '%3.', '（%4）'])
        body = roots['word/document.xml'].find(W + 'body')
        headings = [p for p in body.findall(W + 'p') if p.find('./' + W + 'pPr/' + W + 'pStyle') is not None
                    and p.find('./' + W + 'pPr/' + W + 'pStyle').get(W + 'val').startswith('Heading')]
        self.assertEqual(len(headings), 5)
        for p in headings:
            self.assertEqual(p.find('./' + W + 'pPr/' + W + 'numPr/' + W + 'numId').get(W + 'val'), '1')
        self.assertEqual([n.get(W + 'numId') for n in numbering.findall(W + 'num')], ['1', '2'])
        self.assertEqual(''.join(t.text or '' for t in headings[0].iter(W + 't')), '总体情况')

    def test_decimal_heading_numbering(self):
        numbering = self.parts(self.headings('decimal'))['word/numbering.xml']
        abstract = next(a for a in numbering.findall(W + 'abstractNum') if a.get(W + 'abstractNumId') == '4')
        self.assertEqual([l.find(W + 'lvlText').get(W + 'val') for l in abstract.findall(W + 'lvl')],
                         ['%1 ', '%1.%2 ', '%1.%2.%3 ', '%1.%2.%3.%4 '])

    def test_legacy_has_no_heading_number(self):
        spec = self.headings('none')
        root = self.parts(spec)['word/document.xml']
        heading = root.find('.//' + W + 'p')
        self.assertIsNone(heading.find('./' + W + 'pPr/' + W + 'numPr'))

    def test_complex_grid_spans_and_vertical_continuations(self):
        spec = {'blocks': [{'type': 'table', 'header_rows': 2, 'rows': [
            [{'text': '机构', 'row_span': 2}, {'text': '存款', 'col_span': 2}, None],
            [None, '期初', '期末'],
            [{'text': '机构A', 'row_span': 2}, {'text': '合计', 'col_span': 2}, None],
            [None, '15', '18'],
        ]}]}
        root = self.parts(spec)['word/document.xml']
        rows = root.findall('.//' + W + 'tbl/' + W + 'tr')
        self.assertEqual([len(row.findall(W + 'tc')) for row in rows], [2, 3, 2, 3])
        self.assertEqual(rows[0].findall(W + 'tc')[1].find('./' + W + 'tcPr/' + W + 'gridSpan').get(W + 'val'), '2')
        self.assertEqual(rows[1].findall(W + 'tc')[0].find('./' + W + 'tcPr/' + W + 'vMerge').get(W + 'val'), 'continue')
        self.assertEqual(rows[0].findall(W + 'tc')[0].find('./' + W + 'tcPr/' + W + 'vMerge').get(W + 'val'), 'restart')
        self.assertEqual([r.find('./' + W + 'trPr/' + W + 'tblHeader') is not None for r in rows],
                         [True, True, False, False])
        for row in rows:
            self.assertEqual(sum(int(cell.find('./' + W + 'tcPr/' + W + 'gridSpan').get(W + 'val'))
                                 if cell.find('./' + W + 'tcPr/' + W + 'gridSpan') is not None else 1
                                 for cell in row.findall(W + 'tc')), 3)

    def test_combined_horizontal_vertical_merge(self):
        spec = {'blocks': [{'type': 'table', 'rows': [
            [{'text': '大单元格', 'row_span': 2, 'col_span': 2}, None, '右列'],
            [None, None, '下一行'],
        ]}]}
        rows = self.parts(spec)['word/document.xml'].findall('.//' + W + 'tbl/' + W + 'tr')
        self.assertEqual(len(rows[1].findall(W + 'tc')), 2)
        self.assertEqual(rows[1].findall(W + 'tc')[0].find('./' + W + 'tcPr/' + W + 'gridSpan').get(W + 'val'), '2')
        self.assertEqual(rows[1].findall(W + 'tc')[0].find('./' + W + 'tcPr/' + W + 'vMerge').get(W + 'val'), 'continue')
        self.assertEqual(len(rows[1].findall(W + 'tc')[0].findall('.//' + W + 't')), 0)

    def test_invalid_table_merges_rejected(self):
        bad_rows = [
            [[{'text':'A','col_span':2}, 'B']],
            [[None, 'B']],
            [[{'text':'A','col_span':3}, None]],
            [[{'text':'A','row_span':2}], ['not-null']],
            [[{'text':'A','row_span':0}]],
            [[{'text':'A','col_span':True}]],
            [[{'text':'A','row_span':2,'col_span':2},None], [None, 'wrong']],
            [[{'text':'A','v_merge':'continue'}]],
        ]
        for rows in bad_rows:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                create_docx.create_document({'blocks': [{'type': 'table', 'rows': rows}]}, self.output, verify=True)
            self.assertFalse(self.output.exists())

    def test_repeating_header_cannot_cross_vertical_merge(self):
        spec = {'blocks': [{'type': 'table', 'header_rows': 1,
                            'rows': [[{'text':'A','row_span':2},'B'],[None,'C']]}]}
        with self.assertRaisesRegex(ValueError, 'header'):
            create_docx.create_document(spec, self.output, verify=True)

    def test_bookmarks_and_toc_and_settings_with_header_footer(self):
        spec = {'page_numbers': True, 'header':'header', 'footer':'footer',
                'heading_numbering':'decimal', 'blocks': [
                    {'type':'toc','depth':2},
                    {'type':'heading','level':1,'text':'Introduction','bookmark':'intro_1'},
                    {'type':'paragraph','text':'Body'}]}
        roots = self.parts(spec)
        document = roots['word/document.xml']
        self.assertEqual(document.find('.//' + W + 'bookmarkStart').get(W + 'name'), 'intro_1')
        self.assertEqual(document.find('.//' + W + 'bookmarkEnd').get(W + 'id'), '1')
        self.assertEqual(document.find('.//' + W + 'instrText').text, ' TOC \\o "1-2" \\u ')
        self.assertEqual(roots['word/settings.xml'].find(W + 'updateFields').get(W + 'val'), 'true')
        self.assertIn('word/header1.xml', roots)
        self.assertIn('word/footer1.xml', roots)
        overrides = roots['[Content_Types].xml'].findall('{http://schemas.openxmlformats.org/package/2006/content-types}Override')
        self.assertIn('/word/settings.xml', [item.get('PartName') for item in overrides])

    def test_invalid_bookmarks_and_toc_rejected(self):
        for spec in [
            {'blocks':[{'type':'heading','level':1,'text':'X','bookmark':'1invalid'}]},
            {'blocks':[{'type':'heading','level':1,'text':'X','bookmark':'same'},
                       {'type':'heading','level':2,'text':'Y','bookmark':'Same'}]},
            {'blocks':[{'type':'toc','depth':0}]},
            {'blocks':[{'type':'toc','depth':True}]},
            {'heading_numbering':'roman','blocks':[]},
        ]:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                create_docx.create_document(spec,self.output,verify=True)
            self.assertFalse(self.output.exists())

    def test_static_audit_rejects_bad_merge_continuation(self):
        from office.formatting.audit import audit_roots
        roots = self.parts({'blocks': [{'type': 'table', 'rows': [
            [{'text': 'A', 'row_span': 2}, 'B'], [None, 'C']]}]})
        self.assertTrue(audit_roots(roots)['structure_valid'])
        cell = roots['word/document.xml'].find('.//' + W + 'tbl/' + W + 'tr/' + W + 'tc')
        cell.find('./' + W + 'tcPr/' + W + 'vMerge').set(W + 'val', 'continue')
        audit = audit_roots(roots)
        self.assertFalse(audit['structure_valid'])
        self.assertIn('INVALID_VERTICAL_MERGE', {issue['code'] for issue in audit['issues']})

    def test_three_line_merged_table_keeps_header_separator(self):
        roots = self.parts({'blocks':[{'type':'table', 'header_rows':2,
            'format':{'preset':'three_line'},'rows':[
                [{'text':'机构','row_span':2},{'text':'余额','col_span':2},None],
                [None,'本期','上期'],['甲','20','18']]}]})
        rows = roots['word/document.xml'].findall('.//' + W + 'tbl/' + W + 'tr')
        header_cells = rows[1].findall(W + 'tc')
        self.assertTrue(all(cell.find('./' + W + 'tcPr/' + W + 'tcBorders/' + W + 'bottom') is not None
                            for cell in header_cells))
        data_cells = rows[2].findall(W + 'tc')
        self.assertTrue(all(cell.find('./' + W + 'tcPr/' + W + 'tcBorders/' + W + 'top') is not None
                            for cell in data_cells))

    def test_toc_not_emitted_unless_requested(self):
        roots = self.parts({'blocks':[{'type':'heading','level':1,'text':'No toc'}]})
        self.assertNotIn('word/settings.xml', roots)
        self.assertEqual(len(roots['word/document.xml'].findall('.//' + W + 'instrText')), 0)


if __name__ == '__main__':
    unittest.main()
