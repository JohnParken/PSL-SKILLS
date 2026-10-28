"""Independent additive-summary expectations, no producer claims."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wps_diagnostics import summarize
from read_wps import read_bytes,read
from test_wps import document
from test_wps_cfb import synthetic_cfb
class DiagnosticsTests(unittest.TestCase):
    def test_complete_is_only_within_scope(self):
        out=summarize({'status':'ok','scope':'main','text':'BODY'})
        self.assertEqual(out['text'],'BODY')
        self.assertEqual(out['reading_assessment']['completeness'],'within_declared_scope')
        self.assertFalse(out['reading_assessment']['full_format_verified'])
    def test_partial_location_without_hidden_content(self):
        out=summarize({'status':'partial','issues':[{'code':'hidden','stream':'word/header1.xml','offset':None,'message':'SECRET','text':'SECRET'}], 'related_stories':[{'type':'header','status':'partial','source':{'part':'word/header1.xml'},'text':'SECRET'}]})
        summary=out['reading_assessment']
        self.assertNotIn('SECRET',str(summary))
        self.assertEqual(summary['events'][0]['location'],{'stream':'word/header1.xml','offset':None})
        self.assertEqual(summary['regions'][0]['status'],'partial')
    def test_visibility_summary_is_bounded_to_counts_and_region(self):
        source={'part':'word/header1.xml','path':'/hdr/r[1]'}
        out=summarize({'status':'partial','visibility':{'policy':'exclude','runs':[
            {'source':source,'state':'hidden','action':'excluded','text':'SECRET'},
            {'source':source,'state':'unknown','action':'retained','text':'UNKNOWN'},
            {'source':source,'state':'hidden','action':'revision_excluded'}]}})
        summary=out['reading_assessment']['visibility']
        self.assertEqual(summary['recorded_run_decisions'],{'excluded:hidden':1,'retained:unknown':1,'revision_excluded:hidden':1})
        self.assertEqual(summary['by_part']['word/header1.xml']['recorded_runs'],3)
        self.assertNotIn('SECRET',str(summary))
        self.assertNotIn('/hdr/r[1]',str(summary))

    def test_failure_no_partial_content_claim(self):
        out=read('/definitely-not-a-real-file-diagnostics.wps')
        self.assertEqual(out['reading_assessment']['completeness'],'no_content_delivered')
        self.assertEqual(out['reading_assessment']['outcome'],'io_error')
    def test_binary_entry_keeps_original_fields(self):
        data=synthetic_cfb({path[0]:value for path,value in document().items()})
        out=read_bytes(data)
        self.assertEqual(out['status'],'ok')
        self.assertEqual(out['reading_assessment']['scope'],out['scope'])

    def test_omitted_region_coverage_keeps_cp_mapping_without_reason_text(self):
        out=summarize({'status':'partial','omitted_regions':[
            {'type':'footnotes','source':{'stream':'WordDocument','cp_start':12,
             'cp_end':19},'cp_units':7,'reason':'SECRET'}]})
        coverage=out['reading_assessment']['coverage']
        related=next(item for item in coverage if item['category']=='related_content')
        self.assertEqual(related['state'],'not_requested')
        self.assertEqual(related['evidence_count'],1)
        self.assertEqual(related['locations'][0],{
            'type':'footnotes','source':{'stream':'WordDocument','cp_start':12,
             'cp_end':19},'cp_units':7,'index':0})
        self.assertNotIn('SECRET',str(out['reading_assessment']))

    def test_nested_related_and_sheet_diagnostics_become_events(self):
        out=summarize({'status':'partial','issues':[{'code':'main_issue','offset':1}],
            'related_stories':[{'type':'footnotes','issues':[
                {'code':'picture_or_data_marker_not_decoded','count':1,
                 'locations':[{'stream':'WordDocument','cp':23}],
                 'message':'SECRET','text':'SECRET'}]}],
            'sheets':[{'name':'Sheet1','diagnostics':[
                {'code':'unsupported_sheet_feature','sheet':'Sheet1','cell':'A1',
                 'message':'SECRET'}]}]})
        summary=out['reading_assessment']
        self.assertEqual([event['source_field'] for event in summary['events']],
            ['issues','related_stories[0].issues','sheets[0].diagnostics'])
        self.assertEqual(summary['events'][1]['location'],{})
        objects=next(item for item in summary['coverage'] if item['category']=='objects')
        self.assertEqual(objects['state'],'not_decoded')
        self.assertEqual(objects['locations'][0]['cp'],23)
        unread=[item for item in summary['coverage']
                if item['category']=='unread_content']
        self.assertEqual([(item['state'],item['evidence_count']) for item in unread],
                         [('not_decoded',2)])
        self.assertNotIn('SECRET',str(summary))

    def test_coverage_states_use_only_present_recorded_evidence(self):
        absent=summarize({'status':'ok'})['reading_assessment']['coverage']
        self.assertEqual([(item['category'],item['state']) for item in absent],
            [('related_content','unknown'),('binary_tables','unknown'),('objects','unknown')])
        present=summarize({'status':'ok','related_stories':[],
                           'binary_table_structure':{'status':'verified'}})
        coverage=present['reading_assessment']['coverage']
        self.assertEqual(coverage[0]['state'],'extracted')
        self.assertEqual(coverage[1]['state'],'extracted')
        self.assertEqual(coverage[1]['evidence_count'],1)

    def test_related_story_can_supply_binary_table_evidence(self):
        out=summarize({'status':'ok','related_stories':[{
            'type':'headers_footers','binary_table_structure':{
                'status':'verified','rows':[{'source':{
                    'stream':'WordDocument','cp_start':30,'cp_end':35}}]}}]})
        binary=next(item for item in out['reading_assessment']['coverage']
                    if item['category']=='binary_tables')
        self.assertEqual(binary['state'],'extracted')
        self.assertEqual(binary['evidence_count'],1)
        self.assertEqual(binary['locations'][0]['type'],'headers_footers')
        self.assertEqual(binary['locations'][0]['source'],{
            'stream':'WordDocument','cp_start':30,'cp_end':35})

    def test_binary_rows_keep_cp_source_without_cell_text(self):
        out=summarize({'status':'partial','binary_table_structure':{
            'status':'rows_partial','rows':[{'source':{
                'stream':'WordDocument','cp_start':4,'cp_end':9,'row_mark_cp':8},
                'cells':[{'text':'SECRET'}]}]}})
        binary=next(item for item in out['reading_assessment']['coverage']
                    if item['category']=='binary_tables')
        self.assertEqual(binary['state'],'not_decoded')
        self.assertEqual(binary['locations'][0]['source'],{
            'stream':'WordDocument','cp_start':4,'cp_end':9,'row_mark_cp':8})
        self.assertNotIn('SECRET',str(out['reading_assessment']))

    def test_unique_events_and_coverage_locations_have_budgets(self):
        repeated={'code':'duplicate','offset':1,'message':'SECRET'}
        issues=[dict(repeated),dict(repeated)]
        issues.extend({'code':'unsupported_feature','offset':index}
                      for index in range(1000))
        omitted=[{'type':'region','source':{'stream':'WordDocument','cp':index},
                  'cp_units':1,'reason':'not_requested'}
                 for index in range(25)]
        out=summarize({'status':'partial','issues':issues,
                       'omitted_regions':omitted})
        summary=out['reading_assessment']
        self.assertEqual(len(summary['events']),1000)
        self.assertEqual(summary['events_truncated'],1)
        self.assertEqual(summary['events'][0]['code'],'duplicate')
        related=next(item for item in summary['coverage']
                     if item['category']=='related_content')
        self.assertEqual(related['evidence_count'],25)
        self.assertEqual(len(related['locations']),20)
        self.assertEqual(related['locations_truncated'],5)
        unread=next(item for item in summary['coverage']
                    if item['category']=='unread_content')
        self.assertEqual(unread['evidence_count'],1000)
        self.assertEqual(len(unread['locations']),20)
        self.assertEqual(unread['locations_truncated'],980)
        self.assertNotIn('SECRET',str(summary))

if __name__=='__main__':unittest.main()
