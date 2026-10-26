"""Synthetic internal hyperlink metadata, not real WPS provenance."""
import sys
from pathlib import Path
import unittest
from xml.parsers import expat
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_wps_ooxml_sheet import package,result
@unittest.skipUnless(expat.version_info >= (2,6,0),'requires Expat >=2.6')
class InternalLinks(unittest.TestCase):
    def test_metadata_not_executed_or_resolved(self):
        for strict in (False,True):
            v=result(package(strict=strict,sheet_extra='<hyperlinks><hyperlink ref="A1" location="Data!B2" display="跳转" tooltip="提示"/></hyperlinks>'))
            self.assertEqual(v['status'],'partial')
            self.assertEqual(str(v['sheets'][0]['cells']['A1']['value']),'12.5')
            h=v['sheets'][0]['hyperlinks'][0]
            self.assertEqual((h['cell'],h['location_raw'],h['display_raw']),('A1','Data!B2','跳转'))
            self.assertFalse(h['followed']);self.assertFalse(h['target_resolved'])
    def test_invalid_duplicate_or_relationship_refused(self):
        for body in ('<hyperlink ref="XFE1" location="x"/>','<hyperlink ref="A1"/>','<hyperlink ref="A1" location="x"/><hyperlink ref="A1" location="y"/>','<hyperlink ref="A1" location="x" id="rId1"/>'):
            v=result(package(sheet_extra='<hyperlinks>'+body+'</hyperlinks>'))
            self.assertNotIn(v['status'],('ok','partial'))
        self.assertNotIn(result(package(sheet_extra='<hyperlinks/><hyperlinks/>'))['status'],('ok','partial'))
    def test_count_budget(self):
        v=result(package(sheet_extra='<hyperlinks>'+ '<hyperlink ref="A1" location="x"/>'*4097+'</hyperlinks>'))
        self.assertEqual(v['status'],'resource_limit')
if __name__=='__main__':unittest.main()
