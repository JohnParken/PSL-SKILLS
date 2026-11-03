"""Synthetic Note anchor packages, not real WPS provenance."""
from pathlib import Path
import io
import sys
import unittest
import zipfile
from unittest.mock import patch
from xml.parsers import expat
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_wps_sheet_comments import sample
from test_wps_ooxml_sheet import package,result,R,P


def fixture(vml=None):
    with zipfile.ZipFile(io.BytesIO(sample())) as archive:
        parts={name:archive.read(name).decode() for name in archive.namelist()}
    parts['[Content_Types].xml']=parts['[Content_Types].xml'].replace('</Types>','<Default Extension="vml" ContentType="application/vnd.openxmlformats-officedocument.vmlDrawing"/></Types>')
    parts['xl/worksheets/_rels/sheet1.xml.rels']=parts['xl/worksheets/_rels/sheet1.xml.rels'].replace('</Relationships>','<Relationship Id="v1" Type="'+R+'/vmlDrawing" Target="../drawings/notes.vml"/></Relationships>')
    parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace('<worksheet ', '<worksheet xmlns:r="'+R+'" ',1).replace('</worksheet>','<legacyDrawing r:id="v1"/></worksheet>')
    parts['xl/drawings/notes.vml']=vml or '<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:x="urn:schemas-microsoft-com:office:excel"><v:shape id="note1"><v:textbox><div/></v:textbox><x:ClientData ObjectType="Note"><x:Anchor>1,15,0,2,3,15,3,16</x:Anchor><x:Row>1</x:Row><x:Column>1</x:Column></x:ClientData></v:shape></xml>'
    return parts


@unittest.skipUnless(expat.version_info >= (2,6,0),'requires Expat >=2.6')
class NoteAnchors(unittest.TestCase):
    def test_source_and_cell_anchor_independent_rectangle(self):
        value=result(package(overrides=fixture()))
        self.assertEqual(value['status'],'partial',value)
        note=value['sheets'][0]['comments'][0]
        self.assertEqual((note['cell'],note['text']),('B2','核对余额'))
        self.assertEqual(note['anchor']['components'],[1,15,0,2,3,15,3,16])
        self.assertEqual(note['anchor']['cell_row_zero_based'],1)
        self.assertEqual(note['anchor']['source']['part'],'xl/drawings/notes.vml')
        self.assertFalse(note['anchor']['layout_decoded'])
        self.assertEqual(value['cell_count'],1)

    def test_unbound_duplicate_missing_or_reversed_anchor(self):
        raw=fixture()['xl/drawings/notes.vml']
        for value in (raw.replace('<x:Row>1','<x:Row>2'),raw.replace('<x:Row>1</x:Row>',''),raw.replace('<x:Row>1</x:Row>','<x:Row>1</x:Row>'*2),raw.replace('1,15,0,2,3,15,3,16','3,15,0,2,1,15,3,16'),raw.replace('<x:Column>1','<x:Column>16384')):
            self.assertNotIn(result(package(overrides=fixture(value)))['status'],('ok','partial'))
        with patch('wps_vml_notes.MAX_SHAPES',0):
            self.assertEqual(result(package(overrides=fixture()))['status'],'resource_limit')

    def test_active_content_text_and_external_relations_refused(self):
        raw=fixture()['xl/drawings/notes.vml']
        for value in (raw.replace('<div/>','<div>lost</div>'),raw.replace('<v:shape id="note1"','<v:shape unknown="x" id="note1"'),raw.replace('<v:shape id="note1"','<v:shape href="https://example.invalid" id="note1"'),raw.replace('<x:Row>1</x:Row>','<x:FmlaMacro>RUN</x:FmlaMacro>')):
            self.assertNotIn(result(package(overrides=fixture(value)))['status'],('ok','partial'))
        parts=fixture();parts['xl/drawings/_rels/notes.vml.rels']='<Relationships xmlns="'+P+'"><Relationship Id="i" Type="'+R+'/image" Target="https://example.invalid" TargetMode="External"/></Relationships>'
        self.assertEqual(result(package(overrides=parts))['status'],'unsupported')

    def test_relationship_id_type_missing_and_duplicate(self):
        for replacement in ('r:id="bad"','r:id="v1"/><legacyDrawing r:id="v1"'):
            parts=fixture();parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace('r:id="v1"',replacement)
            self.assertNotIn(result(package(overrides=parts))['status'],('ok','partial'))
        parts=fixture();parts['[Content_Types].xml']=parts['[Content_Types].xml'].replace('application/vnd.openxmlformats-officedocument.vmlDrawing','application/xml')
        self.assertEqual(result(package(overrides=parts))['status'],'corrupt')

if __name__=='__main__':unittest.main()
