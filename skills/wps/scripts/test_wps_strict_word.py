"""Synthetic Strict OOXML packages; no WPS provenance claim."""
import io
from pathlib import Path
import sys
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
import wps_ooxml as reader
from test_wps_ooxml import package


def strict(data, exclude=()):
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source,zipfile.ZipFile(output,'w') as target:
        for name in source.namelist():
            raw=source.read(name)
            if name not in exclude:
                raw=raw.replace(reader.W.encode(),reader.STRICT_W.encode()).replace(reader.R.encode(),reader.STRICT_R.encode())
            target.writestr(name,raw)
    return output.getvalue()


@unittest.skipUnless(reader.expat.version_info >= (2,6,0),'requires Expat >=2.6')
class StrictWordTests(unittest.TestCase):
    def test_main_tables_visibility_and_revisions(self):
        data=package('<w:p><w:r><w:t>甲</w:t></w:r><w:r><w:rPr><w:vanish/></w:rPr><w:t>秘密</w:t></w:r><w:del><w:r><w:delText>旧</w:delText></w:r></w:del></w:p><w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>表</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        result=reader.read_bytes(strict(data),hidden_text='exclude')
        self.assertEqual(result['ooxml_conformance'],'strict')
        self.assertEqual(result['text'],'甲\n表')
        self.assertEqual(result['tables'][0]['rows'][0]['cells'][0]['text'],'表')
        self.assertNotIn('秘密',str(result))
        self.assertIn('旧',reader.read_bytes(strict(data),revisions='original')['text'])

    def test_related_custom_part_and_mixed_related_refused(self):
        from test_wps_ooxml_related import package as related_package, rel, root, paragraph
        data=related_package(paragraph('正文')+'<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>', rel('h','header','custom/head.xml'), [('word/custom/head.xml','header',root('header',paragraph('页眉')))])
        result=reader.read_bytes(strict(data),include_related=True)
        self.assertEqual(result['text'],'正文')
        self.assertEqual(result['related_stories'][0]['text'],'页眉')
        self.assertEqual(result['related_stories'][0]['source']['part'],'word/custom/head.xml')
        self.assertEqual(reader.read_bytes(strict(data,('word/custom/head.xml',)),include_related=True)['status'],'unsupported')

    def test_mixed_package_dialects_rejected(self):
        for excluded in [('_rels/.rels',),('word/document.xml',)]:
            result=reader.read_bytes(strict(package('<w:p/>'),excluded))
            self.assertEqual(result['status'],'unsupported')
            self.assertIsNone(result['text'])

    def test_mixed_tree_attribute_rejected(self):
        document='<w:document xmlns:w="'+reader.STRICT_W+'" xmlns:old="'+reader.W+'"><w:body><w:p old:rsidR="1"/></w:body></w:document>'
        data=strict(package(document=document))
        # Strict conversion would replace the intentionally mixed declaration.
        output=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source,zipfile.ZipFile(output,'w') as target:
            for name in source.namelist(): target.writestr(name, document if name=='word/document.xml' else source.read(name))
        self.assertEqual(reader.read_bytes(output.getvalue())['status'],'unsupported')

if __name__=='__main__': unittest.main()
