"""Synthetic OOXML recovery expectations, not real-producer acceptance evidence."""
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

import wps_ooxml as reader
import wps_word_visibility as visibility
from test_wps_hidden import run, styles_package
from test_wps_ooxml import package
from test_wps_ooxml_related import package as related_package, rel, root
from test_wps_strict_word import strict


def table(content):
    """Create one explicitly bounded table cell."""
    return ('<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc>'
            '<w:p>' + content + '</w:p></w:tc></w:tr></w:tbl>')


@unittest.skipUnless(reader.expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class OoxmlRecoveryTests(unittest.TestCase):
    """Only nonstructural visibility metadata may fall back to stored text."""

    def test_default_recovers_bad_direct_visibility_but_explicit_policies_refuse(self):
        for properties in ('<w:vanish w:val="maybe"/>', '<w:vanish/><w:vanish/>',
                           '<w:webHidden w:val="maybe"/>'):
            with self.subTest(properties=properties):
                data = package('<w:p>' + run('前') + run('保留', properties) + run('后') + '</w:p>')
                result = reader.read_bytes(data)
                self.assertEqual(result['status'], 'partial', result)
                self.assertEqual(result['text'], '前保留后')
                self.assertTrue(result['partial'])
                diagnostic = next(item for item in result['diagnostics'] if item['code'] == 'visibility_unknown')
                self.assertEqual(diagnostic['stream'], 'word/document.xml')
                self.assertIn('/r[', diagnostic['location'])
                self.assertIn('reason', diagnostic)
                self.assertEqual(result['visibility']['runs'][0]['state'], 'unknown')
                for policy in ('exclude', 'annotate'):
                    rejected = reader.read_bytes(data, hidden_text=policy)
                    self.assertEqual(rejected['status'], 'corrupt')
                    self.assertIsNone(rejected['text'])

    def test_duplicate_or_missing_styles_and_defaults_retain_text_with_partial(self):
        cases = (
            '<w:style w:type="character"/>',
            '<w:style w:styleId="A"/><w:style w:styleId="A"/>',
            '<w:style w:type="paragraph" w:styleId="A" w:default="1"/>'
            '<w:style w:type="paragraph" w:styleId="B" w:default="1"/>',
        )
        for styles in cases:
            with self.subTest(styles=styles):
                data = styles_package('<w:p>' + run('正文') + '</w:p>', styles)
                result = reader.read_bytes(data)
                self.assertEqual(result['text'], '正文', result)
                self.assertEqual(result['status'], 'partial')
                diagnostic = next(item for item in result['diagnostics'] if item['code'] == 'visibility_metadata_unresolved')
                self.assertEqual(diagnostic['stream'], 'word/styles.xml')
                self.assertTrue(diagnostic['location'].startswith('/styles/'))
                self.assertEqual(result['visibility']['runs'][0]['state'], 'unknown')
                self.assertIsNone(reader.read_bytes(data, hidden_text='exclude')['text'])

    def test_style_cycle_and_invalid_defaults_recover_without_guessing_visibility(self):
        cycle = ('<w:style w:type="character" w:styleId="A"><w:basedOn w:val="B"/></w:style>'
                 '<w:style w:type="character" w:styleId="B"><w:basedOn w:val="A"/></w:style>')
        invalid_default = '<w:docDefaults><w:rPrDefault><w:rPr><w:vanish w:val="maybe"/></w:rPr></w:rPrDefault></w:docDefaults>'
        for styles in (cycle, invalid_default):
            result = reader.read_bytes(styles_package('<w:p>' + run('正文', '<w:rStyle w:val="A"/>') + '</w:p>', styles))
            self.assertEqual(result['text'], '正文', result)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['visibility']['runs'][0]['state'], 'unknown')

    def test_recovered_style_ambiguity_does_not_invent_stored_headings(self):
        cases = (
            '<w:style w:styleId="A"><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
            '<w:style w:styleId="A"><w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>',
            '<w:style w:styleId="A"><w:basedOn w:val="A"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>',
        )
        for styles in cases:
            body = ('<w:p><w:pPr><w:pStyle w:val="A"/></w:pPr>' + run('Uncertain') + '</w:p>'
                    '<w:p><w:pPr><w:pStyle w:val="A"/><w:outlineLvl w:val="3"/></w:pPr>' + run('Direct') + '</w:p>')
            result = reader.read_bytes(styles_package(body, styles))
            self.assertEqual(result['text'], 'Uncertain\nDirect')
            self.assertEqual(result['paragraph_metadata'], [{'outline_level': None}, {'outline_level': 3}])
            self.assertTrue(result['heading_diagnostics'])
            self.assertEqual(result['status'], 'partial')

    def test_run_style_and_chain_budgets_remain_blocking(self):
        malformed = '<w:vanish w:val="maybe"/>'
        with patch.object(visibility, 'MAX_RUNS', 1):
            result = reader.read_bytes(package('<w:p>' + run('A', malformed) + run('B') + '</w:p>'))
            self.assertEqual(result['status'], 'resource_limit')
            self.assertIsNone(result['text'])
        with patch.object(visibility, 'MAX_STYLES', 1):
            result = reader.read_bytes(styles_package('<w:p/>', '<w:style/><w:style/>'))
            self.assertEqual(result['status'], 'resource_limit')
            self.assertIsNone(result['text'])
        styles = ('<w:style w:styleId="A"><w:basedOn w:val="B"/></w:style>'
                  '<w:style w:styleId="B"/>')
        with patch.object(visibility, 'MAX_CHAIN', 1):
            result = reader.read_bytes(styles_package('<w:p>' + run('A', '<w:rStyle w:val="A"/>') + '</w:p>', styles))
            self.assertEqual(result['status'], 'resource_limit')
            self.assertIsNone(result['text'])

    def test_recovery_applies_in_tables_and_related_stories_and_keeps_revision_view(self):
        malformed = '<w:vanish w:val="maybe"/>'
        body = ('<w:p>' + run('前') + '<w:del>' + run('旧', malformed) + '</w:del><w:ins>'
                + run('新', malformed) + '</w:ins></w:p>' + table(run('单元格', malformed))
                + '<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>')
        header = root('header', '<w:p>' + run('页眉', malformed) + '</w:p>' + table(run('页眉表', malformed)))
        data = related_package(body, rel('h', 'header', 'head.xml'), [('word/head.xml', 'header', header)])
        for revision, expected in (('accepted', '前新'), ('original', '前旧'), ('all', '前旧新')):
            with self.subTest(revision=revision):
                result = reader.read_bytes(data, revisions=revision, include_related=True)
                self.assertEqual(result['text'], expected + '\n单元格', result)
                self.assertEqual(result['related_stories'][0]['text'], '页眉\n页眉表')
                self.assertEqual(result['related_stories'][0]['status'], 'partial')
                self.assertEqual([item['rows'][0]['cells'][0]['text'] for item in result['tables']], ['单元格', '页眉表'])
                self.assertEqual(result['status'], 'partial')

    def test_hyphens_in_main_tables_related_and_strict_dialect(self):
        content = '<w:r><w:t>A</w:t><w:noBreakHyphen/><w:t>B</w:t><w:softHyphen/><w:t>C</w:t></w:r>'
        body = '<w:p>' + content + '</w:p>' + table(content) + '<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>'
        data = related_package(body, rel('h', 'header', 'head.xml'), [('word/head.xml', 'header', root('header', '<w:p>' + content + '</w:p>'))])
        expected = 'A\u2011B\u00adC'
        for payload in (data, strict(data)):
            result = reader.read_bytes(payload, include_related=True)
            self.assertEqual(result['text'], expected + '\n' + expected, result)
            self.assertEqual(result['tables'][0]['rows'][0]['cells'][0]['text'], expected)
            self.assertEqual(result['related_stories'][0]['text'], expected)
            self.assertEqual(result['status'], 'ok', result)

    def test_hyphens_do_not_leak_from_hidden_revisions_or_foreign_namespaces(self):
        content = '<w:r><w:rPr><w:vanish/></w:rPr><w:noBreakHyphen/><w:softHyphen/></w:r>'
        body = '<w:p>' + content + '<w:del><w:r><w:noBreakHyphen/></w:r></w:del>' + run('保留') + '</w:p>'
        self.assertEqual(reader.read_bytes(package(body), hidden_text='exclude')['text'], '保留')
        body = '<w:p><w:r><x:noBreakHyphen xmlns:x="urn:foreign"/>' + '<x:softHyphen xmlns:x="urn:foreign"/></w:r></w:p>'
        self.assertEqual(reader.read_bytes(package(body))['text'], '')

    def test_xml_zip_and_style_part_structure_still_block(self):
        source = styles_package('<w:p>' + run('正文') + '</w:p>', '<w:style/>')
        for replacement in (b'<broken>', b'<!DOCTYPE x [<!ENTITY e "x">]><x>&e;</x>', b'<wrong/>'):
            target = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(source)) as archive, zipfile.ZipFile(target, 'w') as output:
                for name in archive.namelist():
                    output.writestr(name, replacement if name == 'word/styles.xml' else archive.read(name))
            result = reader.read_bytes(target.getvalue())
            self.assertNotIn(result['status'], ('ok', 'partial'))
            self.assertIsNone(result['text'])
        self.assertIsNone(reader.read_bytes(package(extra=[('../escape', 'x')]))['text'])

    def test_cli_retains_partial_json_and_text_with_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.wps'
            path.write_bytes(package('<w:p>' + run('保留', '<w:vanish w:val="maybe"/>') + '</w:p>'))
            command = [sys.executable, '-I', '-S', '-B', str(Path(reader.__file__).with_name('read_wps.py')), './input.wps', '--reading-mode', 'standard']
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True, encoding='utf-8', check=False)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(json.loads(result.stdout)['text'], '保留')
            result = subprocess.run(command + ['--format', 'text'], cwd=directory, capture_output=True, text=True, encoding='utf-8', check=False)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(result.stdout, '保留')
            self.assertEqual(json.loads(result.stderr)['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
