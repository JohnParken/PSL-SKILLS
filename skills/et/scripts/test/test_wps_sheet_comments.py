"""Synthetic comment packages with independently stated tolerant-read expectations."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import io
import sys
import unittest
import zipfile
from unittest.mock import patch
from xml.parsers import expat
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_ooxml_sheet import package, result, codes, T, S, P, R, RS


def sample(strict=False, body=None, relation=None, content_type=None):
    namespace, rel = (S, RS) if strict else (T, R)
    raw = package(strict=strict)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        types = archive.read('[Content_Types].xml').decode()
    types = types.replace(
        '</Types>', '<Override PartName="/xl/notes/c.xml" ContentType="' +
        (content_type or 'application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml')
        + '"/></Types>')
    comments = body or (
        '<authors><author>审阅人</author></authors><commentList>'
        '<comment ref="B2" authorId="0"><text><r><t>核对</t></r><r><t>余额</t></r>'
        '</text></comment></commentList>')
    return package(strict=strict, overrides={
        '[Content_Types].xml': types,
        'xl/worksheets/_rels/sheet1.xml.rels':
            relation or ('<Relationships xmlns="' + P + '"><Relationship Id="note1" '
                         'Type="' + rel + '/comments" Target="../notes/c.xml"/>'
                         '</Relationships>'),
        'xl/notes/c.xml': '<comments xmlns="' + namespace + '">' + comments + '</comments>'})


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class CommentsTests(unittest.TestCase):
    def test_strict_transitional_comment_text_and_provenance(self):
        for strict in (False, True):
            value = result(sample(strict))
            self.assertEqual(value['status'], 'partial', value)
            note = value['sheets'][0]['comments'][0]
            self.assertEqual((note['cell'], note['author'], note['text']),
                             ('B2', '审阅人', '核对余额'))
            self.assertEqual(note['source']['part'], 'xl/notes/c.xml')
            self.assertFalse(note['layout_decoded'])
            self.assertEqual(value['cell_count'], 1)
            self.assertIn('comment_layout_not_decoded', codes(value))

    def test_bad_refs_authors_duplicates_and_nested_text_degrade(self):
        cases = {
            '<authors><author>A</author></authors><commentList>'
            '<comment ref="A0" authorId="0"><text><t>X</t></text></comment>'
            '</commentList>': 'comment_reference_not_interpreted',
            '<authors><author>A</author></authors><commentList>'
            '<comment ref="A1" authorId="1"><text><t>X</t></text></comment>'
            '</commentList>': 'comment_author_not_interpreted',
            '<authors><author>A</author></authors><commentList>' +
            '<comment ref="A1" authorId="0"><text><t>X</t></text></comment>' * 2 +
            '</commentList>': 'duplicate_comment_skipped',
            '<authors><author>A</author></authors><commentList>'
            '<comment ref="A1" authorId="0"><text><t><t>Lost</t></t></text></comment>'
            '</commentList>': 'string_text_container_not_interpreted',
        }
        for body, code in cases.items():
            with self.subTest(code=code):
                value = result(sample(body=body))
                self.assertEqual(value['status'], 'partial')
                self.assertEqual(value['cell_count'], 1)
                self.assertIn(code, codes(value))
                if code == 'comment_reference_not_interpreted':
                    self.assertEqual(value['sheets'][0]['comments'], [])
                if code == 'comment_author_not_interpreted':
                    note = value['sheets'][0]['comments'][0]
                    self.assertEqual((note['author'], note['text']), ('', 'X'))
                if code == 'duplicate_comment_skipped':
                    self.assertEqual(len(value['sheets'][0]['comments']), 1)

    def test_relationship_variance_and_content_type_degrade(self):
        external = ('<Relationships xmlns="' + P + '"><Relationship Id="x" '
                    'Type="' + R + '/comments" Target="https://invalid.example/c" '
                    'TargetMode="External"/></Relationships>')
        value = result(sample(relation=external))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['sheets'][0]['comments'], [])
        self.assertIn('comments_part_not_read', codes(value))
        self.assertIn('external_relationship_not_followed', codes(value))
        self.assertEqual(value['cell_count'], 1)
        drawing = ('<Relationships xmlns="' + P + '"><Relationship Id="x" '
                   'Type="' + R + '/drawing" Target="../notes/c.xml"/></Relationships>')
        value = result(sample(relation=drawing))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['sheets'][0]['comments'], [])
        self.assertIn('worksheet_relationship_not_followed', codes(value))
        missing = ('<Relationships xmlns="' + P + '"><Relationship Id="x" '
                   'Type="' + R + '/comments" Target="missing.xml"/></Relationships>')
        value = result(sample(relation=missing))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['sheets'][0]['comments'], [])
        self.assertIn('comments_part_not_read', codes(value))
        value = result(sample(content_type='application/xml'))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['sheets'][0]['comments'][0]['text'], '核对余额')
        self.assertIn('comments_content_type_mismatch', codes(value))

    def test_unwrapped_collection_text_and_long_author_are_tolerated(self):
        for body in ('Lost<authors/><commentList/>',
                     '<authors>Lost<author>A</author></authors><commentList/>',
                     '<authors><author>A</author>Lost</authors><commentList/>',
                     '<authors><author>A</author></authors><commentList>'
                     '<comment ref="A1" authorId="0">Lost<text><t>X</t></text></comment>'
                     '</commentList>'):
            with self.subTest(body=body):
                value = result(sample(body=body))
                self.assertEqual(value['status'], 'partial')
                self.assertEqual(value['cell_count'], 1)
                self.assertIn('comments_structure_not_interpreted', codes(value))
        long_author = '<authors><author>' + 'A' * 32768 + \
                      '</author></authors><commentList/>'
        value = result(sample(body=long_author))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['sheets'][0]['comments'], [])

    def test_shared_comments_part_alias_is_recorded_not_failed(self):
        raw = sample(body='<authors/><commentList/>')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        parts['xl/workbook.xml'] = parts['xl/workbook.xml'].decode().replace(
            '</sheets>', '<sheet name="Second" sheetId="8" r:id="s2"/></sheets>').encode()
        parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].decode().replace(
            '</Relationships>', '<Relationship Id="s2" Type="' + R +
            '/worksheet" Target="worksheets/sheet2.xml"/></Relationships>').encode()
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].decode().replace(
            '</Types>', '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="'
            'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>').encode()
        parts['xl/worksheets/sheet2.xml'] = parts['xl/worksheets/sheet1.xml']
        parts['xl/worksheets/_rels/sheet2.xml.rels'] = parts[
            'xl/worksheets/_rels/sheet1.xml.rels']
        value = result(package(overrides=parts))
        self.assertEqual(value['status'], 'partial')
        self.assertIn('aliased_worksheet_comments_part', codes(value))
        self.assertEqual(len(value['sheets']), 2)

    def test_empty_comments_and_memory_budgets_degrade(self):
        value = result(sample(body='<authors/><commentList/>'))
        self.assertEqual(value['sheets'][0]['comments'], [])
        with patch('wps_sheet_comments.MAX_COMMENT_TEXT', 2):
            value = result(sample())
            self.assertEqual(value['status'], 'partial')
            self.assertIn('comment_text_limit_reached', codes(value))
        with patch('wps_sheet_comments.MAX_COMMENTS', 0):
            value = result(sample())
            self.assertEqual(value['status'], 'partial')
            self.assertIn('comment_limit_reached', codes(value))


if __name__ == '__main__':
    unittest.main()
