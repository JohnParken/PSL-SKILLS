"""Independent synthetic visibility expectations; no real WPS acceptance evidence."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
import wps_ooxml as reader
from test_wps_ooxml import package
from test_wps_ooxml_related import package as related_package, rel, root


def run(text,props=''):
    return '<w:r>'+('<w:rPr>'+props+'</w:rPr>' if props else '')+'<w:t>'+text+'</w:t></w:r>'


def styles_package(body,styles):
    data=package(body)
    src=zipfile.ZipFile(io.BytesIO(data))
    target=io.BytesIO()
    with zipfile.ZipFile(target,'w') as archive:
        for name in src.namelist():
            value=src.read(name)
            if name=='[Content_Types].xml':
                value=value.decode().replace('</Types>','<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
            archive.writestr(name,value)
        archive.writestr('word/styles.xml','<w:styles xmlns:w="'+reader.W+'">'+styles+'</w:styles>')
        archive.writestr('word/_rels/document.xml.rels','<Relationships xmlns="'+reader.RELATIONSHIPS+'">'+rel('s','styles','styles.xml')+'</Relationships>')
    return target.getvalue()


@unittest.skipUnless(reader.expat.version_info >= (2,6,0),'requires Expat >=2.6')
class HiddenTests(unittest.TestCase):
    def read(self,data,policy='exclude',**options):
        return reader.read_bytes(data,hidden_text=policy,**options)

    def test_direct_flags_false_values_and_default_compatibility(self):
        data=package('<w:p>'+run('A')+run('SECRET','<w:vanish/>')+run('WEBSECRET','<w:webHidden/>')+run('B','<w:vanish w:val="0"/><w:webHidden w:val="false"/>')+'</w:p>')
        self.assertEqual(reader.read_bytes(data)['text'],'ASECRETWEBSECRETB')
        self.assertEqual(self.read(data)['text'],'AWEBSECRETB')
        self.assertEqual(self.read(data,'include')['text'],'ASECRETWEBSECRETB')
        self.assertEqual(self.read(data,'annotate')['text'],'ASECRETWEBSECRETB')
        self.assertNotIn('ASECRET',json.dumps(self.read(data),ensure_ascii=False))
        self.assertEqual(self.read(data)['status'],'partial')

    def test_revision_views_remain_independent_of_hidden_selection(self):
        body='<w:p>'+run('A')+'<w:ins>'+run('I')+run('INSERTSECRET','<w:vanish/>')+'</w:ins><w:del><w:r><w:delText>D</w:delText></w:r></w:del><w:moveFrom>'+run('F')+'</w:moveFrom><w:moveTo>'+run('T')+'</w:moveTo></w:p>'
        for view,expected in [('accepted','AIT'),('original','ADF'),('all','AIDFT')]:
            with self.subTest(view=view):
                result=self.read(package(body),revisions=view)
                self.assertEqual(result['text'],expected)
                self.assertNotIn('INSERTSECRET',json.dumps(result))

    def test_body_related_and_nested_tables_share_policy(self):
        table='<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc><w:p>'+run('C')+run('CELLSECRET','<w:vanish/>')+'</w:p></w:tc></w:tr></w:tbl>'
        body='<w:p>'+run('B')+run('BODYSECRET','<w:vanish/>')+'</w:p>'+table+'<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>'
        header=root('header','<w:p>'+run('H')+run('HEADERSECRET','<w:vanish/>')+'</w:p>'+table)
        data=related_package(body,rel('h','header','header.xml'),[('word/header.xml','header',header)])
        result=self.read(data,include_related=True)
        self.assertEqual(result['text'],'B\nC')
        self.assertEqual(result['related_stories'][0]['text'],'H\nC')
        self.assertEqual([t['rows'][0]['cells'][0]['text'] for t in result['tables']],['C','C'])
        self.assertNotIn('SECRET',json.dumps(result,ensure_ascii=False))

    def test_related_visibility_unknown_marks_region_partial(self):
        body='<w:p>'+run('Body')+'</w:p><w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>'
        header=root('header','<w:p>'+run('WebOnly','<w:webHidden/>')+'</w:p>')
        data=related_package(body,rel('h','header','header.xml'),[('word/header.xml','header',header)])
        result=self.read(data,include_related=True)
        story=result['related_stories'][0]
        self.assertEqual(story['text'],'WebOnly')
        self.assertEqual(story['status'],'partial')
        self.assertEqual([x['code'] for x in story['issues']],['visibility_unknown'])
        self.assertEqual(sum(x.get('code')=='visibility_unknown' for x in result['diagnostics']),1)

    def test_property_revision_partial_and_no_old_property_text(self):
        props='<w:rPrChange w:id="1"><w:rPr><w:vanish/></w:rPr></w:rPrChange>'
        result=self.read(package('<w:p>'+run('Shown',props)+'</w:p>'))
        self.assertEqual(result['text'],'Shown')
        self.assertEqual(result['status'],'partial')

    def test_field_instruction_never_executed_or_copied_to_trace(self):
        data=package('<w:p><w:r><w:instrText>COMMANDSECRET</w:instrText><w:t>Cached</w:t></w:r>'+run('HIDDENSECRET','<w:vanish/>')+'</w:p>')
        result=self.read(data)
        self.assertEqual(result['text'],'Cached')
        self.assertNotIn('COMMANDSECRET',json.dumps(result))
        self.assertNotIn('HIDDENSECRET',json.dumps(result))

    def test_style_based_on_direct_false_and_unknown_reference(self):
        styles='<w:style w:type="character" w:styleId="Base"><w:rPr><w:vanish/></w:rPr></w:style><w:style w:type="character" w:styleId="Child"><w:basedOn w:val="Base"/></w:style>'
        body='<w:p>'+run('STYLESECRET','<w:rStyle w:val="Child"/>')+run('Visible','<w:rStyle w:val="Child"/><w:vanish w:val="0"/>')+'</w:p>'
        result=self.read(styles_package(body,styles))
        self.assertEqual(result['text'],'STYLESECRETVisible')
        self.assertEqual(result['status'],'partial')
        unknown=self.read(styles_package('<w:p>'+run('Keep','<w:rStyle w:val="Missing"/>')+'</w:p>',styles))
        self.assertEqual(unknown['text'],'Keep')
        self.assertEqual(unknown['status'],'partial')

    def test_style_cycle_is_bounded_and_not_successful_guess(self):
        styles='<w:style w:type="character" w:styleId="A"><w:basedOn w:val="B"/></w:style><w:style w:type="character" w:styleId="B"><w:basedOn w:val="A"/></w:style>'
        result=self.read(styles_package('<w:p>'+run('Keep','<w:rStyle w:val="A"/>')+'</w:p>',styles))
        self.assertNotEqual(result['status'],'ok')
        if result['text'] is not None: self.assertEqual(result['text'],'Keep')

    def test_cli_exclude_json_no_hidden_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sample.wps';path.write_bytes(package('<w:p>'+run('A')+run('CLISECRET','<w:vanish/>')+'</w:p>'))
            command=[sys.executable,'-I','-S','-B',str(Path(__file__).with_name('read_wps.py')),str(path),'--hidden-text','exclude']
            process=subprocess.run(command,capture_output=True,text=True,timeout=10,env={'PATH':''})
            self.assertIn(process.returncode,(0,3),process.stderr)
            self.assertEqual(json.loads(process.stdout)['text'],'A')
            self.assertNotIn('CLISECRET',process.stdout+process.stderr)

    def test_visibility_limits_and_invalid_flags_are_explicit(self):
        import wps_word_visibility as visibility
        data=package('<w:p>'+run('A')+run('B')+'</w:p>')
        with patch.object(visibility,'MAX_RUNS',1):
            self.assertEqual(self.read(data)['status'],'resource_limit')
        with patch.object(visibility,'MAX_STYLES',1):
            styles='<w:style w:type="character" w:styleId="A"/><w:style w:type="character" w:styleId="B"/>'
            self.assertEqual(self.read(styles_package('<w:p/>',styles))['status'],'resource_limit')
        for props in ('<w:vanish w:val="maybe"/>','<w:vanish/><w:vanish/>'):
            result=self.read(package('<w:p>'+run('X',props)+'</w:p>'))
            self.assertEqual(result['status'],'corrupt')
            self.assertIsNone(result['text'])

    def test_document_defaults_and_trace_excludes_text(self):
        styles='<w:docDefaults><w:rPrDefault><w:rPr><w:vanish/></w:rPr></w:rPrDefault></w:docDefaults>'
        result=self.read(styles_package('<w:p>'+run('DEFAULTSECRET')+run('Shown','<w:vanish w:val="0"/>')+'</w:p>',styles))
        self.assertEqual(result['text'],'Shown')
        self.assertNotIn('DEFAULTSECRET',json.dumps(result))
        records=result['visibility']['runs']
        self.assertTrue(any(x['state']=='hidden' and x['action']=='excluded' and x['character_count']==13 for x in records))
        self.assertTrue(all(x['source']['part']=='word/document.xml' and x['source']['path'] for x in records))

    def test_web_only_is_unknown_and_retained_without_view_guess(self):
        result=self.read(package('<w:p>'+run('WEB','<w:webHidden/>')+'</w:p>'))
        self.assertEqual(result['text'],'WEB')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['visibility']['runs'][0]['state'],'unknown')
        self.assertEqual(result['visibility']['runs'][0]['action'],'retained')
        self.assertIn('web-view-only',result['visibility']['runs'][0]['basis'])

    def test_styles_custom_part_and_foreign_relationship_rejected(self):
        styles='<w:docDefaults><w:rPrDefault><w:rPr><w:vanish/></w:rPr></w:rPrDefault></w:docDefaults>'
        source=zipfile.ZipFile(io.BytesIO(styles_package('<w:p>'+run('CUSTOMSECRET')+'</w:p>',styles)))
        def renamed(foreign=False):
            target=io.BytesIO()
            with zipfile.ZipFile(target,'w') as archive:
                for name in source.namelist():
                    value=source.read(name)
                    if name=='word/styles.xml': name='word/customStyle.xml'
                    if name in ('[Content_Types].xml','word/_rels/document.xml.rels'):
                        value=value.replace(b'styles.xml',b'customStyle.xml')
                    if foreign and name=='word/_rels/document.xml.rels':
                        value=value.replace(b'<Relationship Id=',b'<x:Relationship xmlns:x="urn:foreign" Id=')
                    archive.writestr(name,value)
            return target.getvalue()
        self.assertEqual(self.read(renamed())['text'],'')
        self.assertEqual(self.read(renamed(True))['status'],'corrupt')

    def test_revision_trace_reports_final_exclusion(self):
        data=package('<w:p>'+run('A')+'<w:del>'+run('DELETED')+'</w:del></w:p>')
        result=self.read(data,'annotate',revisions='accepted')
        self.assertEqual(result['text'],'A')
        actions=[x['action'] for x in result['visibility']['runs']]
        self.assertEqual(actions,['retained','revision_excluded'])

if __name__=='__main__':unittest.main()
