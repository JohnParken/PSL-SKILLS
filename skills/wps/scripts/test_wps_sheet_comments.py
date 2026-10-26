"""Synthetic comment packages with independently stated text and cell expectations."""
from pathlib import Path
import io
import sys
import unittest
import zipfile
from unittest.mock import patch
from xml.parsers import expat
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_ooxml_sheet import package, result, T, S, P, R, RS


def sample(strict=False, body=None, relation=None, content_type=None):
    namespace, rel = (S, RS) if strict else (T, R)
    raw = package(strict=strict)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        types = archive.read('[Content_Types].xml').decode()
    types = types.replace('</Types>', '<Override PartName="/xl/notes/c.xml" ContentType="' + (content_type or 'application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml') + '"/></Types>')
    comments = body or '<authors><author>审阅人</author></authors><commentList><comment ref="B2" authorId="0"><text><r><t>核对</t></r><r><t>余额</t></r></text></comment></commentList>'
    return package(strict=strict, overrides={
        '[Content_Types].xml': types,
        'xl/worksheets/_rels/sheet1.xml.rels': relation or '<Relationships xmlns="' + P + '"><Relationship Id="note1" Type="' + rel + '/comments" Target="../notes/c.xml"/></Relationships>',
        'xl/notes/c.xml': '<comments xmlns="' + namespace + '">' + comments + '</comments>'})


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class CommentsTests(unittest.TestCase):
    def test_strict_transitional_comment_text_and_provenance(self):
        for strict in (False, True):
            value = result(sample(strict))
            self.assertEqual(value['status'], 'partial', value)
            note = value['sheets'][0]['comments'][0]
            self.assertEqual((note['cell'], note['author'], note['text']), ('B2', '审阅人', '核对余额'))
            self.assertEqual(note['source']['part'], 'xl/notes/c.xml')
            self.assertFalse(note['layout_decoded'])
            self.assertEqual(value['cell_count'], 1)
            self.assertIn('comment_layout_not_decoded', [item['code'] for item in value['issues']])

    def test_bad_refs_authors_duplicates_and_nested_text_refused(self):
        for body in (
            '<authors><author>A</author></authors><commentList><comment ref="A0" authorId="0"><text><t>X</t></text></comment></commentList>',
            '<authors><author>A</author></authors><commentList><comment ref="A1" authorId="1"><text><t>X</t></text></comment></commentList>',
            '<authors><author>A</author></authors><commentList>' + '<comment ref="A1" authorId="0"><text><t>X</t></text></comment>' * 2 + '</commentList>',
            '<authors><author>A</author></authors><commentList><comment ref="A1" authorId="0"><text><t><t>Lost</t></t></text></comment></commentList>'):
            with self.subTest(body=body):
                self.assertNotIn(result(sample(body=body))['status'], ('ok', 'partial'))

    def test_relationship_type_external_missing_part_and_content_type(self):
        for relation in (
            '<Relationships xmlns="' + P + '"><Relationship Id="x" Type="' + R + '/comments" Target="https://invalid.example/c" TargetMode="External"/></Relationships>',
            '<Relationships xmlns="' + P + '"><Relationship Id="x" Type="' + R + '/drawing" Target="../notes/c.xml"/></Relationships>',
            '<Relationships xmlns="' + P + '"><Relationship Id="x" Type="' + R + '/comments" Target="missing.xml"/></Relationships>'):
            self.assertNotIn(result(sample(relation=relation))['status'], ('ok', 'partial'))
        self.assertNotIn(result(sample(content_type='application/xml'))['status'], ('ok', 'partial'))

    def test_unwrapped_collection_text_and_author_budget(self):
        for body in (
            'Lost<authors/><commentList/>',
            '<authors>Lost<author>A</author></authors><commentList/>',
            '<authors><author>A</author>Lost</authors><commentList/>',
            '<authors><author>A</author></authors><commentList><comment ref="A1" authorId="0">Lost<text><t>X</t></text></comment></commentList>'):
            self.assertNotIn(result(sample(body=body))['status'], ('ok', 'partial'))
        self.assertEqual(result(sample(body='<authors><author>' + 'A' * 32768 + '</author></authors><commentList/>'))['status'], 'resource_limit')

    def test_shared_empty_comment_part_alias_refused(self):
        raw = sample(body='<authors/><commentList/>')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            parts = {name: archive.read(name).decode() for name in archive.namelist()}
        parts['xl/workbook.xml'] = parts['xl/workbook.xml'].replace('</sheets>', '<sheet name="Second" sheetId="8" r:id="s2"/></sheets>')
        parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].replace('</Relationships>', '<Relationship Id="s2" Type="' + R + '/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace('</Types>', '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        parts['xl/worksheets/sheet2.xml'] = parts['xl/worksheets/sheet1.xml']
        parts['xl/worksheets/_rels/sheet2.xml.rels'] = parts['xl/worksheets/_rels/sheet1.xml.rels']
        self.assertEqual(result(package(overrides=parts))['status'], 'corrupt')

    def test_empty_comments_and_budget(self):
        value = result(sample(body='<authors/><commentList/>'))
        self.assertEqual(value['sheets'][0]['comments'], [])
        with patch('wps_sheet_comments.MAX_COMMENT_TEXT', 2):
            self.assertEqual(result(sample())['status'], 'resource_limit')
        with patch('wps_sheet_comments.MAX_COMMENTS', 0):
            self.assertEqual(result(sample())['status'], 'resource_limit')

if __name__ == '__main__': unittest.main()
