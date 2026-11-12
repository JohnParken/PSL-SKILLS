"""Synthetic MS-XLS rich SST subset; not producer compatibility evidence."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import random
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
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
            self.assertEqual(result['status'],'partial')
            self.assertEqual(result['issues'][0]['code'],'rich_string_formatting_not_applied')
            item=result['shared_string_formatting'][0]
            self.assertEqual(item['sst_index'],0)
            self.assertEqual(item['run_count'],2)
            self.assertEqual(item['runs_raw_hex'],'0000000001000100')
            self.assertEqual(item['segments'], [{'text': text[:1], 'font_index': 0}, {'text': text[1:], 'font_index': 1}])
            self.assertEqual(result['rich_string_fonts']['1']['font_name'], 'A')

    def test_font_properties_and_unresolvable_surrogate_boundary(self):
        raw = rich_workbook(rich('😀Z', [(0, 0), (2, 1)]))
        old_font = rec(0x31, bytes(14) + b'\1\0A')
        font = rec(0x31, struct.pack('<4H', 220, 2, 10, 700) + bytes(6) + b'\1\0B')
        result = parse_stream(raw.replace(old_font, font, 1))
        self.assertEqual(result['rich_string_fonts']['0'], {'font_name': 'B', 'bold': True, 'italic': True,
                                                          'color_index_raw': 10, 'weight_raw': 700, 'height_twips_raw': 220})
        result = parse_stream(rich_workbook(rich('😀Z', [(0, 0), (1, 1)])))
        self.assertEqual(result['shared_string_formatting'][0]['segments_status'], 'run_splits_surrogate_pair')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '😀Z')

    def test_end_marker_font_is_undefined_and_ignored(self):
        result=parse_stream(rich_workbook(rich('Ab',[(0,0),(2,65535)])))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'Ab')

    def test_invalid_run_bounds_order_font_and_utf16_are_omitted(self):
        for runs in ([(1,0),(0,1)],[(0,0),(0,1)],[(0,0),(3,1)],[(0,4)],[(0,1023)],[(0,5)],[]):
            with self.subTest(runs=runs):
                result=parse_stream(rich_workbook(rich('Ab',runs)))
                self.assertEqual(result['status'],'partial')
                self.assertFalse(result['formulas_executed'])
        bad=struct.pack('<HBH',1,9,1)+b'\0\xd8'+struct.pack('<HH',0,0)
        result=parse_stream(rich_workbook(bad))
        self.assertEqual(result['status'],'partial')
        self.assertNotIn('A1',result['sheets'][0]['cells'])

    def test_continued_runs_and_phonetic_retention(self):
        raw=rich('Ab',[(0,0),(1,1)])
        result=parse_stream(rich_workbook(raw[:-4], rec(0x3C,raw[-4:])))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'Ab')
        malformed=struct.pack('<HB',2,4)+struct.pack('<i',4)+b'Ab12'
        result=parse_stream(rich_workbook(malformed))
        self.assertEqual(result['status'],'partial')
        self.assertTrue(any(code in ('global_record_validation_failed',
                                     'sst_index_out_of_range_omitted')
                            for code in [item['code'] for item in result['issues']]))

    def test_rich_metadata_budget_keeps_plain_text(self):
        with patch('wps_biff8.MAX_METADATA_BYTES',70):
            result=parse_stream(rich_workbook(rich('Ab',[(0,0),(1,1)])))
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'Ab')
        self.assertIn('rich_phonetic_metadata_budget_truncated',
                      [item['code'] for item in result['issues']])

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
            with self.subTest(first=first,cont=cont):
                result=parse_stream(rich_workbook(first,cont))
                self.assertEqual(result['status'],'partial')
                self.assertFalse(result['formulas_executed'])

    def test_budget_before_missing_run_read_and_fixed_header_boundaries(self):
        cursor=ContinuedStrings([(0,0xFC,struct.pack('<HBH',1,8,10)+b'A')],0)
        with self.assertRaises(ResourceLimitError):
            cursor.string(True,metadata_budget=39)
        self.assertEqual(cursor.index,0)
        for split in range(1,5):
            header=struct.pack('<HBH',1,8,1)
            cursor=ContinuedStrings([(0,0xFC,header[:split]),(10,0x3C,header[split:]+b'A'+bytes(4))],0)
            with self.assertRaises(ValueError):
                cursor.string(True)

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
            self.assertIn(result['status'],('ok','partial'))
            entry=result['sheets'][0]['cells'].get('A1')
            if entry is not None:
                # Record framing/flag mutations may change the decoded text,
                # but never the type contract or read-only guarantees.
                self.assertEqual(entry['type'],'text')
                self.assertIsInstance(entry['value'],str)
            else:
                self.assertTrue(result['omissions_affect_cell_values'])

if __name__=='__main__':
    unittest.main()
