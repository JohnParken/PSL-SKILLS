"""Synthetic MS-XLS TableStyle records; not WPS creator-version acceptance."""
import copy
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_biff8 import workbook, rec
from test_wps_dxf_integration import EMPTY_DXF
from wps_biff8 import parse_stream, UnsupportedError, ResourceLimitError
from wps_biff8_tablestyles import table_style_group


def frt(record_type):
    return struct.pack('<HH', record_type, 0) + bytes(8)


def table_style(name, element_count, flags=4):
    text = name.encode('utf-16le')
    return rec(0x088f, frt(0x088f) +
               struct.pack('<HIH', flags, element_count, len(text) // 2) + text)


def element(area, dxf, size=1):
    return rec(0x0890, frt(0x0890) + struct.pack('<III', area, size, dxf))


def table_workbook():
    return workbook(extra_global=rec(0x088d, EMPTY_DXF)*3 +
                    rec(0x088e, bytes(20)) +
                    table_style('CaseStyle', 2) +
                    element(0, 0) + element(5, 2))


def saved_cells(book):
    return [{address:{k:v for k,v in cell.items()
                      if k not in ('record_offset', 'cache_record_offset')}
             for address,cell in sheet['cells'].items()}
            for sheet in book['sheets']]


class TableStylesTests(unittest.TestCase):
    def test_valid_group_retains_text_and_all_saved_cells(self):
        before = parse_stream(workbook(), compatibility='data')
        after = parse_stream(table_workbook(), compatibility='data')
        self.assertEqual(saved_cells(before), saved_cells(after))
        self.assertEqual(after['cell_count'], before['cell_count'])
        self.assertEqual(after['status'], 'partial')
        source = after['table_style_sources'][0]
        self.assertEqual(source['style_name_raw'], 'CaseStyle')
        self.assertEqual(source['declared_elements'], 2)
        self.assertEqual([e['dxf_index_raw'] for e in source['elements']], [0,2])
        self.assertEqual(source['effective_formatting'], 'unknown')
        self.assertEqual(sum(x['record_id']=='0x890' for x in after['uninterpreted_metadata']), 2)
        self.assertTrue(any(x.get('record_id')=='0x88f' for x in after['issues']))
        with self.assertRaisesRegex(UnsupportedError, 'Unsupported global record'):
            parse_stream(table_workbook(), compatibility='strict')

    def test_multiple_groups_and_end_of_collection(self):
        source = rec(0x088d, EMPTY_DXF) * 3 + rec(0x088e, bytes(20))
        source += table_style('One',1) + element(1,0)
        source += table_style('Two',2) + element(5,1) + element(8,2)
        parsed = parse_stream(workbook(extra_global=source), compatibility='data')
        self.assertEqual(len(parsed['table_style_sources']),2)
        self.assertEqual(parsed['table_style_sources'][1]['declared_elements'],2)

    def test_unpaired_or_out_of_order_members_remain_rejected(self):
        failures=[
            rec(0x088d,EMPTY_DXF)+table_style('NoHeader',1)+element(0,0),
            rec(0x088e,bytes(20))+element(1,0),
            rec(0x088e,bytes(20))+table_style('NoElements',2)+element(0,0),
            rec(0x088d,EMPTY_DXF)+rec(0x088e,bytes(20))+
                table_style('WrongMember',1)+rec(0x0203,bytes(14)),
            rec(0x088d,EMPTY_DXF)+rec(0x088e,bytes(20))+
                table_style('Gap',1)+rec(0x003c,b'\0')+element(0,0)
        ]
        for raw in failures:
            with self.subTest(rawlen=len(raw)), self.assertRaises(ValueError):
                parse_stream(workbook(extra_global=raw),compatibility='data')

    def test_malformed_group_headers_name_count_and_flags_refused(self):
        standard=frt(0x088f)+struct.pack('<HIH',4,1,1)+b'A\0'
        mutations=[
            standard[:-1],
            standard + bytes(1),
            standard[:12]+struct.pack('<H',1)+standard[14:],
            standard[:14]+struct.pack('<I',29)+standard[18:],
            standard[:18]+struct.pack('<H',0)+standard[20:],
            standard[:18]+struct.pack('<H',2)+standard[20:],
            bytes.fromhex('8f080100')+standard[4:],
            bytes.fromhex('8f0800000100000000000000')+standard[12:],
            standard[:20]+b'\0\xd8',
        ]
        for bad in mutations:
            with self.subTest(first=bad[:20].hex()), self.assertRaises(ValueError):
                parse_stream(workbook(extra_global=rec(0x088d, EMPTY_DXF)+rec(0x088e,bytes(20))+
                                      rec(0x088f,bad)+element(0,0)),compatibility='data')

    def test_element_type_bounds_size_dxf_and_frtheader_fail(self):
        elements=[
            element(28,0),
            element(0,0,size=0),
            element(0,0,size=10),
            element(0,1),
            rec(0x0890,frt(0x088f)+struct.pack('<III',0,1,0)),
            rec(0x0890,frt(0x0890)+struct.pack('<III',0,1,0)+bytes(1)),
        ]
        for candidate in elements:
            with self.subTest(candidate=candidate.hex()[:24]), self.assertRaises(ValueError):
                parse_stream(workbook(extra_global=
                    rec(0x088d, EMPTY_DXF)+rec(0x088e,bytes(20))+
                    table_style('T',1)+candidate),compatibility='data')

    def test_metadata_budget_and_unknown_record_refusal(self):
        x=table_workbook()
        with patch('wps_biff8.MAX_METADATA_BYTES',30),self.assertRaises(ResourceLimitError):
            parse_stream(x,compatibility='data')
        with self.assertRaises(ValueError):
            parse_stream(workbook(extra_global=rec(0x7777,b'unknown')),compatibility='data')

    def test_group_helper_does_not_mutate_records(self):
        from wps_biff8 import records
        data=table_workbook()
        record_list=records(data)
        before=copy.deepcopy(record_list)
        index=next(i for i,r in enumerate(record_list) if r[1]==0x088f)
        stop, source, included=table_style_group(record_list,index,3)
        self.assertEqual(stop,index+3)
        self.assertEqual(len(included),3)
        self.assertEqual(source['declared_elements'],2)
        self.assertEqual(record_list,before)


if __name__ == '__main__':
    unittest.main()
