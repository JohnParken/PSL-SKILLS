"""Synthetic exact-header compatibility fixtures, not producer evidence."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_wps import synthetic_cfb, document
from wps_cfb import CompoundFile
from read_wps import read_bytes, ParseError

GUID=bytes.fromhex('0609020000000000c000000000000046')


def container(streams=None,major=3,guid=GUID):
    streams={key[0]:value for key,value in document().items()} if streams is None else streams
    data=bytearray(synthetic_cfb(streams,major));data[8:24]=guid
    return bytes(data)


class WordProfileTests(unittest.TestCase):
    def test_explicit_exact_profile(self):
        data=container()
        with self.assertRaises(ParseError):read_bytes(data)
        result=read_bytes(data,compatibility='legacy_word_clsid')
        self.assertEqual(result['text'],'Hello 中文\n第二段\n')
        self.assertEqual(result['status'],'partial')
        self.assertTrue(result['partial'])
        self.assertEqual(result['container_issues'][0]['raw_hex'],GUID.hex())
        self.assertFalse(result['producer_verified'])
        self.assertEqual(data[8:24],GUID)

    def test_scope_is_not_generic_relaxation(self):
        for data in (container(guid=bytes([1])*16),container(major=4),
                     container({'Workbook':b'abc'}),container({'WordDocument':b'bad'}),
                     container({'WordDocument':b'\xec\xa5','Workbook':b'abc'})):
            with self.assertRaises(ValueError):CompoundFile(data,compatibility='legacy_word_clsid')
        with self.assertRaises(ValueError):CompoundFile(container(),compatibility='legacy_excel_clsid')

    def test_structural_checks_remain(self):
        data=bytearray(container());data[512:516]=b'\x00\x00\x00\x00'
        with self.assertRaises(ValueError):CompoundFile(bytes(data),compatibility='legacy_word_clsid')


if __name__=='__main__':unittest.main()
