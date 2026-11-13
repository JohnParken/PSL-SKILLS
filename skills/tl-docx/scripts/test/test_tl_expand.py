"""Public CLI coverage for advanced report export and template substitutions."""

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document

ENTRY = Path(__file__).resolve().parents[1] / 'tl_docx.py'
sys.path.insert(0, str(ENTRY.parent))

from tl_report_objects import locate_series


class AdvancedWorkflows(unittest.TestCase):
    """Verify report/export byte equality and guarded non-target preservation."""

    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='tl-docx-advanced-')
        self.addCleanup(work.cleanup)
        self.task = Path(work.name)

    def invoke(self, *args, payload=None, status=0):
        """Run the public entry in a fresh task directory."""
        config = {'commonParam': [{'name': 'questionId', 'value': 'adv'}]}
        process = subprocess.run([sys.executable, '-I', '-B', str(ENTRY), *args], cwd=self.task,
                                 env=dict(os.environ, PATH='', ROUTER_CONFIG_VARIABLES=json.dumps(config)),
                                 input=json.dumps(payload).encode() if payload is not None else None,
                                 capture_output=True, check=False, timeout=30)
        self.assertEqual(status, process.returncode, process.stderr.decode())
        return process.stdout

    def document(self):
        """Create an independent fixture with a merged header and repeated titles."""
        doc = Document()
        doc.add_heading('相同标题', 1)
        doc.add_paragraph('first')
        table = doc.add_table(rows=4, cols=2)
        table.cell(0, 0).merge(table.cell(0, 1)).text = 'Group'
        table.cell(1, 0).text = 'Left'
        table.cell(1, 1).text = 'Right'
        table.cell(2, 0).merge(table.cell(3, 0)).text = 'Region'
        doc.add_heading('相同标题', 1)
        doc.add_paragraph('second')
        doc.save(self.task / 'fixture.docx')

    def test_precise_table_report_and_compact(self):
        self.document()
        result = json.loads(self.invoke('read', 'fixture.docx', '--report', '--table-header', '1:2'))
        table = next(item for item in result['records'] if item['type'] == 'table')
        self.assertEqual('explicit', table['header']['source'])
        self.assertEqual([{'column': 1, 'labels': ['Group', 'Left']}, {'column': 2, 'labels': ['Group', 'Right']}], table['header']['columns'])
        self.assertEqual([{'row': 3, 'column': 1, 'column_span': 1, 'row_span': 2, 'label': 'Region'}], table['merged_groups'])
        row = next(item for item in result['records'] if item['type'] == 'table_row' and item['row_index'] == 4)
        self.assertEqual({'row': 3, 'column': 1, 'label': 'Region'}, row['cells'][0]['merge_anchor'])
        compact = json.loads(self.invoke('read', 'fixture.docx', '--compact-json'))
        self.assertEqual(2, compact['table_rows_omitted'])
        rows = json.loads(self.invoke('read', 'fixture.docx', '--compact-json', '--tables', 'rows'))
        self.assertEqual(4, sum(item['type'] == 'table_row' for item in rows['records']))
        self.invoke('read', 'fixture.docx', '--table-header', '1:5', status=1)
        self.invoke('read', 'fixture.docx', '--table-header', '2:1', status=1)

    def test_report_export_and_archive_copy_match_stdout(self):
        self.document()
        flags = ('--report', '--table-header', '1:2', '--max-output-bytes', '4000')
        original = (self.task / 'fixture.docx').read_bytes()
        stdout = self.invoke('read', 'fixture.docx', *flags)
        receipt = json.loads(self.invoke('read', 'fixture.docx', *flags, '-o', 'report.json', '--delivery'))
        self.assertEqual('exported', receipt['status'])
        self.assertEqual('success', receipt['archive_status'])
        self.assertEqual(stdout, (self.task / 'report.json').read_bytes())
        self.assertEqual(stdout, (self.task / 'output/adv/report.json').read_bytes())
        self.assertEqual(json.loads(stdout)['next_cursor'], receipt['result']['next_cursor'])
        retry = json.loads(self.invoke('archive', 'report.json', '--sha256', receipt['sha256']))
        self.assertEqual('already_archived', retry['files'][0]['archive_status'])
        self.invoke('read', 'fixture.docx', '-o', 'report.json', status=1)
        self.invoke('read', 'fixture.docx', '-o', '../escape.json', status=1)
        self.assertEqual(original, (self.task / 'fixture.docx').read_bytes())
        self.assertEqual([], list(self.task.glob('.tl-docx-*.cleanup.json')))

    def test_repeated_heading_occurrence_and_text_export(self):
        self.document()
        self.invoke('read', 'fixture.docx', '--section', '相同标题', status=1)
        flags = ('--section', '相同标题', '--section-occurrence', '2', '--format', 'text')
        expected = '相同标题\nsecond\n'.encode()
        self.assertEqual(expected, self.invoke('read', 'fixture.docx', *flags))
        receipt = json.loads(self.invoke('read', 'fixture.docx', *flags, '-o', 'excerpt.txt'))
        self.assertEqual('not_required', receipt['archive_status'])
        self.assertEqual(expected, (self.task / 'excerpt.txt').read_bytes())

    def test_raw_xml_member_is_byte_exact_and_binary_members_are_refused(self):
        self.document()
        member = 'word/styles.xml'
        with ZipFile(self.task / 'fixture.docx') as archive:
            original = archive.read(member)
        flags = ('--format', 'xml', '--xml-part', member)
        self.assertEqual(original, self.invoke('read', 'fixture.docx', *flags))
        receipt = json.loads(self.invoke('read', 'fixture.docx', *flags, '-o', 'styles.xml'))
        self.assertEqual(member, receipt['result']['part'])
        self.assertEqual(original, (self.task / 'styles.xml').read_bytes())
        self.invoke('read', 'fixture.docx', '--format', 'xml', '--xml-part', 'docProps/thumbnail.jpeg', status=1)

    def test_validate_export_preserves_strict_pagination_status(self):
        self.invoke('create', 'fixture.docx', payload={'blocks': [{'type': 'page_break'}]})
        receipt = json.loads(self.invoke('validate', 'fixture.docx', '--strict-pagination', '-o', 'audit.json', status=2))
        self.assertEqual('exported', receipt['status'])
        audit = json.loads((self.task / 'audit.json').read_bytes())
        self.assertIn('LEADING_PAGE_BREAK', {item['code'] for item in audit['layout']['pagination_warnings']})

    def test_external_chart_coordinate_drops_workbook_name_and_raw_formula(self):
        series = {'value_formula': "'[private-workbook.xlsx]Sheet 1'!$B$2:$B$3", 'category_formula': '[private-workbook.xlsx]Sheet2!A1:A2'}
        points = [{'index': 0, 'value': '1'}, {'index': 1, 'value': None}]
        locate_series(series, points)
        self.assertEqual('Sheet 1', series['value_reference']['sheet'])
        self.assertTrue(series['value_reference']['external_workbook'])
        self.assertEqual('A2', points[1]['category_cell'])
        self.assertNotIn('private-workbook', json.dumps(series))
        self.assertEqual('B3', points[1]['value_cell'])
        self.assertIsNone(points[1]['value'])

    def test_replace_multiple_parts_and_verify_preservation(self):
        source = self.task / 'template.docx'
        doc = Document()
        doc.add_paragraph().add_run('{{公司}} / {{公司}}').bold = True
        doc.sections[0].header.paragraphs[0].text = '{{标题}}'
        doc.save(source)
        source_bytes = source.read_bytes()
        spec = {'replacements': [{'old': '{{公司}}', 'new': '甲 & 乙', 'count': 2}, {'old': '{{标题}}', 'new': ' 标题 '}],
                'parts': ['word/document.xml', 'word/header1.xml'], 'require_no_placeholders': True}
        result = json.loads(self.invoke('replace', 'template.docx', 'filled.docx', payload=spec))
        self.assertEqual('edited', result['status'])
        self.assertEqual([2, 1], result['result']['rule_counts'])
        filled = Document(self.task / 'filled.docx')
        self.assertEqual('甲 & 乙 / 甲 & 乙', filled.paragraphs[0].text)
        self.assertTrue(filled.paragraphs[0].runs[0].bold)
        self.assertEqual(' 标题 ', filled.sections[0].header.paragraphs[0].text)
        with ZipFile(source) as before, ZipFile(self.task / 'filled.docx') as after:
            changed = [name for name in before.namelist() if before.read(name) != after.read(name)]
        self.assertCountEqual(spec['parts'], changed)
        self.assertEqual(source_bytes, source.read_bytes())

    def test_replacement_guards_refuse_split_overlapping_and_wrong_counts(self):
        doc = Document()
        paragraph = doc.add_paragraph('alpha')
        paragraph.add_run('{{split')
        paragraph.add_run('}}')
        doc.save(self.task / 'input.docx')
        cases = [
            [{'old': 'alpha', 'new': 'new', 'count': 2}],
            [{'old': 'alpha', 'new': 'one'}, {'old': 'pha', 'new': 'two'}],
            [{'old': '{{split}}', 'new': 'one'}],
        ]
        for rules in cases:
            with self.subTest(rules=rules):
                self.invoke('replace', 'input.docx', 'bad.docx', payload={'replacements': rules}, status=1)
                self.assertFalse((self.task / 'bad.docx').exists())
        spec = {'replacements': [{'old': 'alpha', 'new': 'new'}], 'require_no_placeholders': True}
        self.invoke('replace', 'input.docx', 'bad.docx', payload=spec, status=1)
        self.assertFalse((self.task / 'bad.docx').exists())


if __name__ == '__main__':
    unittest.main()
