"""Synthetic saved BIFF metadata expectations, not producer acceptance."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sys, struct, unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wps_visibility import SavedVisibility, select_rows

class VisibilityTests(unittest.TestCase):
    def test_saved_hidden_and_unknown_cause(self):
        v=SavedVisibility()
        row=bytearray(16)
        struct.pack_into('<4H',row,0,4,0,8,305)
        struct.pack_into('<H',row,12,32)
        v.add(0x208,bytes(row),100)
        self.assertEqual(v.report()['rows'][0], {'row_zero_based': 4, 'hidden_saved': True, 'hidden_cause': 'unknown',
                                               'record_offset': 100, 'height_twips_raw': 305, 'height_pt': '15.25'})
        v.add(0x7D,struct.pack('<6H',2,3,2688,0,1,0),120)
        column = v.report()['columns'][0]
        self.assertTrue(column['hidden_saved'])
        self.assertEqual((column['width_256th_char_raw'], column['width_chars']), (2688, '10.5'))
    def test_selection_keeps_unknown_and_default_all(self):
        sheet={'cells':{'A1':{},'A5':{},'B6':{}},'visibility':{'rows':[{'row_zero_based':4,'hidden_saved':True},{'row_zero_based':5,'hidden_saved':False}]}}
        self.assertEqual(len(select_rows(sheet)),3)
        self.assertEqual([x[0] for x in select_rows(sheet,'saved_visible')],['A1','B6'])
        self.assertEqual(select_rows(sheet,'saved_visible')[0][2],'unknown')
    def test_filter_retained_not_executed(self):
        v=SavedVisibility()
        v.add(0x9D,struct.pack('<H',2),1)
        v.add(0x9B,b'',2)
        v.add(0x9E,bytes(24),3)
        f=v.report()['filters']
        self.assertFalse(f['conditions_evaluated'])
        self.assertTrue(f['saved_filter_mode'])
        self.assertFalse(f['records'][0]['conditions_decoded'])
    def test_spec_record_ids_public_biff_and_adjacent_cells(self):
        # MS-XLS record table: 0x009D AutoFilterInfo, 0x009E AutoFilter.
        # The fixtures exercise record framing and retained fields, not complete producer semantics.
        from test_wps_biff8 import workbook, rec, cell
        from test_wps_cfb import synthetic_cfb
        from read_et import read_bytes
        rows = rec(0x0203, cell(0, 0) + struct.pack('<d', 42.0))
        info = rec(0x009D, struct.pack('<H', 2))
        auto = rec(0x009E, struct.pack('<HH', 1, 0) + bytes(20))
        data = synthetic_cfb({'Workbook': workbook(sheet_records=[rows + info + auto])})
        for mode in ('data', 'strict', 'legacy_excel_clsid'):
            with self.subTest(mode=mode):
                result = read_bytes(data, compatibility=mode)
                sheet = result['sheets'][0]
                self.assertEqual(str(sheet['cells']['A1']['value']), '42')
                filters = sheet['visibility']['filters']
                self.assertEqual(filters['column_count'], 2)
                self.assertEqual(filters['records'][0]['column_relative'], 1)
                self.assertEqual(filters['records'][0]['payload_hex'], (struct.pack('<HH', 1, 0) + bytes(20)).hex())
                self.assertFalse(filters['records'][0]['conditions_decoded'])
                self.assertFalse(filters['conditions_evaluated'])
                self.assertIn('saved_filter_not_evaluated', [issue['code'] for issue in result['issues']])
                self.assertEqual(result['status'], 'partial')

    def test_spec_record_ids_invalid_and_conflicts_degrade(self):
        from test_wps_biff8 import workbook, rec
        from test_wps_cfb import synthetic_cfb
        from read_et import read_bytes
        info = rec(0x009D, struct.pack('<H', 1))
        filt = rec(0x009E, bytes(24))
        cases = [
            rec(0x009D, b'\x00'),
            rec(0x009D, bytes(2)),
            info + info,
            info + rec(0x009E, bytes(23)),
            info + filt + filt,
            filt,
            info + rec(0x009E, struct.pack('<H', 1) + bytes(22)),
        ]
        for records in cases:
            data = synthetic_cfb({'Workbook': workbook(sheet_records=[records])})
            with self.subTest(records=records.hex()):
                result = read_bytes(data, compatibility='data')
                self.assertEqual(result['status'], 'partial')
                self.assertFalse(result['formulas_executed'])
                self.assertTrue([item for item in result['issues']
                                 if item['code'] in ('row_column_metadata_invalid',
                                                     'visibility_metadata_invalid',
                                                     'saved_filter_not_evaluated')])

    def test_filter_limits_and_info_only(self):
        v = SavedVisibility()
        v.add(0x009D, struct.pack('<H', 256), 0)
        self.assertEqual(v.report()['filters']['records'], [])
        for column in range(256):
            v.add(0x009E, struct.pack('<H', column) + bytes(22), column + 1)
        self.assertEqual(len(v.report()['filters']['records']), 256)
        with self.assertRaisesRegex(ValueError, 'Malformed/oversized AutoFilter'):
            v.add(0x009E, bytes(24), 257)
        with self.assertRaisesRegex(ValueError, 'Invalid AutoFilterInfo'):
            SavedVisibility().add(0x009D, struct.pack('<H', 257), 0)

    def test_standard_cli_paths_with_filter(self):
        import json, subprocess, tempfile
        from test_wps_biff8 import workbook, rec, cell
        from test_wps_cfb import synthetic_cfb
        rows = rec(0x0203, cell(0, 0) + struct.pack('<d', 42.0))
        info = rec(0x009D, struct.pack('<H', 1))
        filt = rec(0x009E, bytes(24))
        script_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'fixture.et'
            source.write_bytes(synthetic_cfb({'Workbook': workbook(sheet_records=[rows + info + filt])}))
            commands = [
                [str(script_dir / 'et_cli.py'), 'run', str(source)],
                [str(script_dir / 'read_et.py'), str(source), '--summary'],
                [str(script_dir / 'analyze_et.py'), str(source), '--sheet', '中文表1', '--range', 'A1:A1', '--preview'],
                [str(script_dir / 'analyze_et.py'), str(source), '--sheet', '中文表1', '--range', 'A1:A1'],
            ]
            for args in commands:
                with self.subTest(entry=args[0], args=args[1:]):
                    result = subprocess.run([sys.executable, '-I', '-S', '-B', *args],
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                    output = json.loads(result.stdout)
                    self.assertEqual(output['status'], 'partial')
                    if output.get('schema_version') == 'et_table_preview_v1':
                        self.assertEqual(output['rows'][0]['cells']['A']['value'], '42')
                    if output.get('schema_version') == 'et_table_analysis_v1':
                        self.assertEqual(output['profiles']['A']['sum'], '42')
            self.assertEqual(source.read_bytes(), synthetic_cfb({'Workbook': workbook(sheet_records=[rows + info + filt])}))

    def test_default_column_sentinel_and_filter_zero(self):
        v=SavedVisibility()
        v.add(0x7D,struct.pack('<6H',0,256,0,0,1,0),1)
        item=v.report()['columns'][0]
        self.assertTrue(item['default_column_format_included'])
        self.assertEqual(item['actual_last_zero_based'],255)
        with self.assertRaises(ValueError):
            SavedVisibility().add(0x9D,bytes(2),0)
    def test_biff_integration_and_record_budget(self):
        from test_wps_biff8 import workbook,rec
        from wps_biff8 import parse_stream
        from unittest.mock import patch
        row=bytearray(16)
        struct.pack_into('<HHH',row,0,1,0,8)
        struct.pack_into('<H',row,12,32)
        result=parse_stream(workbook(sheet_records=[rec(0x208,bytes(row))]))
        saved_row = result['sheets'][0]['visibility']['rows'][0]
        self.assertTrue(saved_row['hidden_saved'])
        self.assertEqual(saved_row['height_pt_status'], 'uninterpreted')
        self.assertNotIn('height_pt', saved_row)
        self.assertEqual(result['status'], parse_stream(workbook(sheet_records=[b'']))['status'])
        # Structured Row/ColInfo has a separate budget and no raw payload_hex;
        # a tiny raw-metadata byte budget therefore cannot hide saved rows.
        with patch('wps_biff8.MAX_METADATA_BYTES',1):
            result = parse_stream(workbook(sheet_records=[rec(0x208,bytes(row))]))
        self.assertEqual(len(result['sheets'][0]['visibility']['rows']), 1)
        self.assertIn(result['status'], ('ok', 'partial'))

    def test_structured_row_columninfo_budget_is_separate_and_truncates(self):
        from test_wps_biff8 import workbook, rec, cell
        from wps_biff8 import parse_stream
        from unittest.mock import patch
        import struct as _struct
        rows = []
        for row_number in range(4):
            row = bytearray(16)
            _struct.pack_into('<HHH', row, 0, row_number, 0, 8)
            _struct.pack_into('<H', row, 12, 32)
            rows.append(rec(0x208, bytes(row)))
        payload = b''.join(rows) + rec(0x0203, cell(0, 0) + _struct.pack('<d', 7))
        with patch('wps_biff8.MAX_STRUCTURED_ROWS', 2):
            result = parse_stream(workbook(sheet_records=[payload], strings=[]))
        self.assertEqual(len(result['sheets'][0]['visibility']['rows']), 2)
        self.assertEqual(result['structured_metadata_truncated_count'], 2)
        self.assertIn('structured_metadata_budget_truncated',
                      [item['code'] for item in result['issues']])
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 7)
        with patch('wps_biff8.MAX_STRUCTURED_BYTES', 16):
            result = parse_stream(workbook(sheet_records=[payload], strings=[]))
        self.assertEqual(result['status'], 'partial')
        self.assertIn('structured_metadata_byte_budget_truncated',
                      [item['code'] for item in result['issues']])
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 7)


    def test_corruption_and_conflicts(self):
        for kind,data in ((0x208,b''),(0x7D,struct.pack('<6H',5,2,0,0,0,0)),(0x9B,b'x'),(0x9D,b''),(0x9E,b'')):
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                SavedVisibility().add(kind,data,0)
        v=SavedVisibility()
        v.add(0x9E,bytes(24),0)
        with self.assertRaises(ValueError):
            v.report()
        v=SavedVisibility()
        v.add(0x7D,struct.pack('<6H',2,3,0,0,0,0),0)
        with self.assertRaises(ValueError):
            v.add(0x7D,struct.pack('<6H',3,4,0,0,0,0),1)

if __name__=='__main__':
    unittest.main()
