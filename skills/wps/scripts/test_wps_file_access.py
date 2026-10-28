"""Cross-entry file safety and retained JSON contracts; synthetic fixtures only."""
from decimal import Decimal
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import ParseError, read, read_bytes
from wps_biff8 import read_file
from test_wps_biff8 import workbook, rec, formula, special
from test_wps_cfb import synthetic_cfb
from test_wps_legacy_compat import legacy_container

MODES = ('data', 'strict', 'legacy_excel_clsid')
SCRIPTS = ('read_wps.py', 'wps_biff8.py')


class FileAccessTests(unittest.TestCase):
    def cli(self, name, path, mode='data'):
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name(name)),
             str(path), '--compatibility', mode],
            capture_output=True, text=True, encoding='utf-8', timeout=5,
        )

    def rejected(self, data, expected=None, cli=False):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'input.et'
            path.write_bytes(data)
            for mode in MODES:
                with self.subTest(mode=mode):
                    result = read(path, compatibility=mode)
                    self.assertNotIn(result['status'], ('ok', 'partial'))
                    if expected is not None:
                        self.assertEqual(result['status'], expected)
                    with self.assertRaises(ParseError):
                        read_bytes(data, compatibility=mode)
                    with self.assertRaises((ValueError, OSError)):
                        read_file(path, compatibility=mode)
                    if cli:
                        for script in SCRIPTS:
                            process = self.cli(script, path, mode)
                            self.assertEqual(process.returncode, 1, process.stderr)
                            report = json.loads(process.stdout)
                            self.assertNotIn(report['status'], ('ok', 'partial'))
                            self.assertNotIn('sheets', report)
            self.assertEqual(path.read_bytes(), data)

    def test_minor_zero_refused_by_all_file_apis_and_clis(self):
        data = bytearray(synthetic_cfb({'Workbook': workbook()}))
        struct.pack_into('<H', data, 24, 0)
        self.rejected(bytes(data), 'unsupported', cli=True)

    def test_truncated_reserved_and_bad_sector_headers_refused_everywhere(self):
        original = synthetic_cfb({'Workbook': workbook()})
        reserved = bytearray(original)
        reserved[34] = 1
        sectors = bytearray(original)
        struct.pack_into('<H', sectors, 30, 10)
        for data in (original[:-1], original[:20], bytes(reserved), bytes(sectors)):
            with self.subTest(length=len(data)):
                self.rejected(data)

    def test_legacy_profile_never_relaxes_minor_or_structural_checks(self):
        data = bytearray(legacy_container({'Workbook': workbook()}))
        struct.pack_into('<H', data, 24, 0)
        self.rejected(bytes(data), 'unsupported')
        data = bytearray(legacy_container({'Workbook': workbook()}))
        data[34] = 1
        self.rejected(bytes(data))

    def test_all_known_encryption_names_refused_before_workbook_decode(self):
        for name in ('EncryptedPackage', 'EncryptionInfo', 'EncryptedSummary', 'DRMContent'):
            with self.subTest(name=name):
                self.rejected(synthetic_cfb({'Workbook': workbook(), name: b'x'}), 'encrypted')

    def test_ambiguous_word_workbook_payload_has_no_branch(self):
        self.rejected(synthetic_cfb({'Workbook': workbook(), 'WordDocument': b'not interpreted'}),
                      'unsupported', cli=True)

    def test_encrypted_biff_and_unknown_data_are_not_partial(self):
        for stream in (workbook(extra_global=rec(0x2F, b'x')),
                       workbook(extra_global=rec(0x7777, b'x'))):
            self.rejected(synthetic_cfb({'Workbook': stream}))

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

    def test_data_acceptance_and_historical_strict_result_contract(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'input.ett'
            path.write_bytes(synthetic_cfb({'Workbook': workbook()}))
            strict = read_file(path, compatibility='strict')
            self.assertEqual(strict['status'], 'read_biff8_subset')
            self.assertEqual(strict['schema_version'], 'biff8_readonly_v1')
            self.assertIn(read(path, compatibility='strict')['status'], ('ok', 'partial'))
            path.write_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'raw'))}))
            data = read_file(path)
            self.assertEqual(data['status'], 'partial')
            self.assertTrue(data['issues'])
            self.assertEqual(data['reading_assessment']['outcome'], 'partial')
            self.assertEqual(read(path)['issues'], data['issues'])
            self.assertNotIn(read(path, compatibility='strict')['status'], ('ok', 'partial'))
            with self.assertRaises(ValueError):
                read_file(path, compatibility='strict')

    def test_partial_cli_keeps_precise_number_text_identifier_and_cache_evidence(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'input.et'
            path.write_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0xE1, b'raw'))}))
            for script in SCRIPTS:
                process = self.cli(script, path)
                self.assertIn(process.returncode, (0, 3), process.stderr)
                report = json.loads(process.stdout)
                self.assertEqual((process.returncode, report['status']), (3, 'partial'))
                self.assertEqual(report['schema_version'], 'biff8_readonly_v1')
                self.assertTrue(report['issues'])
                cells = report['sheets'][0]['cells']
                self.assertEqual(cells['A2']['type'], 'text')
                self.assertEqual(cells['A2']['value'], '00012345678901234567890')
                self.assertEqual(cells['B2']['type'], 'number')
                self.assertEqual(cells['B2']['value'], str(Decimal.from_float(.1)))
                self.assertTrue(cells['B4']['formula']['cache_may_be_stale'])
                self.assertEqual(cells['C4']['cache_status'], 'unverified')
                self.assertEqual(report['cell_value_coverage'], 'complete_within_declared_sheets')

    def test_missing_cache_stays_null_and_gaps_not_complete(self):
        data = synthetic_cfb({'Workbook': workbook([formula(0, 0, special(0))], strings=[])})
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'input.et'
            path.write_bytes(data)
            for result in (read(path), read_file(path)):
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['cell_value_coverage'], 'missing_formula_caches')
                self.assertTrue(result['omissions_affect_cell_values'])
                self.assertIsNone(result['sheets'][0]['cells']['A1']['value'])
                self.assertEqual(result['sheets'][0]['cells']['A1']['cache_status'], 'missing')

    def test_invalid_chart_policy_not_ignored_by_file_apis(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path = Path(directory) / 'input.et'
            path.write_bytes(synthetic_cfb({'Workbook': workbook()}))
            for mode in MODES:
                self.assertNotIn(read(path, compatibility=mode, chart_policy='bad')['status'], ('ok', 'partial'))
                with self.assertRaises(ValueError):
                    read_file(path, compatibility=mode, chart_policy='bad')


if __name__ == '__main__':
    unittest.main()
