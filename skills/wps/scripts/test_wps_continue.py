"""Synthetic independent expected values for plain MS-XLS continuation records."""
import struct
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_biff8 import parse_stream, Reader, ResourceLimitError
from test_wps_biff8 import rec, bof, cell, special, formula, string


def build(sst_parts, sheet=b''):
    globals_part = bof()+rec(0x22,b'\0\0')+rec(0xE0,bytes(20))
    globals_part += rec(0xFC,sst_parts[0])+b''.join(rec(0x3C,p) for p in sst_parts[1:])
    bound = rec(0x85, struct.pack('<IBB',0,0,0)+b'\x01\x00S')
    offset = len(globals_part)+len(bound)+4
    return globals_part+rec(0x85,struct.pack('<IBB',offset,0,0)+b'\x01\x00S')+rec(0xA)+bof(0x10)+rec(0x81,b'\0\0')+sheet+rec(0xA)


class ContinueTests(unittest.TestCase):
    def test_sst_encoding_switch_surrogate_pair_and_new_header_boundary(self):
        # CP units A + 中 + astral smiling face = 4; next string is empty.
        parts=[struct.pack('<IIHB',2,2,4,0)+b'A',b'\x01\x2d\x4e\x3d\xd8',b'\x01\x00\xde',b'\x00\x00\x00']
        cells=rec(0xFD,cell(0,0)+struct.pack('<I',0))+rec(0xFD,cell(1,0)+struct.pack('<I',1))
        result=parse_stream(build(parts,cells))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'A中😀')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'],'')

    def test_long_string_exceeds_single_record_and_formula_string_cache(self):
        text='中'*5000
        parts=[struct.pack('<IIHB',1,1,5000,1)+text[:4000].encode('utf-16le'), b'\1'+text[4000:].encode('utf-16le')]
        cells=rec(0xFD,cell(0,0)+struct.pack('<I',0))
        cells+=formula(1,0,special(0))+rec(0x207,struct.pack('<HB',3,0)+b'AB')+rec(0x3C,b'\1\x2d\x4e')
        result=parse_stream(build(parts,cells))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],text)
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'],'AB中')
        self.assertEqual(result['sheets'][0]['cells']['A2']['cache_status'],'unverified')

    def test_repeated_sst_references_charge_each_cell_before_store(self):
        sst = struct.pack('<IIHB', 3, 1, 3, 0) + b'abc'
        cells = b''.join(rec(0xFD, cell(row, 0)+struct.pack('<I', 0)) for row in range(3))
        reader = Reader(build([sst], cells))
        with patch('wps_biff8.MAX_OUTPUT_TEXT_UNITS', 6):
            with self.assertRaises(ResourceLimitError):
                reader.parse()
        self.assertEqual(reader.cell_count, 2)
        self.assertEqual(reader.output_text_units, 6)

    def test_formula_string_cache_charged_before_assignment(self):
        sst = struct.pack('<IIHB', 1, 1, 3, 0) + b'abc'
        cells = rec(0xFD, cell(0, 0)+struct.pack('<I', 0))
        cells += formula(1, 0, special(0)) + rec(0x207, string('中文'))
        reader = Reader(build([sst], cells))
        with patch('wps_biff8.MAX_OUTPUT_TEXT_UNITS', 4):
            with self.assertRaises(ResourceLimitError):
                reader.parse()
        self.assertEqual(reader.output_text_units, 3)
        self.assertEqual(reader.cell_count, 2)  # pending cache cell; no text assigned

    def test_malformed_continuations_fail_without_partial_result(self):
        head=struct.pack('<IIHB',1,1,2,1)
        for parts in ([head+b'\x2d',b'\1\x4e\x2d\x4e'],[head+b'\x2d\x4e',b'\2xx'],[head+b'\x2d\x4e',b''],[head+b'\x2d\x4e',b'\1'],[head+b'\x3d\xd8',b'\0A'],[head+b'\x2d\x4e'],[struct.pack('<II',1,1)+b'\2',b'\0\0AB']):
            with self.subTest(parts=parts), self.assertRaises(ValueError): parse_stream(build(parts))
        # Orphan Continue after complete SST is not silently ignored.
        with self.assertRaises(ValueError): parse_stream(build([struct.pack('<IIHB',1,1,0,0),b'\0']))

if __name__=='__main__': unittest.main()
