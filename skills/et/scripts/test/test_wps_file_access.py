#!/usr/bin/env python3
"""Cross-entry content-first contracts: tolerant containers and retained JSON."""
from decimal import Decimal
import json
import os
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_wps import ParseError, read, read_bytes
from wps_biff8 import read_file
from test_wps_biff8 import workbook, rec
from test_wps_cfb import synthetic_cfb
from test_wps_legacy_compat import legacy_container

MODES = ('data', 'strict', 'legacy_excel_clsid')
SCRIPTS = ('read_wps.py', 'wps_biff8.py')


class FileAccessTests(unittest.TestCase):
    def cli(self, name, path, mode='data'):
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str((Path(__file__).resolve().parents[1] / name)),
             str(path), '--compatibility', mode],
            capture_output=True, text=True, encoding='utf-8', timeout=10,
        )

    def assert_tolerant(self, data, expected_codes, status='partial'):
        for mode in MODES:
            with self.subTest(mode=mode):
                result = read_bytes(data, compatibility=mode)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
                codes = [issue['code'] for issue in result['issues']]
                for code in expected_codes:
                    self.assertIn(code, codes)

    def test_cfb_padding_tail_and_header_variants_keep_cells(self):
        for major, sector_size in ((3, 512), (4, 4096)):
            with self.subTest(major=major):
                raw = synthetic_cfb({'Workbook': workbook()}, major=major)
                expected = read_bytes(raw)
                self.assertEqual(expected['status'], 'ok')
                for tail, code in ((bytes(sector_size - 1), 'cfb_zero_trailing_padding_ignored'),
                                   (b'\x00\x01\x02', 'cfb_trailing_remainder_ignored')):
                    padded = raw + tail
                    result = read_bytes(padded)
                    self.assertEqual(result['status'], 'partial')
                    self.assertEqual(result['sheets'], expected['sheets'])
                    self.assertIn(code, [issue['code'] for issue in result['container_issues']])
                minor = bytearray(raw)
                struct.pack_into('<H', minor, 24, 0x003B)
                self.assert_tolerant(bytes(minor), ['cfb_nonstandard_minor_version'])
                clsid = bytearray(raw)
                clsid[8:24] = bytes.fromhex('0102030405060708090a0b0c0d0e0f10')
                self.assert_tolerant(bytes(clsid), ['nonstandard_header_clsid'])
                reserved = bytearray(raw)
                reserved[34] = 1
                self.assert_tolerant(bytes(reserved), ['cfb_nonzero_reserved_header'])
                with tempfile.TemporaryDirectory(dir='.') as directory:
                    path = Path(directory) / 'input.et'
                    path.write_bytes(bytes(clsid))
                    for mode in MODES:
                        self.assertEqual(read(path, compatibility=mode)['status'], 'partial')
                    process = self.cli('read_wps.py', path, 'strict')
                    self.assertEqual(process.returncode, 3, process.stderr)
                    self.assertEqual(json.loads(process.stdout)['status'], 'partial')

    def test_hard_container_and_security_failures_remain_closed(self):
        original = synthetic_cfb({'Workbook': workbook()})
        for data in (b'unknown', b'', original[:20], b'bad' + original[3:]):
            with self.subTest(data=data[:12]), tempfile.TemporaryDirectory(dir='.') as directory:
                path = Path(directory) / 'input.et'
                path.write_bytes(data)
                result = read(path)
                self.assertNotIn(result['status'], ('ok', 'partial'))
                with self.assertRaises((ValueError, OSError)):
                    read_file(path)
        for offset, value in ((44, 513), (72, 17)):
            data = bytearray(original)
            struct.pack_into('<I', data, offset, value)
            with self.assertRaises(ParseError):
                read_bytes(bytes(data))
        bad_shift = bytearray(original)
        struct.pack_into('<H', bad_shift, 30, 10)
        with self.assertRaises(ParseError):
            read_bytes(bytes(bad_shift))
        for name in ('EncryptedPackage', 'EncryptionInfo', 'EncryptedSummary', 'DRMContent'):
            with self.subTest(name=name), self.assertRaises(ParseError):
                read_bytes(synthetic_cfb({'Workbook': workbook(), name: b'x'}))
        filepass = synthetic_cfb({'Workbook': workbook(extra_global=rec(0x2F, b'x'))})
        with self.assertRaises(ParseError):
            read_bytes(filepass)

    def test_zero_fat_count_uses_validated_difat(self):
        for major in (3, 4):
            with self.subTest(major=major), tempfile.TemporaryDirectory(dir='.') as directory:
                original = synthetic_cfb({'Workbook': workbook()}, major=major)
                expected = read_bytes(original)
                data = bytearray(original)
                struct.pack_into('<I', data, 44, 0)
                path = Path(directory) / 'input.et'
                path.write_bytes(data)
                for result in (read(path), read_file(path), read_bytes(bytes(data))):
                    self.assertEqual(result['status'], 'partial')
                    self.assertEqual(result['sheets'], expected['sheets'])
                    self.assertIn('cfb_zero_fat_count_recovered',
                                  [issue['code'] for issue in result['container_issues']])
                process = self.cli('read_wps.py', path)
                self.assertEqual(process.returncode, 3, process.stderr)
                self.assertEqual(path.read_bytes(), bytes(data))

    def test_ambiguous_payload_and_macro_storage_are_read_with_diagnostics(self):
        ambiguous = synthetic_cfb({'Workbook': workbook(), 'WordDocument': b'not interpreted'})
        self.assert_tolerant(ambiguous, ['ambiguous_document_and_workbook_payloads'])
        macro = synthetic_cfb({'Workbook': workbook(), 'VBA': b'not a project'})
        self.assert_tolerant(macro, ['macro_storage_present_unloaded'])
        self.assertEqual(read_bytes(macro)['status'], 'partial')

    def test_malformed_cell_record_is_an_omission_not_a_whole_failure(self):
        raw = synthetic_cfb({'Workbook': workbook([rec(0x0203)])})
        result = read_bytes(raw)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['omissions_affect_cell_values'])
        self.assertIn('cell_value_record_omitted', [issue['code'] for issue in result['issues']])

    def test_compatibility_options_are_accepted_as_noops(self):
        raw = synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'raw'))})
        expected = read_bytes(raw)
        self.assertEqual(expected['status'], 'partial')
        for mode in MODES:
            result = read_bytes(raw, compatibility=mode)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['sheets'], expected['sheets'])
        strict = read_bytes(raw, compatibility='strict')
        self.assertIn('compatibility_option_ignored', [issue['code'] for issue in strict['issues']])
        salvage = read_bytes(raw, salvage_unknown_metadata=True)
        self.assertIn('salvage_option_ignored', [issue['code'] for issue in salvage['issues']])

    def test_legacy_container_and_cli_retain_cells(self):
        for builder in (synthetic_cfb, legacy_container):
            data = builder({'Workbook': workbook()})
            with tempfile.TemporaryDirectory(dir='.') as directory:
                path = Path(directory) / 'input.et'
                path.write_bytes(data)
                for result in (read(path), read_file(path), read_bytes(data)):
                    self.assertIn(result['status'], ('ok', 'partial'))
                    self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
                for mode in MODES:
                    process = self.cli('read_wps.py', path, mode)
                    self.assertIn(process.returncode, (0, 3), process.stderr)
                    report = json.loads(process.stdout)
                    self.assertIn(report['status'], ('ok', 'partial'))
                    self.assertEqual(report['sheets'][0]['cells']['A1']['value'], '部门')
                self.assertEqual(path.read_bytes(), data)

    def test_resource_limit_precedes_read_for_both_file_apis(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'oversized.et'
            with path.open('wb') as handle:
                handle.truncate(32 * 1024 * 1024 + 1)
            original_fdopen = os.fdopen

            class NoDataRead:
                def __init__(self, *args, **kwargs):
                    self.handle = original_fdopen(*args, **kwargs)

                def __enter__(self):
                    self.handle.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.handle.__exit__(*args)

                def fileno(self):
                    return self.handle.fileno()

                def read(self, *args):
                    raise AssertionError('oversized input must fail before data allocation')

            for mode in MODES:
                with self.subTest(mode=mode), patch('wps_access.os.fdopen', NoDataRead):
                    self.assertEqual(read(path, compatibility=mode)['status'], 'resource_limit')
                    with self.assertRaises(ValueError) as caught:
                        read_file(path, compatibility=mode)
                    self.assertEqual(getattr(caught.exception, 'status', None), 'resource_limit')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'requires FIFO support')
    def test_nonregular_input_cannot_block_either_cli(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'input.et'
            os.mkfifo(path)
            for script in SCRIPTS:
                process = self.cli(script, path)
                self.assertEqual(process.returncode, 1, process.stderr)
                self.assertEqual(json.loads(process.stdout)['status'], 'unsupported')

    def test_partial_cli_keeps_precise_text_identifier_and_cache_evidence(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'input.et'
            path.write_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'raw'))}))
            for script in SCRIPTS:
                process = self.cli(script, path)
                self.assertEqual(process.returncode, 3, process.stderr)
                report = json.loads(process.stdout)
                self.assertEqual(report['status'], 'partial')
                self.assertEqual(report['schema_version'], 'biff8_readonly_v1')
                cells = report['sheets'][0]['cells']
                self.assertEqual(cells['A1']['value'], '部门')
                self.assertEqual(cells['A2']['value'], '00012345678901234567890')
                self.assertTrue(cells['B2']['value'].startswith('0.100000'))


if __name__ == '__main__':
    unittest.main()
