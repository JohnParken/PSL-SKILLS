"""Independently specified synthetic OOXML packages, not WPS provenance.

These fixtures state the tolerant reading contract directly: value-bearing
spreadsheet elements are read whenever possible, and every degradation appears
as an aggregated issue with the result marked partial.
"""
import io
import json
from decimal import Decimal
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from xml.parsers import expat
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_wps import read

T = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
S = 'http://purl.oclc.org/ooxml/spreadsheetml/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
RS = 'http://purl.oclc.org/ooxml/officeDocument/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
C = 'http://schemas.openxmlformats.org/package/2006/content-types'
BOOK_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'
TEMPLATE_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml'
SHEET_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'
SST_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml'
STYLES_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml'
COMMENTS_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml'


def package(cells='<c r="A1"><v>12.5</v></c>', strict=False, workbook_extra='',
            sheet_extra='', overrides=None, main_type=BOOK_TYPE, extra_parts=None):
    ns, rel = (S, RS) if strict else (T, R)
    parts = {
        '[Content_Types].xml': (
            f'<Types xmlns="{C}">'
            f'<Override PartName="/xl/workbook.xml" ContentType="{main_type}"/>'
            f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{SHEET_TYPE}"/>'
            f'<Override PartName="/xl/sharedStrings.xml" ContentType="{SST_TYPE}"/>'
            '</Types>'
        ),
        '_rels/.rels': (f'<Relationships xmlns="{P}"><Relationship Id="root" '
                        f'Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        'xl/workbook.xml': (f'<workbook xmlns="{ns}" xmlns:r="{rel}"><workbookPr date1904="1"/>'
                            f'<sheets><sheet name="Data" sheetId="7" state="hidden" '
                            f'r:id="s1"/></sheets>{workbook_extra}</workbook>'),
        'xl/_rels/workbook.xml.rels': (
            f'<Relationships xmlns="{P}"><Relationship Id="s1" Type="{rel}/worksheet" '
            f'Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="str" Type="{rel}/sharedStrings" '
            f'Target="sharedStrings.xml"/></Relationships>'
        ),
        'xl/sharedStrings.xml': (f'<sst xmlns="{ns}" count="1" uniqueCount="1">'
                                 '<si><r><t>部门</t></r><r><t>名称</t></r></si></sst>'),
        'xl/worksheets/sheet1.xml': (
            f'<worksheet xmlns="{ns}" xmlns:r="{rel}"><sheetData><row r="1">{cells}</row>'
            f'</sheetData>{sheet_extra}</worksheet>'
        ),
    }
    if extra_parts:
        parts.update(extra_parts)
    if overrides:
        parts.update(overrides)
    return build_package(parts)


def build_package(parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value.encode('utf-8') if isinstance(value, str) else value)
    return out.getvalue()


def parts_of(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def text_parts_of(data):
    return {name: value.decode('utf-8') for name, value in parts_of(data).items()}


def add_sheet_relationships(data, relationships_xml):
    parts = text_parts_of(data)
    parts['xl/worksheets/_rels/sheet1.xml.rels'] = relationships_xml
    return build_package(parts)


def result(data):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'source.et'
        path.write_bytes(data)
        return read(path)


def direct(data):
    from wps_ooxml_sheet import read_bytes
    return read_bytes(data)


def codes(value):
    return {issue['code'] for issue in value.get('issues', [])}


def issue(value, code):
    matches = [item for item in value.get('issues', []) if item['code'] == code]
    return matches[0] if matches else None


def cells_of(value, sheet=0):
    return value['sheets'][sheet]['cells']


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class SheetTests(unittest.TestCase):
    def success(self, data):
        value = result(data)
        self.assertIn(value['status'], ('ok', 'partial'), value)
        return value

    def rejected(self, data):
        value = result(data)
        self.assertNotIn(value['status'], ('ok', 'partial'), value)
        return value

    def test_strict_transitional_types_and_exact_decimal(self):
        raw = (
            '<c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>000123</t></is></c>'
            '<c r="C1"><v>123456789012345678.125</v></c><c r="D1" t="b"><v>1</v></c>'
            '<c r="E1" t="e"><v>#DIV/0!</v></c><c r="F1"/>'
        )
        for strict in (False, True):
            value = self.success(package(raw, strict))
            cells = value['sheets'][0]['cells']
            self.assertEqual(value['schema_version'], 'ooxml_spreadsheet_readonly_v1')
            self.assertTrue(value['spreadsheet_parsed'])
            self.assertEqual(value['date_system'], '1904')
            self.assertEqual(value['sheets'][0]['name'], 'Data')
            self.assertEqual(value['sheets'][0]['hidden_state'], 1)
            self.assertEqual([cells[key]['value'] for key in ('A1', 'B1', 'C1', 'D1', 'E1', 'F1')],
                             ['部门名称', '000123', Decimal('123456789012345678.125'), True,
                              '#DIV/0!', None])
            self.assertEqual(cells['F1']['type'], 'blank')

    def test_formula_saved_cache_no_recalculation(self):
        value = self.success(package(
            '<c r="A1"><f>1+1</f><v>999</v></c>'
            '<c r="B1" t="str"><f>"x"</f><v>缓存</v></c><c r="C1"><f>2+2</f></c>'))
        cells = cells_of(value)
        self.assertEqual(cells['A1']['value'], Decimal(999))
        self.assertEqual(cells['A1']['cache_status'], 'unverified')
        self.assertEqual(cells['B1']['value'], '缓存')
        self.assertEqual(cells['C1']['cache_status'], 'missing')
        self.assertIsNone(cells['C1']['value'])
        self.assertEqual(value['status'], 'partial')
        self.assertFalse(value['formulas_executed'])
        missing = issue(value, 'missing_formula_cache')
        self.assertEqual(missing['cell'], 'C1')
        self.assertFalse(missing['evaluated'])

    def test_rich_text_formats_in_inline_and_shared_strings(self):
        nodes = ('<r><rPr><rFont val="Aptos"/><b/><i val="0"/><color rgb="FFFF0000"/></rPr>'
                 '<t>强调</t></r>'
                 '<r><rPr><i/><color theme="1"/></rPr><t>文字😀</t></r>')
        expected = [
            {'text': '强调', 'font': {'font_name': 'Aptos', 'bold': True, 'italic': False,
                                     'color': {'rgb': 'FFFF0000'}}},
            {'text': '文字😀', 'font': {'italic': True, 'color': {'theme': '1'}}}]
        xml = (f'<sst xmlns="{T}" count="2" uniqueCount="2"><si>{nodes}</si>'
               '<si><r><t>未引用</t></r></si></sst>')
        raw = '<c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is>' + nodes + '</is></c>'
        value = self.success(package(raw, overrides={'xl/sharedStrings.xml': xml}))
        cells = cells_of(value)
        self.assertEqual([cells[key]['value'] for key in ('A1', 'B1')], ['强调文字😀'] * 2)
        self.assertEqual(value['shared_string_formatting'],
                         [{'sst_index': cells['A1']['sst_index'], 'segments': expected}])
        self.assertEqual(cells['B1']['rich_text']['segments'], expected)
        self.assertIn('rich_string_formatting_not_applied', codes(value))

    def test_saved_row_and_column_dimensions_are_optional_metadata(self):
        sheet = (f'<worksheet xmlns="{T}"><cols><col min="1" max="2" width="12.5" hidden="1"/>'
                 '<col min="3" max="3" width="NaN"/><col min="4" max="4"/></cols><sheetData>'
                 '<row r="1" ht="18.25"><c r="A1"><v>12.5</v></c></row><row r="2" ht="-2"/>'
                 '<row r="3"/></sheetData></worksheet>')
        value = self.success(package(overrides={'xl/worksheets/sheet1.xml': sheet}))
        without_dimensions = (sheet.replace(' width="12.5"', '')
                              .replace(' width="NaN"', '').replace(' ht="18.25"', '')
                              .replace(' ht="-2"', ''))
        baseline = self.success(package(
            overrides={'xl/worksheets/sheet1.xml': without_dimensions}))
        self.assertEqual(value['status'], baseline['status'])
        self.assertEqual(value['reading_assessment'], baseline['reading_assessment'])
        self.assertEqual(cells_of(value), cells_of(baseline))
        visibility = value['sheets'][0]['visibility']
        rows, columns = visibility['rows'], visibility['columns']
        self.assertEqual(rows[0]['height_pt'], '18.25')
        self.assertEqual(rows[1]['height_pt_status'], 'uninterpreted')
        self.assertNotIn('height_pt', rows[1])
        self.assertNotIn('height_pt_status', rows[2])
        self.assertEqual(columns[0]['width_chars'], '12.5')
        self.assertTrue(columns[0]['hidden_saved'])
        self.assertEqual(columns[1]['width_chars_status'], 'uninterpreted')
        self.assertNotIn('width_chars', columns[1])
        self.assertNotIn('width_chars_status', columns[2])

    def test_duplicate_and_invalid_cells_are_skipped_with_omission_diagnostics(self):
        for raw in ('<c r="A1"><v>1</v></c><c r="A1"><v>2</v></c>',
                    '<c r="A1" t="mystery"><v>x</v></c>',
                    '<c r="A1" t="b"><v>2</v></c>',
                    '<c r="A1" t="s"><v>9</v></c>',
                    '<c r="A1"><v>NaN</v></c>',
                    '<c r="XFE1"><v>1</v></c>',
                    '<c r="A1048577"><v>1</v></c>'):
            with self.subTest(raw=raw):
                value = self.success(package(raw))
                self.assertEqual(value['status'], 'partial')
                self.assertEqual(value['sheets'][0]['name'], 'Data')
        duplicate = self.success(package(
            '<c r="A1"><v>1</v></c><c r="A1"><v>2</v></c>'))
        self.assertEqual(duplicate['sheets'][0]['cells']['A1']['value'], 1)
        self.assertIn('duplicate_cell_ignored', codes(duplicate))
        self.assertTrue(duplicate['omissions_affect_cell_values'])
        unknown_type = self.success(package('<c r="A1" t="mystery"><v>raw-text</v></c>'))
        cell = unknown_type['sheets'][0]['cells']['A1']
        self.assertEqual((cell['type'], cell['value']), ('text', 'raw-text'))
        self.assertIn('unknown_cell_type_text_retained', codes(unknown_type))
        self.assertFalse(unknown_type['omissions_affect_cell_values'])
        invalid_number = self.success(package('<c r="A1"><v>NaN</v></c>'))
        self.assertNotIn('A1', invalid_number['sheets'][0]['cells'])
        self.assertEqual(invalid_number['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')

    def test_merges_are_tolerant(self):
        value = self.success(package(
            sheet_extra='<mergeCells count="1"><mergeCell ref="A1:B2"/></mergeCells>'))
        self.assertEqual(value['sheets'][0]['merges'][0]['range'], 'A1:B2')
        overlap = self.success(package(
            sheet_extra='<mergeCells count="2"><mergeCell ref="A1:B2"/>'
                        '<mergeCell ref="B2:C3"/></mergeCells>'))
        self.assertEqual(len(overlap['sheets'][0]['merges']), 2)
        self.assertIn('overlapping_merges_retained', codes(overlap))
        self.assertEqual(overlap['status'], 'partial')
        single = self.success(package(
            sheet_extra='<mergeCells><mergeCell ref="A1"/></mergeCells>'))
        self.assertEqual(single['sheets'][0]['merges'], [])
        self.assertIn('single_cell_merge_ignored', codes(single))
        invalid = self.success(package(
            sheet_extra='<mergeCells><mergeCell ref="XFE1:XFE2"/></mergeCells>'))
        self.assertEqual(invalid['sheets'][0]['merges'], [])
        self.assertIn('merge_range_not_interpreted', codes(invalid))

    def test_sheet_declaration_variance_keeps_cells(self):
        base = text_parts_of(package())
        duplicate = self.success(package(overrides={'xl/workbook.xml': base['xl/workbook.xml'].replace(
            '</sheets>', '<sheet name="Other" sheetId="7" r:id="s1"/></sheets>')}))
        self.assertEqual([sheet['name'] for sheet in duplicate['sheets']],
                         ['Data', 'Other'])
        self.assertIn('duplicate_sheet_id_retained', codes(duplicate))
        self.assertEqual(len(duplicate['sheets'][0]['cells']), 1)
        missing = self.success(package(overrides={'xl/workbook.xml': base['xl/workbook.xml'].replace(
            'r:id="s1"', 'r:id="missing"')}))
        self.assertEqual([sheet['name'] for sheet in missing['sheets']], ['Data'])
        self.assertEqual(missing['sheets'][0]['cells'], {})
        self.assertIn('worksheet_relationship_unknown', codes(missing))
        self.assertTrue(missing['omissions_affect_cell_values'])
        external = self.success(package(overrides={
            'xl/_rels/workbook.xml.rels': base['xl/_rels/workbook.xml.rels'].replace(
                'Target="worksheets/sheet1.xml"',
                'Target="https://example.invalid/evil" TargetMode="External"'),
        }))
        self.assertEqual(external['sheets'][0]['cells'], {})
        self.assertIn('worksheet_part_not_followed', codes(external))
        self.assertIn('external_relationship_not_followed', codes(external))
        self.assertFalse(external['external_links_followed'])
        self.assertTrue(external['omissions_affect_cell_values'])

    def test_mixed_spreadsheet_namespace_is_partial_but_value_reaching_elements_read(self):
        mixed = self.success(package(overrides={
            'xl/worksheets/sheet1.xml':
                f'<worksheet xmlns="{S}"><sheetData><row r="1">'
                '<c r="A1"><v>7</v></c></row></sheetData></worksheet>'}))
        self.assertEqual(mixed['sheets'][0]['cells']['A1']['value'], 7)
        self.assertIn('spreadsheet_conformance_namespace_variance', codes(mixed))
        self.rejected(package(overrides={
            'xl/worksheets/sheet1.xml': '<!DOCTYPE x [<!ENTITY secret "boom">]>'
                                        f'<worksheet xmlns="{T}"><sheetData/></worksheet>'}))
        self.rejected(package(overrides={
            'xl/worksheets/sheet1.xml':
                '<!DOCTYPE x SYSTEM "https://example.invalid/evil.dtd">'
                f'<worksheet xmlns="{T}"><sheetData/></worksheet>'}))

    def test_unknown_cell_type_and_foreign_value_element(self):
        missing_value = self.success(package('<c r="A1" t="mystery"/>'))
        self.assertEqual(missing_value['sheets'][0]['cells']['A1']['type'], 'blank')
        self.assertIn('unknown_cell_type_without_value', codes(missing_value))
        foreign_value = self.success(package(
            '<c r="A1"><v xmlns="urn:example:foreign">12</v></c>'))
        self.assertEqual(foreign_value['sheets'][0]['cells']['A1']['type'], 'blank')
        self.assertIn('unknown_cell_element_ignored', codes(foreign_value))
        self.assertEqual(foreign_value['status'], 'partial')

    def test_cell_row_column_budgets_degrade_instead_of_failing(self):
        with patch('wps_ooxml_worksheet.MAX_CELLS', 1):
            value = result(package('<c r="A1"><v>1</v></c><c r="B1"><v>2</v></c>'))
            self.assertEqual(value['status'], 'partial')
            self.assertEqual(list(value['sheets'][0]['cells']), ['A1'])
            self.assertIn('cell_count_limit_reached', codes(value))
            self.assertTrue(value['omissions_affect_cell_values'])
        with patch('wps_ooxml_worksheet.MAX_COLUMN_RANGES', 2):
            columns = ('<cols>' + ''.join(
                f'<col min="{index}" max="{index}"/>' for index in range(1, 5)) + '</cols>')
            value = result(package(sheet_extra=columns))
            self.assertEqual(value['status'], 'partial')
            self.assertIn('column_metadata_limit_reached', codes(value))
            self.assertEqual(len(value['sheets'][0]['visibility']['columns']), 2)
        with patch('wps_ooxml_worksheet.MAX_ROWS', 1):
            value = result(package(overrides={'xl/worksheets/sheet1.xml': (
                f'<worksheet xmlns="{T}"><sheetData>'
                '<row r="1"><c r="A1"><v>1</v></c></row>'
                '<row r="2"><c r="A2"><v>2</v></c></row>'
                '</sheetData></worksheet>')}))
            self.assertEqual(value['status'], 'partial')
            self.assertIn('row_metadata_limit_reached', codes(value))
            self.assertNotIn('A2', value['sheets'][0]['cells'])

    def test_corrupt_compression_is_structured_failure(self):
        from wps_ooxml_sheet import read_bytes
        from read_wps import read_bytes as unified
        raw = package()
        with patch('wps_ooxml_sheet.read_part', side_effect=zlib.error('invalid compressed data')):
            value = read_bytes(raw)
            self.assertEqual(value['status'], 'corrupt')
            self.assertIsNone(value['text'])
            unified_value = unified(raw)
            self.assertNotIn(unified_value['status'], ('ok', 'partial'))
            self.assertIsNone(unified_value['text'])

    def test_cli_json_decimal_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.et'
            path.write_bytes(package('<c r="A1"><v>0.1234567890123456789</v></c>'))
            process = subprocess.run(
                [sys.executable, '-I', '-S', '-B',
                 str((Path(__file__).resolve().parents[1] / 'read_wps.py')), str(path)],
                capture_output=True, text=True, env={'PATH': ''}, timeout=10)
            self.assertIn(process.returncode, (0, 3), process.stderr)
            output = json.loads(process.stdout)
            self.assertEqual(output['sheets'][0]['cells']['A1']['value'],
                             '0.1234567890123456789')

    def test_template_main_type_is_accepted_as_spreadsheet(self):
        from wps_ooxml_sheet import package_kind
        data = package(main_type=TEMPLATE_TYPE)
        self.assertEqual(package_kind(data), 'spreadsheet')
        value = result(data)
        self.assertIn(value['status'], ('ok', 'partial'))
        self.assertEqual(cells_of(value)['A1']['value'], Decimal('12.5'))

    def test_issue_entries_are_aggregated_by_code(self):
        value = self.success(package(
            '<c r="A1"><f>1</f></c><c r="B1"><f>2</f></c><c r="C1"><f>3</f></c>'))
        entry = issue(value, 'missing_formula_cache')
        self.assertEqual(entry['count'], 3)
        self.assertIs(entry['evaluated'], False)
        self.assertEqual(entry['cell'], 'A1')
        self.assertEqual(len(entry['samples']), 3)
        for item in value['issues']:
            self.assertIn('code', item)
            self.assertIn('count', item)
            self.assertIn('evaluated', item)
            self.assertIsInstance(item['samples'], list)
        self.assertFalse(value['issues_truncated'])

    def test_missing_shared_strings_part_degrades_to_issues(self):
        parts = text_parts_of(package('<c r="A1" t="s"><v>0</v></c>'))
        del parts['xl/sharedStrings.xml']
        value = self.success(build_package(parts))
        self.assertEqual(value['sheets'][0]['cells'], {})
        self.assertIn('shared_strings_part_not_read', codes(value))
        self.assertIn('shared_string_index_outside_collection', codes(value))
        self.assertEqual(value['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')
        self.assertTrue(value['omissions_affect_cell_values'])

    def test_rows_and_cells_without_references_are_inferred(self):
        value = self.success(package(overrides={'xl/worksheets/sheet1.xml': (
            f'<worksheet xmlns="{T}"><sheetData><row><c><v>1</v></c><c><v>2</v></c>'
            '</row></sheetData></worksheet>')}))
        self.assertEqual(value['sheets'][0]['cells']['A1']['value'], 1)
        self.assertEqual(value['sheets'][0]['cells']['B1']['value'], 2)
        self.assertIn('cell_reference_inferred', codes(value))
        self.assertEqual(value['status'], 'partial')

    def test_formula_without_cache_uses_cache_coverage_value(self):
        value = self.success(package('<c r="A1"><f>1+1</f></c>'))
        self.assertEqual(value['cell_value_coverage'], 'missing_formula_caches')
        self.assertTrue(value['omissions_affect_cell_values'])


if __name__ == '__main__':
    unittest.main()
