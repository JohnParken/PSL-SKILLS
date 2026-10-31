"""Opt-in banking layout; XML intent, not visual pagination acceptance."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parent))
from create_docx import create_document, preflight_document, _validate_spec, _package_parts
W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class BankLayoutTests(unittest.TestCase):
    def test_column_formats_verified_and_values_unchanged(self):
        spec={'blocks':[{'type':'table','header':True,'rows':[['项目','金额'],['甲','00123']], 'column_formats':{'2':{'body':{'align':'right','size_pt':10},'header':{'align':'center'}}}}]}
        original=copy.deepcopy(spec)
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'table.docx'
            create_document(spec,output,verify=True)
            with zipfile.ZipFile(output) as package: root=ET.fromstring(package.read('word/document.xml'))
        cells=root.findall('.//'+W+'tr')[1].findall(W+'tc')
        self.assertEqual(cells[1].find('.//'+W+'jc').get(W+'val'),'right')
        self.assertEqual(''.join(cells[1].itertext()),'00123')
        self.assertEqual(spec,original)

    def test_optional_presets_do_not_change_unspecified_defaults(self):
        base={'blocks':[{'type':'paragraph','text':'正文'}]}
        before=_package_parts(_validate_spec(base,Path.cwd()))
        for name in ('bank_report','bank_letter'):
            spec={**base,'office_preset':name,'pagination_preset':'stable'}
            result=_validate_spec(spec,Path.cwd())
            self.assertTrue(result['styles']['body']['widow_control'])
            self.assertTrue(result['styles']['heading2']['keep_with_next'])
            preflight_document(spec)
        self.assertEqual(before,_package_parts(_validate_spec(base,Path.cwd())))

    def test_explicit_style_and_theme_override_presets(self):
        result=_validate_spec({'office_preset':'bank_report','pagination_preset':'stable','theme':'official','styles':{'body':{'size_pt':16,'widow_control':False}},'blocks':[]},Path.cwd())
        self.assertEqual(result['styles']['body']['size_pt'],16)
        self.assertFalse(result['styles']['body']['widow_control'])

    def test_header_footer_distances_verified(self):
        spec={'header':'内部资料','footer':'经营分析','page_numbers':True,'page':{'header_distance_mm':10,'footer_distance_mm':12},'blocks':[{'type':'paragraph','text':'正文'}]}
        with tempfile.TemporaryDirectory() as temp: create_document(spec,Path(temp)/'report.docx',verify=True)

    def test_invalid_columns_and_distances_rejected(self):
        for key in ('0','01','3'):
            with self.assertRaises(ValueError): preflight_document({'blocks':[{'type':'table','rows':[['A','B']],'column_formats':{key:{'body':{'align':'right'}}}}]})
        with self.assertRaises(ValueError): preflight_document({'page':{'header_distance_mm':100},'blocks':[]})

    def test_column_override_participates_in_nonblocking_diagnostics(self):
        spec={'blocks':[{'type':'table','rows':[['A']],'column_formats':{'1':{'body':{'size_pt':40,'line_spacing_pt':10}}}}]}
        result=preflight_document(spec)
        self.assertIn('FIXED_LINE_HEIGHT_RISK',[x['code'] for x in result['layout_issues']])
