"""Aggressive opt-in BIFF8 salvage: data provenance, mandatory safety and refusals.

Synthetic MS-XLS only, never WPS Office producer evidence.
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


class Biff8SalvageTests(unittest.TestCase):
    def test_optin_global_unknown_and_original_cells(self):
        original = parse_stream(workbook(), compatibility='data')
        with_extra = workbook(extra_global=rec(0x7777, b'metadata-not-cell'))
        with self.assertRaises(UnsupportedError):
            parse_stream(with_extra, compatibility='data')
        got = parse_stream(with_extra, compatibility='data', salvage_unknown_metadata=True)
        self.assertEqual(values(got), values(original))
        self.assertEqual(got['status'], 'partial')
        self.assertEqual(got['cell_value_coverage'], 'unverified_due_to_best_effort_salvage')
        self.assertTrue(got['omissions_affect_cell_values'])
        self.assertFalse(got['saved_value_completeness_certified'])
        self.assertEqual(got['salvage_unknown_record_count'], 1)
        self.assertEqual(got['issues'][0]['code'], 'unknown_biff_record_salvaged')
        record = next(x for x in got['uninterpreted_metadata'] if x['record_id']=='0x7777')
        self.assertEqual(record['payload_hex'], b'metadata-not-cell'.hex())
        self.assertTrue(record['may_affect_cell_values'])

    def test_default_json_excludes_salvage_fields(self):
        original = parse_stream(workbook(), compatibility='data')
        for name in ('salvage_requested', 'salvage_unknown_record_count',
                     'saved_value_completeness_certified'):
            self.assertNotIn(name, original)
        self.assertEqual(original['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertIn('Fail closed', original['policies']['unknown_records'])
        optin = parse_stream(workbook(), compatibility='data',
                             salvage_unknown_metadata=True)
        self.assertTrue(optin['salvage_requested'])
        self.assertFalse(optin['saved_value_completeness_certified'])
        self.assertEqual(optin['salvage_unknown_record_count'], 0)

    def test_optin_sheet_unknown_and_original_cells(self):
        sheet=rec(0x7777, b'opaque')+rec(0x0203, cell(2,3)+struct.pack('<d', 2.5))
        raw=workbook(sheet_records=[sheet], strings=[])
        with self.assertRaises(UnsupportedError):
            parse_stream(raw, compatibility='data')
        got=parse_stream(raw, compatibility='data', salvage_unknown_metadata=True)
        self.assertEqual(got['sheets'][0]['cells']['D3']['value'], 2.5)
        self.assertTrue(got['omissions_affect_cell_values'])
        self.assertEqual(got['status'], 'partial')

    def test_same_unknown_record_grouped_and_bounded(self):
        raw=workbook(extra_global=rec(0x7777,b'1')+rec(0x7777,b'2'))
        result=parse_stream(raw,compatibility='data',salvage_unknown_metadata=True)
        self.assertEqual(result['salvage_unknown_record_count'],2)
        event=[x for x in result['issues'] if x['code']=='unknown_biff_record_salvaged']
        self.assertEqual(len(event),1)
        self.assertEqual(event[0]['count'],2)

    def test_unknown_codepage_declaration_salvaged_without_recoding_text(self):
        # The CodePage is only raw metadata in BIFF8: no legacy decoder.
        codepage = 65001
        incoming = workbook(extra_global=rec(0x0042, struct.pack('<H', codepage)))
        with self.assertRaises(ValueError):
            parse_stream(incoming, compatibility='data')
        got = parse_stream(incoming, compatibility='data',
                           salvage_unknown_metadata=True)
        before = parse_stream(workbook(), compatibility='data')
        self.assertEqual(values(got), values(before))
        self.assertEqual(got['code_page_raw'], codepage)
        self.assertEqual(got['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')
        self.assertTrue(any(x['code']=='unknown_codepage_decl_salvaged'
                            for x in got['issues']))

    def test_missing_mandatory_date_only_optin_and_no_fake_date(self):
        normal=workbook()
        without=remove_global_date_marker(normal)
        with self.assertRaises(ValueError):
            parse_stream(without, compatibility='data')
        got=parse_stream(without,compatibility='data',salvage_unknown_metadata=True)
        self.assertEqual(got['date_system'],'unknown')
        self.assertTrue(got['omissions_affect_cell_values'])
        self.assertEqual(values(got),values(parse_stream(normal,compatibility='data')))
        self.assertNotIn('numeric_interpretation', got['sheets'][0]['cells']['C2'])
        self.assertTrue(any(x['code']=='date_system_missing_salvage' for x in got['issues']))

    def test_missing_wsbool_only_optin(self):
        original=workbook(sheet_records=[rec(0x0203, cell(0,0)+struct.pack('<d', 6))],strings=[])
        without=remove_worksheet_wsbool(original)
        with self.assertRaises(ValueError):
            parse_stream(without,compatibility='data')
        got=parse_stream(without,compatibility='data',salvage_unknown_metadata=True)
        self.assertEqual(got['sheets'][0]['cells']['A1']['value'],6)
        self.assertTrue(any(x['code']=='wsbool_missing_salvage' for x in got['issues']))
        self.assertEqual(got['status'],'partial')

    def test_core_security_and_framing_still_rejected(self):
        for kind,payload in [(0x002f,b'\0\0'),(0x003c,b'\0'),(0x0809,b'\0\0'),
                             (0x04bc,b'\0'),(0x0203,b'\0'),(0x0207,b'A')]:
            with self.subTest(kind=hex(kind)),self.assertRaises(ValueError):
                parse_stream(workbook(extra_global=rec(kind,payload)),compatibility='data',salvage_unknown_metadata=True)
        raw=workbook(extra_global=rec(0x7777,b'A'))
        with self.assertRaises(ValueError):
            parse_stream(raw[:-1],compatibility='data',salvage_unknown_metadata=True)
        with self.assertRaises(ValueError):
            parse_stream(raw,compatibility='strict',salvage_unknown_metadata=True)

    def test_source_metadata_budget_not_bypassed(self):
        import unittest.mock
        raw=workbook(extra_global=rec(0x7777, bytes(50)))
        with unittest.mock.patch('wps_biff8.MAX_METADATA_BYTES', 30):
            with self.assertRaises(ResourceLimitError):
                parse_stream(raw,compatibility='data',salvage_unknown_metadata=True)

    def test_macro_storage_still_rejected_by_salvage_cli(self):
        raw = synthetic_cfb({'Workbook': workbook(), 'VBA': b'not-executed'})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'malicious.et'
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'Macro'):
                read_file(path, compatibility='data',
                          salvage_unknown_metadata=True)
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
            entry = Path(__file__).resolve().parents[1] / 'et_cli.py'
            unified = subprocess.run([sys.executable, '-I', '-S', '-B', str(entry), 'run', str(p)],
                                     text=True, capture_output=True, timeout=20)
            self.assertEqual(unified.returncode, 3, unified.stdout + unified.stderr)
            self.assertEqual(json.loads(unified.stdout)['sheets'], default['sheets'])
            self.assertEqual(p.read_bytes(),raw)
            self.assertEqual([x.name for x in Path(folder).iterdir()],['example.et'])


if __name__ == '__main__':
    unittest.main()
