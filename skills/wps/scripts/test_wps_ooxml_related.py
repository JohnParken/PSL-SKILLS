"""Synthetic OPC relationship fixtures; no WPS producer evidence."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wps_ooxml as reader
W=reader.W
R=reader.R
P=reader.RELATIONSHIPS


def package(body='', rels='', parts=()):
    types=['<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>']
    for part,kind,text in parts:
        types.append('<Override PartName="/'+part+'" ContentType="'+reader.RELATED_TYPES[kind][1]+'"/>')
    files={'[Content_Types].xml':'<Types xmlns="'+reader.CONTENT_TYPES+'">'+''.join(types)+'</Types>',
           '_rels/.rels':'<Relationships xmlns="'+P+'"><Relationship Id="main" Type="'+R+'/officeDocument" Target="word/document.xml"/></Relationships>',
           'word/document.xml':'<w:document xmlns:w="'+W+'" xmlns:r="'+R+'"><w:body>'+body+'</w:body></w:document>'}
    if rels: files['word/_rels/document.xml.rels']='<Relationships xmlns="'+P+'">'+rels+'</Relationships>'
    for part,kind,text in parts: files[part]=text
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        for name,value in files.items(): archive.writestr(name,value)
    return output.getvalue()


def rel(identity,kind,target,mode='Internal'):
    return '<Relationship Id="'+identity+'" Type="'+R+'/'+kind+'" Target="'+target+'" TargetMode="'+mode+'"/>'


def root(kind,text):
    tag=reader.RELATED_TYPES[kind][0]
    return '<w:'+tag+' xmlns:w="'+W+'">'+text+'</w:'+tag+'>'


def paragraph(text): return '<w:p><w:r><w:t>'+text+'</w:t></w:r></w:p>'


@unittest.skipUnless(reader.expat.version_info >= (2,6,0),'requires Expat >=2.6')
class RelatedTests(unittest.TestCase):
    def test_relationship_custom_filename_and_main_stays_main(self):
        body=paragraph('正文😀')+'<w:sectPr><w:headerReference w:type="default" r:id="h"/><w:footerReference w:type="first" r:id="f"/></w:sectPr>'
        data=package(body,rel('h','header','custom/head.xml')+rel('f','footer','custom/foot.xml'),
                     [('word/custom/head.xml','header',root('header',paragraph('页眉中'))),
                      ('word/custom/foot.xml','footer',root('footer','')),
                      ('word/header1.xml','header','BROKEN UNRELATED')])
        result=reader.read_bytes(data,include_related=True)
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual((result['text'],result['paragraphs']),('正文😀',['正文😀']))
        self.assertEqual([x['text'] for x in result['related_stories']],['页眉中',''])
        self.assertEqual(result['related_stories'][0]['source']['part'],'word/custom/head.xml')
        self.assertEqual(result['related_stories'][0]['references'][0]['relationship_id'],'h')
        self.assertEqual(result['related_stories'][0]['page_mapping'],'unknown')
        self.assertNotIn('related_stories',reader.read_bytes(data))

    def test_notes_comments_ids_only_no_unreferenced_text(self):
        body=paragraph('主')+'<w:p><w:r><w:footnoteReference w:id="2"/><w:endnoteReference w:id="3"/><w:commentReference w:id="0"/></w:r></w:p>'
        parts=[];relations=''
        for kind,singular,identity,expected in [('footnotes','footnote','2','脚注'),('endnotes','endnote','3','尾注'),('comments','comment','0','批注')]:
            entries='<w:'+singular+' w:id="'+identity+'">'+paragraph(expected)+'</w:'+singular+'><w:'+singular+' w:id="99">'+paragraph('UNREFERENCED')+'</w:'+singular+'>'
            parts.append(('word/custom_'+kind+'.xml',kind,root(kind,entries)))
            relations+=rel(kind,kind,'custom_'+kind+'.xml')
        result=reader.read_bytes(package(body,relations,parts),include_related=True)
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual([x['text'] for x in result['related_stories']],['脚注','尾注','批注'])
        self.assertEqual([x['source']['id'] for x in result['related_stories']],['2','3','0'])
        self.assertEqual(result['text'],'主\n')

    def test_unlinked_parts_not_read_even_without_relationships(self):
        result=reader.read_bytes(package(paragraph('Main'),parts=[('word/header1.xml','header','bad xml')]),include_related=True)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['related_stories'],[])

    def test_missing_wrong_external_and_duplicate_relationship(self):
        body='<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>'
        good=('word/h.xml','header',root('header',paragraph('H')))
        for relationships,parts,status in [('',[good],'corrupt'),(rel('h','footer','h.xml'),[good],'corrupt'),
                (rel('h','header','h.xml','External'),[good],'unsupported'),
                (rel('h','header','h.xml')*2,[good],'corrupt'),(rel('h','header','missing.xml'),[],'corrupt')]:
            with self.subTest(relationships=relationships):
                result=reader.read_bytes(package(body,relationships,parts),include_related=True)
                self.assertEqual(result['status'],status,result)
                self.assertIsNone(result['text'])

    def test_definition_ids_and_reference_bounds(self):
        body='<w:p><w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
        for entries in ('<w:footnote w:id="3"/>','<w:footnote w:id="2"/><w:footnote w:id="2"/>','<w:footnote w:id="2147483648"/>', '<w:footnote w:id="2" w:type="separator"/>'):
            result=reader.read_bytes(package(body,rel('n','footnotes','n.xml'),[('word/n.xml','footnotes',root('footnotes',entries))]),include_related=True)
            self.assertEqual(result['status'],'corrupt',result)
        for identity in ('-1','2147483648','a','0'*20):
            result=reader.read_bytes(package(body.replace('"2"','"'+identity+'"')),include_related=True)
            self.assertEqual(result['status'],'corrupt',result)

    def test_revision_references_do_not_leak_and_repeated_ids_are_linked(self):
        body='<w:del><w:p><w:r><w:footnoteReference w:id="9"/></w:r></w:p></w:del><w:p><w:r><w:commentReference w:id="0"/><w:commentReference w:id="0"/></w:r></w:p>'
        data=package(body,rel('c','comments','c.xml'),[('word/c.xml','comments',root('comments','<w:comment w:id="0">'+paragraph('C')+'</w:comment>'))])
        result=reader.read_bytes(data,include_related=True)
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(len(result['related_stories']),1)
        self.assertEqual(len(result['related_stories'][0]['references']),2)
        self.assertEqual(reader.read_bytes(data,include_related=True,revisions='all')['status'],'corrupt')

    def test_target_and_content_type_guards(self):
        body='<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>'
        for target in ('../../escape.xml','https://example/a','h.xml#x','%68.xml','h.xml?x','\\h.xml'):
            result=reader.read_bytes(package(body,rel('h','header',target)),include_related=True)
            self.assertNotEqual(result['status'],'ok')
        data=package(body,rel('h','header','h.xml'),[('word/h.xml','footer',root('header',''))])
        self.assertEqual(reader.read_bytes(data,include_related=True)['status'],'corrupt')

    def test_reference_and_region_budgets(self):
        body='<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>'
        data=package(body,rel('h','header','h.xml'),[('word/h.xml','header',root('header',''))])
        for name in ('MAX_RELATED_REFERENCES','MAX_RELATED_REGIONS'):
            with patch.object(reader,name,0):
                self.assertEqual(reader.read_bytes(data,include_related=True)['status'],'resource_limit')

    def test_section_duplicates_and_reference_location(self):
        reference='<w:headerReference w:type="default" r:id="h"/>'
        for body in ('<w:sectPr>'+reference*2+'</w:sectPr>',reference):
            result=reader.read_bytes(package(body),include_related=True)
            self.assertEqual(result['status'],'corrupt',result)

    def test_related_partial_and_definition_budget(self):
        body='<w:p><w:r><w:commentReference w:id="1"/></w:r></w:p>'
        entries='<w:comment w:id="1">'+paragraph('保留')+'<w:p><w:drawing/></w:p></w:comment>'
        data=package(body,rel('c','comments','c.xml'),[('word/c.xml','comments',root('comments',entries))])
        result=reader.read_bytes(data,include_related=True)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['related_stories'][0]['status'],'partial')
        self.assertTrue(result['related_stories'][0]['issues'])
        with patch.object(reader,'MAX_RELATED_REGIONS',0):
            self.assertEqual(reader.read_bytes(data,include_related=True)['status'],'resource_limit')

    def test_nested_related_reference_not_silent(self):
        body='<w:p><w:r><w:commentReference w:id="1"/></w:r></w:p>'
        entries='<w:comment w:id="1"><w:p><w:r><w:commentReference w:id="2"/></w:r></w:p></w:comment>'
        data=package(body,rel('c','comments','c.xml'),[('word/c.xml','comments',root('comments',entries))])
        result=reader.read_bytes(data,include_related=True)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['related_stories'][0]['issues'][0]['code'],'related_nested_reference_not_followed')

if __name__=='__main__': unittest.main()
