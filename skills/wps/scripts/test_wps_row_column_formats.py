"""Synthetic style-source tests, not real WPS display evidence."""
import io
from pathlib import Path
import sys
import unittest
import zipfile
from xml.parsers import expat
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_wps_ooxml_sheet import package,result,T,P,R


def fixture(row_style='',columns='',cell_style=''):
    data=package()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts={name:z.read(name).decode() for name in z.namelist()}
    parts['[Content_Types].xml']=parts['[Content_Types].xml'].replace('</Types>','<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    parts['xl/_rels/workbook.xml.rels']=parts['xl/_rels/workbook.xml.rels'].replace('</Relationships>',f'<Relationship Id="sty" Type="{R}/styles" Target="styles.xml"/></Relationships>')
    parts['xl/styles.xml']=f'<styleSheet xmlns="{T}"><cellXfs count="3"><xf numFmtId="0" applyNumberFormat="1"/><xf numFmtId="10" applyNumberFormat="1"/><xf numFmtId="14" applyNumberFormat="0"/></cellXfs></styleSheet>'
    parts['xl/worksheets/sheet1.xml']=f'<worksheet xmlns="{T}">{columns}<sheetData><row r="1" {row_style}><c r="A1" {cell_style}><v>0.125</v></c></row></sheetData></worksheet>'
    return result(package(overrides=parts))


@unittest.skipUnless(expat.version_info>=(2,6,0),'requires Expat >=2.6')
class RowColumnFormats(unittest.TestCase):
    def test_single_row_source_interpreted_without_changing_raw_cell_style(self):
        out=fixture('s="1" customFormat="1"');item=out['sheets'][0]['cells']['A1']
        self.assertEqual(item['raw_numeric_text'],'0.125')
        self.assertEqual(item['xf_index'],0)
        self.assertEqual(item['numeric_interpretation']['display_text'],'12.50%')
        self.assertEqual(item['numeric_interpretation']['style_source']['kind'],'row')

    def test_single_column_source_and_explicit_cell_priority(self):
        cols='<cols><col min="1" max="1" style="1"/></cols>'
        self.assertEqual(fixture(columns=cols)['sheets'][0]['cells']['A1']['numeric_interpretation']['display_text'],'12.50%')
        cell=fixture(columns=cols,cell_style='s="0"')['sheets'][0]['cells']['A1']
        self.assertEqual(cell['numeric_interpretation']['kind'],'number')
        self.assertNotIn('style_source',cell['numeric_interpretation'])

    def test_conflict_inactive_invalid_and_parent_format_stay_unknown(self):
        for row,cols in [('s="1"',''),('s="99" customFormat="1"',''),('s="2" customFormat="1"','')]:
            with self.subTest(row=row,cols=cols):
                out=fixture(row,cols);item=out['sheets'][0]['cells']['A1']
                self.assertEqual(item['numeric_interpretation']['status'],'unknown')
                self.assertNotIn('display_text',item['numeric_interpretation'])
                self.assertEqual(out['status'],'partial')

if __name__=='__main__':unittest.main()
