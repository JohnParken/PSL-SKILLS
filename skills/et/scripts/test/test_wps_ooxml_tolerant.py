"""Contract tests for tolerant OOXML spreadsheet reading.

The packages here are synthetic and combine unrelated extension parts, foreign
namespaces and malformed optional metadata with readable saved cells. The stated
expectations are independent of the implementation: cells survive, every
degradation is an aggregated issue, and only safety faults are rejected.
"""
import io
from decimal import Decimal
from pathlib import Path
import socket
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
import warnings
import zipfile
from unittest.mock import patch
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_ooxml_sheet import (
    P, R, STYLES_TYPE, T, TEMPLATE_TYPE, build_package, cells_of, codes, direct,
    issue, package, result, text_parts_of,
)
from wps_access import ParseError

if expat.version_info < (2, 6, 0):
    raise unittest.SkipTest('requires Expat >=2.6')


def sheet_with_rels(sheet_xml, sheet_rels, extra_parts=None, strict=False):
    parts = text_parts_of(package(strict=strict))
    parts['xl/worksheets/sheet1.xml'] = sheet_xml
    if sheet_rels is not None:
        parts['xl/worksheets/_rels/sheet1.xml.rels'] = sheet_rels
    if extra_parts:
        parts.update(extra_parts)
    return build_package(parts)


def spread_sheet(*rows, extra='', namespace=T):
    body = ''.join(f'<row r="{index}">{cells}</row>' for index, cells in enumerate(rows, 1))
    return f'<worksheet xmlns="{namespace}"><sheetData>{body}</sheetData>{extra}</worksheet>'


class ForeignExtensionTests(unittest.TestCase):
    def test_extension_attributes_and_elements_are_tolerated(self):
        mc = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
        x14ac = 'http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac'
        xr = 'http://schemas.microsoft.com/office/spreadsheetml/2014/revision'
        sheet = (
            f'<worksheet xmlns="{T}" xmlns:mc="{mc}" xmlns:x14ac="{x14ac}" xmlns:xr="{xr}" '
            'mc:Ignorable="x14ac xr"><sheetData>'
            '<row r="1" x14ac:dyDescent="0.25">'
            '<c r="A1" xr:uid="{00000000-0001-0000-0000-000000000000}"><v>5</v></c>'
            '</row></sheetData>'
            '<extLst><ext uri="{TEST}"/></extLst>'
            f'<mc:AlternateContent><mc:Choice Requires="x14"/></mc:AlternateContent>'
            '</worksheet>')
        value = result(sheet_with_rels(sheet, None))
        self.assertEqual(cells_of(value)['A1']['value'], 5)
        self.assertEqual(value['status'], 'partial')
        self.assertIn('foreign_attribute_not_interpreted', codes(value))
        self.assertIn('worksheet_metadata_not_interpreted', codes(value))
        entry = issue(value, 'foreign_attribute_not_interpreted')
        self.assertGreaterEqual(entry['count'], 2)
        self.assertFalse(entry['evaluated'])

    def test_alternate_content_around_readable_worksheet_does_not_hide_cells(self):
        mc = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
        sheet = (f'<worksheet xmlns="{T}" xmlns:mc="{mc}"><sheetData>'
                 '<row r="1"><c r="A1"><v>11</v></c><c r="B1"><v>12</v></c></row>'
                 '</sheetData><mc:AlternateContent><mc:Choice Requires="x14"/>'
                 '</mc:AlternateContent></worksheet>')
        value = result(sheet_with_rels(sheet, None))
        self.assertEqual(cells_of(value)['A1']['value'], 11)
        self.assertEqual(cells_of(value)['B1']['value'], 12)
        self.assertIn('worksheet_metadata_not_interpreted', codes(value))

    def test_workbook_extension_attributes_are_recorded(self):
        parts = text_parts_of(package())
        mc = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
        parts['xl/workbook.xml'] = (f'<workbook xmlns="{T}" xmlns:r="{R}" xmlns:mc="{mc}" '
                                    'mc:Ignorable="x15"><sheets><sheet name="Data" '
                                    'sheetId="7" r:id="s1"/></sheets></workbook>')
        value = result(build_package(parts))
        self.assertEqual(cells_of(value)['A1']['value'], Decimal('12.5'))
        self.assertIn('foreign_attribute_not_interpreted', codes(value))

    def test_foreign_namespace_cell_elements_are_not_values(self):
        sheet = (f'<worksheet xmlns="{T}"><sheetData><row r="1">'
                 '<urn:extra xmlns:urn="urn:example"><c r="A1"><v>99</v></c></urn:extra>'
                 '<c r="B1"><v>7</v></c></row></sheetData></worksheet>')
        value = result(sheet_with_rels(sheet, None))
        self.assertEqual(cells_of(value)['B1']['value'], 7)
        self.assertNotIn('A1', cells_of(value))
        self.assertIn('row_element_not_interpreted', codes(value))

    def test_extension_only_worksheet_returns_empty_sheet_not_failure(self):
        sheet = (f'<worksheet xmlns="{T}" xmlns:mc="http://schemas.openxmlformats.org/'
                 f'markup-compatibility/2006"><sheetData/>'
                 '<mc:AlternateContent><mc:Choice Requires="x14"/></mc:AlternateContent>'
                 '</worksheet>')
        value = result(sheet_with_rels(sheet, None))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['sheets'][0]['cells'], {})


class RelationshipTests(unittest.TestCase):
    def test_unknown_workbook_relationships_are_not_read(self):
        parts = text_parts_of(package())
        rel = R
        extra = ''.join(
            f'<Relationship Id="{kind}" Type="{rel}/{kind}" Target="{kind}.bin"/>'
            for kind in ('theme', 'calcChain', 'externalLink', 'connections',
                         'customXml', 'sheetMetadata', 'chartsheet', 'pivotCache'))
        parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].replace(
            '</Relationships>', extra + '</Relationships>')
        for kind in ('theme', 'calcChain', 'externalLink', 'connections', 'customXml',
                     'sheetMetadata', 'chartsheet', 'pivotCache'):
            parts[f'xl/{kind}.bin'] = b'<not-xml ' + kind.encode() + b'\xff\xfe>'
        value = result(build_package(parts))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(cells_of(value)['A1']['value'], Decimal('12.5'))
        entry = issue(value, 'workbook_relationship_not_followed')
        self.assertGreaterEqual(entry['count'], 8)
        self.assertIn('unrelated_package_part_not_read', codes(value))
        self.assertEqual(value['external_links_followed'], False)

    def test_unknown_worksheet_relationships_are_ignored_but_comments_read(self):
        comments = (f'<comments xmlns="{T}"><authors><author>A</author></authors>'
                    '<commentList><comment ref="A1" authorId="0"><text><t>note</t></text>'
                    '</comment></commentList></comments>')
        rels = (f'<Relationships xmlns="{P}">'
                f'<Relationship Id="c1" Type="{R}/comments" Target="../notes/c.xml"/>'
                f'<Relationship Id="d1" Type="{R}/drawing" Target="../drawings/drawing1.xml"/>'
                f'<Relationship Id="p1" Type="{R}/printerSettings" '
                'Target="../printerSettings/printer1.bin"/>'
                f'<Relationship Id="h1" Type="{R}/hyperlink" Target="https://example.invalid/" '
                'TargetMode="External"/></Relationships>')
        value = result(sheet_with_rels(spread_sheet('<c r="A1"><v>12.5</v></c>'), rels, {
            'xl/notes/c.xml': comments,
            'xl/drawings/drawing1.xml': b'<broken drawing \xff',
            'xl/printerSettings/printer1.bin': b'\x00\x01binary',
        }))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(cells_of(value)['A1']['value'], 12.5)
        self.assertEqual(value['sheets'][0]['comments'][0]['text'], 'note')
        self.assertGreaterEqual(issue(value, 'worksheet_relationship_not_followed')['count'], 2)
        self.assertIn('external_relationship_not_followed', codes(value))

    def test_external_targets_are_recorded_and_never_followed(self):
        parts = text_parts_of(package())
        parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].replace(
            '</Relationships>',
            f'<Relationship Id="ext" Type="{R}/externalLink" '
            'Target="https://example.invalid/link.xml" TargetMode="External"/>'
            '</Relationships>')
        data = package(sheet_extra='<hyperlinks><hyperlink ref="A1" r:id="x1"/>'
                                   '</hyperlinks>',
                       extra_parts={'xl/worksheets/_rels/sheet1.xml.rels': (
                           f'<Relationships xmlns="{P}"><Relationship Id="x1" '
                           f'Type="{R}/hyperlink" '
                           'Target="https://example.invalid/page" '
                           'TargetMode="External"/></Relationships>')})
        with patch.object(socket, 'socket', side_effect=AssertionError('network attempted')):
            value = result(data)
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(cells_of(value)['A1']['value'], Decimal('12.5'))
        external = issue(value, 'external_relationship_not_followed')
        self.assertIsNotNone(external)
        self.assertEqual(external['target_resolved'], False)
        self.assertEqual(external['followed'], False)
        self.assertEqual(value['sheets'][0]['hyperlinks'][0]['target_raw'],
                         'https://example.invalid/page')
        self.assertFalse(value['external_links_followed'])

    def test_comments_and_vml_parse_failures_degrade(self):
        rels = (f'<Relationships xmlns="{P}">'
                f'<Relationship Id="c1" Type="{R}/comments" Target="../notes/c.xml"/>'
                f'<Relationship Id="v1" Type="{R}/vmlDrawing" Target="../drawings/n.vml"/>'
                '</Relationships>')
        valid_comments = (f'<comments xmlns="{T}"><authors><author>A</author></authors>'
                          '<commentList><comment ref="A1" authorId="0"><text><t>kept</t>'
                          '</text></comment></commentList></comments>')
        parts = text_parts_of(package())
        parts['xl/worksheets/_rels/sheet1.xml.rels'] = rels
        parts['xl/notes/c.xml'] = valid_comments
        parts['xl/drawings/n.vml'] = b'<xml><broken \xff'
        value = result(build_package(parts))
        self.assertEqual(cells_of(value)['A1']['value'], Decimal('12.5'))
        self.assertEqual(value['sheets'][0]['comments'][0]['text'], 'kept')
        self.assertIn('vml_note_anchors_not_read', codes(value))
        self.assertIn('comment_layout_not_decoded', codes(value))
        self.assertEqual(value['status'], 'partial')
        broken = text_parts_of(package())
        broken['xl/worksheets/_rels/sheet1.xml.rels'] = rels
        broken['xl/notes/c.xml'] = b'<comments><broken \xff'
        value = result(build_package(broken))
        self.assertEqual(value['sheets'][0]['comments'], [])
        self.assertIn('comments_part_not_read', codes(value))
        self.assertEqual(cells_of(value)['A1']['value'], Decimal('12.5'))

    def test_unrelated_parts_are_listed_and_not_interpreted(self):
        value = result(package(extra_parts={
            'docProps/core.xml': b'<broken \xff',
            'xl/media/image1.bin': b'\x00\x01\x02not-an-image'}))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(cells_of(value)['A1']['value'], Decimal('12.5'))
        entry = issue(value, 'unrelated_package_part_not_read')
        self.assertGreaterEqual(entry['count'], 2)
        self.assertIn('part', entry)


class WorksheetElementTests(unittest.TestCase):
    def test_unknown_worksheet_elements_are_issues_not_aborts(self):
        extra = ('<conditionalFormatting sqref="A1:B2"/>'
                 '<dataValidations count="1"/>'
                 '<pageSetup orientation="landscape"/>'
                 '<sheetProtection/>'
                 '<tableParts count="0"/>'
                 '<drawing/>'
                 '<extLst/>'
                 '<urn:foreign xmlns:urn="urn:example:extension"><urn:data/></urn:foreign>')
        value = result(sheet_with_rels(
            spread_sheet('<c r="A1"><v>12.5</v></c>', extra=extra), None))
        self.assertEqual(cells_of(value)['A1']['value'], 12.5)
        entry = issue(value, 'worksheet_metadata_not_interpreted')
        self.assertGreaterEqual(entry['count'], 6)
        self.assertEqual(value['status'], 'partial')

    def test_second_sheet_data_collection_recovers_distinct_cells(self):
        sheet = (f'<worksheet xmlns="{T}"><sheetData><row r="1"><c r="A1"><v>1</v></c>'
                 '</row></sheetData><sheetData><row r="1"><c r="B1"><v>2</v></c></row>'
                 '</sheetData></worksheet>')
        value = result(sheet_with_rels(sheet, None))
        self.assertIn('A1', cells_of(value))
        self.assertEqual(cells_of(value)['B1']['value'], 2)
        self.assertIn('duplicate_sheet_data_recovered', codes(value))
        self.assertTrue(value['omissions_affect_cell_values'])

    def test_missing_sheet_data_is_reported_without_failure(self):
        value = result(sheet_with_rels(f'<worksheet xmlns="{T}"/>', None))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(cells_of(value), {})
        self.assertIn('worksheet_sheet_data_missing', codes(value))

    def test_non_row_sheet_data_children_are_ignored(self):
        sheet = (f'<worksheet xmlns="{T}"><sheetData>'
                 '<urn:foreign xmlns:urn="urn:example:extension"/>'
                 '<row r="1"><c r="A1"><v>3</v></c></row></sheetData></worksheet>')
        value = result(sheet_with_rels(sheet, None))
        self.assertEqual(cells_of(value)['A1']['value'], 3)
        self.assertIn('sheet_data_element_not_interpreted', codes(value))


class DiagnosticsTests(unittest.TestCase):
    def test_issues_are_aggregated_with_bounded_samples(self):
        cells = ''.join(f'<c r="A{index}" t="mystery"><v>v{index}</v></c>'
                        for index in range(1, 21))
        value = result(package(cells))
        entry = issue(value, 'unknown_cell_type_text_retained')
        self.assertEqual(entry['count'], 20)
        self.assertEqual(entry['cell'], 'A1')
        self.assertIs(entry['evaluated'], False)
        self.assertEqual(len(entry['samples']), 8)
        self.assertEqual(value['status'], 'partial')
        self.assertFalse(value['issues_truncated'])

    def test_thousands_of_diagnostics_stay_aggregated(self):
        cells = ''.join(f'<c r="A{index}" t="mystery"><v>v</v></c>'
                        for index in range(1, 1501))
        value = result(package(cells))
        entry = issue(value, 'unknown_cell_type_text_retained')
        self.assertEqual(entry['count'], 1500)
        self.assertEqual(len(entry['samples']), 8)
        self.assertEqual(len(cells_of(value)), 1500)
        self.assertEqual(value['status'], 'partial')
        self.assertFalse(value['issues_truncated'])

    def test_issue_code_budget_truncates_instead_of_failing(self):
        cells = ('<c r="A1" t="mystery"><v>x</v></c>'
                 '<c r="B1" t="mystery"/>')
        sheet = (f'<worksheet xmlns="{T}"><sheetData><row r="1">{cells}</row>'
                 '</sheetData><extLst/></worksheet>')
        with patch('wps_ooxml.MAX_ISSUE_CODES', 2):
            value = result(sheet_with_rels(sheet, None))
        self.assertEqual(value['status'], 'partial')
        self.assertTrue(value['issues_truncated'])
        self.assertLessEqual(len(value['issues']), 2)
        self.assertIn('A1', cells_of(value))

    def test_clean_package_is_ok_and_issue_free(self):
        plain_sst = f'<sst xmlns="{T}"><si><t>only</t></si></sst>'
        data = package('<c r="A1"><v>42</v></c>',
                       overrides={'xl/sharedStrings.xml': plain_sst})
        value = direct(data)
        self.assertEqual(value['status'], 'ok')
        self.assertFalse(value['partial'])
        self.assertEqual(value['issues'], [])
        self.assertFalse(value['issues_truncated'])
        self.assertEqual(value['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertFalse(value['omissions_affect_cell_values'])


class ResourceContractTests(unittest.TestCase):
    def test_resource_constants_support_two_hundred_thousand_cells(self):
        import wps_ooxml
        import wps_ooxml_worksheet
        self.assertGreaterEqual(wps_ooxml_worksheet.MAX_CELLS, 200000)
        self.assertGreaterEqual(wps_ooxml_worksheet.MAX_ROWS, 200000)
        self.assertGreaterEqual(wps_ooxml_worksheet.MAX_SST, 200000)
        self.assertGreaterEqual(wps_ooxml_worksheet.MAX_MERGES, 65536)
        self.assertGreaterEqual(wps_ooxml.MAX_XML_ELEMENTS, 2000000)
        self.assertGreaterEqual(wps_ooxml.MAX_XML_NODES, 8000000)
        self.assertGreaterEqual(wps_ooxml.MAX_XML_ATTRIBUTES, 2000000)
        self.assertEqual(wps_ooxml.MAX_XML_DEPTH, 128)
        self.assertGreaterEqual(wps_ooxml.MAX_XML_EXPANDED_NAME_CHARS, 64 * 1024 * 1024)
        self.assertEqual(wps_ooxml.MAX_XML_BYTES, 16 * 1024 * 1024)

    def test_two_hundred_thousand_cells_are_read(self):
        rows = 2000
        columns = 100
        row_xml = []
        for row in range(1, rows + 1):
            row_xml.append(f'<row r="{row}">')
            row_xml.append(''.join(
                f'<c r="{column_name(index)}{row}"><v>{row * columns + index}</v></c>'
                for index in range(columns)))
            row_xml.append('</row>')
        sheet = (f'<worksheet xmlns="{T}"><sheetData>{"".join(row_xml)}'
                 '</sheetData></worksheet>')
        plain_sst = f'<sst xmlns="{T}"><si><t>only</t></si></sst>'
        value = direct(package(overrides={'xl/worksheets/sheet1.xml': sheet,
                                          'xl/sharedStrings.xml': plain_sst}))
        self.assertIn(value['status'], ('ok', 'partial'))
        self.assertEqual(value['cell_count'], 200000)
        self.assertEqual(len(value['sheets'][0]['cells']), 200000)
        self.assertEqual(value['sheets'][0]['cells']['A1']['value'], 100)
        self.assertEqual(value['sheets'][0]['cells']['CV2000']['value'], 200099)
        self.assertEqual(value['cell_value_coverage'],
                         'complete_within_declared_sheets')

    def test_merge_and_sst_caps_degrade(self):
        with patch('wps_ooxml_worksheet.MAX_MERGES', 2):
            sheet = spread_sheet(extra=(
                '<mergeCells><mergeCell ref="A1:B1"/><mergeCell ref="C1:D1"/>'
                '<mergeCell ref="E1:F1"/></mergeCells>'))
            value = direct(sheet_with_rels(sheet, None))
            self.assertEqual(len(value['sheets'][0]['merges']), 2)
            self.assertIn('merge_count_limit_reached', codes(value))
            self.assertEqual(value['cell_value_coverage'],
                             'complete_within_declared_sheets')
            self.assertFalse(value['omissions_affect_cell_values'])
        sst = f'<sst xmlns="{T}"><si><t>a</t></si><si><t>b</t></si><si><t>c</t></si></sst>'
        with patch('wps_ooxml_sheet.MAX_SST', 2):
            value = direct(package('<c r="A1" t="s"><v>2</v></c>',
                                   overrides={'xl/sharedStrings.xml': sst}))
            self.assertIn('shared_string_count_limit_reached', codes(value))
            self.assertEqual(value['sheets'][0]['cells'], {})
            self.assertTrue(value['omissions_affect_cell_values'])


def column_name(index):
    number = index + 1
    letters = ''
    while number:
        number, digit = divmod(number - 1, 26)
        letters = chr(65 + digit) + letters
    return letters


class ContentTypeSecurityTests(unittest.TestCase):
    def test_template_main_type_is_spreadsheet(self):
        from wps_ooxml_sheet import package_kind
        data = package(main_type=TEMPLATE_TYPE)
        self.assertEqual(package_kind(data), 'spreadsheet')
        value = result(data)
        self.assertIn(value['status'], ('ok', 'partial'))
        self.assertEqual(cells_of(value)['A1']['value'], Decimal('12.5'))

    def test_macro_main_type_is_still_rejected(self):
        macro_type = 'application/vnd.ms-excel.sheet.macroEnabled.main+xml'
        data = package(main_type=macro_type)
        self.assertEqual(direct(data)['status'], 'unsupported')
        with self.assertRaises(ParseError):
            from read_et import read_bytes
            read_bytes(data)

    def test_dtd_entities_and_unsafe_zip_members_are_still_rejected(self):
        for sheet_xml in (
                '<!DOCTYPE x [<!ENTITY e "boom">]>'
                f'<worksheet xmlns="{T}"><sheetData/></worksheet>',
                '<!DOCTYPE x SYSTEM "https://example.invalid/x.dtd">'
                f'<worksheet xmlns="{T}"><sheetData/></worksheet>'):
            with self.subTest(sheet_xml=sheet_xml):
                value = direct(sheet_with_rels(sheet_xml, None))
                self.assertEqual(value['status'], 'corrupt')
        unsafe = text_parts_of(package())
        unsafe['../evil.xml'] = '<evil/>'
        self.assertEqual(direct(build_package(unsafe))['status'], 'corrupt')
        duplicate = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(duplicate, 'w') as archive:
                archive.writestr('xl/workbook.xml', '<workbook/>')
                archive.writestr('xl/workbook.xml', '<workbook/>')
        self.assertEqual(direct(duplicate.getvalue())['status'], 'corrupt')


class CellToleranceTests(unittest.TestCase):
    def test_unknown_error_text_and_bad_iso_date_keep_saved_text(self):
        value = result(package(
            '<c r="A1" t="e"><v>#CUSTOM!</v></c>'
            '<c r="B1" t="d"><v>1900-02-29</v></c>'
            '<c r="C1" t="d"><v>2024-02-29</v></c>'))
        cells = cells_of(value)
        self.assertEqual((cells['A1']['type'], cells['A1']['value']),
                         ('error', '#CUSTOM!'))
        self.assertEqual((cells['B1']['type'], cells['B1']['value']), ('text', '1900-02-29'))
        self.assertNotIn('iso_date', cells['B1'])
        self.assertEqual(cells['B1']['source_cell_type'], 'd')
        self.assertEqual(cells['C1']['iso_date']['lexical'], '2024-02-29')
        self.assertIn('unknown_error_text_retained', codes(value))
        self.assertIn('iso_date_value_not_interpreted', codes(value))
        self.assertEqual(value['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertFalse(value['omissions_affect_cell_values'])

    def test_unknown_cell_children_do_not_discard_known_values(self):
        value = result(package(
            f'<c r="A1"><urn:extra xmlns:urn="urn:example"/><v>9</v></c>'))
        self.assertEqual(cells_of(value)['A1']['value'], 9)
        self.assertIn('unknown_cell_element_ignored', codes(value))

    def test_cell_value_coverage_variants(self):
        plain_sst = f'<sst xmlns="{T}"><si><t>only</t></si></sst>'
        clean = direct(package('<c r="A1"><v>1</v></c>',
                               overrides={'xl/sharedStrings.xml': plain_sst}))
        self.assertEqual((clean['cell_value_coverage'],
                          clean['omissions_affect_cell_values']),
                         ('complete_within_declared_sheets', False))
        missing = direct(package('<c r="A1"><f>1+1</f></c>',
                                 overrides={'xl/sharedStrings.xml': plain_sst}))
        self.assertEqual((missing['cell_value_coverage'],
                          missing['omissions_affect_cell_values']),
                         ('missing_formula_caches', True))
        skipped = direct(package('<c r="A1"><v>NaN</v></c>',
                                 overrides={'xl/sharedStrings.xml': plain_sst}))
        self.assertEqual((skipped['cell_value_coverage'],
                          skipped['omissions_affect_cell_values']),
                         ('unverified_due_to_best_effort_salvage', True))


class SheetMetadataTests(unittest.TestCase):
    def test_sheet_names_and_ids_are_repaired_with_issues(self):
        parts = text_parts_of(package())
        parts['xl/workbook.xml'] = (f'<workbook xmlns="{T}" xmlns:r="{R}"><sheets>'
                                    '<sheet name="Data" sheetId="7" r:id="s1"/>'
                                    '<sheet name="Data" sheetId="seven" r:id="s1"/>'
                                    '<sheet sheetId="9" r:id="s1"/></sheets></workbook>')
        value = result(build_package(parts))
        names = [sheet['name'] for sheet in value['sheets']]
        self.assertEqual(len(names), 3)
        self.assertEqual(len(set(names)), 3)
        self.assertIn('Data', names)
        self.assertIn('duplicate_sheet_name_retained', codes(value))
        self.assertIn('sheet_id_not_interpreted', codes(value))
        self.assertIn('sheet_name_not_interpreted', codes(value))
        for sheet in value['sheets']:
            self.assertEqual(sheet['cells']['A1']['value'], Decimal('12.5'))

    def test_sheet_count_cap_keeps_leading_sheets(self):
        parts = text_parts_of(package())
        extra_sheets = ''.join(f'<sheet name="S{index}" sheetId="{index + 8}" r:id="s1"/>'
                               for index in range(4))
        parts['xl/workbook.xml'] = parts['xl/workbook.xml'].replace(
            '</sheets>', extra_sheets + '</sheets>')
        with patch('wps_ooxml_sheet.MAX_SHEETS', 2):
            value = result(build_package(parts))
        self.assertIn('sheet_count_limit_reached', codes(value))
        self.assertEqual(len(value['sheets']), 2)
        self.assertTrue(value['omissions_affect_cell_values'])

    def test_unknown_workbook_and_style_metadata_are_tolerated(self):
        styles = (f'<styleSheet xmlns="{T}"><fonts count="1"><font/></fonts>'
                  '<cellXfs count="1"><xf numFmtId="14" applyNumberFormat="1"/></cellXfs>'
                  '<dxfs count="0"/><tableStyles count="0"/><extLst/></styleSheet>')
        parts = text_parts_of(package(
            '<c r="A1" s="0"><v>1</v></c>',
            workbook_extra='<bookViews/><definedNames><definedName name="x">Data!$A$1'
                           '</definedName></definedNames><calcPr/>'))
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace(
            '</Types>', f'<Override PartName="/xl/styles.xml" '
                        f'ContentType="{STYLES_TYPE}"/></Types>')
        parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].replace(
            '</Relationships>', f'<Relationship Id="sty" Type="{R}/styles" '
                                'Target="styles.xml"/></Relationships>')
        parts['xl/styles.xml'] = styles
        value = result(build_package(parts))
        self.assertEqual(cells_of(value)['A1']['value'], 1)
        self.assertIn('workbook_metadata_not_interpreted', codes(value))
        self.assertIn('style_metadata_not_interpreted', codes(value))
        self.assertEqual(len(value['styles']), 1)
        self.assertEqual(value['sheets'][0]['cells']['A1']['number_format_id_raw'], 14)


if __name__ == '__main__':
    unittest.main()
