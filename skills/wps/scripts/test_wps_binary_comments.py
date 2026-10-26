"""Literal synthetic annotation metadata, not real producer evidence."""
from pathlib import Path
import struct,sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_wps import document
from read_wps import parse_streams,ParseError

def fixture(author=0,initials=1,bookmark=0xFFFFFFFF,anchor=0,txt=(0,3,0xFFFFFFFF),owner='张三'):
    streams=document([(b'\x05A\r',True)],{92:'\x05注\r\r'.encode('utf-16le')})
    word=bytearray(streams[('WordDocument',)]);table=bytearray(streams[('0Table',)])
    atrd=struct.pack('<H',initials)+'张'.encode('utf-16le')+bytes(16)+struct.pack('<HHHI',author,0,0,bookmark)
    for pair,data in ((4,struct.pack('<II',anchor,0xFFFFFFFF)+atrd),(5,struct.pack('<III',*txt)),(36,struct.pack('<H',len(owner))+owner.encode('utf-16le'))):
        struct.pack_into('<II',word,154+pair*8,len(table),len(data));table.extend(data)
    streams[('WordDocument',)]=bytes(word);streams[('0Table',)]=bytes(table)
    return streams

class CommentTests(unittest.TestCase):
    def test_author_initials_zero_range_and_marker_optin(self):
        s=fixture()
        with self.assertRaises(ParseError):parse_streams(s)
        r=parse_streams(s,include_related=True);self.assertEqual(r['text'],'\ufffcA\n')
        region=r['related_stories'][2];ref=region['references'][0]
        self.assertEqual(region['text'],'\ufffc注\n\n')
        self.assertEqual((ref['author']['name'],ref['initials']),('张三','张'))
        self.assertEqual(ref['annotation_range'],{'cp_start':0,'cp_end':0})
        self.assertEqual(ref['text_range']['cp_start'],3)
        self.assertEqual(region['status'],'partial')
    def test_bookmark_unknown_no_fake_range(self):
        ref=parse_streams(fixture(bookmark=12),include_related=True)['related_stories'][2]['references'][0]
        self.assertIsNone(ref['annotation_range']);self.assertEqual(ref['range_status'],'bookmark_not_decoded')
    def test_bad_author_initials_anchor_and_story_ranges(self):
        for options in ({'author':1},{'initials':10},{'anchor':1},{'txt':(1,3,0)},{'txt':(0,2,0)},{'owner':'X'*56}):
            with self.subTest(options=options),self.assertRaises(ParseError):parse_streams(fixture(**options),include_related=True)
    def test_missing_and_truncated_tables(self):
        for pair,length in ((4,33),(5,8),(36,0)):
            s=fixture();w=bytearray(s[('WordDocument',)]);struct.pack_into('<I',w,158+pair*8,length);s[('WordDocument',)]=bytes(w)
            with self.assertRaises(ParseError):parse_streams(s,include_related=True)
    def test_author_duplicate_and_invalid_utf16(self):
        for suffix in (struct.pack('<H',2)+'张三'.encode('utf-16le'),b'\1\0\0\xd8'):
            s=fixture();t=bytearray(s[('0Table',)]);w=bytearray(s[('WordDocument',)]);t.extend(suffix);at=158+36*8;struct.pack_into('<I',w,at,struct.unpack_from('<I',w,at)[0]+len(suffix));s[('WordDocument',)]=bytes(w);s[('0Table',)]=bytes(t)
            with self.assertRaises(ParseError):parse_streams(s,include_related=True)
    def test_guard_only_zero_comments(self):
        streams=document([(b'A\r',True)],{92:'\r'.encode('utf-16le')})
        word=bytearray(streams[('WordDocument',)]);table=bytearray(streams[('0Table',)])
        for pair,data in ((4,struct.pack('<I',0xFFFFFFFF)),(5,struct.pack('<II',0,0xFFFFFFFF))):
            struct.pack_into('<II',word,154+pair*8,len(table),len(data));table.extend(data)
        streams[('WordDocument',)]=bytes(word);streams[('0Table',)]=bytes(table)
        region=parse_streams(streams,include_related=True)['related_stories'][2]
        self.assertEqual(region['references'],[])
        self.assertEqual(region['text'],'\n')

    def test_budget_before_reference_entries(self):
        with patch('wps_binary_comments.MAX_COMMENTS',0),self.assertRaises(ParseError) as e:parse_streams(fixture(),include_related=True)
        self.assertEqual(e.exception.status,'resource_limit')
if __name__=='__main__':unittest.main()
