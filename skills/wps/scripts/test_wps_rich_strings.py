"""Synthetic MS-XLS rich SST subset; not producer compatibility evidence."""
from pathlib import Path
import struct
import random
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from wps_biff8 import parse_stream, UnsupportedError, ResourceLimitError, ContinuedStrings
from test_wps_biff8 import rec, bof, cell


def rich_workbook(raw_string, continuation=b'', font_count=4):
    # Fixed fixture with independently stated shared-string cell and font indexes.
    global_bytes=bof()+rec(0x22,b'\0\0')+rec(0xE0,bytes(20))
    global_bytes+=b''.join(rec(0x31, bytes(14)+b'\1\0A') for _ in range(font_count))
    global_bytes+=rec(0xFC, struct.pack('<II',1,1)+raw_string)+continuation
    bound=rec(0x85,struct.pack('<IBB',len(global_bytes)+17,0,0)+b'\1\0S')
    body=bof(0x10)+rec(0x81,b'\0\0')+rec(0xFD,cell(0,0)+bytes(4))+rec(0xA)
    return global_bytes+bound+rec(0xA)+body


def rich(text, runs, compressed=False):
    raw=text.encode('latin1' if compressed else 'utf-16le')
    count=len(raw) if compressed else len(raw)//2
    return struct.pack('<HBH',count,8 if compressed else 9,len(runs))+raw+b''.join(struct.pack('<HH',*item) for item in runs)


class RichStringTests(unittest.TestCase):
    def test_text_and_raw_runs_retained_once_partial_diagnostic(self):
        for text,compressed in [('中A😀',False),('Cafe',True)]:
            result=parse_stream(rich_workbook(rich(text,[(0,0),(1,1)],compressed)))
            self.assertEqual(result['sheets'][0]['cells']['A1']['value'],text)
            self.assertEqual(result['sheets'][0]['cells']['A1']['sst_index'],0)
            self.assertEqual(result['status'],'read_biff8_subset_with_diagnostics')
            self.assertEqual(result['issues'][0]['code'],'rich_string_formatting_not_applied')
            item=result['shared_string_formatting'][0]
            self.assertEqual(item['sst_index'],0)
            self.assertEqual(item['run_count'],2)
            self.assertEqual(item['runs_raw_hex'],'0000000001000100')

    def test_end_marker_font_is_undefined_and_ignored(self):
        result=parse_stream(rich_workbook(rich('Ab',[(0,0),(2,65535)])))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'Ab')

    def test_invalid_run_bounds_order_font_and_utf16_rejected(self):
        for runs in ([(1,0),(0,1)],[(0,0),(0,1)],[(0,0),(3,1)],[(0,4)],[(0,1023)],[(0,5)],[]):
            with self.subTest(runs=runs),self.assertRaises(ValueError):
                parse_stream(rich_workbook(rich('Ab',runs)))
        bad=struct.pack('<HBH',1,9,1)+b'\0\xd8'+struct.pack('<HH',0,0)
        with self.assertRaises(ValueError): parse_stream(rich_workbook(bad))

    def test_continued_runs_and_phonetic_rejection(self):
        raw=rich('Ab',[(0,0),(1,1)])
        result=parse_stream(rich_workbook(raw[:-4], rec(0x3C,raw[-4:])))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'Ab')
        with self.assertRaises(UnsupportedError):
            parse_stream(rich_workbook(struct.pack('<HB',2,4)+b'\0'*8))

    def test_rich_metadata_budget_fails_before_retention(self):
        with patch('wps_biff8.MAX_METADATA_BYTES',70),self.assertRaises(ResourceLimitError):
            parse_stream(rich_workbook(rich('Ab',[(0,0),(1,1)])))

    def test_character_encoding_switch_surrogate_and_run_continuations(self):
        # A (compressed), Chinese + high surrogate, low surrogate, B compressed.
        first=struct.pack('<HBH',5,8,2)+b'A'
        runs=bytes.fromhex('0000000001000100')
        cont=rec(0x3C,b'\1'+bytes.fromhex('2d4e3dd8'))
        cont+=rec(0x3C,b'\1'+bytes.fromhex('00de'))
        cont+=rec(0x3C,b'\0B'+runs[:3])+rec(0x3C,runs[3:])
        result=parse_stream(rich_workbook(first,cont))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'A中😀B')
        item=result['shared_string_formatting'][0]
        self.assertEqual(item['runs_raw_hex'],'0000000001000100')
        self.assertEqual(item['utf16_units'],5)
        self.assertGreater(item['record_offset'],0)

    def test_every_raw_run_split_and_following_string(self):
        runs=bytes.fromhex('0000000001000100')
        for split in range(9):
            parts=[(100,0xFC,struct.pack('<HBH',2,8,2)+b'Ab'+runs[:split]),
                   (200,0x3C,runs[split:]+b'\1\0\0Z')]
            cursor=ContinuedStrings(parts,0)
            self.assertEqual(cursor.string(True),'Ab')
            self.assertEqual(cursor.last_rich['record_offset'],100)
            self.assertEqual(cursor.string(True),'Z')
            self.assertEqual(cursor.finish(),2)

    def test_missing_empty_bad_flag_odd_utf16_and_run_truncation(self):
        header=struct.pack('<HBH',2,9,2)
        cases=[(header+b'A\0',b''),
               (header+b'A\0',rec(0x3C,b'')),
               (header+b'A\0',rec(0x3C,b'\2B\0'+bytes(8))),
               (header+b'A',rec(0x3C,b'\1\0B\0'+bytes(8))),
               (header+b'A\0B\0'+bytes(7),b'')]
        for first,cont in cases:
            with self.subTest(first=first,cont=cont),self.assertRaises(ValueError):
                parse_stream(rich_workbook(first,cont))

    def test_budget_before_missing_run_read_and_fixed_header_boundaries(self):
        cursor=ContinuedStrings([(0,0xFC,struct.pack('<HBH',1,8,10)+b'A')],0)
        with self.assertRaises(ResourceLimitError):
            cursor.string(True,metadata_budget=39)
        self.assertEqual(cursor.index,0)
        for split in range(1,5):
            header=struct.pack('<HBH',1,8,1)
            cursor=ContinuedStrings([(0,0xFC,header[:split]),(10,0x3C,header[split:]+b'A'+bytes(4))],0)
            with self.assertRaises(ValueError): cursor.string(True)

    def test_long_rich_string_with_many_record_boundaries(self):
        text='中A'*4000
        raw=text.encode('utf-16le')
        runs=bytes.fromhex('0000000001000100')
        first=struct.pack('<HBH',8000,9,2)+raw[:6000]
        cont=rec(0x3C,b'\1'+raw[6000:12000])+rec(0x3C,b'\1'+raw[12000:]+runs)
        result=parse_stream(rich_workbook(first,cont))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],text)

    def test_bounded_deterministic_structural_mutations(self):
        # Mutate count/header/run metadata, never the expected text; either reject
        # or preserve the independently specified text. No arbitrary-exception catch.
        original=struct.pack('<HBH',2,8,2)+b'Ab'+bytes.fromhex('0000000001000100')
        randomizer=random.Random(26025)
        for _ in range(128):
            changed=bytearray(original)
            at=randomizer.choice([0,1,2,3,4]+list(range(7,15)))
            changed[at]^=1<<randomizer.randrange(8)
            try:
                result=parse_stream(rich_workbook(bytes(changed[:10]),rec(0x3C,bytes(changed[10:]))))
            except ValueError:
                continue
            self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'Ab')

if __name__=='__main__': unittest.main()
