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

    def with_coverage(self, *, missing=False):
        data = copy.deepcopy(BASE)
        coverage = 'missing_formula_caches' if missing else 'complete_within_declared_sheets'
        data.update(cell_value_coverage=coverage, omissions_affect_cell_values=missing)
        data['reading_assessment'] = {
            'schema_version': 'wps_reading_assessment_v1', 'outcome': 'partial',
            'completeness': 'partial', 'scope': 'stored_cells_and_metadata_subset',
            'full_format_verified': False,
            'events': [{'code': 'non_cell_metadata_not_decoded', 'source_field': 'issues',
                        'location': {'offset': 178}}],
            'events_truncated': 0, 'regions': [],
            'visibility': {'policy': 'not_available', 'recorded_run_decisions': {},
                           'by_part': {}, 'positions': 'See original visibility.runs source paths; not duplicated',
                           'coverage': 'recorded decisions only; unrecorded runs are not inferred'},
            # Fixed producer shape from wps_reading_assessment_v1, not an adapter-created schema.
            'coverage': [
                {'category': 'related_content', 'state': 'unknown', 'evidence_count': 0,
                 'locations': [], 'locations_truncated': 0},
                {'category': 'binary_tables', 'state': 'unknown', 'evidence_count': 0,
                 'locations': [], 'locations_truncated': 0},
                {'category': 'objects', 'state': 'unknown', 'evidence_count': 0,
                 'locations': [], 'locations_truncated': 0},
                {'category': 'unread_content', 'state': 'not_decoded', 'evidence_count': 1,
                 'locations': [{'code': 'non_cell_metadata_not_decoded', 'offset': 178,
                                'source_field': 'issues'}], 'locations_truncated': 0},
            ],
            'position_policy': 'Only recorded positions are reported; absent positions are unknown',
            'text_policy': 'No omitted or excluded document text is copied into this summary',
            'cell_values': {'coverage': coverage, 'omissions_affect_cell_values': missing,
                            'scope': 'Supported BIFF cells only; source-wide, not analysis completeness'},
        }
        return data

    def test_source_coverage_and_original_assessment_survive_analysis_cli(self):
        data = self.with_coverage()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'result.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            process = subprocess.run([
                sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('xlsx_analysis.py')),
                'profile', str(path), '--sheet', 'Data', '--formulas', 'cached'],
                capture_output=True, text=True, encoding='utf-8', check=False)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            result = json.loads(process.stdout)
        self.assertEqual(result['source_cell_value_coverage'], data['cell_value_coverage'])
        self.assertIs(result['source_omissions_affect_cell_values'], False)
        self.assertEqual(result['source_reading_assessment'], data['reading_assessment'])
        self.assertEqual(result['source_issues'], data['issues'])
        self.assertEqual(result['source_status'], 'partial')
        self.assertEqual(result['columns'][0]['numeric']['sum'], '63.0')

    def test_missing_cache_elsewhere_is_preserved_as_source_scope(self):
        data = self.with_coverage(missing=True)
        data['sheets'][0]['cells']['A4'] = {
            'type': 'blank', 'value': None, 'formula': {'tokens_hex': '1e'}, 'cache_status': 'missing'}
        _, _, rows, metadata = self.run_region(data, cell_range='A1:B2')
        self.assertEqual(rows[0][1], [('number', Decimal('60.5')), ('text', '000123')])
        self.assertEqual(metadata['source_cell_value_coverage'], 'missing_formula_caches')
        self.assertIs(metadata['source_omissions_affect_cell_values'], True)
        self.assertEqual(metadata['source_reading_assessment'], data['reading_assessment'])
        with self.assertRaisesRegex(ValueError, 'Missing'):
            self.run_region(data, formulas='cached')

    def test_old_result_without_optional_coverage_still_analyzes(self):
        _, _, _, metadata = self.run_region(BASE, formulas='cached')
        for key in ('source_cell_value_coverage', 'source_omissions_affect_cell_values',
                    'source_reading_assessment'):
            self.assertNotIn(key, metadata)

    def test_malformed_or_conflicting_source_assertions_are_refused(self):
        for update in ({'cell_value_coverage': 'everything'},
                       {'omissions_affect_cell_values': 0},
                       {'cell_value_coverage': 'missing_formula_caches'},
                       {'reading_assessment': []}):
            data = self.with_coverage()
            data.update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.run_region(data, formulas='cached')
        for key, value in (('schema_version', 'unknown'), ('outcome', 'ok'),
                           ('completeness', 'within_declared_scope'), ('full_format_verified', True),
                           ('events', {}), ('coverage', {}), ('cell_values', [])):
            data = self.with_coverage()
            data['reading_assessment'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.run_region(data, formulas='cached')
        data = self.with_coverage()
        data['reading_assessment']['cell_values']['coverage'] = 'missing_formula_caches'
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.run_region(data, formulas='cached')
        for coverage, omissions in (('complete_within_declared_sheets', 0),
                                     ('complete_within_declared_sheets', True),
                                     ('unknown', False)):
            data = self.with_coverage()
            data.pop('cell_value_coverage')
            data.pop('omissions_affect_cell_values')
            data['reading_assessment']['cell_values'].update(
                coverage=coverage, omissions_affect_cell_values=omissions)
            with self.subTest(coverage=coverage, omissions=omissions), self.assertRaises(ValueError):
                self.run_region(data, formulas='cached')

    def test_source_assessment_does_not_bypass_json_depth_budget(self):
        data = self.with_coverage()
        value = []
        for _ in range(100):
            value = [value]
        data['reading_assessment']['events'] = value
        with self.assertRaisesRegex(ValueError, 'nesting'):
            self.run_region(data, formulas='cached')
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
