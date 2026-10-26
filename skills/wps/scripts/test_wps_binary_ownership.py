"""Independent cross-structure alias tests, synthetic only."""
from pathlib import Path
import struct,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from read_wps import parse_streams,ParseError
from test_wps_binary_comments import fixture as comments
from test_wps_binary_headers import fixture as headers
from test_wps_charprops import fixture as properties

class OwnershipTests(unittest.TestCase):
    def test_cross_kind_table_alias_precedes_decoding(self):
        for source_pair,target_pair in ((4,33),(36,4),(6,11),(12,33)):
            with self.subTest(pair=source_pair):
                streams=properties() if source_pair==12 else headers() if source_pair==6 else comments()
                word=bytearray(streams[('WordDocument',)])
                offset,size=struct.unpack_from('<II',word,154+target_pair*8)
                struct.pack_into('<II',word,154+source_pair*8,offset,size)
                streams[('WordDocument',)]=bytes(word)
                with self.assertRaises(ParseError) as caught:
                    parse_streams(streams,include_related=True,hidden_text='annotate')
                self.assertEqual(caught.exception.status,'corrupt')
                self.assertIn('Overlapping decoded table',str(caught.exception))

    def test_sepx_and_fkp_cannot_share_word_bytes(self):
        streams=headers(False)
        word=bytearray(streams[('WordDocument',)])
        word.extend(bytes(5632-len(word)))
        page=5120
        struct.pack_into('<2I',word,page,4096,4098)
        word[page+8]=0;word[page+511]=1
        # SEPX size low two bytes of first FC (4096), fits cbMac only after enlargement.
        struct.pack_into('<I',word,64,len(word))
        table=bytearray(streams[('0Table',)])
        sed=struct.unpack_from('<I',word,202)[0]
        # Put a zero-sized SEPX at FKP offset+10 (not text, arrays permit it).
        struct.pack_into('<I',table,sed+10,page+10)
        start=len(table);table.extend(struct.pack('<III',4096,4098,10))
        struct.pack_into('<II',word,250,start,12)
        streams[('WordDocument',)]=bytes(word);streams[('0Table',)]=bytes(table)
        with self.assertRaises(ParseError) as caught:
            parse_streams(streams,include_related=True,hidden_text='annotate')
        self.assertIn('Section properties overlap character FKP',str(caught.exception))

if __name__=='__main__':unittest.main()
