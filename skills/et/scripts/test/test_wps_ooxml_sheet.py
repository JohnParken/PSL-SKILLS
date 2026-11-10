"""Independently specified synthetic OOXML packages, not WPS provenance."""
import io
import json
from decimal import Decimal
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from xml.parsers import expat
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from read_wps import read, encode_result

T='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
S='http://purl.oclc.org/ooxml/spreadsheetml/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
RS='http://purl.oclc.org/ooxml/officeDocument/relationships'
P='http://schemas.openxmlformats.org/package/2006/relationships'
C='http://schemas.openxmlformats.org/package/2006/content-types'

def package(cells='<c r="A1"><v>12.5</v></c>',strict=False,workbook_extra='',sheet_extra='',overrides=None):
    ns,rel=(S,RS) if strict else (T,R)
    parts={
      '[Content_Types].xml': (
          f'<Types xmlns="{C}">'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
          '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
          '</Types>'
      ),
      '_rels/.rels':f'<Relationships xmlns="{P}"><Relationship Id="root" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      'xl/workbook.xml':f'<workbook xmlns="{ns}" xmlns:r="{rel}"><workbookPr date1904="1"/><sheets><sheet name="Data" sheetId="7" state="hidden" r:id="s1"/></sheets>{workbook_extra}</workbook>',
      'xl/_rels/workbook.xml.rels': (
          f'<Relationships xmlns="{P}"><Relationship Id="s1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/>'
          f'<Relationship Id="str" Type="{rel}/sharedStrings" Target="sharedStrings.xml"/></Relationships>'
      ),
      'xl/sharedStrings.xml':f'<sst xmlns="{ns}" count="1" uniqueCount="1"><si><r><t>部门</t></r><r><t>名称</t></r></si></sst>',
      'xl/worksheets/sheet1.xml':f'<worksheet xmlns="{ns}"><sheetData><row r="1">{cells}</row></sheetData>{sheet_extra}</worksheet>'}
    if overrides:
        parts.update(overrides)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name,text in parts.items():
            z.writestr(name,text)
    return out.getvalue()

def result(data):
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'source.et'
        p.write_bytes(data)
        return read(p)

@unittest.skipUnless(expat.version_info >= (2,6,0),'requires Expat >=2.6')
class SheetTests(unittest.TestCase):
    def success(self,data):
        value=result(data)
        self.assertIn(value['status'],('ok','partial'),value)
        return value
    def rejected(self,data):
        value=result(data)
        self.assertNotIn(value['status'],('ok','partial'),value)
        return value
    def test_strict_transitional_types_and_exact_decimal(self):
        raw = (
            '<c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>000123</t></is></c><c r="C1"><v>123456789012345678.125</v>'
            '</c><c r="D1" t="b"><v>1</v></c><c r="E1" t="e"><v>#DIV/0!</v></c><c r="F1"/>'
        )
        for strict in (False,True):
            value=self.success(package(raw,strict))
            cells=value['sheets'][0]['cells']
            self.assertEqual(value['schema_version'],'ooxml_spreadsheet_readonly_v1')
            self.assertTrue(value['spreadsheet_parsed'])
            self.assertEqual(value['date_system'],'1904')
            self.assertEqual(value['sheets'][0]['name'],'Data')
            self.assertEqual(value['sheets'][0]['hidden_state'],1)
            self.assertEqual([cells[c]['value'] for c in ('A1','B1','C1','D1','E1','F1')],['部门名称','000123',Decimal('123456789012345678.125'),True,'#DIV/0!',None])
    def test_formula_saved_cache_no_recalculation(self):
        value=self.success(package('<c r="A1"><f>1+1</f><v>999</v></c><c r="B1" t="str"><f>"x"</f><v>缓存</v></c><c r="C1"><f>2+2</f></c>'))
        cells=value['sheets'][0]['cells']
        self.assertEqual(cells['A1']['value'],Decimal(999))
        self.assertEqual(cells['A1']['cache_status'],'unverified')
        self.assertEqual(cells['B1']['value'],'缓存')
        self.assertEqual(cells['C1']['cache_status'],'missing')
        self.assertEqual(value['status'],'partial')

    def test_rich_text_formats_in_inline_and_shared_strings(self):
        nodes = ('<r><rPr><rFont val="Aptos"/><b/><i val="0"/><color rgb="FFFF0000"/></rPr><t>强调</t></r>'
                 '<r><rPr><i/><color theme="1"/></rPr><t>文字😀</t></r>')
        expected = [{'text': '强调', 'font': {'font_name': 'Aptos', 'bold': True, 'italic': False, 'color': {'rgb': 'FFFF0000'}}},
                    {'text': '文字😀', 'font': {'italic': True, 'color': {'theme': '1'}}}]
        xml = f'<sst xmlns="{T}" count="2" uniqueCount="2"><si>{nodes}</si><si><r><t>未引用</t></r></si></sst>'
        raw = f'<c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is>{nodes}</is></c>'
        result = self.success(package(raw, overrides={'xl/sharedStrings.xml': xml}))
        cells = result['sheets'][0]['cells']
        self.assertEqual([cells[key]['value'] for key in ('A1', 'B1')], ['强调文字😀'] * 2)
        self.assertEqual(result['shared_string_formatting'], [{'sst_index': cells['A1']['sst_index'], 'segments': expected}])
        self.assertEqual(cells['B1']['rich_text']['segments'], expected)

    def test_saved_row_and_column_dimensions_are_optional_metadata(self):
        sheet = (f'<worksheet xmlns="{T}"><cols><col min="1" max="2" width="12.5" hidden="1"/>'
                 '<col min="3" max="3" width="NaN"/><col min="4" max="4"/></cols><sheetData>'
                 '<row r="1" ht="18.25"><c r="A1"><v>12.5</v></c></row><row r="2" ht="-2"/><row r="3"/>'
                 '</sheetData></worksheet>')
        value = self.success(package(overrides={'xl/worksheets/sheet1.xml': sheet}))
        without_dimensions = sheet.replace(' width="12.5"', '').replace(' width="NaN"', '').replace(' ht="18.25"', '').replace(' ht="-2"', '')
        baseline = self.success(package(overrides={'xl/worksheets/sheet1.xml': without_dimensions}))
        self.assertEqual(value['status'], baseline['status'])
        self.assertEqual(value['reading_assessment'], baseline['reading_assessment'])
        self.assertEqual(value['sheets'][0]['cells'], baseline['sheets'][0]['cells'])
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

    def test_duplicate_cell_bad_type_values_and_coordinates(self):
        for raw in ('<c r="A1"/><c r="A1"/>','<c r="A1" t="mystery"><v>x</v></c>','<c r="A1" t="b"><v>2</v></c>',
                    '<c r="A1" t="s"><v>9</v></c>','<c r="A1"><v>NaN</v></c>','<c r="XFE1"><v>1</v></c>','<c r="A1048577"><v>1</v></c>'):
            with self.subTest(raw=raw):
                self.rejected(package(raw))
    def test_merges_and_overlap(self):
        value=self.success(package(sheet_extra='<mergeCells count="1"><mergeCell ref="A1:B2"/></mergeCells>'))
        self.assertEqual(value['sheets'][0]['merges'][0]['range'],'A1:B2')
        self.rejected(package(sheet_extra='<mergeCells count="2"><mergeCell ref="A1:B2"/><mergeCell ref="B2:C3"/></mergeCells>'))
    def test_duplicate_sheet_id_missing_reference_and_external(self):
        base=package()
        with zipfile.ZipFile(io.BytesIO(base)) as z:
            parts={n:z.read(n).decode() for n in z.namelist()}
        self.rejected(package(overrides={'xl/workbook.xml':parts['xl/workbook.xml'].replace('</sheets>','<sheet name="Other" sheetId="7" r:id="s1"/></sheets>')}))
        self.rejected(package(overrides={'xl/workbook.xml':parts['xl/workbook.xml'].replace('r:id="s1"','r:id="missing"')}))
        self.rejected(package(overrides={
            'xl/_rels/workbook.xml.rels': parts['xl/_rels/workbook.xml.rels'].replace(
                'Target="worksheets/sheet1.xml"', 'Target="https://example.invalid/evil" TargetMode="External"',
            ),
        }))
    def test_mixed_namespace_and_xml_entities(self):
        self.rejected(package(overrides={'xl/worksheets/sheet1.xml':f'<worksheet xmlns="{S}"><sheetData/></worksheet>'}))
        self.rejected(package(overrides={'xl/worksheets/sheet1.xml':'<!DOCTYPE x [<!ENTITY secret "boom">]><worksheet xmlns="'+T+'"><sheetData/></worksheet>'}))
    def test_empty_unknown_type_and_namespace_child_refused(self):
        self.rejected(package('<c r="A1" t="mystery"/>'))
        self.rejected(package('<c r="A1"><v xmlns="'+S+'">12</v></c>'))

    def test_cell_and_column_metadata_budgets(self):
        with patch('wps_ooxml_sheet.MAX_CELLS',1):
            value=self.rejected(package('<c r="A1"/><c r="B1"/>'))
            self.assertEqual(value['status'],'resource_limit')
        columns='<cols>'+''.join('<col min="'+str(i)+'" max="'+str(i)+'"/>' for i in range(1,4098))+'</cols>'
        value=self.rejected(package(sheet_extra=columns))
        self.assertEqual(value['status'],'resource_limit')

    def test_corrupt_compression_is_structured_failure(self):
        from wps_ooxml_sheet import read_bytes
        from read_wps import read_bytes as unified, ParseError
        raw=package()
        with patch('wps_ooxml_sheet.read_part', side_effect=zlib.error('invalid compressed data')):
            result=read_bytes(raw)
            self.assertEqual(result['status'],'corrupt')
            self.assertIsNone(result['text'])
            with self.assertRaises(ParseError) as caught:
                unified(raw)
            self.assertEqual(caught.exception.status,'invalid_or_unsupported')

    def test_cli_json_decimal_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'source.et'
            p.write_bytes(package('<c r="A1"><v>0.1234567890123456789</v></c>'))
            proc=subprocess.run([sys.executable,'-I','-S','-B',str((Path(__file__).resolve().parents[1] / 'read_wps.py')),str(p)],capture_output=True,text=True,env={'PATH':''},timeout=10)
            self.assertIn(proc.returncode,(0,3),proc.stderr)
            out=json.loads(proc.stdout)
            self.assertEqual(out['sheets'][0]['cells']['A1']['value'],'0.1234567890123456789')

if __name__=='__main__':
    unittest.main()
