"""Independent synthetic CHPX/FKP evidence fixtures, not genuine WPS samples."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import parse_streams, ParseError, read_bytes
from test_wps import document, synthetic_cfb


def fixture(compressed=False, related=False):
    streams=document([(b'AB\r', True)] if compressed else [('甲乙\r'.encode('utf-16le'), False)],
                     other_stories={84:'页眉\r'.encode('utf-16le')} if related else None)
    word=bytearray(streams[('WordDocument',)])
    text_end=len(word)
    page_offset=5120
    word.extend(bytes(page_offset+512-len(word)))
    step=1 if compressed else 2
    fc=[4096,4096+step,text_end]
    struct.pack_into('<3I',word,page_offset,*fc)
    word[page_offset+12:page_offset+14]=bytes((16,24))
    word[page_offset+32:page_offset+36]=b'\3\x3c\x08\1'
    word[page_offset+48:page_offset+52]=b'\3\0\x08\0'
    word[page_offset+511]=2
    table=bytearray(streams[('0Table',)])
    start=len(table)
    table.extend(struct.pack('<3I',fc[0],fc[-1],page_offset//512))
    struct.pack_into('<II',word,250,start,12)
    return {('WordDocument',):bytes(word),('0Table',):bytes(table)}


def mutate(streams, offset, value, width=1, stream='WordDocument'):
    changed=dict(streams);data=bytearray(changed[(stream,)])
    data[offset:offset+width]=value.to_bytes(width,'little')
    changed[(stream,)]=bytes(data);return changed


class CharacterPropertiesTests(unittest.TestCase):
    def test_literal_cp_modifiers_and_no_effective_visibility(self):
        for compressed in (False,True):
            streams=fixture(compressed)
            default=parse_streams(streams)
            result=parse_streams(streams,hidden_text='annotate')
            self.assertEqual(result['text'],default['text'])
            self.assertEqual(result['status'],'partial')
            info=result['character_properties']
            self.assertEqual(info['effective_visibility'],'unknown')
            self.assertFalse(info['text_filtered'])
            self.assertEqual([(r['cp_start'],r['cp_end']) for r in info['runs']],[(0,1),(1,3)])
            self.assertEqual(info['runs'][0]['modifiers'][0]['property'],'hidden')
            self.assertEqual(info['runs'][0]['modifiers'][0]['operand_raw'],1)
            self.assertEqual(info['runs'][1]['modifiers'][0]['property'],'deleted_revision')
            self.assertNotIn('text',info['runs'][0])

    def test_annotation_scope_does_not_include_unselected_stories_or_final_cp(self):
        streams=fixture(related=True)
        main=parse_streams(streams,hidden_text='annotate')
        self.assertEqual(main['character_properties']['annotation_scope'],'declared_main')
        self.assertTrue(all(r['cp_end']<=3 for r in main['character_properties']['runs']))
        related=parse_streams(streams,hidden_text='annotate',include_related=True)
        self.assertEqual(related['character_properties']['annotation_scope'],'selected_stories')
        self.assertEqual(max(r['cp_end'] for r in related['character_properties']['runs']),6)
        self.assertEqual(related['main_cp_units'],3)
        self.assertEqual(related['text'],'甲乙\n')

    def test_no_bte_and_exclude_remains_unsupported(self):
        result=parse_streams(document(),hidden_text='annotate')
        self.assertEqual(result['character_properties']['availability'],'missing_bte')
        self.assertEqual(result['status'],'partial')
        with self.assertRaises(ParseError) as error: parse_streams(fixture(),hidden_text='exclude')
        self.assertEqual(error.exception.status,'unsupported')

    def test_container_annotate_and_default_compatible(self):
        data=synthetic_cfb({key[0]:value for key,value in fixture().items()})
        result=read_bytes(data,hidden_text='annotate')
        self.assertEqual(result['text'],'甲乙\n')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(read_bytes(data)['status'],'ok')

    def test_malformed_pages_pointers_prls_and_alignment(self):
        cases=[(250,0,4),(5120+511,0,1),(5120+511,102,1),(5120+4,4096,4),
               (5120+12,1,1),(5120+32,255,1),(5120+32,2,1),
               (5120+35,2,1),(5120+4,4097,4)]
        for offset,value,width in cases:
            with self.subTest(offset=offset,value=value), self.assertRaises(ParseError):
                parse_streams(mutate(fixture(),offset,value,width),hidden_text='annotate')
        # The low 22 bits select page; undefined upper ten bits are ignored.
        streams=fixture();start=struct.unpack_from('<I',streams[('WordDocument',)],250)[0]
        good=mutate(streams,start+8,10|0xffc00000,4,'0Table')
        self.assertEqual(parse_streams(good,hidden_text='annotate')['text'],'甲乙\n')

    def test_budget_and_page_text_overlap(self):
        with patch('wps_msdoc_charprops.MAX_CHPX_RUNS',1),self.assertRaises(ParseError) as error:
            parse_streams(fixture(),hidden_text='annotate')
        self.assertEqual(error.exception.status,'resource_limit')
        streams=fixture();start=struct.unpack_from('<I',streams[('WordDocument',)],250)[0]
        with self.assertRaises(ParseError):
            parse_streams(mutate(streams,start+8,8,4,'0Table'),hidden_text='annotate')

    def test_variable_operand_is_not_scanned_as_sprm(self):
        streams=fixture();word=bytearray(streams[('WordDocument',)])
        word[5152:5160]=b'\7\0\xc8\4\x3c\x08\1\0'
        streams[('WordDocument',)]=bytes(word)
        result=parse_streams(streams,hidden_text='annotate')
        self.assertEqual(result['character_properties']['runs'][0]['modifiers'],[])
        self.assertEqual(result['character_properties']['runs'][0]['uninterpreted_modifier_count'],1)

if __name__=='__main__':unittest.main()
