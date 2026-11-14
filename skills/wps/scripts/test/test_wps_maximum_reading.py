"""Synthetic maximum-reading contracts, not WPS 12.1 producer evidence."""

import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.parsers import expat

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from read_wps import ParseError, read_bytes
from test_wps import document, synthetic_cfb


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"


def ooxml_with_textbox():
    body = (
        '<w:p><w:r><w:t>Main</w:t></w:r>'
        '<w:ins><w:r><w:t>New</w:t></w:r></w:ins>'
        '<w:del><w:r><w:delText>Old</w:delText></w:r></w:del></w:p>'
        '<w:p><w:r><w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
        '<v:textbox><w:txbxContent><w:p><w:r><w:t>Box text</w:t></w:r></w:p>'
        '</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>'
    )
    files = {
        '[Content_Types].xml': (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.wordprocessingml.document.main+xml"/></Types>'
        ),
        '_rels/.rels': (
            '<Relationships xmlns="' + P + '"><Relationship Id="main" Type="' + R
            + '/officeDocument" Target="word/document.xml"/></Relationships>'
        ),
        'word/document.xml': '<w:document xmlns:w="' + W + '"><w:body>' + body + '</w:body></w:document>',
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class MaximumReadingTests(unittest.TestCase):
    def test_ooxml_returns_all_revision_text_and_separate_textbox(self):
        data = ooxml_with_textbox()
        standard = read_bytes(data, reading_mode='standard')
        self.assertEqual(standard['text'], 'MainNew\n')
        self.assertNotIn('related_stories', standard)

        result = read_bytes(data, reading_mode='maximum')
        self.assertEqual(result['text'], 'MainNew\n')
        self.assertEqual(result['revision_content']['content']['text'], 'MainNewOld\n')
        self.assertEqual(result['reading_mode'], 'maximum')
        self.assertEqual(result['revisions'], 'accepted')
        textboxes = [story for story in result['related_stories'] if story['type'] == 'textboxes']
        self.assertEqual([story['text'] for story in textboxes], ['Box text'])
        self.assertEqual(textboxes[0]['anchor_status'], 'stored_textbox_subtree_only')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['maximum_reading']['producer_profile_verified'])

    def test_rtf_returns_related_destination_groups_separately(self):
        data = b'{\\rtf1\\ansi Main\\par{\\header Head\\par}{\\footer Foot\\par}{\\footnote Note\\par}}'
        standard = read_bytes(data, reading_mode='standard')
        self.assertEqual(standard['text'], 'Main')
        self.assertNotIn('related_stories', standard)

        result = read_bytes(data, reading_mode='maximum')
        self.assertEqual(result['text'], 'Main')
        self.assertEqual([story['type'] for story in result['related_stories']],
                         ['header', 'footer', 'footnote'])
        self.assertEqual([story['text'] for story in result['related_stories']],
                         ['Head', 'Foot', 'Note'])
        self.assertTrue(all(story['status'] == 'partial' for story in result['related_stories']))
        self.assertEqual(result['status'], 'partial')

    def test_ms_doc_uses_existing_bounded_recovery_and_related_stories(self):
        streams = document([(b'Main\r', True)], {80: 'Foot\r'.encode('utf-16le')})
        data = synthetic_cfb({path[0]: value for path, value in streams.items()})
        result = read_bytes(data, reading_mode='maximum')
        self.assertEqual(result['text'], 'Main\n')
        self.assertEqual(result['related_stories'][0]['text'], 'Foot\n')
        self.assertEqual(result['text_policy'], 'recover')
        self.assertEqual(result['field_policy'], 'cached_result')
        self.assertEqual(result['object_policy'], 'placeholder')

    def test_conflicting_maximum_policies_are_rejected(self):
        with self.assertRaises(ParseError):
            read_bytes(ooxml_with_textbox(), reading_mode='maximum', revisions='original')
        with self.assertRaises(ParseError):
            read_bytes(b'{\\rtf1 Text}', reading_mode='maximum', object_policy='reject')

    def test_cli_rejects_lossy_projections_before_reading_input(self):
        script = str(Path(__file__).resolve().parents[1] / 'read_wps.py')
        for extra in (('--format', 'text'), ('--summary',), ('--heading-only',)):
            process = subprocess.run(
                [sys.executable, '-I', '-S', '-B', script, 'missing.wps',
                 '--reading-mode', 'maximum', *extra],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(process.returncode, 2)
            self.assertEqual(process.stdout, '')
            self.assertIn('maximum', process.stderr)

    def test_cli_full_json_marks_mode(self):
        script = str(Path(__file__).resolve().parents[1] / 'read_wps.py')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.wps'
            path.write_bytes(b'{\\rtf1 Main\\par{\\header Head\\par}}')
            process = subprocess.run(
                [sys.executable, '-I', '-S', '-B', script, path.name,
                 '--reading-mode', 'maximum'],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(process.returncode, 3, process.stderr)
        self.assertIn('"reading_mode": "maximum"', process.stdout)


if __name__ == '__main__':
    unittest.main()
