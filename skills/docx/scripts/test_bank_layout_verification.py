"""Final XML checks and rejection independent of serializer expectations."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parent))
import create_docx as generator
from office.validators.generation import verify_generation
W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class BankVerificationTests(unittest.TestCase):
    def test_preset_final_xml_and_tamper_rejection(self):
        for preset in ('bank_report','bank_letter'):
            spec={'office_preset':preset,'pagination_preset':'stable','page':{'header_distance_mm':10},'blocks':[{'type':'heading','level':2,'text':'分析'},{'type':'paragraph','text':'正文'},{'type':'table','rows':[['编号','00123']],'column_formats':{'2':{'body':{'align':'right'}}}}]}
            validated=generator._validate_spec(spec,Path.cwd())
            parts=generator._package_parts(validated)
            roots={name:ET.fromstring(data) for name,data in parts.items() if name.endswith('.xml')}
            checker=SimpleNamespace(roots=roots,relations={},archive=None)
            verify_generation(validated,checker)
            styles=roots['word/styles.xml']
            heading=next(n for n in styles.findall(W+'style') if n.get(W+'styleId')=='Heading2')
            self.assertIsNotNone(heading.find('.//'+W+'keepNext'))
            body=next(n for n in styles.findall(W+'style') if n.get(W+'styleId')=='BodyText')
            self.assertIsNotNone(body.find('.//'+W+'widowControl'))
            for mode in ('column','distance','pagination'):
                changed=copy.deepcopy(roots)
                if mode=='column': changed['word/document.xml'].find('.//'+W+'tbl').findall('.//'+W+'tc')[1].find('.//'+W+'jc').set(W+'val','left')
                elif mode=='distance': changed['word/document.xml'].find('.//'+W+'pgMar').set(W+'header','1')
                else:
                    style=next(n for n in changed['word/styles.xml'].findall(W+'style') if n.get(W+'styleId')=='Heading2')
                    style.find(W+'pPr').remove(style.find('.//'+W+'keepNext'))
                with self.assertRaises(ValueError): verify_generation(validated,SimpleNamespace(roots=changed,relations={},archive=None))

    def test_default_parts_equal_committed_baseline(self):
        root=Path(__file__).resolve().parents[3]
        if not (root/'.git').exists():
            self.skipTest('Repository baseline test; independent package has no Git history')
        source=subprocess.check_output(['git','show','6fcd827260633968d77269b4f701ae816b6f312e:skills/docx/scripts/create_docx.py'],cwd=root,text=True)
        baseline=type(sys)('baseline_create_docx')
        baseline.__file__=str(Path(generator.__file__))
        exec(compile(source,baseline.__file__,'exec'),baseline.__dict__)
        for theme in ('official','business','academic'):
            spec={'theme':theme,'header':'内部','footer':'报告','page_numbers':True,'blocks':[{'type':'title','text':'标题'},{'type':'paragraph','text':'正文'},{'type':'table','header':True,'rows':[['项目','金额'],['甲','00123']]}]}
            self.assertEqual(baseline._package_parts(baseline._validate_spec(spec,Path.cwd())),generator._package_parts(generator._validate_spec(spec,Path.cwd())))

    def test_report_risks_are_nonblocking_and_do_not_change_xml(self):
        spec={'styles':{'body':{'keep_with_next':True}},'blocks':[{'type':'paragraph','text':'说明'+str(i)} for i in range(9)]+[{'type':'heading','level':2,'text':'待补章节'}]}
        before=copy.deepcopy(spec)
        result=generator.preflight_document(spec)
        codes={item['code'] for item in result['layout_issues']}
        self.assertIn('KEEP_CHAIN_RISK',codes)
        self.assertIn('TRAILING_HEADING_RISK',codes)
        self.assertEqual(result['status'],'preflight-ok')
        self.assertEqual(spec,before)
        normal=generator.preflight_document({'blocks':[{'type':'heading','level':2,'text':'标题'},{'type':'paragraph','text':'正文'}]})
        self.assertFalse(normal['layout_issues'])
