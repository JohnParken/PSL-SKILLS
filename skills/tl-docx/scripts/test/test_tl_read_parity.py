"""Regressions for heading inheritance, note anchors and selected report scope."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tl_docx import arguments, read_result, encode
from tl_read import report
from tl_read_selection import selected_issues, select_body
from test_tl_read import MemoryPackage

WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def fixture():
    """Build explicit independent XML expectations rather than using a creator."""
    styles = f'''<w:styles xmlns:w="{WORD}">
      <w:style w:styleId="Base" w:type="paragraph"><w:name w:val="Heading 1"/></w:style>
      <w:style w:styleId="Inherited" w:type="paragraph"><w:basedOn w:val="Base"/></w:style>
      <w:style w:styleId="CycleA"><w:basedOn w:val="CycleB"/></w:style>
      <w:style w:styleId="CycleB"><w:basedOn w:val="CycleA"/></w:style>
      <w:style w:styleId="Body"><w:basedOn w:val="Base"/><w:pPr><w:outlineLvl w:val="9"/></w:pPr></w:style>
    </w:styles>'''
    document = f'''<w:document xmlns:w="{WORD}"><w:body>
      <w:p><w:pPr><w:pStyle w:val="Inherited"/></w:pPr><w:r><w:t> First </w:t></w:r></w:p>
      <w:p><w:r><w:t>Body</w:t><w:footnoteReference w:id="3"/><w:endnoteReference w:id="4"/>
        <w:commentReference w:id="5"/></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>One cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      <w:p><w:pPr><w:pStyle w:val="Inherited"/></w:pPr><w:r><w:t>Second</w:t><w:object/></w:r></w:p>
      <w:p><w:pPr><w:pStyle w:val="Body"/></w:pPr><w:r><w:t>Not heading</w:t></w:r></w:p>
      <w:p><w:pPr><w:pStyle w:val="CycleA"/></w:pPr><w:r><w:t>Cycle</w:t></w:r></w:p>
    </w:body></w:document>'''
    package = MemoryPackage({'word/document.xml': document, 'word/styles.xml': styles})
    package.sha256 = 'a' * 64
    return package


def read(package, *flags):
    """Run the same argument and report construction used by the CLI."""
    return read_result(package, arguments(['read', 'fixture.docx', *flags]))


class ReadParityTests(unittest.TestCase):
    """Check selected-view semantics and preserve global diagnostic evidence."""

    def test_heading_inheritance_and_explicit_body_level(self):
        records, issues, _ = report(fixture())
        paragraphs = {item['text']: item for item in records if item['type'] == 'paragraph'}
        self.assertEqual(1, paragraphs[' First ']['heading_level'])
        self.assertNotIn('heading_level', paragraphs['Not heading'])
        self.assertNotIn('heading_level', paragraphs['Cycle'])
        self.assertIn('heading_style_inheritance_cycle', {item['code'] for item in issues})

    def test_direct_outline_overrides_style(self):
        package = fixture()
        raw = package._parts['word/document.xml']
        package._parts['word/document.xml'] = raw.replace(b'<w:pStyle w:val="Inherited"/>', b'<w:pStyle w:val="Inherited"/><w:outlineLvl w:val="2"/>', 1)
        records, _, _ = report(package)
        self.assertEqual(3, next(item for item in records if item.get('text') == ' First ')['heading_level'])

    def test_note_and_comment_reference_locations(self):
        records, _, _ = report(fixture())
        references = [item for item in records if item['type'] == 'reference']
        self.assertEqual(['footnoteReference', 'endnoteReference', 'commentReference'], [item['kind'] for item in references])
        self.assertEqual(['3', '4', '5'], [item['id'] for item in references])
        self.assertTrue(all(item['story_kind'] == 'main' and '/w:p[2]/' in item['path'] for item in references))

    def test_summary_returns_all_headings_without_body(self):
        result = read(fixture(), '--summary')
        self.assertEqual([' First ', 'Second'], [item['text'] for item in result['records']])
        self.assertEqual(2, result['summary']['heading_count'])
        self.assertFalse(result['summary']['content_records_included'])
        self.assertEqual(1, result['coverage']['tables']['count'])

    def test_section_statistics_and_diagnostics_are_local(self):
        result = read(fixture(), '--section', ' First ')
        self.assertEqual('ok', result['status'])
        self.assertEqual(1, result['coverage']['tables']['count'])
        self.assertEqual(3, result['coverage']['counts']['reference'])
        self.assertEqual([], result['issues'])
        self.assertTrue(result['document_issues'])
        self.assertFalse(result['coverage']['document_complete'])
        self.assertNotIn('Second', [item.get('text') for item in result['records']])
        other = read(fixture(), '--section', 'Second')
        self.assertEqual('partial', other['status'])
        self.assertEqual(0, other['coverage']['tables']['count'])
        self.assertEqual(1, other['document_coverage']['tables']['count'])

    def test_summary_paging_is_complete_and_binds_view(self):
        package = fixture()
        full = read(package, '--summary')
        # Pick a budget between one and two headings rather than hard-code field sizes.
        budget = len(encode(full)) - 1
        first = read(package, '--summary', '--max-output-bytes', str(budget))
        self.assertTrue(first['has_more'])
        last = read(package, '--summary', '--cursor', first['next_cursor'])
        self.assertEqual(full['records'], first['records'] + last['records'])
        self.assertFalse(last['has_more'])
        with self.assertRaisesRegex(ValueError, 'another document or view'):
            read(package, '--headings', '--cursor', first['next_cursor'])

    def test_last_section_does_not_include_related_chart_series(self):
        main = 'word/document.xml'
        records = [
            {'type': 'paragraph', 'part': main, 'path': '/w:document[1]/w:body[1]/w:p[1]', 'text': 'Only', 'heading_level': 1},
            {'type': 'story', 'part': 'word/header1.xml', 'path': '/', 'story_kind': 'header'},
            {'type': 'chart_series', 'part': 'word/charts/chart1.xml', 'path': '/'},
        ]
        result = select_body(records, main, arguments(['read', 'fixture.docx', '--section', 'Only']))
        self.assertEqual([records[0]], result)

    def test_unlocated_or_boundary_issues_are_not_silenced(self):
        issues = [{'code': 'unlocated', 'part': 'word/document.xml'},
                  {'code': 'alternate_content_branch_unavailable', 'part': 'word/document.xml', 'path': '/missing'}]
        self.assertEqual(issues, selected_issues(issues, [], 'word/document.xml'))


if __name__ == '__main__':
    unittest.main()
