"""Tolerant BIFF8 raw-retention contract: provenance, mandatory safety, values.

The reader has a single tolerant policy, so unknown records, missing Date1904,
missing WsBool and unknown CodePage declarations degrade to ``partial`` with
bounded diagnostics instead of aborting. Synthetic MS-XLS only; never WPS Office
producer evidence.
"""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_biff8 import workbook, rec, cell
from test_wps_cfb import synthetic_cfb
from wps_biff8 import parse_stream, read_file, UnsupportedError, EncryptedError, ResourceLimitError


def values(result):
    """Stable cell semantics: physical offset changes are expected after inserts."""
    return [{pos: {key: val for key,val in item.items()
                   if key not in ('record_offset', 'cache_record_offset', 'numeric_interpretation')}
             for pos,item in sheet['cells'].items()}
            for sheet in result['sheets']]


def issue_codes(result):
    return [item.get('code') for item in result.get('issues', ())]


def remove_global_date_marker(data):
    # Fixture's first record after Globals BOF is Date1904.
    assert data[20:24] == rec(0x0022, b'\0\0')[:4]
    assert data[20:26] == rec(0x0022, b'\0\0')
    modified = bytearray(data[:20] + data[26:])
    pos = 20
    while pos < len(modified):
        kind,size = struct.unpack_from('<HH', modified, pos)
        if kind == 0x0085:
            offset = struct.unpack_from('<I', modified, pos+4)[0]
            struct.pack_into('<I', modified, pos+4, offset-6)
        if kind == 0x000A:
            break
        pos += 4 + size
    return bytes(modified)


def remove_worksheet_wsbool(data):
    # One-sheet only; removes worksheet marker, not the worksheet BOF.
    modified = bytearray(data)
    pos = 20
    while pos < len(data):
        kind,size=struct.unpack_from('<HH', modified, pos)
        if kind == 0x0085:
            at = struct.unpack_from('<I', modified, pos+4)[0]
            assert modified[at+20:at+26] == rec(0x0081,b'\0\0')
            del modified[at+20:at+26]
            return bytes(modified)
        if kind == 0x000A:
            break
        pos += 4+size
    raise AssertionError('Missing BoundSheet8')


class Biff8ToleranceTests(unittest.TestCase):
    def test_unknown_global_and_original_cells_are_all_retained(self):
        original = parse_stream(workbook())
        with_extra = workbook(extra_global=rec(0x7777, b'metadata-not-cell'))
        got = parse_stream(with_extra)
        self.assertEqual(values(got), values(original))
        self.assertEqual(got['status'], 'partial')
        self.assertEqual(got['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')
        self.assertTrue(got['omissions_affect_cell_values'])
        self.assertEqual(got['salvage_unknown_record_count'], 1)
        self.assertIn('unknown_biff_record_salvaged', issue_codes(got))
        record = next(x for x in got['uninterpreted_metadata'] if x['record_id']=='0x7777')
        self.assertEqual(record['payload_hex'], b'metadata-not-cell'.hex())
        self.assertTrue(record['may_affect_cell_values'])

    def test_tolerant_result_fields_and_requested_salvage_flag(self):
        original = parse_stream(workbook())
        self.assertEqual(original['status'], 'ok')
        self.assertEqual(original['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertNotIn('salvage_requested', original)
        optin = parse_stream(workbook(), salvage_unknown_metadata=True)
        self.assertTrue(optin['salvage_requested'])
        self.assertFalse(optin['saved_value_completeness_certified'])
        self.assertIn('salvage_option_ignored', issue_codes(optin))
        self.assertEqual(optin['salvage_unknown_record_count'], 0)

    def test_unknown_sheet_record_retained_and_later_cells_read(self):
        sheet=rec(0x7777, b'opaque')+rec(0x0203, cell(2,3)+struct.pack('<d', 2.5))
        raw=workbook(sheet_records=[sheet], strings=[])
        got=parse_stream(raw)
        self.assertEqual(got['sheets'][0]['cells']['D3']['value'], 2.5)
        self.assertTrue(got['omissions_affect_cell_values'])
        self.assertEqual(got['status'], 'partial')
        self.assertIn('unknown_biff_record_salvaged', issue_codes(got))

    def test_same_unknown_record_grouped_and_bounded(self):
        raw=workbook(extra_global=rec(0x7777,b'1')+rec(0x7777,b'2'))
        result=parse_stream(raw)
        self.assertEqual(result['salvage_unknown_record_count'],2)
        event=[x for x in result['issues'] if x['code']=='unknown_biff_record_salvaged']
        self.assertEqual(len(event),1)
        self.assertEqual(event[0]['count'],2)

    def test_unknown_codepage_kept_raw_without_recoding_text(self):
        codepage = 65001
        incoming = workbook(extra_global=rec(0x0042, struct.pack('<H', codepage)))
        got = parse_stream(incoming)
        before = parse_stream(workbook())
        self.assertEqual(values(got), values(before))
        self.assertEqual(got['code_page_raw'], codepage)
        self.assertEqual(got['status'], 'partial')
        self.assertIn('unknown_codepage_declaration', issue_codes(got))
        # Unicode text is decoded from each string's own flags, not the code page.
        self.assertEqual(got['sheets'][0]['cells']['A1']['value'], '部门')

    def test_missing_date1904_keeps_numbers_with_unknown_date_system(self):
        normal=workbook()
        without=remove_global_date_marker(normal)
        got=parse_stream(without)
        self.assertEqual(got['date_system'],'unknown')
        self.assertEqual(values(got),values(parse_stream(normal)))
        self.assertNotIn('numeric_interpretation', got['sheets'][0]['cells']['C2'])
        self.assertIn('date_system_missing', issue_codes(got))

    def test_missing_wsbool_keeps_cells_and_reports_partial(self):
        original=workbook(sheet_records=[rec(0x0203, cell(0,0)+struct.pack('<d', 6))],strings=[])
        without=remove_worksheet_wsbool(original)
        got=parse_stream(without)
        self.assertEqual(got['sheets'][0]['cells']['A1']['value'],6)
        self.assertIn('wsbool_missing', issue_codes(got))
        self.assertEqual(got['status'],'partial')

    def test_hard_safety_and_bounded_framing_still_rejected(self):
        # A stream that does not start with a BIFF8 BOF and an oversized
        # declared payload remain hard framing refusals.
        with self.assertRaises(ValueError):
            parse_stream(b'not BIFF')
        with self.assertRaises(ValueError):
            parse_stream(workbook(extra_global=struct.pack('<HH',0x7777,9000)+bytes(10)))
        # FILEPASS remains encryption, never decrypted.
        with self.assertRaises(EncryptedError):
            parse_stream(workbook(extra_global=rec(0x002F,b'\0\0')))
        # A structurally misplaced BOF is retained raw with a diagnostic.
        nested = parse_stream(workbook(extra_global=rec(0x0809,b'\0')))
        self.assertEqual(nested['status'],'partial')
        self.assertIn('unknown_biff_record_salvaged',issue_codes(nested))
        self.assertEqual(nested['sheets'][0]['cells']['A1']['value'],'部门')

    def test_metadata_budget_truncates_instead_of_aborting(self):
        raw=workbook(extra_global=rec(0x7777, bytes(50)))
        with patch('wps_biff8.MAX_METADATA_BYTES', 30):
            result=parse_stream(raw)
        self.assertEqual(result['status'],'partial')
        self.assertIn('metadata_budget_truncated', issue_codes(result))
        self.assertGreater(result['metadata_truncated_count'],0)
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'部门')
        with patch('wps_biff8.MAX_RECORDS', 10), self.assertRaises(ResourceLimitError):
            parse_stream(raw)

    def test_macro_storage_is_reported_but_not_loaded(self):
        raw = synthetic_cfb({'Workbook': workbook(), 'VBA': b'not-executed'})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'malicious.et'
            path.write_bytes(raw)
            result = read_file(path)
            self.assertEqual(result['status'], 'partial')
            self.assertIn('macro_storage_present_unloaded', issue_codes(result))
            self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
            self.assertFalse(result['formulas_executed'])
            self.assertEqual(path.read_bytes(), raw)

    def test_standalone_cli_default_partial_and_original_bytes_unchanged(self):
        raw=synthetic_cfb({'Workbook':workbook(extra_global=rec(0x7777,b'X'))})
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'example.et'
            p.write_bytes(raw)
            cmd=[sys.executable,'-I','-S','-B',str((Path(__file__).resolve().parents[1] / 'wps_biff8.py')),str(p)]
            result=subprocess.run(cmd+['--salvage-unknown-metadata'],text=True,capture_output=True,timeout=20)
            self.assertEqual(result.returncode,3,result.stdout+result.stderr)
            obj=json.loads(result.stdout)
            self.assertEqual(obj['status'],'partial')
            self.assertTrue(obj['omissions_affect_cell_values'])
            self.assertEqual(obj['salvage_unknown_record_count'],1)
            regular=subprocess.run(cmd,text=True,capture_output=True,timeout=20)
            self.assertEqual(regular.returncode,3,regular.stdout+regular.stderr)
            default = json.loads(regular.stdout)
            self.assertEqual(default['sheets'], obj['sheets'])
            self.assertTrue(default['omissions_affect_cell_values'])
            self.assertNotIn('salvage_requested', default)
            self.assertIn('salvage_option_ignored', [x['code'] for x in obj['issues']])
            entry = Path(__file__).resolve().parents[1] / 'et_cli.py'
            unified = subprocess.run([sys.executable, '-I', '-S', '-B', str(entry), 'run', str(p)],
                                     text=True, capture_output=True, timeout=20)
            self.assertEqual(unified.returncode, 3, unified.stdout + unified.stderr)
            self.assertEqual(json.loads(unified.stdout)['sheets'], default['sheets'])
            self.assertEqual(p.read_bytes(),raw)
            self.assertEqual([x.name for x in Path(folder).iterdir()],['example.et'])


if __name__ == '__main__':
    unittest.main()
