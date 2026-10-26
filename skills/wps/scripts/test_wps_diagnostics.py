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
if __name__=='__main__':unittest.main()
