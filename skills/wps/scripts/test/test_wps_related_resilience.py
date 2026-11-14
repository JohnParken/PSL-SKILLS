"""Synthetic coverage for independent content faults; not producer acceptance."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from test_wps_ooxml_related import package, paragraph, rel, root
import read_wps
import wps_ooxml


def rewrite(data, transform):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, 'w') as target:
        for name in source.namelist():
            new_name, text = transform(name, source.read(name).decode())
            target.writestr(new_name, text)
    return output.getvalue()


def header_package(header='<broken>'):
    body = paragraph('Intact body') + '<w:sectPr><w:headerReference w:type="default" r:id="h"/><w:footerReference w:type="default" r:id="f"/></w:sectPr>'
    return package(body, rel('h', 'header', 'h.xml') + rel('f', 'footer', 'f.xml'),
                   [('word/h.xml', 'header', header), ('word/f.xml', 'footer', root('footer', paragraph('Intact footer')))])


@unittest.skipUnless(wps_ooxml.expat.version_info >= (2, 6), 'Expat >=2.6 required')
class RelatedResilienceTests(unittest.TestCase):
    def test_broken_header_preserves_body_and_footer(self):
        result = read_wps.read_bytes(header_package(), reading_mode='maximum')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['text'], 'Intact body')
        self.assertEqual([x['text'] for x in result['related_stories']], [None, 'Intact footer'])
        self.assertEqual(result['diagnostics'][0]['location'], 'word/h.xml')
        coverage = result['reading_assessment']['coverage']
        self.assertEqual(coverage[0]['state'], 'partially_extracted')
        self.assertTrue(any(x['category'] == 'unread_content' and x['state'] == 'not_decoded' for x in coverage))

    def test_default_reports_unrequested_regions_without_parsing_header(self):
        result = read_wps.read_bytes(header_package(), reading_mode='standard')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['text'], 'Intact body')
        self.assertNotIn('related_stories', result)
        self.assertEqual(result['reading_assessment']['coverage'][0]['state'], 'not_requested')
        self.assertIn('related_content_not_requested', [x['code'] for x in result['diagnostics']])

    def test_renamed_main_and_relative_related_part(self):
        def rename(name, text):
            names = {'word/document.xml': 'content/main.xml', 'word/_rels/document.xml.rels': 'content/_rels/main.xml.rels',
                     'word/h.xml': 'content/h.xml', 'word/f.xml': 'content/f.xml'}
            for old, new in names.items():
                text = text.replace(old, new)
            return names.get(name, name), text
        data = rewrite(header_package(root('header', paragraph('Header'))), rename)
        result = read_wps.read_bytes(data, reading_mode='maximum')
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['text'], 'Intact body')
        self.assertEqual(result['related_stories'][0]['source']['part'], 'content/h.xml')
        self.assertEqual(result['related_stories'][0]['references'][0]['source_part'], 'content/main.xml')

    def test_math_is_explicitly_unread_not_flattened(self):
        math = '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:r><m:t>2</m:t></m:r></m:den></m:f></m:oMath>'
        result = read_wps.read_bytes(package('<w:p><w:r><w:t>Ratio:</w:t></w:r>' + math + '</w:p>'), reading_mode='maximum')
        self.assertEqual(result['text'], 'Ratio:')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('math_content_not_decoded', [x['code'] for x in result['diagnostics']])

    def test_unknown_text_wrapper_is_not_silent(self):
        result = read_wps.read_bytes(package(paragraph('Known') + '<w:unknownContainer>' + paragraph('Uninterpreted') + '</w:unknownContainer>'), reading_mode='standard')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['text'], 'Known')
        self.assertIn('unknown_block_content_not_decoded', [x['code'] for x in result['diagnostics']])

    def test_xml_security_failure_is_not_recovered(self):
        result = read_wps.read_bytes(header_package('<!DOCTYPE x [<!ENTITY x "test">]><x>&x;</x>'), reading_mode='maximum')
        self.assertNotIn(result['status'], ('ok', 'partial'))
        self.assertIsNone(result['text'])

    def test_failed_regions_still_consume_output_budget(self):
        with patch.object(wps_ooxml, 'MAX_RELATED_REGIONS', 1):
            result = read_wps.read_bytes(header_package(), reading_mode='maximum')
        self.assertEqual(result['status'], 'resource_limit')
        self.assertIsNone(result['text'])

    def test_core_body_corruption_is_not_recovered(self):
        data = rewrite(header_package(), lambda name, text: (name, '<bad>' if name == 'word/document.xml' else text))
        result = read_wps.read_bytes(data, reading_mode='maximum')
        self.assertEqual(result['status'], 'corrupt')
        self.assertIsNone(result['text'])

    def test_external_and_escape_main_targets_stay_rejected(self):
        for target in ('../outside.xml', 'https://example.invalid/doc.xml'):
            data = rewrite(package(paragraph('body')), lambda name, text: (name, text.replace('Target="word/document.xml"', 'Target="' + target + '"') if name == '_rels/.rels' else text))
            self.assertNotIn(read_wps.read_bytes(data, reading_mode='standard')['status'], ('ok', 'partial'))


if __name__ == '__main__':
    unittest.main()
