"""Synthetic exact-header compatibility fixtures, not producer evidence."""
from pathlib import Path
import struct
import json
import subprocess
import sys
import tempfile
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
        with self.assertRaises(ParseError):read_bytes(data,compatibility='strict')
        for mode in ('data', 'legacy_word_clsid'):
            result=read_bytes(data,compatibility=mode)
            self.assertEqual(result['text'],'Hello 中文\n第二段\n')
            self.assertEqual(result['status'],'partial')
            self.assertTrue(result['partial'])
            self.assertEqual(result['container_issues'][0]['raw_hex'],GUID.hex())
            self.assertEqual(result['container_compatibility'], 'legacy_word_clsid')
            self.assertEqual(result['compatibility_mode'], mode)
            self.assertFalse(result['producer_verified'])
        self.assertEqual(data[8:24],GUID)

    def test_legacy_minor_default_preserves_text_and_diagnostics(self):
        for guid in (GUID, bytes(16)):
            original = bytearray(container(guid=guid))
            struct.pack_into('<H', original, 24, 0x003B)
            data = bytes(original)
            result = read_bytes(data)
            self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
            self.assertEqual(result['status'], 'partial')
            self.assertIn('legacy_cfb_minor_version',
                          [issue['code'] for issue in result['container_issues']])
            with self.assertRaises(ParseError):
                read_bytes(data, compatibility='strict')
            broken = bytearray(data)
            broken[34] = 1
            with self.assertRaises(ParseError):
                read_bytes(bytes(broken))
            self.assertEqual(data, bytes(original))

    def test_scope_is_not_generic_relaxation(self):
        for data in (container(guid=bytes([1])*16),container(major=4),
                     container({'Workbook':b'abc'}),container({'WordDocument':b'bad'}),
                     container({'WordDocument':b'\xec\xa5','Workbook':b'abc'})):
            with self.assertRaises(ValueError):CompoundFile(data,compatibility='legacy_word_clsid')
        with self.assertRaises(ValueError):CompoundFile(container(),compatibility='legacy_excel_clsid')

    def test_default_cli_reads_legacy_word_header_without_rewriting(self):
        data = bytearray(container())
        struct.pack_into('<H', data, 24, 0x003B)
        original = bytes(data)
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'legacy.wps'
            path.write_bytes(original)
            command = [sys.executable, '-I', '-S', '-B',
                       str(Path(__file__).with_name('read_wps.py')), str(path)]
            process = subprocess.run(command, capture_output=True, text=True,
                                     env={'PATH': ''}, timeout=5)
            self.assertEqual(process.returncode, 3, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
            self.assertEqual(len(result['container_issues']), 2)
            self.assertEqual(path.read_bytes(), original)

    def test_structural_checks_remain(self):
        data=bytearray(container());data[512:516]=b'\x00\x00\x00\x00'
        with self.assertRaises(ValueError):CompoundFile(bytes(data),compatibility='legacy_word_clsid')


if __name__=='__main__':unittest.main()
