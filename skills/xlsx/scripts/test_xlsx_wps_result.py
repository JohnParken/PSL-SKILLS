"""JSON-only BIFF result consumption; does not parse or convert WPS files."""
import copy
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_analysis import region, statistics

BASE={'schema_version':'biff8_readonly_v1','biff_parsed':True,'status':'partial','date_system':'1900','issues':[{'code':'known_metadata'}], 'sheets':[{'name':'Data','merges':[], 'cells':{'A1':{'type':'text','value':'Amount'},'A2':{'type':'number','value':'60.5'},'B2':{'type':'text','value':'000123'},'A3':{'type':'number','value':'2.5','formula':{'tokens_hex':'1e'},'cache_status':'unverified'}}}]}
class ResultTests(unittest.TestCase):
    def run_region(self,data,**kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.json'; path.write_text(json.dumps(data))
            return region(path,'Data',**kwargs)
    def test_types_dates_diagnostics_and_formula_policy(self):
        with self.assertRaisesRegex(ValueError,'Formula'): self.run_region(BASE)
        columns,headers,rows,metadata=self.run_region(BASE,formulas='cached')
        self.assertEqual(rows[0][1],[('number',Decimal('60.5')),('text','000123')])
        self.assertEqual(statistics([r[1][0] for r in rows])['sum'],Decimal(63))
        self.assertEqual(metadata['date_system'],'1900')
        self.assertEqual(metadata['source_status'],'partial')
        self.assertEqual(metadata['source_issues'],BASE['issues'])
    def test_ooxml_schema_same_analysis_and_explicit_bounds(self):
        data=copy.deepcopy(BASE)
        data.pop('biff_parsed'); data.update(schema_version='ooxml_spreadsheet_readonly_v1',spreadsheet_parsed=True)
        old=self.run_region(BASE,formulas='cached')
        new=self.run_region(data,formulas='cached')
        self.assertEqual(old[:3],new[:3])
        self.assertEqual(old[3]['input_schema'],'biff8_readonly_v1')
        self.assertEqual(new[3]['input_schema'],'ooxml_spreadsheet_readonly_v1')
        self.assertEqual({k:v for k,v in old[3].items() if k!='input_schema'},
                         {k:v for k,v in new[3].items() if k!='input_schema'})
        from xlsx_wps_result import load_result
        from stdlib_xlsx import address
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.json'
            data['sheets'][0]['cells']={'XFD1048576':{'type':'number','value':'12345678901234567890.125'}}
            path.write_text(json.dumps(data))
            book=load_result(path,address)
            self.assertEqual(book.cells['Data']['XFD1048576']['value'],Decimal('12345678901234567890.125'))
            data['schema_version']='biff8_readonly_v1';data['biff_parsed']=True
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):load_result(path,address)
        data=copy.deepcopy(BASE);data.update(schema_version='ooxml_spreadsheet_readonly_v1')
        with self.assertRaises(ValueError):self.run_region(data,formulas='cached')

    def test_bad_schema_values_and_missing_cache(self):
        for key,value in [('schema_version','bogus'),('status','failed'),('date_system','bogus')]:
            data=copy.deepcopy(BASE); data[key]=value
            with self.assertRaises(ValueError): self.run_region(data)
        for value in ('NaN','1e9999',123):
            data=copy.deepcopy(BASE); data['sheets'][0]['cells']['A2']['value']=value
            with self.assertRaises(ValueError): self.run_region(data,formulas='cached')
        data=copy.deepcopy(BASE); data['sheets'][0]['cells']['A3']['cache_status']='missing'
        with self.assertRaisesRegex(ValueError,'Missing'): self.run_region(data,formulas='cached')
    def test_diagnostics_depth_is_bounded(self):
        data=copy.deepcopy(BASE)
        value=[]
        for _ in range(100): value=[value]
        data['issues']=value
        with self.assertRaisesRegex(ValueError,'nesting'): self.run_region(data)
    def test_nonfinite_json_constants_are_refused(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            data=copy.deepcopy(BASE); data['issues']=[value]
            with self.assertRaisesRegex(ValueError,'Nonfinite JSON constant'):
                self.run_region(data)

    def test_oversize_preflight_and_changed_metadata(self):
        from xlsx_wps_result import load_result
        from stdlib_xlsx import address
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.json'
            with path.open('wb') as stream: stream.truncate(16*1024*1024+1)
            with patch('xlsx_wps_result.os.fdopen',side_effect=AssertionError('must not read')):
                with self.assertRaisesRegex(ValueError,'before reading'): load_result(path,address)
            path.write_text(json.dumps(BASE)); info=path.stat()
            altered=SimpleNamespace(st_size=info.st_size,st_mtime_ns=info.st_mtime_ns+1,st_ctime_ns=info.st_ctime_ns)
            with patch('xlsx_wps_result.os.fstat',side_effect=[info,altered]):
                with self.assertRaisesRegex(ValueError,'changed during read'): load_result(path,address)

    @unittest.skipUnless(hasattr(os,'mkfifo'),'requires POSIX FIFO')
    def test_fifo_analysis_cli_exits_without_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'pipe.json'; os.mkfifo(path)
            result=subprocess.run([sys.executable,'-I','-S','-B',str(Path(__file__).with_name('xlsx_analysis.py')),'profile',str(path),'--sheet','Data'],capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('regular file',result.stdout)
if __name__=='__main__': unittest.main()
