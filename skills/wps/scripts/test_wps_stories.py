"""Synthetic literal story ranges; not real WPS producer acceptance evidence."""
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from read_wps import parse_streams, read_bytes, ParseError
from test_wps import document, replace_word, synthetic_cfb

class StoriesTests(unittest.TestCase):
    def result(self, streams, **options):
        return parse_streams(streams,include_related=True,**options)

    def test_all_ranges_and_main_unchanged(self):
        chunks={80:'脚注\r',84:'页眉\r页脚\r',92:'批注\r',96:'尾注\r',100:'框😀\r',104:'页框\r'}
        streams=document([(b'Main\r',True)],{k:v.encode('utf-16le') for k,v in chunks.items()})
        plain=parse_streams(streams)
        result=self.result(streams)
        self.assertEqual((result['text'],result['paragraphs'],result['main_cp_units']),('Main\n',['Main'],5))
        self.assertNotIn('related_stories',plain)
        self.assertEqual(result['status'],'partial')
        cursor=5
        for region,(offset,text) in zip(result['related_stories'],chunks.items()):
            units=len(text.encode('utf-16le'))//2
            self.assertEqual(region['source'],{'stream':'WordDocument','cp_start':cursor,'cp_end':cursor+units,'count_offset':offset})
            self.assertEqual(region['text'],text.replace('\r','\n'))
            self.assertEqual(region['references'],[])
            self.assertEqual(region['anchor_status'],'unknown')
            self.assertEqual(region['status'],'partial')
            cursor+=units
        self.assertEqual([x['type'] for x in result['related_stories']],['footnotes','headers_footers','comments','endnotes','textboxes','header_textboxes'])

    def test_empty_main_and_regions(self):
        result=self.result(document([]))
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['text'],'')
        for region in result['related_stories']:
            self.assertEqual(region['status'],'ok')
            self.assertEqual(region['text'],'')
            self.assertEqual(region['anchor_status'],'not_applicable')
        result=self.result(document([],{96:'尾\r'.encode('utf-16le')}))
        self.assertEqual(result['text'],'')
        self.assertEqual(result['related_stories'][3]['source']['cp_start'],0)

    def test_piece_crosses_story_boundary_and_table_one(self):
        streams=document([('甲\r'.encode('utf-16le'),False)],{80:'乙😀\r'.encode('utf-16le')},table_one=True)
        # Rebuild CLX to a single contiguous UTF16 piece crossing main/story boundary.
        word=bytearray(streams[('WordDocument',)])
        raw='甲\r乙😀\r\r'.encode('utf-16le')
        word[4096:4096+len(raw)]=raw
        struct.pack_into('<I',word,64,4096+len(raw))
        table=b'\x02'+struct.pack('<I',16)+struct.pack('<II',0,7)+b'\0\0'+struct.pack('<I',4096)+b'\0\0'
        struct.pack_into('<II',word,418,0,len(table))
        streams={('WordDocument',):bytes(word),('1Table',):table}
        result=self.result(streams)
        self.assertEqual(result['text'],'甲\n')
        self.assertEqual(result['related_stories'][0]['text'],'乙😀\n')

    def test_invalid_related_utf16_only_optin(self):
        streams=document([(b'A\r',True)],{80:b'\0\xd8',84:b'\0\xdc'})
        self.assertEqual(parse_streams(streams)['text'],'A\n')
        with self.assertRaises(ParseError) as error: self.result(streams)
        self.assertEqual(error.exception.status,'corrupt')
        self.assertEqual(error.exception.location['cp'],2)

    def test_field_and_object_policies_scoped(self):
        streams=document([(b'A\r',True)],{80:'\x13F\x14R\x15\x01\r'.encode('utf-16le')})
        with self.assertRaises(ParseError): self.result(streams)
        result=self.result(streams,field_policy='cached_result',object_policy='placeholder')
        region=result['related_stories'][0]
        self.assertEqual(region['text'],'R\ufffc\n')
        self.assertEqual(region['issues'][1]['locations'][0]['cp'],7)
        streams=document([(b'A\r',True)],{80:'\x13X'.encode('utf-16le'),84:'Y\x14Z\x15\r'.encode('utf-16le')})
        with self.assertRaises(ParseError): self.result(streams,field_policy='cached_result')

    def test_bad_counts_terminal_and_budgets(self):
        streams=document([(b'A\r',True)],{80:'乙\r'.encode('utf-16le')})
        for value in (3,0xFFFFFFFF):
            with self.assertRaises(ParseError): self.result(replace_word(streams,80,value))
        with patch('read_wps.MAX_UNITS',3),self.assertRaises(ParseError): self.result(streams)
        with patch('read_wps.MAX_PARAGRAPHS',1),self.assertRaises(ParseError): self.result(streams)
        fields=document([(b'A\r',True)],{80:'\x13X\x14Y\x15\r'.encode('utf-16le'),96:'\x13X\x14Y\x15\r'.encode('utf-16le')})
        with patch('read_wps.MAX_FIELDS',1),self.assertRaises(ParseError): self.result(fields,field_policy='cached_result')
        objects=document([(b'A\r',True)],{80:'\x01\r'.encode('utf-16le'),96:'\x01\r'.encode('utf-16le')})
        with patch('read_wps.MAX_OBJECT_MARKERS',1),self.assertRaises(ParseError): self.result(objects,object_policy='placeholder')

    def test_cli_related_text_rejected_before_input(self):
        process = subprocess.run([sys.executable, '-I', '-S', '-B',
            str(Path(__file__).with_name('read_wps.py')), 'does-not-exist.wps',
            '--include-related', '--format', 'text'], capture_output=True, text=True,
            env={'PATH': ''}, timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, '')
        self.assertIn('requires --format json', process.stderr)

    def test_unified_cfb_optin(self):
        streams=document([(b'A\r',True)],{92:'批\r'.encode('utf-16le')})
        data=synthetic_cfb({k[0]:v for k,v in streams.items()})
        result=read_bytes(data,include_related=True)
        self.assertEqual(result['related_stories'][2]['text'],'批\n')
        self.assertEqual(result['text'],'A\n')

if __name__=='__main__':unittest.main()
