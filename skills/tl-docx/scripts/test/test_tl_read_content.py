"""Content projection retains content while removing source-proven duplicates."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
import unittest

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_docx import arguments, read_result
from tl_package import Package
from tl_read_content import content_records


def record(kind, path, **fields):
    """Use explicit source identities for independent projection fixtures."""
    return {'type': kind, 'part': 'word/document.xml', 'path': path, **fields}


class ContentTests(unittest.TestCase):
    """Validate semantic projection and public cursor option bindings."""

    def sample(self):
        """Build a real DOCX with body, table, nested table and header content."""
        doc = Document()
        doc.add_heading('First', 1)
        doc.add_paragraph('Body text')
        table = doc.add_table(rows=3, cols=2)
        for index, row in enumerate(table.rows):
            for column, cell in enumerate(row.cells):
                cell.text = f'R{index}C{column}'
        nested = table.cell(1, 0).add_table(rows=1, cols=1)
        nested.cell(0, 0).text = 'nested unique text'
        doc.sections[0].header.paragraphs[0].text = 'header unique text'
        doc.add_heading('Second', 1)
        for index in range(25):
            doc.add_paragraph('Long content ' + str(index) + 'x' * 50)
        stream = BytesIO()
        doc.save(stream)
        return Package(stream.getvalue())

    def test_no_text_only_deduplication(self):
        """Repeated actual paragraphs are not deduplicated by text equality."""
        records = [record('paragraph', '/p[1]', text='same'), record('paragraph', '/p[2]', text='same'),
                   record('textbox', '/p[3]/box', text='box text'),
                   record('paragraph', '/p[3]/box/p', text='box text'),
                   record('paragraph', '/p[4]', text='box text'), record('image', '/p[5]/image')]
        projected = content_records(records)
        self.assertEqual(['same', 'same', 'box text', 'box text'], [item['text'] for item in projected if 'text' in item])
        self.assertNotIn('part', projected[0])
        self.assertIn('OCR', projected[-1]['notice'])
        self.assertIn('part', content_records(records, details=True)[0])
        self.assertEqual(6, len(records))

    def test_nested_and_revision_content_preserved(self):
        """Nested table rows are separate; revision detail must not disappear."""
        records = [record('table_row', '/t/r', table_index=1, row_index=1,
                          cells=[{'column': 1, 'text': 'outer\ninner'}]),
                   record('paragraph', '/t/r/c/p', table_index=1, row_index=1, column=1, text='outer'),
                   record('table', '/t/r/c/t', table_index=2, parent_table_index=1, parent_row=1, parent_column=1),
                   record('table_row', '/t/r/c/t/r', table_index=2, row_index=1, cells=[{'column': 1, 'text': 'inner'}]),
                   record('paragraph', '/t/r/c/t/r/c/p', table_index=2, row_index=1, column=1, text='inner'),
                   record('paragraph', '/note', text='retained', revisions=[{'text': 'deleted'}])]
        snapshot = deepcopy(records)
        projected = content_records(records)
        self.assertEqual(['', 'inner'], [item['cells'][0]['text'] for item in projected if item['type'] == 'table_row'])
        self.assertEqual(['outer', 'retained'], [item['text'] for item in projected if item['type'] == 'paragraph'])
        self.assertEqual(snapshot, records)

    def test_real_table_story_and_section(self):
        """All table rows and related stories remain in the content view."""
        package = self.sample()
        result = read_result(package, arguments(['read', 'sample.docx', '--content']))
        rows = [item for item in result['records'] if item['type'] == 'table_row']
        self.assertEqual(4, len(rows))
        self.assertEqual(1, sum('nested unique text' in cell['text'] for item in rows for cell in item['cells']))
        self.assertTrue(any(item.get('text') == 'header unique text' for item in result['records']))
        self.assertEqual('content', result['view'])
        self.assertFalse(any(item['type'] in ('style', 'numbering_definition', 'section') for item in result['records']))
        selected = read_result(package, arguments(['read', 'sample.docx', '--content', '--section', 'First']))
        self.assertEqual(4, sum(item['type'] == 'table_row' for item in selected['records']))
        self.assertFalse(any(item.get('text') == 'header unique text' for item in selected['records']))
        self.assertIn('document_issues', selected)

    def test_pages_and_details_cursor_binding(self):
        """Pagination covers the projection once and rejects another view."""
        package = self.sample()
        full = read_result(package, arguments(['read', 'sample.docx', '--content']))
        args = arguments(['read', 'sample.docx', '--content', '--max-output-bytes', '4500'])
        first = read_result(package, args)
        self.assertTrue(first['has_more'])
        collected = list(first['records'])
        cursor = first['next_cursor']
        while cursor:
            args.cursor = cursor
            result = read_result(package, args)
            collected.extend(result['records'])
            cursor = result['next_cursor']
        self.assertEqual(full['records'], collected)
        for options in (['--full'], ['--content', '--details']):
            other = arguments(['read', 'sample.docx', *options, '--cursor', first['next_cursor']])
            with self.assertRaisesRegex(ValueError, 'another document or view'):
                read_result(package, other)

    def test_default_content_and_explicit_routes(self):
        """Default content equals its explicit spelling; full remains available."""
        package = self.sample()
        default = read_result(package, arguments(['read', 'sample.docx']))
        explicit = read_result(package, arguments(['read', 'sample.docx', '--content']))
        self.assertEqual(default, explicit)
        self.assertEqual('content', default['view'])
        full = read_result(package, arguments(['read', 'sample.docx', '--full']))
        self.assertEqual('full', full['view'])
        self.assertGreater(len(full['records']), len(default['records']))
        self.assertTrue(arguments(['read', 'sample.docx', '--details']).content)
        self.assertTrue(arguments(['read', 'sample.docx', '--table-header', '1:1']).content)
        for options in (['--summary'], ['--headings'], ['--report'], ['--compact-json'],
                        ['--format', 'text'], ['--format', 'xml'], ['--format', 'structure']):
            self.assertFalse(arguments(['read', 'sample.docx', *options]).content)
        for options in (['--full', '--content'], ['--full', '--report']):
            with self.assertRaises(SystemExit):
                arguments(['read', 'sample.docx', *options])

    def test_conflicting_modes(self):
        """Never silently reinterpret explicit structure or preview selections."""
        for options in (['--format', 'structure'], ['--format', 'xml'], ['--summary'], ['--headings'],
                        ['--compact-json'], ['--report'], ['--format', 'text']):
            with self.subTest(options=options), self.assertRaises(SystemExit):
                arguments(['read', 'sample.docx', '--content', *options])
        with self.assertRaises(SystemExit):
            arguments(['read', 'sample.docx', '--full', '--details'])


class ContentBoundaryTests(unittest.TestCase):
    """Projection never hides provenance or reorders nested cell prose."""

    def test_nested_cell_order_and_unknown_record(self):
        """Outer, nested and trailing content stay in original encounter order."""
        records = [record('table_row', '/t/r', table_index=1, row_index=1,
                          cells=[{'column': 1, 'text': 'Outer\nNested\nLast'}]),
                   record('paragraph', '/t/r/c/p[1]', table_index=1, row_index=1, column=1, text='Outer'),
                   record('table', '/t/r/c/t', table_index=2, parent_table_index=1, parent_row=1, parent_column=1),
                   record('table_row', '/t/r/c/t/r', table_index=2, row_index=1, cells=[{'column': 1, 'text': 'Nested'}]),
                   record('paragraph', '/t/r/c/t/r/c/p', table_index=2, row_index=1, column=1, text='Nested'),
                   record('paragraph', '/t/r/c/p[2]', table_index=1, row_index=1, column=1, text='Last'),
                   record('future_object', '/future', text='Unknown but real')]
        projected = content_records(records)
        observed = []
        for item in projected:
            if item.get('text'):
                observed.append(item['text'])
            for cell in item.get('cells', []):
                if cell.get('text'):
                    observed.append(cell['text'])
        self.assertEqual(['Outer', 'Nested', 'Last', 'Unknown but real'], observed)
        self.assertTrue(projected[0]['cells'][0]['content_in_following_records'])

    def test_unproven_nested_text_is_retained(self):
        """Never replace aggregate text if the known paragraphs cannot cover it."""
        records = [record('table_row', '/t/r', table_index=1, row_index=1,
                          cells=[{'column': 1, 'text': 'Unknown extra\nOuter\nNested'}]),
                   record('paragraph', '/t/r/c/p', table_index=1, row_index=1, column=1, text='Outer'),
                   record('table', '/t/r/c/t', table_index=2, parent_table_index=1, parent_row=1, parent_column=1)]
        projected = content_records(records)
        self.assertEqual('Unknown extra\nOuter\nNested', projected[0]['cells'][0]['text'])

    def test_diagnostic_summary_is_explicit_and_bounded(self):
        """Repeated geometry diagnostics fit content pages without pretending none occurred."""
        package = ContentTests().sample()
        raw = ([record('paragraph', '/p', text='retained')],
               [{'code': 'drawing_geometry_not_rendered', 'part': package.main, 'path': '/p/' + str(index)} for index in range(200)],
               {'status': 'partial', 'main_part': package.main})
        args = arguments(['read', 'sample.docx', '--content', '--max-output-bytes', '3000'])
        result = read_result(package, args, raw_report=raw)
        self.assertEqual('partial', result['status'])
        self.assertEqual(200, result['issues'][0]['count'])
        self.assertTrue(result['issues'][0]['details_available'])
        detailed = read_result(package, arguments(['read', 'sample.docx', '--content', '--details']), raw_report=raw)
        self.assertEqual(200, len(detailed['issues']))
        self.assertEqual('retained', detailed['records'][0]['text'])


    def test_content_cache_pages_release_at_end(self):
        """Content projection is repeatable across explicitly cached pages."""
        import os
        import tempfile
        from tl_docx import cached_read
        package = ContentTests().sample()
        full = read_result(package, arguments(['read', 'sample.docx', '--content']))
        with tempfile.TemporaryDirectory(prefix='tl-content-cache-') as temporary:
            root = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
            self.addCleanup(os.close, root)
            args = arguments(['read', 'sample.docx', '--content', '--cache', '--max-output-bytes', '4500'])
            result = cached_read(root, package, args)
            self.assertTrue(result['has_more'])
            token = result['cache']['token']
            records = list(result['records'])
            while result['has_more']:
                args = arguments(['read', 'sample.docx', '--content', '--cache-token', token,
                                  '--max-output-bytes', '4500', '--cursor', result['next_cursor']])
                result = cached_read(root, package, args)
                records.extend(result['records'])
            self.assertEqual(full['records'], records)
            self.assertEqual('released', result['cache']['status'])
            self.assertFalse(any(Path(temporary).iterdir()))


if __name__ == '__main__':
    unittest.main()
