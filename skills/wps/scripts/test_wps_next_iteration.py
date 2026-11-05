"""Independent bytes/XML for projections, accepted conversion and raw-format gates."""
from test_task_cli_support import run_task_cli
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import json
import subprocess
import sys

from test_wps_ooxml import package
from read_wps import read_bytes
from wps_selective import project_headings, project_section, project_paragraph_range
from docx_to_wps_rtf import convert_docx_to_rtf_wps, parse_docx_to_rtf_doc
from export_wps import convert_docx_to_wps_rtf
from run_wps_task import create_document
from wps_rtf_builder import build_document_from_spec


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def styled_package(body, styles):
    with zipfile.ZipFile(BytesIO(package(body))) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files['[Content_Types].xml'] = files['[Content_Types].xml'].replace(
        b'</Types>', b'<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    files['word/_rels/document.xml.rels'] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>').encode()
    files['word/styles.xml'] = ('<w:styles xmlns:w="' + W + '">' + styles + '</w:styles>').encode()
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


class NextIterationTests(unittest.TestCase):
    def test_selective_modes_reject_conflicting_views(self):
        script = Path(__file__).with_name('read_wps.py')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'in.rtf'
            source.write_bytes(br'{\rtf1\pard\outlinelevel0 Topic\par Body\par}')
            for options in (['--summary', '--section', 'Topic'], ['--heading-only', '--section', 'Topic']):
                result = run_task_cli([sys.executable, '-I', '-S', '-B', str(script), str(source)] + options,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stdout)

    def test_partial_duplicate_section_cli_retains_selection_diagnostics(self):
        script = Path(__file__).with_name('read_wps.py')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'in.rtf'
            source.write_bytes(br'{\rtf1{\header omitted}\pard\outlinelevel0 Topic\par\pard Body\par\outlinelevel0 Topic\par}')
            result = run_task_cli([sys.executable, '-I', '-S', '-B', str(script), str(source), '--section', 'Topic'],
                                    capture_output=True, text=True)
            data = json.loads(result.stdout)
            self.assertEqual(result.returncode, 3)
            self.assertEqual(data['selection_status'], 'ambiguous')
            self.assertEqual([item['index'] for item in data['candidates']], [1, 3])
            self.assertTrue(data['issues'])

    def test_simple_field_cache_and_carriage_return_are_preserved(self):
        body = ('<w:p><w:hyperlink><w:ins><w:r><w:t>A</w:t><w:cr/><w:t>B</w:t></w:r></w:ins></w:hyperlink>'
                '<w:fldSimple w:instr="DATE"><w:r><w:t>Cached</w:t></w:r></w:fldSimple>'
                '<w:moveFrom><w:r><w:t>OLD</w:t></w:r></w:moveFrom>'
                '<w:moveTo><w:sdt><w:sdtContent><w:r><w:t>NEW</w:t></w:r></w:sdtContent></w:sdt></w:moveTo></w:p>')
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'in.docx', Path(directory) / 'out.wps'
            source.write_bytes(package(body))
            result = convert_docx_to_wps_rtf(source, target)
            self.assertEqual(result['status'], 'partial', result)
            self.assertEqual(read_bytes(target.read_bytes())['text'], 'A\nBCachedNEW')
            self.assertEqual(result['source_readback']['reader_status'], 'partial')
            self.assertTrue(result['source_readback'].get('issues') or result['source_readback'].get('diagnostics'))

    def test_projection_retains_partial_omissions_and_diagnostics(self):
        source = {'status': 'partial', 'partial': True, 'issues': [{'code': 'gap'}],
                  'omitted_regions': [{'type': 'object'}], 'reading_assessment': {'outcome': 'partial'},
                  'paragraphs': [{'text': 'Topic', 'outline_level': 0}, {'text': 'Body'}]}
        for output in (project_headings(source), project_paragraph_range(source, '1:1'), project_section(source, 'Topic')):
            self.assertEqual(output['issues'], source['issues'])
            self.assertEqual(output['omitted_regions'], source['omitted_regions'])
            self.assertEqual(output['status'], 'partial')
            self.assertEqual(output['reading_assessment']['outcome'], 'partial')

    def test_structural_headings_and_duplicate_disambiguation(self):
        source = {'status': 'ok', 'paragraphs': [{'text': 'Topic', 'outline_level': 0}, {'text': 'Body Topic'},
                                               {'text': 'Topic', 'outline_level': 0}, {'text': 'Second'}]}
        ambiguous = project_section(source, 'Topic')
        self.assertEqual(ambiguous['selection_status'], 'ambiguous')
        self.assertEqual([item['index'] for item in ambiguous['candidates']], [1, 3])
        self.assertEqual(project_section(source, 'Topic', 3)['text'], 'Topic\nSecond')
        self.assertEqual(project_section(source, 'Body Topic')['status'], 'not_found')

    def test_rtf_stored_outline_outranks_numbering_heuristic(self):
        data = b'{\\rtf1\\pard\\outlinelevel1 Plain heading\\par\\pard Body\\par}'
        headings = project_headings(read_bytes(data))['headings']
        self.assertEqual((headings[0]['text'], headings[0]['level'], headings[0]['basis']), ('Plain heading', 2, 'stored_outline'))

    def test_hyperlinks_controls_and_accepted_revisions_keep_text(self):
        body = ('<w:p><w:r><w:t>A</w:t></w:r><w:hyperlink><w:r><w:t>B</w:t></w:r></w:hyperlink>'
                '<w:sdt><w:sdtContent><w:r><w:t>C</w:t></w:r></w:sdtContent></w:sdt>'
                '<w:ins><w:r><w:t>D</w:t></w:r></w:ins><w:del><w:r><w:delText>DROP</w:delText></w:r></w:del></w:p>'
                '<w:sdt><w:sdtContent><w:p><w:r><w:t>Z</w:t></w:r></w:p></w:sdtContent></w:sdt>')
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'in.docx', Path(directory) / 'out.wps'
            source.write_bytes(package(body))
            result = convert_docx_to_rtf_wps(str(source), str(target))
            self.assertEqual(read_bytes(target.read_bytes())['text'], 'ABCD\nZ')
            self.assertEqual(result['source_content_comparison'], 'normalized-readable-main-text-matched')

    def test_style_chain_direct_override_and_page_size(self):
        styles = ('<w:style w:type="paragraph" w:styleId="Base"><w:pPr><w:spacing w:line="560" w:lineRule="exact"/>'
                  '<w:ind w:firstLine="400"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:sz w:val="40"/><w:b/></w:rPr></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Custom"><w:basedOn w:val="Base"/>'
                  '<w:pPr><w:spacing w:after="120"/></w:pPr></w:style>')
        body = ('<w:p><w:pPr><w:pStyle w:val="Custom"/></w:pPr><w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>Heading</w:t></w:r></w:p>'
                '<w:sectPr><w:pgSz w:w="15840" w:h="12240"/></w:sectPr>')
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'in.docx', Path(directory) / 'out.wps'
            source.write_bytes(styled_package(body, styles))
            document = parse_docx_to_rtf_doc(str(source))
            paragraph = document.elements[0]
            self.assertEqual((paragraph.line_spacing_exact_pt, paragraph.first_line_indent_pt), (28, 20))
            self.assertEqual((paragraph.runs[0].size_pt, paragraph.runs[0].bold), (20, False))
            self.assertEqual(project_headings(read_bytes(source.read_bytes()))['headings'][0]['level'], 1)
            result = convert_docx_to_rtf_wps(str(source), str(target))
            self.assertEqual(read_bytes(target.read_bytes())['page_properties']['paperw'], 15840)
            self.assertEqual(result['format_validation']['status'], 'verified_raw_controls')

    def test_content_and_format_faults_never_overwrite_target(self):
        spec = {'blocks': [{'type': 'paragraph', 'text': 'Reliable'}]}
        rendered = build_document_from_spec(spec).render()
        for wrong in (rendered.replace('Reliable', 'Relia'), rendered.replace('\\sl-560', '\\sl-561')):
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'old.wps'
                target.write_bytes(b'old')
                with patch('wps_rtf_builder.RtfDocument.render', return_value=wrong), self.assertRaises(RuntimeError):
                    create_document(spec, target)
                self.assertEqual(target.read_bytes(), b'old')
                self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_conversion_valid_but_missing_text_is_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'in.docx', Path(directory) / 'out.wps'
            source.write_bytes(package('<w:p><w:r><w:t>Original</w:t></w:r></w:p>'))
            target.write_bytes(b'old')
            wrong = build_document_from_spec({'blocks': [{'type': 'paragraph', 'text': 'Missing'}]}).render()
            with patch('wps_rtf_builder.RtfDocument.render', return_value=wrong):
                result = convert_docx_to_wps_rtf(source, target)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(target.read_bytes(), b'old')
            self.assertEqual(sorted(item.name for item in Path(directory).iterdir()), ['in.docx', 'out.wps'])


class StyleAndEnvironmentTests(unittest.TestCase):
    def test_page_control_fault_preserves_target(self):
        spec = {'blocks': [{'type': 'paragraph', 'text': 'Page check'}]}
        rendered = build_document_from_spec(spec).render()
        wrong = rendered.replace('\\paperw11906', '\\paperw11907')
        self.assertNotEqual(rendered, wrong)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'old.wps'
            target.write_bytes(b'old')
            with patch('wps_rtf_builder.RtfDocument.render', return_value=wrong), self.assertRaises(RuntimeError):
                create_document(spec, target)
            self.assertEqual(target.read_bytes(), b'old')

    def test_embedded_newline_is_not_a_new_paragraph(self):
        document = build_document_from_spec({'blocks': [{'type': 'paragraph', 'text': 'A\nB'}]})
        rendered = document.render()
        self.assertIn('A\\line B', rendered)
        result = read_bytes(rendered.encode('ascii'))
        self.assertEqual([item['text'] for item in result['paragraphs']], ['A\nB'])

    def test_style_cycle_is_bounded_and_reports_metadata(self):
        from xml.etree.ElementTree import fromstring
        from wps_word_styles import WordStyles
        styles = fromstring(f'<w:styles xmlns:w="{W}"><w:style w:styleId="A"><w:basedOn w:val="A"/></w:style></w:styles>')
        resolver = WordStyles(styles)
        self.assertEqual(len(resolver.chain('A')), 1)
        self.assertEqual(resolver.issues[0]['code'], 'word_style_inheritance_unresolved')

    def test_style_toggle_and_direct_override(self):
        from xml.etree.ElementTree import fromstring
        from wps_word_styles import WordStyles
        styles = fromstring(f'<w:styles xmlns:w="{W}"><w:style w:styleId="A"><w:rPr><w:b/></w:rPr></w:style>'
                            '<w:style w:styleId="B"><w:basedOn w:val="A"/><w:rPr><w:b/></w:rPr></w:style></w:styles>')
        paragraph = fromstring(f'<w:p xmlns:w="{W}"><w:pPr><w:pStyle w:val="B"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p>')
        resolver = WordStyles(styles)
        run = paragraph.find(f'{{{W}}}r')
        self.assertEqual(resolver.properties(paragraph, run).find(f'{{{W}}}b').get(f'{{{W}}}val'), 'false')
        run.insert(0, fromstring(f'<w:rPr xmlns:w="{W}"><w:b/></w:rPr>'))
        self.assertEqual(resolver.properties(paragraph, run).find(f'{{{W}}}b').get(f'{{{W}}}val'), 'true')

    def test_rtf_check_does_not_probe_external_backend(self):
        from export_wps import check_environment
        with patch('export_wps.find_soffice', side_effect=AssertionError('external probe')):
            self.assertEqual(check_environment(backend='rtf')['status'], 'environment-ok')

    def test_final_paragraph_retains_outline(self):
        result = read_bytes(br'{\rtf1\ansi\outlinelevel0 Title}')
        self.assertEqual(project_headings(result)['headings'][0]['text'], 'Title')


if __name__ == '__main__':
    unittest.main()
