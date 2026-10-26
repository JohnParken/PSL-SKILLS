"""Explicitly synthetic OOXML fixtures, not evidence of WPS producer coverage."""
import io
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import warnings
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wps_ooxml as reader


def package(body='', extra=(), document=None):
    stream = io.BytesIO()
    files = [('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'),
             ('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'),
             ('word/document.xml', document if document is not None else '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
            for name, value in files + list(extra):
                archive.writestr(name, value)
    return stream.getvalue()


class EnvironmentTests(unittest.TestCase):
    def test_old_expat_refused(self):
        with patch.object(reader.expat, 'version_info', (2, 5, 0)):
            result = reader.read_bytes(package())
        self.assertEqual(result['status'], 'unsupported')
        self.assertIsNone(result['text'])


@unittest.skipUnless(reader.expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class OoxmlTests(unittest.TestCase):
    def test_text_revision_related_and_table(self):
        body = '<w:p><w:r><w:t>中文 A</w:t><w:tab/><w:t>B</w:t></w:r><w:ins><w:r><w:t>新</w:t></w:r></w:ins><w:del><w:r><w:delText>旧</w:delText></w:r></w:del></w:p><w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>甲</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>乙</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        data = package(body, [('word/header1.xml', '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>页眉</w:t></w:r></w:p></w:hdr>')])
        for view, text in [('accepted','新'), ('original','旧'), ('all','新旧')]:
            result = reader.read_bytes(data, revisions=view)
            self.assertEqual(result['text'], '中文 A\tB' + text + '\n甲\t乙')
            self.assertEqual(result['status'], 'ok')
            self.assertNotIn('页眉', result['text'])
        self.assertEqual(reader.read_bytes(data, include_related=True)['related_stories'], [])

    def test_unrendered_symbol_and_hyphen_content_is_partial(self):
        for marker in ('<w:noBreakHyphen/>', '<w:softHyphen/>', '<w:sym w:font="Symbol" w:char="F061"/>'):
            result = reader.read_bytes(package('<w:p><w:r><w:t>A</w:t>'+marker+'<w:t>B</w:t></w:r></w:p>'))
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['text'], 'AB')
            self.assertEqual(result['diagnostics'][0]['code'], 'uninterpreted_content')

    def test_empty_and_bad_xml(self):
        self.assertEqual(reader.read_bytes(package())['text'], '')
        self.assertEqual(reader.read_bytes(package())['paragraphs'], [])
        self.assertEqual(reader.read_bytes(package(document='<broken>'))['status'], 'corrupt')
        data = package(document='<!DOCTYPE x [<!ENTITY e "unsafe">]><x>&e;</x>')
        self.assertEqual(reader.read_bytes(data)['status'], 'unsupported')

    def test_archive_guards_and_crc(self):
        for extra in ([('../escape', 'x')], [('word/document.xml', 'x')], [('Word/other', 'x')]):
            result = reader.read_bytes(package(extra=extra))
            self.assertNotEqual(result['status'], 'ok')
            self.assertIsNone(result['text'])
        data = bytearray(package('<w:p><w:r><w:t>UNIQUE</w:t></w:r></w:p>'))
        at = data.index(b'UNIQUE'); data[at] ^= 1
        self.assertEqual(reader.read_bytes(bytes(data))['status'], 'corrupt')

    def test_budget_before_xml_tree(self):
        data = package('<w:p/>')
        with patch.object(reader, 'MAX_XML_ELEMENTS', 1):
            self.assertEqual(reader.read_bytes(data)['status'], 'resource_limit')
        with patch.object(reader, 'MAX_MEMBERS', 1):
            self.assertEqual(reader.read_bytes(data)['status'], 'resource_limit')

    def test_partial_and_exact_related_names(self):
        body = '<w:p><w:r><w:t>缓存</w:t><w:instrText>unsafe instruction</w:instrText></w:r><w:drawing/></w:p>'
        result = reader.read_bytes(package(body, [('word/headerFoo.xml', 'bad xml')]), include_related=True)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['partial'])
        self.assertEqual(result['text'], '缓存')
        self.assertEqual(result['diagnostics'][0]['elements'], ['drawing', 'instrText'])

    def test_foreign_names_do_not_become_word_text(self):
        body = '<w:p><w:r><w:t>正文</w:t><x:t xmlns:x="urn:unrelated">FOREIGN</x:t><x:tab xmlns:x="urn:unrelated"/></w:r></w:p>'
        result = reader.read_bytes(package(body))
        self.assertEqual(result['text'], '正文')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['diagnostics'][0]['code'], 'uninterpreted_namespace')
        # A foreign wrapper also cannot smuggle legitimate Word descendants.
        result = reader.read_bytes(package('<x:sdt xmlns:x="urn:unrelated"><w:p><w:r><w:t>HIDDEN</w:t></w:r></w:p></x:sdt><w:sdt><w:sdtContent><w:p><w:r><w:t>保留</w:t></w:r></w:p></w:sdtContent></w:sdt>'))
        self.assertEqual(result['text'], '保留')
        self.assertEqual(result['status'], 'partial')

    def test_markup_compatibility_not_successful_empty(self):
        body = '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><mc:Choice Requires="x"><w:p><w:r><w:t>CHOICE</w:t></w:r></w:p></mc:Choice><mc:Fallback><w:p><w:r><w:t>FALLBACK</w:t></w:r></w:p></mc:Fallback></mc:AlternateContent>'
        result = reader.read_bytes(package(body))
        self.assertEqual(result['text'], '')
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['partial'])
        self.assertEqual(result['diagnostics'][0]['code'], 'uninterpreted_namespace')
        # Neither branch is concatenated inside a paragraph either.
        self.assertEqual(reader.read_bytes(package('<w:p>' + body + '</w:p>'))['text'], '')

    def test_bounded_mutations(self):
        data = package('<w:p><w:r><w:t>文字</w:t></w:r></w:p>')
        for end in range(0, len(data), max(1, len(data)//40)):
            self.assertNotEqual(reader.read_bytes(data[:end])['status'], 'ok')
        for pos in (0, 2, len(data)-22, len(data)-20, len(data)-12):
            damaged = bytearray(data); damaged[pos] ^= 0x80
            self.assertNotEqual(reader.read_bytes(bytes(damaged))['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
