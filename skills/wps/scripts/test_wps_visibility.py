"""Synthetic saved BIFF metadata expectations, not producer acceptance."""
from pathlib import Path
import sys, struct, unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wps_visibility import SavedVisibility, select_rows

class VisibilityTests(unittest.TestCase):
    def test_saved_hidden_and_unknown_cause(self):
        v=SavedVisibility(); row=bytearray(16)
        struct.pack_into('<HHH',row,0,4,0,8);struct.pack_into('<H',row,12,32)
        v.add(0x208,bytes(row),100)
        self.assertEqual(v.report()['rows'][0],{'row_zero_based':4,'hidden_saved':True,'hidden_cause':'unknown','record_offset':100})
        v.add(0x7D,struct.pack('<6H',2,3,100,0,1,0),120)
        self.assertTrue(v.report()['columns'][0]['hidden_saved'])
    def test_selection_keeps_unknown_and_default_all(self):
        sheet={'cells':{'A1':{},'A5':{},'B6':{}},'visibility':{'rows':[{'row_zero_based':4,'hidden_saved':True},{'row_zero_based':5,'hidden_saved':False}]}}
        self.assertEqual(len(select_rows(sheet)),3)
        self.assertEqual([x[0] for x in select_rows(sheet,'saved_visible')],['A1','B6'])
        self.assertEqual(select_rows(sheet,'saved_visible')[0][2],'unknown')
    def test_filter_retained_not_executed(self):
        v=SavedVisibility();v.add(0x9E,struct.pack('<H',2),1);v.add(0x9B,b'',2);v.add(0x9D,bytes(24),3)
        f=v.report()['filters'];self.assertFalse(f['conditions_evaluated']);self.assertTrue(f['saved_filter_mode']);self.assertFalse(f['records'][0]['conditions_decoded'])
    def test_default_column_sentinel_and_filter_zero(self):
        v=SavedVisibility();v.add(0x7D,struct.pack('<6H',0,256,0,0,1,0),1)
        item=v.report()['columns'][0]
        self.assertTrue(item['default_column_format_included']);self.assertEqual(item['actual_last_zero_based'],255)
        with self.assertRaises(ValueError):SavedVisibility().add(0x9E,bytes(2),0)
    def test_biff_integration_and_record_budget(self):
        from test_wps_biff8 import workbook,rec
        from wps_biff8 import parse_stream
        from unittest.mock import patch
        row=bytearray(16);struct.pack_into('<HHH',row,0,1,0,8);struct.pack_into('<H',row,12,32)
        result=parse_stream(workbook(sheet_records=[rec(0x208,bytes(row))]))
        self.assertTrue(result['sheets'][0]['visibility']['rows'][0]['hidden_saved'])
        with patch('wps_biff8.MAX_METADATA_BYTES',1),self.assertRaises(ValueError):
            parse_stream(workbook(sheet_records=[rec(0x208,bytes(row))]))

    def test_corruption_and_conflicts(self):
        for kind,data in ((0x208,b''),(0x7D,struct.pack('<6H',5,2,0,0,0,0)),(0x9B,b'x'),(0x9D,b''),(0x9E,b'')):
            with self.subTest(kind=kind),self.assertRaises(ValueError):SavedVisibility().add(kind,data,0)
        v=SavedVisibility();v.add(0x9D,bytes(24),0)
        with self.assertRaises(ValueError):v.report()
        v=SavedVisibility();v.add(0x7D,struct.pack('<6H',2,3,0,0,0,0),0)
        with self.assertRaises(ValueError):v.add(0x7D,struct.pack('<6H',3,4,0,0,0,0),1)

if __name__=='__main__':unittest.main()
