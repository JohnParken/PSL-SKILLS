#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic interoperability regressions; no redistributed ET document bytes."""
from decimal import Decimal
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_wps_cfb import synthetic_cfb
from test_wps_biff8 import workbook,rec
from wps_cfb import CompoundFile,LEGACY_EXCEL_CLSID
from wps_biff8 import parse_stream,read_file,LEGACY_GLOBAL_METADATA,LEGACY_SHEET_METADATA

MODE='legacy_excel_clsid'
EXPECTED_CLSID=bytes.fromhex('2008020000000000c000000000000046')


def legacy_container(streams=None,major=3):
    data=bytearray(synthetic_cfb(streams,major))
    data[8:24]=EXPECTED_CLSID
    return bytes(data)


class LegacyCompatibilityTests(unittest.TestCase):
    def test_explicit_known_guid_is_a_diagnostic_and_no_format_claim(self):
        self.assertEqual(LEGACY_EXCEL_CLSID,EXPECTED_CLSID)
        data=legacy_container()
        book=CompoundFile(data)
        report=book.report()
        self.assertEqual(report['issues'][0]['code'],'nonstandard_header_clsid')
        self.assertEqual(report['header_clsid_raw_hex'],EXPECTED_CLSID.hex())
        self.assertFalse(report['biff_parsed'])
        self.assertFalse(report['native_et_ett_parsed'])
        for bad in (bytes([1])*16,bytes.fromhex('1008020000000000c000000000000046')):
            value=bytearray(data)
            value[8:24]=bad
            report=CompoundFile(bytes(value)).report()
            self.assertIn('nonstandard_header_clsid',
                          [item['code'] for item in report['issues']])
        self.assertEqual(CompoundFile(legacy_container(major=4)).report()['major_version'],4)
        self.assertEqual(CompoundFile(legacy_container({'Other':b'x'})).report()['workbook_stream_candidates'],[])
        # Compatibility values are accepted as a deprecated no-op.
        self.assertIsInstance(CompoundFile(data,compatibility='anything').report(),dict)

    def test_compatibility_preserves_chain_reserved_length_and_budget_checks(self):
        original=legacy_container({'Workbook':b'x'*4096,'Other':b'y'*4096})
        first=struct.unpack_from('<I',original,1024+128+116)[0]
        mutations=[lambda d:struct.pack_into('<I',d,512+first*4,first),
                   lambda d:struct.pack_into('<I',d,1024+256+116,first),
                   lambda d:struct.pack_into('<I',d,512+first*4,999999),
                   lambda d:struct.pack_into('<Q',d,1024+128+120,5000)]
        for change in mutations:
            data=bytearray(original)
            change(data)
            try:
                report=CompoundFile(bytes(data)).report()
            except ValueError:
                continue
            self.assertTrue(report['issues'])
        # Nonzero reserved header bytes are retained as a diagnostic.
        data=bytearray(original)
        data[34]=1
        report=CompoundFile(bytes(data)).report()
        self.assertIn('cfb_nonzero_reserved_header',
                      [item['code'] for item in report['issues']])
        with patch('wps_cfb.MAX_STREAM_BYTES',4):
            try:
                report=CompoundFile(original).report()
            except ValueError:
                pass
            else:
                self.assertTrue(report['issues'])

    def test_mini_cycle_and_cross_link_still_fail_in_compatibility(self):
        original=legacy_container({'Workbook':b'x'*100,'Other':b'y'*64})
        mini=struct.unpack_from('<I',original,60)[0]
        for offset,value in (((mini+1)*512,0),(1024+256+116,0)):
            data=bytearray(original)
            struct.pack_into('<I',data,offset,value)
            try:
                report=CompoundFile(bytes(data)).report()
            except ValueError as exc:
                self.assertRegex(str(exc),'cycle|cross-link|out of|allocation')
                continue
            self.assertTrue(report['issues'])

    def test_known_metadata_retains_raw_payload_and_scope(self):
        marker=b'raw metadata not interpreted'
        global_records=b''.join(rec(kind,marker) for kind in LEGACY_GLOBAL_METADATA)
        sheet_records=b''.join(rec(kind,struct.pack('<6H',0,1,100,0,0,0) if kind==0x7D else marker) for kind in LEGACY_SHEET_METADATA)
        data=workbook(sheet_records=[sheet_records],extra_global=global_records)
        for mode in (None,MODE,'strict'):
            with self.subTest(mode=mode):
                result=parse_stream(data,compatibility=mode)
                self.assertEqual(result['status'],'partial')
                raw=result['uninterpreted_metadata']
                names={x['name'] for x in raw}
                self.assertTrue({*LEGACY_GLOBAL_METADATA.values()}.issubset(names))
                self.assertTrue({v for k,v in LEGACY_SHEET_METADATA.items() if k!=0x7D}.issubset(names))
                self.assertEqual({x['scope'] for x in raw},{'globals','中文表1'})
                self.assertFalse(result['formulas_executed'])
                self.assertFalse(result['native_et_ett_parsed'])
        with self.assertRaisesRegex(ValueError,'Unknown'):
            parse_stream(data,compatibility='anything')

    def test_unknown_records_degrade_and_encryption_remains_hard(self):
        for kind in (0xFFFF,0x003C,0x04BC,0x0221,0x0017):
            with self.subTest(kind=kind):
                result=parse_stream(workbook(extra_global=rec(kind,b'xx')),compatibility=MODE)
                self.assertEqual(result['status'],'partial')
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'部门')
        with self.assertRaises(ValueError):
            parse_stream(workbook(extra_global=rec(0x002F,b'xx')),compatibility=MODE)
        with patch('wps_biff8.MAX_METADATA_BYTES',1):
            result=parse_stream(workbook(extra_global=rec(0xE1,b'xx')),compatibility=MODE)
            self.assertEqual(result['status'],'partial')
            self.assertIn('metadata_budget_truncated',
                          [item['code'] for item in result['issues']])
        # A short non-zero framing tail is retained, not misread as truncation.
        result=parse_stream(workbook()+b'\xff\xff\x10\x00x',compatibility=MODE)
        self.assertEqual(result['status'],'partial')
        self.assertTrue(any(code in ('biff_unframed_tail_retained',
                                     'unbound_trailing_bytes_retained')
                            for code in [item['code'] for item in result['issues']]))
    def test_existing_dates_and_caches_preserve_unverified_semantics(self):
        result=parse_stream(workbook(extra_global=rec(0xE1,b'xx')),compatibility=MODE)
        cells=result['sheets'][0]['cells']
        self.assertEqual(cells['C2']['value'],Decimal('60.5'))
        self.assertEqual(result['date_system'],'1900')
        self.assertEqual(cells['C2']['custom_format_raw'],'yyyy-mm-dd')
        self.assertEqual(cells['C4']['value'],'缓存中文')
        self.assertEqual(cells['C4']['cache_status'],'unverified')
        self.assertEqual(cells['D4']['cache_status'],'error_unverified')
        self.assertFalse(result['formulas_executed'])

    def test_read_file_cli_identify_and_original_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic-legacy.cfb'
            data=legacy_container({'Workbook':workbook(extra_global=rec(0xE1,b'xx')),'ETExtData':b'opaque'})
            path.write_bytes(data)
            result=read_file(path,compatibility=MODE)
            self.assertEqual(result['source']['container_issues'][0]['code'],'nonstandard_header_clsid')
            self.assertEqual(result['other_container_streams_not_decoded'][0]['path'],['ETExtData'])
            self.assertFalse(result['wps_compatibility_verified'])
            for script, expected_code in (('wps_cfb.py',0),('wps_biff8.py',3)):
                command=[sys.executable,'-I','-S','-B',str((Path(__file__).resolve().parents[1] / script)),str(path),'--compatibility',MODE]
                run=subprocess.run(command,capture_output=True,text=True,env={'PATH':''},timeout=10)
                self.assertEqual(run.returncode,expected_code,run.stdout+run.stderr)
                payload=json.loads(run.stdout)
                self.assertFalse(payload['native_et_ett_parsed'])
                if script == 'wps_biff8.py':
                    self.assertEqual(payload['status'],'partial')
            self.assertEqual(path.read_bytes(),data)
            self.assertEqual(len(list(Path(folder).iterdir())),1)


if __name__=='__main__':
    unittest.main()
