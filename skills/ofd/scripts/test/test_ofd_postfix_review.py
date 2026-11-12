"""Independent post-fix role/ordering checks; tiny synthetic packages only.

The reviewer constructs inputs and expected text/roles independently of the OFD
implementation's test fixtures. No Library, producer samples or network access.
"""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from contextlib import contextmanager
import importlib.util
import itertools
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ofd_independent_postfix', SCRIPTS / 'read_ofd.py')
READER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(READER)
NAMESPACE = 'http://www.ofdspec.org/2016'


def xml(name, content='', attrs=''):
    return f'<o:{name} xmlns:o="{NAMESPACE}" {attrs}>{content}</o:{name}>'.encode('utf-8')


def text_page(text):
    return xml('Page', '<o:Content><o:Layer ID="2"><o:TextObject ID="3" Size="3">'
               '<o:TextCode X="0" Y="3">' + text + '</o:TextCode></o:TextObject></o:Layer></o:Content>')


def minimal(metadata='', common=''):
    return {'OFD.xml': xml('OFD', '<o:DocBody><o:DocRoot>D/Document.xml</o:DocRoot></o:DocBody>', 'Version="1.0" DocType="OFD"'),
            'D/Document.xml': xml('Document', '<o:CommonData>' + common + '</o:CommonData>' + metadata
                                 + '<o:Pages><o:Page ID="1" BaseLoc="P.xml"/></o:Pages>'),
            'D/P.xml': text_page('正文')}


@contextmanager
def package(files, tail_order=()):
    with tempfile.TemporaryDirectory(prefix='ofd-postfix-review-') as directory:
        path = Path(directory) / 'input.ofd'
        names = [name for name in files if name not in tail_order] + list(tail_order)
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_STORED) as archive:
            for name in names:
                archive.writestr(name, files[name])
        opened = []
        original = zipfile.ZipFile.open
        def observe(archive, name, *args, **kwargs):
            opened.append(name if isinstance(name, str) else name.filename)
            return original(archive, name, *args, **kwargs)
        with patch.object(READER.zipfile.ZipFile, 'open', observe):
            yield path, opened


class OfdIndependentPostfixTests(unittest.TestCase):
    def test_active_attachment_is_never_opened_for_either_zip_order(self):
        files = minimal(metadata='<o:Attachments>A.xml</o:Attachments>')
        files['D/A.xml'] = xml('Attachments', '<o:Attachment ID="9"><o:FileLoc>payload.xml</o:FileLoc></o:Attachment>')
        files['D/payload.xml'] = b'opaque attachment bytes, deliberately not XML'
        for order in itertools.permutations(('D/A.xml', 'D/payload.xml', 'D/P.xml')):
            with self.subTest(order=order), package(files, order) as (path, opened):
                result = READER.read_ofd_structure(path)
                self.assertNotIn('D/payload.xml', opened)
                self.assertNotIn('D/payload.xml', result['parsed_xml'])
                self.assertIn({'source': 'D/payload.xml', 'role': 'attachment', 'bytes': len(files['D/payload.xml'])},
                              result['structure']['opaque_payloads'])
                self.assertEqual([run['text'] for run in result['documents'][0]['pages'][0]['text_runs']], ['正文'])

    def test_unreferenced_attachment_index_has_no_authority_in_either_order(self):
        files = minimal()
        files['Extra.xml'] = xml('Attachments', '<o:Attachment ID="9"><o:FileLoc>Supplement.xml</o:FileLoc></o:Attachment>')
        files['Supplement.xml'] = text_page('附加存储文字')
        for order in itertools.permutations(('Extra.xml', 'Supplement.xml')):
            with self.subTest(order=order), package(files, order) as (path, opened):
                result = READER.read_ofd_structure(path)
                self.assertIn('Supplement.xml', opened)
                self.assertEqual(result['structure']['opaque_payloads'], [])
                self.assertTrue(any(item['source'] == 'Supplement.xml' and item['raw_text'] == '附加存储文字'
                                    for item in result['text_sources']))
                self.assertEqual([run['text'] for run in result['documents'][0]['pages'][0]['text_runs']], ['正文'])

    def test_active_page_attachment_conflict_refused_before_page_open(self):
        files = minimal(metadata='<o:Attachments>A.xml</o:Attachments>')
        files['D/A.xml'] = xml('Attachments', '<o:Attachment ID="9"><o:FileLoc>P.xml</o:FileLoc></o:Attachment>')
        for order in itertools.permutations(('D/A.xml', 'D/P.xml')):
            with self.subTest(order=order), package(files, order) as (path, opened):
                with self.assertRaisesRegex(READER.ReadError, 'conflicting XML part'):
                    READER.read_ofd_structure(path)
                self.assertNotIn('D/P.xml', opened)

    def test_unknown_signed_value_does_not_hide_another_xml_member(self):
        files = minimal()
        files['Extra.xml'] = xml('Unknown', '<o:SignedValue>Supplement.xml</o:SignedValue>')
        files['Supplement.xml'] = text_page('独立登记')
        for order in itertools.permutations(('Extra.xml', 'Supplement.xml')):
            with self.subTest(order=order), package(files, order) as (path, opened):
                result = READER.read_ofd_structure(path)
                self.assertIn('Supplement.xml', opened)
                self.assertEqual(result['structure']['opaque_payloads'], [])
                self.assertTrue(any(item['text'] == '独立登记' for item in result['text_sources']))

    def test_unreferenced_signature_root_cannot_hide_payload(self):
        files = minimal()
        files['Extra.xml'] = xml('Signature', '<o:SignedValue>Supplement.xml</o:SignedValue>')
        files['Supplement.xml'] = text_page('存储文字')
        with package(files) as (path, opened):
            result = READER.read_ofd_structure(path)
        self.assertIn('Supplement.xml', opened)
        self.assertEqual(result['structure']['opaque_payloads'], [])

    def test_active_font_payload_stays_opaque_but_wrong_hierarchy_does_not(self):
        for valid in (True, False):
            files = minimal(common='<o:PublicRes>R.xml</o:PublicRes>')
            inner = '<o:Fonts><o:Font ID="8"><o:FontFile>font.xml</o:FontFile></o:Font></o:Fonts>'
            if not valid:
                inner = '<o:Wrapper>' + inner + '</o:Wrapper>'
            files['D/R.xml'] = xml('Res', inner)
            files['D/font.xml'] = text_page('保留') if not valid else b'opaque font'
            with self.subTest(valid=valid), package(files) as (path, opened):
                result = READER.read_ofd_structure(path)
                self.assertEqual('D/font.xml' not in opened, valid)
                self.assertEqual(any(item['source'] == 'D/font.xml' for item in result['structure']['opaque_payloads']), valid)


if __name__ == '__main__':
    unittest.main()
