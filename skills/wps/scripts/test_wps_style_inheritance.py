"""Synthetic literal XF ancestry; no real WPS rendering evidence."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
from xml.parsers import expat
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wps_style_formats import NumberFormats, parse_xfs


def xf(fmt, apply=None, parent=None):
    attrs={'numFmtId':str(fmt)}
    if apply is not None:attrs['applyNumberFormat']=str(apply)
    if parent is not None:attrs['xfId']=str(parent)
    return {'number_format_id_raw':fmt,'apply_number_format':apply in ('1',1,'true'), 'attributes_raw':attrs}


class StyleAncestry(unittest.TestCase):
    def test_linked_agreement_direct_override_and_disabled_parent(self):
        resolver=NumberFormats([xf(10,parent=0),xf(14,1,0),xf(10,0,1)], [xf(10),xf(10,0)])
        self.assertEqual(resolver.resolve(0)['number_format_id'],10)
        self.assertEqual(resolver.resolve(0)['source']['style_xf_index'],0)
        self.assertEqual(resolver.resolve(1)['number_format_id'],14)
        self.assertEqual(resolver.resolve(2)['reason'],'parent_number_format_not_applied')

    def test_conflict_does_not_substitute_parent_and_cache_is_not_mutable(self):
        resolver=NumberFormats([xf(0,0,0)], [xf(14)])
        resolved=resolver.resolve(0)
        self.assertEqual(resolved['reason'],'cell_parent_number_format_conflict')
        self.assertNotIn('number_format_id',resolved)
        resolved['status']='resolved'
        self.assertEqual(resolver.resolve(0)['status'],'unknown')

    def test_invalid_parent_cycle_forbidden_chain_depth_and_bounds(self):
        cases=[([xf(10,parent=1)],[xf(10)],'style_parent_outside_collection'),
               ([xf(10,parent=0)],[xf(10,parent=0)],'style_parent_cycle'),
               ([xf(10,parent=0)],[xf(10,parent=1),xf(10,parent=0)],'style_parent_cycle'),
               ([xf(10,parent=0)],[xf(10,parent=1),xf(10)],'style_parent_chain_not_allowed')]
        for cells,parents,reason in cases:
            with self.subTest(reason=reason):self.assertEqual(NumberFormats(cells,parents).resolve(0)['reason'],reason)
        with patch('wps_style_formats.MAX_STYLE_DEPTH',2):
            parents=[xf(10,parent=1),xf(10,parent=2),xf(10)]
            self.assertEqual(NumberFormats([xf(10,parent=0)],parents).resolve(0)['reason'],'style_parent_depth_limit')

    def test_collection_count_flags_and_budget(self):
        self.assertEqual(parse_xfs(fromstring('<cellXfs count="1"><xf numFmtId="10"/></cellXfs>'),lambda n:n)[0]['number_format_id_raw'],10)
        for xml in ('<cellXfs count="2"><xf/></cellXfs>', '<cellXfs><xf applyNumberFormat="yes"/></cellXfs>', '<cellXfs><xf numFmtId="999999"/></cellXfs>'):
            with self.assertRaises(ValueError):parse_xfs(fromstring(xml),lambda n:n)
        with patch('wps_style_formats.MAX_XFS',1),self.assertRaises(ValueError):
            parse_xfs(fromstring('<cellXfs><xf/><xf/></cellXfs>'),lambda n:n)


@unittest.skipUnless(expat.version_info >=(2,6,0),'requires Expat >=2.6')
class StyleIntegration(unittest.TestCase):
    def test_cell_and_row_linked_format_agreement_and_conflict(self):
        import io,zipfile
        from test_wps_ooxml_sheet import package,result,T,R
        with zipfile.ZipFile(io.BytesIO(package())) as archive:parts={n:archive.read(n).decode() for n in archive.namelist()}
        parts['[Content_Types].xml']=parts['[Content_Types].xml'].replace('</Types>','<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        parts['xl/_rels/workbook.xml.rels']=parts['xl/_rels/workbook.xml.rels'].replace('</Relationships>',f'<Relationship Id="sty" Type="{R}/styles" Target="styles.xml"/></Relationships>')
        parts['xl/styles.xml']=f'<styleSheet xmlns="{T}"><cellStyleXfs count="1"><xf numFmtId="10"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="10" xfId="0"/><xf numFmtId="0" xfId="0"/></cellXfs></styleSheet>'
        parts['xl/worksheets/sheet1.xml']=f'<worksheet xmlns="{T}"><sheetData><row r="1" s="0" customFormat="1"><c r="A1"><v>0.125</v></c><c r="B1" s="1"><v>0.125</v></c></row></sheetData></worksheet>'
        out=result(package(overrides=parts));cells=out['sheets'][0]['cells']
        self.assertEqual(cells['A1']['numeric_interpretation']['display_text'],'12.50%')
        self.assertEqual(cells['A1']['numeric_interpretation']['style_source']['kind'],'row')
        self.assertEqual(cells['B1']['numeric_interpretation']['reason'],'cell_parent_number_format_conflict')
        self.assertEqual(str(cells['A1']['value']),'0.125')
        self.assertEqual(len(out['style_xfs']),1)

if __name__=='__main__':unittest.main()
