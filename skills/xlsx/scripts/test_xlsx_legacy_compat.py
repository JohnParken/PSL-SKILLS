#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic interoperability regressions; no redistributed ET document bytes."""
from decimal import Decimal
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_xlsx_cfb import synthetic_cfb
from test_xlsx_biff8 import workbook,rec
from xlsx_cfb import CompoundFile,LEGACY_EXCEL_CLSID
from xlsx_identify import identify
from xlsx_biff8 import parse_stream,read_file,LEGACY_GLOBAL_METADATA,LEGACY_SHEET_METADATA

MODE='legacy_excel_clsid'
EXPECTED_CLSID=bytes.fromhex('2008020000000000c000000000000046')


def legacy_container(streams=None,major=3):
    data=bytearray(synthetic_cfb(streams,major)); data[8:24]=EXPECTED_CLSID
    return bytes(data)


class LegacyCompatibilityTests(unittest.TestCase):
    def test_explicit_known_guid_only_and_no_format_claim(self):
        self.assertEqual(LEGACY_EXCEL_CLSID,EXPECTED_CLSID)
        data=legacy_container()
        with self.assertRaisesRegex(ValueError,'Nonzero CFB header CLSID'): CompoundFile(data)
        book=CompoundFile(data,compatibility=MODE); report=book.report()
        self.assertEqual(report['issues'][0]['code'],'nonstandard_header_clsid')
        self.assertEqual(report['header_clsid_raw_hex'],EXPECTED_CLSID.hex())
        self.assertFalse(report['biff_parsed']); self.assertFalse(report['native_et_ett_parsed'])
        for bad in (bytes([1])*16,bytes.fromhex('1008020000000000c000000000000046')):
            value=bytearray(data); value[8:24]=bad
            with self.assertRaisesRegex(ValueError,'CLSID'): CompoundFile(bytes(value),compatibility=MODE)
        with self.assertRaisesRegex(ValueError,'CLSID'): CompoundFile(legacy_container(major=4),compatibility=MODE)
        with self.assertRaisesRegex(ValueError,'root Workbook'): CompoundFile(legacy_container({'Other':b'x'}),compatibility=MODE)
        with self.assertRaisesRegex(ValueError,'Unknown'): CompoundFile(data,compatibility='anything')

    def test_compatibility_preserves_chain_reserved_length_and_budget_checks(self):
        original=legacy_container({'Workbook':b'x'*4096,'Other':b'y'*4096})
        first=struct.unpack_from('<I',original,1024+128+116)[0]
        mutations=[lambda d:struct.pack_into('<I',d,512+first*4,first),
                   lambda d:struct.pack_into('<I',d,1024+256+116,first),
                   lambda d:struct.pack_into('<I',d,512+first*4,999999),
                   lambda d:struct.pack_into('<Q',d,1024+128+120,5000),
                   lambda d:d.__setitem__(34,1)]
        for change in mutations:
            data=bytearray(original); change(data)
            with self.assertRaises(ValueError): CompoundFile(bytes(data),compatibility=MODE)
        with self.assertRaises(ValueError): CompoundFile(original[:-1],compatibility=MODE)
        with patch('xlsx_cfb.MAX_STREAM_BYTES',4):
            with self.assertRaisesRegex(ValueError,'8 MiB'): CompoundFile(original,compatibility=MODE)

    def test_mini_cycle_and_cross_link_still_fail_in_compatibility(self):
        original=legacy_container({'Workbook':b'x'*100,'Other':b'y'*64})
        mini=struct.unpack_from('<I',original,60)[0]
        for offset,value in (((mini+1)*512,0),(1024+256+116,0)):
            data=bytearray(original); struct.pack_into('<I',data,offset,value)
            with self.assertRaisesRegex(ValueError,'cycle|cross-link'): CompoundFile(bytes(data),compatibility=MODE)

    def test_known_metadata_retains_raw_payload_and_scope(self):
        marker=b'raw metadata not interpreted'
        global_records=b''.join(rec(kind,marker) for kind in LEGACY_GLOBAL_METADATA)
        sheet_records=b''.join(rec(kind,marker) for kind in LEGACY_SHEET_METADATA)
        data=workbook(sheet_records=[sheet_records],extra_global=global_records)
        with self.assertRaisesRegex(ValueError,'Unsupported global record'): parse_stream(data)
        result=parse_stream(data,compatibility=MODE)
        self.assertEqual(result['status'],'read_biff8_subset_with_diagnostics')
        raw=result['uninterpreted_metadata']
        self.assertEqual({x['name'] for x in raw},{*LEGACY_GLOBAL_METADATA.values(),*LEGACY_SHEET_METADATA.values()})
        self.assertTrue(all(x['payload_hex']==marker.hex() for x in raw))
        self.assertEqual({x['scope'] for x in raw},{'globals','中文表1'})
        self.assertFalse(result['formulas_executed']); self.assertFalse(result['native_et_ett_parsed'])
        with self.assertRaisesRegex(ValueError,'Unknown'): parse_stream(data,compatibility='anything')

    def test_unknown_encrypted_continued_and_data_extension_records_fail(self):
        for kind in (0xFFFF,0x002F,0x003C,0x04BC,0x0221,0x0017):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError): parse_stream(workbook(extra_global=rec(kind,b'xx')),compatibility=MODE)
        with patch('xlsx_biff8.MAX_METADATA_BYTES',1):
            with self.assertRaisesRegex(ValueError,'budget'): parse_stream(workbook(extra_global=rec(0xE1,b'xx')),compatibility=MODE)
        with self.assertRaisesRegex(ValueError,'truncated'): parse_stream(workbook()+b'\xff\xff\x10\x00x',compatibility=MODE)

    def test_existing_dates_and_caches_preserve_unverified_semantics(self):
        result=parse_stream(workbook(extra_global=rec(0xE1,b'xx')),compatibility=MODE)
        cells=result['sheets'][0]['cells']
        self.assertEqual(cells['C2']['value'],Decimal('60.5')); self.assertEqual(result['date_system'],'1900')
        self.assertEqual(cells['C2']['custom_format_raw'],'yyyy-mm-dd')
        self.assertEqual(cells['C4']['value'],'缓存中文'); self.assertEqual(cells['C4']['cache_status'],'unverified')
        self.assertEqual(cells['D4']['cache_status'],'error_unverified'); self.assertFalse(result['formulas_executed'])

    def test_read_file_cli_identify_and_original_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic-legacy.cfb'; data=legacy_container({'Workbook':workbook(extra_global=rec(0xE1,b'xx')),'ETExtData':b'opaque'})
            path.write_bytes(data)
            self.assertEqual(identify(path)['status'],'invalid_or_unsupported')
            self.assertEqual(identify(path,compatibility=MODE)['container_status'],'container_readable')
            result=read_file(path,compatibility=MODE)
            self.assertEqual(result['source']['container_issues'][0]['code'],'nonstandard_header_clsid')
            self.assertEqual(result['other_container_streams_not_decoded'][0]['path'],['ETExtData'])
            self.assertFalse(result['wps_compatibility_verified'])
            for script in ('xlsx_cfb.py','xlsx_identify.py','xlsx_biff8.py'):
                command=[sys.executable,'-I','-S','-B',str(Path(__file__).with_name(script)),str(path),'--compatibility',MODE]
                run=subprocess.run(command,capture_output=True,text=True,env={'PATH':''},timeout=10)
                self.assertEqual(run.returncode,0,run.stdout+run.stderr); self.assertFalse(json.loads(run.stdout)['native_et_ett_parsed'])
            self.assertEqual(path.read_bytes(),data); self.assertEqual(len(list(Path(folder).iterdir())),1)


if __name__=='__main__': unittest.main()
