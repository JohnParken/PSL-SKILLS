"""Synthetic OFD and security regressions; no third-party modules."""
import contextlib
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import struct
import zlib
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SPEC = importlib.util.spec_from_file_location('read_ofd', Path(__file__).with_name('read_ofd.py'))
READER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(READER)
NS = READER.NS


def xml(tag, content='', attrs=''):
    return f'<ofd:{tag} xmlns:ofd="{NS}" {attrs}>{content}</ofd:{tag}>'.encode()


def fixture(text='甲乙', page_extra='', common_extra='', extra=None):
    files = {'OFD.xml': xml('OFD', '<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>', 'Version="1.0" DocType="OFD"'),
             'Doc/Document.xml': xml('Document', '<ofd:CommonData>'+common_extra+'</ofd:CommonData><ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/P.xml"/></ofd:Pages>'),
             'Doc/Pages/P.xml': xml('Page', page_extra+'<ofd:Content><ofd:Layer ID="2"><ofd:TextObject ID="3" Boundary="1 2 30 4" Size="3"><ofd:TextCode X="0" Y="3">'+text+'</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>')}
    files.update(extra or {})
    return files


def write_package(path, files, compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(path, 'w', compression=compression) as archive:
        for name, data in files.items():
            archive.writestr(name, data)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root/'sample.ofd'

    def tearDown(self):
        self.temp.cleanup()

    def read(self, files=None):
        write_package(self.path, fixture() if files is None else files)
        return READER.read_ofd(self.path)

    def test_literal_provenance_and_repeat(self):
        result = self.read()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        run = result['documents'][0]['pages'][0]['text_runs'][0]
        self.assertEqual(run['text'], '甲乙')
        self.assertEqual(run['object_attributes']['Boundary'], '1 2 30 4')
        self.assertEqual(run['code_attributes'], {'X': '0', 'Y': '3'})
        self.assertEqual(run['source'], 'Doc/Pages/P.xml')
        self.assertEqual(result, READER.read_ofd(self.path))
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_storage_order_not_coordinates(self):
        files = fixture()
        files['Doc/Pages/P.xml'] = xml('Page', '<ofd:Content><ofd:Layer><ofd:TextObject><ofd:TextCode Y="99">后</ofd:TextCode><ofd:TextCode Y="1">前</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>')
        runs = self.read(files)['documents'][0]['pages'][0]['text_runs']
        self.assertEqual([r['text'] for r in runs], ['后', '前'])

    def test_template_dedup_and_nested_ignore(self):
        files = fixture(page_extra='<ofd:Template TemplateID="9"/><ofd:Template TemplateID="9"/>', common_extra='<ofd:TemplatePage ID="9" BaseLoc="Templates/T.xml"/>', extra={'Doc/Templates/T.xml':xml('Page', '<ofd:Content><ofd:Layer><ofd:TextObject><ofd:TextCode>页眉</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>')})
        result = self.read(files)
        self.assertEqual([r['text'] for r in result['documents'][0]['pages'][0]['text_runs']], ['页眉','甲乙'])
        self.assertEqual(result['status'], 'partial')
        files['Doc/Templates/T.xml'] = xml('Page', '<ofd:Template TemplateID="9"/>')
        result = self.read(files)
        self.assertIn('ignored_template_in_template', [d['code'] for d in result['diagnostics']])
        self.assertEqual([r['text'] for r in result['documents'][0]['pages'][0]['text_runs']], ['甲乙'])

    def test_empty_scan_unknown(self):
        for content, code in [('', 'no_explainable_text'), ('<ofd:ImageObject ResourceID="1"/>','image_not_read'), ('<ofd:CompositeObject ID="9"/>','uninterpreted_object')]:
            files = fixture()
            files['Doc/Pages/P.xml'] = xml('Page', '<ofd:Content><ofd:Layer>'+content+'</ofd:Layer></ofd:Content>')
            result = self.read(files)
            self.assertEqual(result['status'], 'partial')
            self.assertIn(code, [d['code'] for d in result['diagnostics']])
            self.assertEqual(result['tables'], [])

    def test_resources_metadata_and_signature(self):
        files = fixture(common_extra='<ofd:PublicRes>Res.xml</ofd:PublicRes>',extra={'Doc/Res.xml':xml('Res','<ofd:Fonts><ofd:Font ID="7" FontName="example"><ofd:FontFile>font.dat</ofd:FontFile></ofd:Font></ofd:Fonts>','BaseLoc="Res"'),'Doc/Res/font.dat':b'font', 'Signs/Signatures.xml':xml('Signatures','<ofd:Signature ID="1" BaseLoc="S/Signature.xml"/>'),'Signs/S/Signature.xml':xml('Signature','<ofd:SignedInfo><ofd:SignatureDateTime>2020-01-01</ofd:SignatureDateTime></ofd:SignedInfo>')})
        files['OFD.xml'] = xml('OFD','<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot><ofd:Signatures>Signs/Signatures.xml</ofd:Signatures></ofd:DocBody>')
        result = self.read(files)
        self.assertEqual(result['signature_verification'], 'not_performed')
        self.assertEqual(result['documents'][0]['resources'][0]['attributes']['ID'], '7')
        self.assertTrue(any(r['target']=='Doc/Res/font.dat' for r in result['references']))

    def test_bad_paths(self):
        for name in ('../evil','/absolute','a\\b','a//b','a/./b','a%2fb','https:evil','a\x00b'):
            with self.subTest(name=name), self.assertRaises(READER.ReadError):
                READER.safe_name(name)
        for reference in ('https://example.org/a','../../outside.xml','//host/path','C:/a'):
            with self.subTest(reference=reference), self.assertRaises(READER.ReadError):
                READER.resolve('Doc/Document.xml',reference)

    def test_standard_relative_paths(self):
        self.assertEqual(READER.resolve('Doc/Pages/P.xml','../Res/./font.dat'),'Doc/Res/font.dat')
        self.assertEqual(READER.resolve('Doc/Pages/P.xml','/Doc/Res/font.dat'),'Doc/Res/font.dat')
        self.assertEqual(READER.resolve('Doc/Document.xml','../OFD.xml'),'OFD.xml')
        with self.assertRaises(READER.ReadError):
            READER.resolve('Doc/Document.xml','/../OFD.xml')
        files=fixture()
        files['OFD.xml']=xml('OFD','<ofd:DocBody><ofd:DocRoot>./Doc/Document.xml</ofd:DocRoot></ofd:DocBody>')
        files['Doc/Document.xml']=xml('Document','<ofd:Pages><ofd:Page ID="1" BaseLoc="./Pages/../Pages/P.xml"/></ofd:Pages>')
        self.assertEqual(self.read(files)['status'],'complete')

    def test_standard_textcode_escapes(self):
        raw = r'A\0020B\005CC'
        result=self.read(fixture(text=raw))
        run=result['documents'][0]['pages'][0]['text_runs'][0]
        self.assertEqual(run['raw_text'],raw)
        self.assertEqual(run['text'],'A B'+chr(92)+'C')
        result=self.read(fixture(text=r'\D800'+chr(0xA4)))
        self.assertEqual(result['status'],'partial')
        self.assertIn('surrogate_text_escape',[d['code'] for d in result['diagnostics']])
        self.assertIn('text_placeholder_unresolved',[d['code'] for d in result['diagnostics']])

    def test_standard_root_blank_and_resource_directory(self):
        files=fixture()
        files['extra/OFD.xml']=xml('OFD')
        with self.assertRaisesRegex(READER.ReadError,'exactly one'):
            self.read(files)
        files=fixture()
        files['Doc/Pages/P.xml']=xml('Page')
        result=self.read(files)
        self.assertEqual(result['status'],'partial')
        self.assertIn('blank_page_no_content',[d['code'] for d in result['diagnostics']])
        files=fixture(common_extra='<ofd:PublicRes>Res.xml</ofd:PublicRes>',extra={'Doc/Res.xml':xml('Res','<ofd:Fonts><ofd:Font ID="7"><ofd:FontFile>font.dat</ofd:FontFile></ofd:Font></ofd:Fonts>','BaseLoc="/"'),'font.dat':b'font'})
        result=self.read(files)
        self.assertEqual(result['documents'][0]['resources'][0]['files'],['font.dat'])
        files['Doc/Res.xml']=xml('Res','<ofd:Fonts><ofd:Font ID="7"><ofd:FontFile>../font.dat</ofd:FontFile></ofd:Font></ofd:Fonts>','BaseLoc="../Res/"')
        result=self.read(files)
        self.assertEqual(result['documents'][0]['resources'][0]['files'],['font.dat'])

    def test_review_objects_outside_content(self):
        files=fixture()
        files['Doc/Pages/P.xml']=xml('Page','<ofd:TextObject><ofd:TextCode>OUTSIDE</ofd:TextCode></ofd:TextObject>')
        result=self.read(files)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['documents'][0]['pages'][0]['text_runs'],[])
        self.assertIn('object_outside_content',[d['code'] for d in result['diagnostics']])
        self.assertIn('blank_page_no_content',[d['code'] for d in result['diagnostics']])

    def test_dishonest_deflate_size(self):
        prefix=xml('OFD','<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>')
        files=fixture();files['OFD.xml']=prefix+b' '*100000
        write_package(self.path,files,zipfile.ZIP_DEFLATED)
        data=bytearray(self.path.read_bytes())
        central=data.index(b'PK\x01\x02')
        # Forge both size and CRC to make zipfile accept only the XML prefix.
        struct.pack_into('<I',data,14,zlib.crc32(prefix))
        struct.pack_into('<I',data,22,len(prefix))
        struct.pack_into('<I',data,central+16,zlib.crc32(prefix))
        struct.pack_into('<I',data,central+24,len(prefix))
        self.path.write_bytes(data)
        with self.assertRaisesRegex(READER.ReadError,'inconsistent DEFLATE'):
            READER.read_ofd(self.path)

    def test_foreign_namespace_in_common_and_resources(self):
        files=fixture(common_extra='<x:Unexpected xmlns:x="urn:foreign"/>')
        result=self.read(files)
        self.assertEqual(result['status'],'partial')
        self.assertIn('foreign_namespace',[d['code'] for d in result['diagnostics']])
        files=fixture(common_extra='<ofd:PublicRes>Res.xml</ofd:PublicRes>',extra={'Doc/Res.xml':xml('Res','<ofd:Fonts><x:Font xmlns:x="urn:foreign" ID="7"/><x:Wrapper xmlns:x="urn:foreign"><ofd:Font ID="8"/></x:Wrapper></ofd:Fonts>')})
        result=self.read(files)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['documents'][0]['resources'],[])
        self.assertIn('foreign_namespace',[d['code'] for d in result['diagnostics']])

    def test_check_requires_working_zlib(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(READER.main(['--check']),0)
        self.assertIn('environment-ok',output.getvalue())
        output=io.StringIO()
        with mock.patch.object(READER.zipfile,'zlib',None), contextlib.redirect_stdout(output):
            self.assertEqual(READER.main(['--check']),1)
        self.assertEqual(json.loads(output.getvalue())['status'],'failed')
        self.assertNotIn('environment-ok',output.getvalue())

    def test_dishonest_stored_size(self):
        prefix=xml('OFD','<ofd:DocBody><ofd:DocRoot>Doc/Document.xml</ofd:DocRoot></ofd:DocBody>')
        files=fixture();files['OFD.xml']=prefix+b'HIDDEN'*100
        write_package(self.path,files,zipfile.ZIP_STORED)
        data=bytearray(self.path.read_bytes())
        central=data.index(b'PK\x01\x02')
        struct.pack_into('<I',data,14,zlib.crc32(prefix))
        struct.pack_into('<I',data,22,len(prefix))
        struct.pack_into('<I',data,central+16,zlib.crc32(prefix))
        struct.pack_into('<I',data,central+24,len(prefix))
        self.path.write_bytes(data)
        with self.assertRaisesRegex(READER.ReadError,'inconsistent stored'):
            READER.read_ofd(self.path)

    def test_cli_usage_error(self):
        target=Path(__file__).with_name('read_ofd.py')
        completed=subprocess.run([sys.executable,'-I','-S','-B',str(target),'--unknown'],cwd=self.root,capture_output=True,text=True)
        self.assertEqual(completed.returncode,2)
        self.assertIn('usage:',completed.stderr)
        self.assertEqual(completed.stdout,'')

    def test_duplicate_and_symlink(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(self.path,'w') as archive:
                archive.writestr('OFD.xml', b'a')
                archive.writestr('OFD.xml', b'b')
        with self.assertRaisesRegex(READER.ReadError,'duplicate'):
            READER.read_ofd(self.path)
        info=zipfile.ZipInfo('link')
        info.external_attr=(0o120777 << 16)
        with zipfile.ZipFile(self.path,'w') as archive:
            archive.writestr(info,b'target')
        with self.assertRaisesRegex(READER.ReadError,'special'):
            READER.read_ofd(self.path)

    def test_entities_encodings_malformed(self):
        for payload in (b'<!DOCTYPE x [<!ENTITY x "abc">]><x>&x;</x>', '<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><x>&x;</x>'.encode('utf-16'), b'<broken>'):
            files=fixture(); files['OFD.xml']=payload
            with self.assertRaises((READER.ReadError,READER.expat.ExpatError)):
                self.read(files)

    def test_limits(self):
        for key,value in [('depth',2),('nodes',2),('text_chars',1),('xml_bytes',5),('xml_total',5),('references',1),('pages',0),('entries',1),('member_bytes',5),('total_bytes',5),('input_bytes',5)]:
            with self.subTest(key=key):
                original=READER.LIMITS[key]
                READER.LIMITS[key]=value
                try:
                    with self.assertRaisesRegex(READER.ReadError,'limit'):
                        self.read()
                finally:
                    READER.LIMITS[key]=original
        write_package(self.path,{'bomb': b'0'*100000},zipfile.ZIP_DEFLATED)
        with self.assertRaisesRegex(READER.ReadError,'expansion'):
            READER.read_ofd(self.path)

    def test_missing_namespace_and_version(self):
        for data in (b'<OFD/>', xml('OFD','', 'Version="2.0"')):
            with self.assertRaises(READER.ReadError):
                self.read({'OFD.xml':data})
        files=fixture(); del files['Doc/Pages/P.xml']
        with self.assertRaisesRegex(READER.ReadError,'missing'):
            self.read(files)

    def test_attachment_annotation_and_no_execution(self):
        files=fixture(extra={'Doc/Attachments.xml':xml('Attachments','<ofd:Attachment ID="8" Name="unsafe.py"><ofd:FileLoc>Payload/unsafe.py</ofd:FileLoc></ofd:Attachment>'),'Doc/Payload/unsafe.py':b'raise RuntimeError("must not execute")','Doc/Annotations.xml':xml('Annotations','<ofd:Page PageID="1"><ofd:FileLoc>Annots/A.xml</ofd:FileLoc></ofd:Page>'),'Doc/Annots/A.xml':xml('PageAnnot','<ofd:Annot ID="10"><ofd:Remark>note only</ofd:Remark></ofd:Annot>')})
        files['Doc/Document.xml']=xml('Document','<ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/P.xml"/></ofd:Pages><ofd:Attachments>Attachments.xml</ofd:Attachments><ofd:Annotations>Annotations.xml</ofd:Annotations>')
        result=self.read(files)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(len(result['documents'][0]['pages'][0]['text_runs']),1)
        self.assertIn('PageAnnotations',[m['kind'] for m in result['documents'][0]['metadata']])
        self.assertIn('Doc/Payload/unsafe.py',result['unparsed_members'])

    def test_encrypted_container_rejected(self):
        write_package(self.path,fixture())
        data=bytearray(self.path.read_bytes())
        central=data.index(b'PK\x01\x02')
        data[central+8] |= 1
        self.path.write_bytes(data)
        with self.assertRaisesRegex(READER.ReadError,'unsupported: encrypted ZIP'):
            READER.read_ofd(self.path)

    def test_crc_and_unsupported_compression(self):
        write_package(self.path,fixture())
        data=self.path.read_bytes().replace('甲乙'.encode(),'丙丁'.encode(),1)
        self.path.write_bytes(data)
        with self.assertRaises(zipfile.BadZipFile):
            READER.read_ofd(self.path)
        write_package(self.path,fixture(),zipfile.ZIP_BZIP2)
        with self.assertRaisesRegex(READER.ReadError,'unsupported: ZIP compression'):
            READER.read_ofd(self.path)

    def test_cli_failure_partial_and_unsupported(self):
        target=Path(__file__).with_name('read_ofd.py')
        for files,status,code in [(fixture(text=''),'partial',3),({'OFD.xml':b'<wrong/>'},'unsupported',4),({'OFD.xml':b'<broken>'},'failed',1)]:
            write_package(self.path,files)
            completed=subprocess.run([sys.executable,'-I','-S','-B',str(target),'sample.ofd'],cwd=self.root,capture_output=True,text=True)
            self.assertEqual(completed.returncode,code,completed.stdout+completed.stderr)
            result=json.loads(completed.stdout)
            self.assertEqual(result['status'],status)
            self.assertEqual(result['schema_version'],'ofd-read-2')
            if code==1:
                self.assertNotIn('documents',result)

    def test_review_many_codes_large_parent(self):
        files=fixture()
        files['Doc/Pages/P.xml']=xml('Page','<ofd:Content><ofd:Layer><ofd:TextObject ID="'+'a'*10000+'">'+'<ofd:TextCode>A</ofd:TextCode>'*100+'</ofd:TextObject></ofd:Layer></ofd:Content>')
        original=READER.LIMITS['output_chars']
        READER.LIMITS['output_chars']=2*1024*1024
        try:
            with self.assertRaisesRegex(READER.ReadError,'expanded output'):
                self.read(files)
        finally:
            READER.LIMITS['output_chars']=original

    def test_review_structure_and_namespace(self):
        files=fixture()
        files['Doc/Pages/P.xml']=xml('Page','<ofd:TextCode>LOST</ofd:TextCode><f:TextObject xmlns:f="urn:foreign"><ofd:TextCode>FOREIGN</ofd:TextCode></f:TextObject><ofd:Content><ofd:Layer><ofd:TextObject><ofd:TextCode>A</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>')
        result=self.read(files)
        self.assertEqual(result['status'],'partial')
        self.assertIn('unexpected_textcode_parent',[d['code'] for d in result['diagnostics']])
        self.assertEqual([r['text'] for r in result['documents'][0]['pages'][0]['text_runs']],['A'])

    def test_review_output_amplification(self):
        files=fixture()
        files['Doc/Document.xml']=xml('Document','<ofd:Pages>'+''.join(f'<ofd:Page ID="{i}" BaseLoc="Pages/P.xml"/>' for i in range(20))+'</ofd:Pages>')
        files['Doc/Pages/P.xml']=xml('Page','<ofd:Content><ofd:Layer><ofd:TextObject ID="'+'a'*10000+'"><ofd:TextCode>A</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>')
        original=READER.LIMITS['output_chars']
        READER.LIMITS['output_chars']=500000
        try:
            with self.assertRaisesRegex(READER.ReadError,'expanded output'):
                self.read(files)
        finally:
            READER.LIMITS['output_chars']=original

    def test_cli_and_readonly_isolation(self):
        installation=self.root/'install'
        installation.mkdir()
        source=Path(__file__).with_name('read_ofd.py')
        target=installation/'read_ofd.py'
        shutil.copyfile(source,target)
        target.chmod(0o444); installation.chmod(0o555)
        write_package(self.path,fixture())
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in installation.iterdir()}
        try:
            for _ in range(2):
                completed=subprocess.run([sys.executable,'-I','-S','-B',str(target),'sample.ofd'],cwd=self.root,env={'PATH':''},capture_output=True,text=True)
                self.assertEqual(completed.returncode,0,completed.stderr+completed.stdout)
                self.assertEqual(json.loads(completed.stdout)['status'],'complete')
                self.assertEqual(json.loads(completed.stdout)['schema_version'],'ofd-read-2')
            for argument in ('../escape.ofd',str(self.path)):
                completed=subprocess.run([sys.executable,'-I','-S','-B',str(target),argument],cwd=self.root,capture_output=True,text=True)
                self.assertEqual(completed.returncode,1)
            after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in installation.iterdir()}
            self.assertEqual(before,after)
        finally:
            installation.chmod(0o755); target.chmod(0o644)


if __name__=='__main__':
    unittest.main()
