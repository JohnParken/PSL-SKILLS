"""Synthetic public-entry object parity tests; no user documents or render claims."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from read_wps import read_bytes
import wps_ooxml as core
import wps_ooxml_charts as charts
import wps_ooxml_objects as objects
from test_wps_ooxml import package

C = charts.C
A = charts.A
R = core.R
WPS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'


def box(text):
    return '<w:txbxContent><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:txbxContent>'


def alternative(choice, fallback, requires='q', namespace=WPS):
    return ('<mc:AlternateContent xmlns:mc="' + objects.MC + '" xmlns:q="' + namespace + '">'
            '<mc:Choice Requires="' + requires + '">' + choice + '</mc:Choice>'
            + ('<mc:Fallback>' + fallback + '</mc:Fallback>' if fallback is not None else '') + '</mc:AlternateContent>')


def chart_xml():
    return ('<c:chartSpace xmlns:c="' + C + '" xmlns:a="' + A + '"><c:chart><c:title><c:tx><c:rich>'
            '<a:p><a:r><a:t>Visible title</a:t></a:r></a:p></c:rich></c:tx></c:title><c:plotArea><c:barChart>'
            '<c:ser><c:tx><c:v>Series A</c:v></c:tx><c:cat><c:strRef><c:f>Sheet1!A1:A2</c:f>'
            '<c:strCache><c:ptCount val="2"/><c:pt idx="0"><c:v>One</c:v></c:pt>'
            '<c:pt idx="1"><c:v>Two</c:v></c:pt></c:strCache></c:strRef></c:cat>'
            '<c:val><c:numRef><c:f>Sheet1!B1:B2</c:f><c:numCache><c:formatCode>0.00%</c:formatCode>'
            '<c:ptCount val="2"/><c:pt idx="0"><c:v>0.1250</c:v></c:pt>'
            '<c:pt idx="1"><c:v>0002.500</c:v></c:pt></c:numCache></c:numRef></c:val></c:ser>'
            '</c:barChart></c:plotArea></c:chart><c:externalData/></c:chartSpace>')


def native_package(payload=None, target='charts/chart1.xml', mode='Internal', repeat=1):
    reference = '<c:chart xmlns:c="' + C + '" xmlns:r="' + R + '" r:id="chart"/>'
    body = '<w:p><w:r><w:t>Body</w:t></w:r><w:drawing>' + reference * repeat + '</w:drawing></w:p>'
    data = package(body, [('word/_rels/document.xml.rels', '<Relationships xmlns="' + core.RELATIONSHIPS + '">'
                          '<Relationship Id="chart" Type="' + R + '/chart" Target="' + target
                          + '" TargetMode="' + mode + '"/></Relationships>'),
                         ('word/charts/chart1.xml', chart_xml() if payload is None else payload)])
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(result, 'w') as output:
        for name in source.namelist():
            value = source.read(name)
            if name == '[Content_Types].xml':
                value = value.replace(b'</Types>', ('<Override PartName="/word/charts/chart1.xml" ContentType="'
                                                   + charts.CHART_TYPE + '"/></Types>').encode())
            output.writestr(name, value)
    return result.getvalue()


class OoxmlObjectTests(unittest.TestCase):
    """Exercise guarded native chart extraction and namespace-scoped alternatives."""

    def test_chart_content_and_maximum_preserve_raw_values_and_missing_scope(self):
        for mode in ('content', 'maximum'):
            result = read_bytes(native_package(), reading_mode=mode)
            chart = result['ooxml_charts']['charts'][0]
            self.assertEqual(chart['title']['rich_text'], ['Visible title'])
            self.assertEqual(chart['series'][0]['name']['literal_values'], ['Series A'])
            self.assertEqual([p['raw_value'] for p in chart['series'][0]['categories']['caches'][0]['points']], ['One', 'Two'])
            self.assertEqual([p['raw_value'] for p in chart['series'][0]['values']['caches'][0]['points']], ['0.1250', '0002.500'])
            self.assertIn('external_or_embedded_workbook_not_read', chart['limitations'])
            self.assertFalse(chart['current_values_verified'])
        self.assertNotIn('ooxml_charts', read_bytes(native_package(), reading_mode='standard'))

    def test_scoped_requires_selects_choice_and_preserves_independent_duplicates(self):
        choice = '<w:drawing>' + box('Choice') + '</w:drawing>'
        fallback = '<w:pict>' + box('Fallback') + '</w:pict>'
        body = '<w:p>' + alternative(choice, fallback) * 2 + '</w:p>'
        result = read_bytes(package(body))
        self.assertEqual([s['text'] for s in result['related_stories']], ['Choice', 'Choice'])
        self.assertEqual(result['ooxml_compatibility'][0]['selected_count'], 2)
        unknown = alternative(choice, fallback, namespace='urn:unsupported')
        result = read_bytes(package('<w:p>' + unknown + '</w:p>'))
        self.assertEqual([s['text'] for s in result['related_stories']], ['Fallback'])
        # A shadowed prefix must not inherit the outer binding.
        unknown = alternative(choice, fallback).replace('<mc:Choice ', '<mc:Choice xmlns:q="urn:shadowed" ')
        result = read_bytes(package('<w:p>' + unknown + '</w:p>'))
        self.assertEqual([s['text'] for s in result['related_stories']], ['Fallback'])

    def test_chart_requires_and_deleted_unknown_branch(self):
        body = '<w:p>' + alternative(box('Chart namespace'), box('Fallback'), namespace=C) + '</w:p>'
        result = read_bytes(package(body))
        self.assertEqual(result['related_stories'][0]['text'], 'Chart namespace')
        body = '<w:del><w:p>' + alternative(box('Old'), None, namespace='urn:unknown') + '</w:p></w:del><w:p/>'
        result = read_bytes(package(body))
        self.assertTrue(result['current_view_available'])
        self.assertFalse(any(item.get('code') == 'alternate_content_unresolved' for item in result['diagnostics']))

    def test_chart_in_referenced_header_uses_header_relationships(self):
        data = native_package()
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, 'w') as target:
            for name in source.namelist():
                value = source.read(name)
                if name == 'word/document.xml':
                    header = value.replace(b'w:document', b'w:hdr').replace(b'<w:body>', b'').replace(b'</w:body>', b'')
                    target.writestr('word/header1.xml', header)
                    value = ('<w:document xmlns:w="' + core.W + '" xmlns:r="' + R + '"><w:body><w:p/>'
                             '<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr></w:body></w:document>').encode()
                elif name == 'word/_rels/document.xml.rels':
                    target.writestr('word/_rels/header1.xml.rels', value)
                    value = ('<Relationships xmlns="' + core.RELATIONSHIPS + '"><Relationship Id="h" Type="'
                             + R + '/header" Target="header1.xml"/></Relationships>').encode()
                elif name == '[Content_Types].xml':
                    value = value.replace(b'</Types>', ('<Override PartName="/word/header1.xml" ContentType="'
                                                       + core.RELATED_TYPES['header'][1] + '"/></Types>').encode())
                target.writestr(name, value)
        result = read_bytes(output.getvalue())
        chart = result['ooxml_charts']['charts'][0]
        self.assertEqual(chart['source']['part'], 'word/header1.xml')
        self.assertEqual(chart['title']['rich_text'], ['Visible title'])

    def test_unknown_without_fallback_is_not_a_verified_current_view(self):
        result = read_bytes(package('<w:p>' + alternative(box('Stored'), None, namespace='urn:unknown') + '</w:p>'))
        self.assertFalse(result['current_view_available'])
        self.assertEqual(result['related_stories'][0]['source']['alternate_content'][0]['selection'], 'unresolved')
        self.assertIn('alternate_content_unresolved', [i.get('code') for i in result['diagnostics']])

    def test_revision_excludes_deleted_objects_and_nonselected_bad_chart(self):
        body = '<w:del><w:p>' + box('Deleted') + '</w:p></w:del><w:p>' + box('Current') + '</w:p>'
        result = read_bytes(package(body))
        self.assertEqual([s['text'] for s in result['related_stories']], ['Current'])
        self.assertEqual([s['text'] for s in result['revision_content']['content']['related_stories']], ['Deleted', 'Current'])
        bad = '<c:chart xmlns:c="' + C + '"/>'
        result = read_bytes(package('<w:p>' + alternative(box('Current'), bad) + '</w:p>'))
        self.assertEqual(result['ooxml_charts']['charts'], [])

    def test_chart_missing_corrupt_and_sparse_cache_are_partial(self):
        for payload in ('<bad>', '<wrong/>', chart_xml().replace('<c:pt idx="1"><c:v>0002.500</c:v></c:pt>', '')):
            result = read_bytes(native_package(payload))
            self.assertEqual(result['text'], 'Body')
            self.assertEqual(result['status'], 'partial')
            self.assertTrue(result['ooxml_charts']['charts'][0]['limitations'])
        sparse = chart_xml().replace('<c:pt idx="1"><c:v>0002.500</c:v></c:pt>', '')
        result = read_bytes(native_package(sparse))
        self.assertEqual(result['ooxml_charts']['charts'][0]['series'][0]['values']['caches'][0]['missing_index_ranges'],
                         [{'start': 1, 'end': 1}])
        result = read_bytes(native_package(target='charts/missing.xml'))
        self.assertIn('missing_chart_part_or_content_type', result['ooxml_charts']['charts'][0]['limitations'])

    def test_security_and_shared_output_budgets_fail_closed(self):
        for kwargs in ({'target': 'https://example.invalid/chart', 'mode': 'External'},
                       {'target': '../../escape.xml'}, {'payload': '<!DOCTYPE x [<!ENTITY e "x">]><x>&e;</x>'}):
            result = read_bytes(native_package(**kwargs))
            self.assertNotIn(result["status"], ("ok", "partial"))
            self.assertIsNone(result["text"])
        for field, limit in (('MAX_CHART_POINTS', 6), ('MAX_CHART_RECORDS', 4), ('MAX_CHART_REFERENCES', 1), ('MAX_CHART_TEXT_CHARS', 5)):
            with patch.object(charts, field, limit):
                result = read_bytes(native_package(repeat=2))
            self.assertEqual(result["status"], "resource_limit")
        with patch.object(core, 'MAX_XML_ELEMENTS', 10):
            result = read_bytes(native_package())
        self.assertEqual(result['status'], 'resource_limit')

    def test_public_cli_preserves_chart_json_and_partial_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'input.wps').write_bytes(native_package())
            command = [sys.executable, '-I', '-S', '-B', str(Path(core.__file__).with_name('read_wps.py')), './input.wps', '--profile', 'full']
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True, encoding='utf-8', check=False)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(json.loads(result.stdout)['ooxml_charts']['charts'][0]['title']['rich_text'], ['Visible title'])


if __name__ == '__main__':
    unittest.main()
