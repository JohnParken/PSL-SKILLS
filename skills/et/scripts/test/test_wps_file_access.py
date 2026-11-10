"""Cross-entry file safety and retained JSON contracts; synthetic fixtures only."""
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
from test_wps_biff8 import workbook, rec, formula, special
from test_wps_cfb import END, FREE, synthetic_cfb
from test_wps_legacy_compat import legacy_container

MODES = ('data', 'strict', 'legacy_excel_clsid')
SCRIPTS = ('read_wps.py', 'wps_biff8.py')


class FileAccessTests(unittest.TestCase):
    def test_zero_cfb_tail_only_after_full_allocation_checks(self):
        for major, sector_size in ((3, 512), (4, 4096)):
            with self.subTest(major=major):
                # The workbook is in the mini stream and Other uses regular sectors.
                raw = synthetic_cfb({'Workbook': workbook(), 'Other': b'x' * 4096}, major=major)
                expected = read_bytes(raw)
                padded = raw + bytes(sector_size - 1)
                result = read_bytes(padded)
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'], expected['sheets'])
                self.assertEqual(result['cell_value_coverage'], expected['cell_value_coverage'])
                issue = next(item for item in result['container_issues'] if item['code'] == 'cfb_zero_trailing_padding_ignored')
                self.assertEqual((issue['offset'], issue['padding_bytes']), (len(raw), sector_size - 1))
                for mode in ('strict', 'legacy_excel_clsid'):
                    with self.subTest(mode=mode), self.assertRaises(ParseError):
                        read_bytes(padded, compatibility=mode)
                with self.assertRaises(ParseError):
                    read_bytes(raw + b'\0x')
                # Remove a required sector and leave a zero sub-sector suffix.
                with self.assertRaises(ParseError):
                    read_bytes(raw[:-sector_size] + b'\0')
                directory_offset = 2 * sector_size
                corrupt = bytearray(padded)
                struct.pack_into('<Q', corrupt, directory_offset + 2 * 128 + 120, 4097)
                with self.assertRaises(ParseError):
                    read_bytes(bytes(corrupt))
                # Repoint the directory FAT entry to itself while keeping the suffix zero.
                cycle = bytearray(padded)
                struct.pack_into('<I', cycle, sector_size + 4, 1)
                with self.assertRaises(ParseError):
                    read_bytes(bytes(cycle))

    def cli(self, name, path, mode='data'):
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str((Path(__file__).resolve().parents[1] / name)),
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

    def test_legacy_minor_retains_cells_in_data_apis_and_clis(self):
        for builder in (synthetic_cfb, legacy_container):
            original = bytearray(builder({'Workbook': workbook()}))
            struct.pack_into('<H', original, 24, 0x003B)
            data = bytes(original)
            with tempfile.TemporaryDirectory(dir='.') as directory:
                path = Path(directory) / 'input.et'
                path.write_bytes(data)
                for result in (read(path), read_file(path), read_bytes(data)):
                    self.assertEqual(result['status'], 'partial')
                    self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
                    self.assertIn('legacy_cfb_minor_version',
                                  [issue['code'] for issue in result['issues']])
                for script in SCRIPTS:
                    process = self.cli(script, path)
                    self.assertEqual(process.returncode, 3, process.stderr)
                    report = json.loads(process.stdout)
                    self.assertEqual(report['sheets'][0]['cells']['A1']['value'], '部门')
                self.assertEqual(read(path, compatibility='strict')['status'], 'unsupported')
                self.assertEqual(path.read_bytes(), data)

    def test_zero_fat_count_uses_validated_difat_in_data_mode(self):
        for major in (3, 4):
            with self.subTest(major=major), tempfile.TemporaryDirectory(dir='.') as directory:
                original = synthetic_cfb({'Workbook': workbook()}, major=major)
                expected = read_bytes(original)
                self.assertEqual(expected['status'], 'ok')
                data = bytearray(original)
                struct.pack_into('<I', data, 44, 0)
                path = Path(directory) / 'input.et'
                path.write_bytes(data)
                for result in (read(path), read_file(path), read_bytes(bytes(data))):
                    self.assertEqual(result['status'], 'partial')
                    self.assertEqual(result['sheets'], expected['sheets'])
                    self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
                    issue, = result['container_issues']
                    self.assertEqual(issue['code'], 'cfb_zero_fat_count_recovered')
                    self.assertEqual((issue['declared_fat_count'], issue['inferred_fat_count']), (0, 1))
                for mode in ('strict', 'legacy_excel_clsid'):
                    failure = read(path, compatibility=mode)
                    self.assertEqual(failure['location']['reason_code'], 'cfb_zero_fat_count')
                    self.assertNotIn('sheets', failure)
                cli = Path(__file__).resolve().parents[1] / 'et_cli.py'
                process = subprocess.run([sys.executable, '-I', '-S', '-B', str(cli), 'run', str(path)],
                                         capture_output=True, text=True, encoding='utf-8', timeout=5)
                self.assertEqual(process.returncode, 3, process.stdout + process.stderr)
                self.assertEqual(json.loads(process.stdout)['status'], 'partial')
                self.assertEqual(path.read_bytes(), data)

    def test_zero_fat_count_requires_sound_allocation_and_supported_payload(self):
        original = bytearray(synthetic_cfb({'Workbook': workbook()}))
        struct.pack_into('<I', original, 44, 0)
        for offset, value in ((76, FREE), (80, 0), (76, 999999), (512, END), (516, 1)):
            with self.subTest(offset=offset, value=value):
                data = bytearray(original)
                struct.pack_into('<I', data, offset, value)
                self.rejected(bytes(data), 'invalid_or_unsupported')
                if value == FREE:
                    with self.assertRaises(ParseError) as caught:
                        read_bytes(bytes(data))
                    self.assertEqual(caught.exception.location['reason_code'], 'cfb_zero_fat_count')
        data = bytearray(synthetic_cfb({'Workbook': b'private payload'}))
        struct.pack_into('<I', data, 44, 0)
        self.rejected(bytes(data))

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

    def test_encrypted_biff_and_unhandled_cell_records_are_not_partial(self):
        for stream in (workbook(extra_global=rec(0x2F, b'x')),
                       workbook([rec(0x00D6, b'x')])):
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
