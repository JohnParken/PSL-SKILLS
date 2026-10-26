"""Independent literal PLC fixtures, synthetic not real WPS acceptance."""
from pathlib import Path
import struct, sys, unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps import document
from read_wps import parse_streams, ParseError

def fixture(kind='footnotes', anchors=(0,), text_cps=(0,2,0xFFFFFFFF), flags=(0,)):
    offset, pair = (80,2) if kind == 'footnotes' else (96,46)
    streams=document([(b'A\r',True)],{offset:'甲\r\r'.encode('utf-16le')})
    table=bytearray(streams[('0Table',)]); word=bytearray(streams[('WordDocument',)])
    for index, payload in ((pair, struct.pack('<'+'I'*(len(anchors)+1),*anchors,0xFFFFFFFF)+struct.pack('<'+'H'*len(flags),*flags)),
                           (pair+1,struct.pack('<'+'I'*len(text_cps),*text_cps))):
        struct.pack_into('<II',word,154+index*8,len(table),len(payload)); table.extend(payload)
    streams[('WordDocument',)]=bytes(word);streams[('0Table',)]=bytes(table)
    return streams

class BinaryRefsTests(unittest.TestCase):
    def test_literal_note_mappings_and_default_unchanged(self):
        for kind,index in [('footnotes',0),('endnotes',3)]:
            s=fixture(kind);self.assertEqual(parse_streams(s)['text'],'A\n')
            r=parse_streams(s,include_related=True); region=r['related_stories'][index]
            self.assertEqual(region['references'][0]['anchor'],{'stream':'WordDocument','cp':0})
            self.assertEqual(region['references'][0]['text_range'],{'stream':'WordDocument','cp_start':2,'cp_end':4})
            self.assertEqual(region['anchor_status'],'cp_mapped_properties_unverified')
            self.assertEqual(region['status'],'partial')
    def test_auto_marker_only_at_verified_cp_and_only_optin(self):
        s=fixture(flags=(1,)); w=bytearray(s[('WordDocument',)]); w[-2]=2; s[('WordDocument',)]=bytes(w)
        with self.assertRaises(ParseError):parse_streams(s)
        result=parse_streams(s,include_related=True)
        self.assertEqual(result['text'],'\ufffc\n')
        self.assertEqual(result['issues'][0]['code'],'automatic_note_marker_not_rendered')
        self.assertEqual(result['related_stories'][0]['references'][0]['numbering'],'automatic')
        w[-1]=2;s[('WordDocument',)]=bytes(w)
        with self.assertRaises(ParseError):parse_streams(s,include_related=True)

    def test_undefined_last_cp_ignored(self):
        for terminal in (0,1,0xFFFFFFFF):
            self.assertEqual(len(parse_streams(fixture(text_cps=(0,2,terminal)),include_related=True)['related_stories'][0]['references']),1)
    def test_wrong_anchor_and_text_boundaries(self):
        for kwargs in ({'anchors':(2,)},{'text_cps':(1,2,9)},{'text_cps':(0,1,9)},{'text_cps':(0,0,9)},{'flags':(1,)}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ParseError):parse_streams(fixture(**kwargs),include_related=True)
    def test_missing_pair_and_truncated_lengths(self):
        for at,value in ((154+3*8+4,0),(154+3*8+4,11),(154+2*8+4,9)):
            s=fixture(); w=bytearray(s[('WordDocument',)]);struct.pack_into('<I',w,at,value);s[('WordDocument',)]=bytes(w)
            with self.assertRaises(ParseError):parse_streams(s,include_related=True)
    def test_two_note_stories_use_separate_global_ranges(self):
        s=document([(b'AB\r',True)],{80:'脚\r\r'.encode('utf-16le'),96:'尾\r\r'.encode('utf-16le')})
        word=bytearray(s[('WordDocument',)]);table=bytearray(s[('0Table',)])
        for pair,anchor in ((2,0),(46,1)):
            for index,payload in ((pair,struct.pack('<IIH',anchor,0xFFFFFFFF,0)),(pair+1,struct.pack('<III',0,2,0))):
                struct.pack_into('<II',word,154+index*8,len(table),len(payload));table.extend(payload)
        s[('WordDocument',)]=bytes(word);s[('0Table',)]=bytes(table)
        r=parse_streams(s,include_related=True)
        self.assertEqual(r['related_stories'][0]['references'][0]['text_range']['cp_start'],3)
        self.assertEqual(r['related_stories'][3]['references'][0]['text_range']['cp_start'],6)
        self.assertEqual(r['related_stories'][3]['references'][0]['anchor']['cp'],1)

    def test_cross_kind_alias_rejected(self):
        s=fixture(); w=bytearray(s[('WordDocument',)])
        w[154+46*8:154+48*8]=w[154+2*8:154+4*8]
        s[('WordDocument',)]=bytes(w)
        with self.assertRaises(ParseError) as error:parse_streams(s,include_related=True)
        self.assertIn('Overlapping',str(error.exception))

    def test_budget_before_entries(self):
        with patch('wps_binary_refs.MAX_REFERENCES',0),self.assertRaises(ParseError) as error:
            parse_streams(fixture(),include_related=True)
        self.assertEqual(error.exception.status,'resource_limit')
    def test_missing_tables_still_explicit_unknown(self):
        s=document([(b'A\r',True)],{80:'甲\r'.encode('utf-16le')})
        r=parse_streams(s,include_related=True)['related_stories'][0]
        self.assertEqual((r['status'],r['anchor_status']),('partial','unknown'))
if __name__=='__main__':unittest.main()
