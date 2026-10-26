"""Literal synthetic section/header tables, not real WPS evidence."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_wps import document
from read_wps import parse_streams, ParseError


def fixture(two=True):
    # 6 separator slots + 6 slots per section; header slots end content CR+guard CR.
    main=b'A\x0cB\r' if two else b'A\r'
    chunks=['']*6 + ['','H\r\r','','','','']
    if two: chunks += ['','','','F\r\r','','']
    text=''.join(chunks)+'\r'
    streams=document([(main,True)],{84:text.encode('utf-16le')})
    word=bytearray(streams[('WordDocument',)]);table=bytearray(streams[('0Table',)])
    ends=[0,2,4] if two else [0,2]
    sed=struct.pack('<'+'I'*len(ends),*ends)
    # SEPX lives inside unused post-FIB gap, not piece text. Empty raw property list.
    for i in range(len(ends)-1): sed+=struct.pack('<HIHI',13,2000+i*2,0,0xffffffff)
    h=[0]
    for chunk in chunks: h.append(h[-1]+len(chunk))
    h.append(0xffffffff)  # undefined last CP MUST ignored
    for pair,raw in ((6,sed),(11,struct.pack('<'+'I'*len(h),*h))):
        struct.pack_into('<II',word,154+pair*8,len(table),len(raw));table.extend(raw)
    streams[('WordDocument',)]=bytes(word);streams[('0Table',)]=bytes(table)
    return streams


def word_change(streams,at,value):
    changed=dict(streams); word=bytearray(changed[('WordDocument',)]);struct.pack_into('<I',word,at,value);changed[('WordDocument',)]=bytes(word);return changed


class HeaderSectionsTests(unittest.TestCase):
    def mapping(self,streams): return parse_streams(streams,include_related=True)['related_stories'][1]['section_mapping']

    def test_two_sections_slots_guards_and_empty_inheritance(self):
        streams=fixture();plain=parse_streams(streams);result=parse_streams(streams,include_related=True)
        self.assertEqual(result['text'],plain['text'])
        m=result['related_stories'][1]['section_mapping']
        self.assertEqual([x['main_range'] for x in m['sections']],[{'cp_start':0,'cp_end':2},{'cp_start':2,'cp_end':4}])
        self.assertEqual(len(m['slots']),18)
        self.assertEqual(m['slots'][7]['kind'],'odd_header')
        self.assertEqual(m['slots'][7]['content_range'],{'stream':'WordDocument','cp_start':4,'cp_end':6})
        self.assertEqual(m['slots'][13]['inheritance_candidate_section'],0)
        self.assertEqual(result['related_stories'][1]['status'],'partial')
        self.assertEqual(m['page_mapping'],'unknown')

    def test_single_section_all_empty_slots(self):
        streams=fixture(False)
        self.assertEqual(len(self.mapping(streams)['sections']),1)
        self.assertTrue(self.mapping(streams)['slots'][0]['empty'])

    def test_sed_count_order_break_marker_and_end_clamp(self):
        streams=fixture();word=streams[('WordDocument',)];start=struct.unpack_from('<I',word,202)[0]
        for relative,value in ((0,1),(4,1),(4,4),(8,3)):
            changed=dict(streams);t=bytearray(changed[('0Table',)]);struct.pack_into('<I',t,start+relative,value);changed[('0Table',)]=bytes(t)
            with self.assertRaises(ParseError): self.mapping(changed)
        changed=dict(streams);t=bytearray(changed[('0Table',)]);struct.pack_into('<I',t,start+8,999);changed[('0Table',)]=bytes(t)
        self.assertEqual(self.mapping(changed)['sections'][-1]['main_range']['cp_end'],4)

    def test_bad_hdd_count_order_bounds_and_guard(self):
        streams=fixture();word=streams[('WordDocument',)];start=struct.unpack_from('<I',word,242)[0]
        for relative,value in ((0,1),(4,8),(4,0xffffffff),(7*4,2)):
            changed=dict(streams);t=bytearray(changed[('0Table',)]);struct.pack_into('<I',t,start+relative,value);changed[('0Table',)]=bytes(t)
            with self.assertRaises(ParseError): self.mapping(changed)
        with self.assertRaises(ParseError): self.mapping(word_change(streams,246,12))

    def test_missing_tables_and_overlap_explicit(self):
        streams=fixture()
        with self.assertRaises(ParseError): self.mapping(word_change(streams,206,0))
        with self.assertRaises(ParseError): self.mapping(word_change(streams,242,0))
        legacy=document([(b'A\r',True)],{84:'H\r'.encode('utf-16le')})
        self.assertNotIn('section_mapping',parse_streams(legacy,include_related=True)['related_stories'][1])

    def test_all_empty_slots_and_guard_corruption(self):
        streams=fixture(False)
        word=bytearray(streams[('WordDocument',)])
        # Header story remains one guard CR, every stored slot range is empty.
        # Build fresh CP table plus one counted padding CR by same fixture helper.
        all_empty=document([(b'A\r',True)],{84:b'\r\0'})
        w=bytearray(all_empty[('WordDocument',)]);t=bytearray(all_empty[('0Table',)])
        sed=struct.pack('<IIHIHI',0,2,13,2000,0,0xffffffff)
        for pair,raw in ((6,sed),(11,struct.pack('<14I',*([0]*13+[0xffffffff])))):
            struct.pack_into('<II',w,154+pair*8,len(t),len(raw));t.extend(raw)
        all_empty[('WordDocument',)]=bytes(w);all_empty[('0Table',)]=bytes(t)
        self.assertTrue(all(x['empty'] for x in self.mapping(all_empty)['slots']))
        # Missing the first non-empty slot's dedicated guard must fail.
        changed=dict(streams);w=bytearray(changed[('WordDocument',)])
        # Physical header UTF16 piece begins after overall final CP piece.
        struct.pack_into('<H',w,4102,65)
        changed[('WordDocument',)]=bytes(w)
        with self.assertRaises(ParseError): self.mapping(changed)

    def test_exact_shared_sepx_allowed_but_partial_overlap_rejected(self):
        streams=fixture()
        start=struct.unpack_from('<I',streams[('WordDocument',)],202)[0]
        changed=dict(streams);table=bytearray(changed[('0Table',)])
        struct.pack_into('<I',table,start+26,2000)
        changed[('0Table',)]=bytes(table)
        self.assertEqual(len(self.mapping(changed)['word_ranges']),1)
        changed=dict(streams);word=bytearray(changed[('WordDocument',)])
        struct.pack_into('<H',word,2000,2)
        changed[('WordDocument',)]=bytes(word)
        with self.assertRaises(ParseError): self.mapping(changed)

    def test_sepx_bounds_text_overlap_and_budget(self):
        streams=fixture();word=streams[('WordDocument',)];sed=struct.unpack_from('<I',word,202)[0]
        for fc in (0,0xffffffff,len(word),4096):
            changed=dict(streams);t=bytearray(changed[('0Table',)]);struct.pack_into('<I',t,sed+14,fc);changed[('0Table',)]=bytes(t)
            with self.assertRaises(ParseError): self.mapping(changed)
        with patch('wps_binary_headers.MAX_SECTIONS',1),self.assertRaises(ParseError): self.mapping(streams)

if __name__=='__main__': unittest.main()
