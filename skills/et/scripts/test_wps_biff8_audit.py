"""Synthetic, stdlib-only audit tests; not native WPS producer acceptance."""
import copy
import json
import struct
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_biff8 import workbook, parse_stream, formula, special, rec, cell
from test_wps_rich_strings import rich, rich_workbook
from test_wps_cfb import synthetic_cfb
from wps_biff8_audit import audit_biff8, main


class Biff8AuditTests(unittest.TestCase):
    def test_default_reader_output_not_modified(self):
        parsed = parse_stream(workbook(), compatibility='data')
        before = copy.deepcopy(parsed)
        audit = audit_biff8(parsed)
        self.assertEqual(parsed, before)
        self.assertEqual(audit['schema_version'], 'biff8_stored_evidence_audit_v1')
        self.assertFalse(audit['full_format_verified'])
        self.assertFalse(audit['effective_styles_verified'])
        self.assertEqual(audit['source_read_status'], parsed['status'])

    def test_rich_utf16_segments_with_supplementary_character(self):
        parsed = parse_stream(rich_workbook(rich('A😀中', [(0, 0), (3, 1)])))
        report = audit_biff8(parsed)
        entry = report['rich_format_runs'][0]
        self.assertEqual(entry['sst_index'], 0)
        self.assertEqual(entry['status'], 'direct_run_locations_only')
        self.assertEqual(entry['spans'], [
            {'cp_start': 0, 'cp_end': 3, 'font_index_raw': 0},
            {'cp_start': 3, 'cp_end': 4, 'font_index_raw': 1},
        ])
        self.assertEqual(entry['effective_font'], 'unknown')

    def test_unattributed_prefix_and_terminal_sentinel(self):
        parsed = parse_stream(rich_workbook(rich('abcd', [(1, 1), (4, 65535)])))
        entry = audit_biff8(parsed)['rich_format_runs'][0]
        self.assertEqual(entry['unattributed_prefix_utf16_units'], 1)
        self.assertEqual(entry['spans'], [
            {'cp_start': 1, 'cp_end': 4, 'font_index_raw': 1},
        ])

    def test_split_surrogate_run_is_never_exposed_as_styled(self):
        parsed = parse_stream(rich_workbook(rich('A😀B', [(0, 0), (2, 1)])))
        entry = audit_biff8(parsed)['rich_format_runs'][0]
        self.assertEqual(entry['status'], 'run_splits_unicode_scalar')
        self.assertEqual(entry['spans'], [])

    def test_unreferenced_sst_has_no_invented_text_witness(self):
        parsed = parse_stream(rich_workbook(rich('abcd', [(0, 0), (1, 1)])))
        parsed['sheets'][0]['cells'].clear()
        entry = audit_biff8(parsed)['rich_format_runs'][0]
        self.assertEqual(entry['status'], 'unreferenced_string_text_unavailable')
        self.assertEqual(entry['spans'], [])

    def test_formula_cache_summary_preserves_stale_missing_error(self):
        parsed = parse_stream(workbook())
        report = audit_biff8(parsed)['formula_caches']
        self.assertEqual(report['total_formulas'], 5)
        counts = report['cache_status_counts']
        self.assertEqual(counts['unverified'], 3)
        self.assertEqual(counts['error_unverified'], 1)
        self.assertEqual(counts['unverified_empty_string'], 1)
        self.assertTrue(report['all_caches_unverified'])
        self.assertEqual(report['saved_cache_is_current'], 'unknown')
        self.assertFalse(report['cache_values_recalculated'])
        missing = parse_stream(workbook(sheet_records=[formula(0, 0, special(0))], strings=[]))
        other = audit_biff8(missing)['formula_caches']
        self.assertEqual(other['cache_status_counts'], {'missing': 1})
        self.assertEqual(other['sheets'][0]['possibly_stale'], 1)

    def test_xf_metadata_is_raw_never_effective_formatting(self):
        parsed = parse_stream(workbook())
        sources = audit_biff8(parsed)['xf_sources']
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[1]['number_format_id_raw'], 164)
        self.assertEqual(sources[1]['font_index_raw'], 0)
        self.assertEqual(sources[1]['effective_formatting'], 'unknown')
        self.assertFalse(sources[1]['style_inheritance_resolved'])

    def test_reject_word_or_ooxml_json_and_oversize_without_rewrite(self):
        for bad in ({'schema_version': 'ooxml_spreadsheet_readonly_v1'},
                    {'schema_version': 'ms_doc_subset'},
                    {}):
            with self.assertRaises(ValueError):
                audit_biff8(bad)
        parsed = parse_stream(workbook())
        with patch('wps_biff8_audit.MAX_AUDIT_XFS', 1), self.assertRaises(ValueError):
            audit_biff8(parsed)

    def test_rich_font_links_observe_font_index_four_gap(self):
        parsed = parse_stream(rich_workbook(rich('AB', [(0, 5)]), font_count=5))
        audit = audit_biff8(parsed)
        self.assertEqual([x['font_index_raw'] for x in audit['font_sources']],
                         [0, 1, 2, 3, 5])
        last = audit['font_sources'][-1]
        self.assertEqual(last['status'], 'direct_font_record_fields')
        self.assertEqual(last['name_raw'], 'A')
        self.assertEqual(last['height_twips_raw'], 0)
        ref = audit['rich_font_references'][0]['font_references'][0]
        self.assertEqual(ref['font_index_raw'], 5)
        self.assertEqual(ref['font_record_index'], 4)
        self.assertEqual(ref['status'], 'direct_font_record_reference')
        self.assertFalse(audit['effective_styles_verified'])

    def test_unreadable_font_does_not_invent_attributes(self):
        parsed = parse_stream(rich_workbook(rich('AB', [(0, 0)])))
        original = copy.deepcopy(parsed)
        for item in parsed['uninterpreted_metadata']:
            if item['record_id'] == '0x31':
                item['payload_hex'] = '0a'
                break
        audit = audit_biff8(parsed)
        self.assertEqual(audit['font_sources'][0]['status'],
                         'uninterpreted_short_font_record')
        ref = audit['rich_font_references'][0]['font_references'][0]
        self.assertEqual(ref['status'], 'font_details_unresolved')
        self.assertEqual(original['sheets'], parsed['sheets'])

    def test_calc_mode_recalc_save_and_engine_are_source_flags_only(self):
        from test_wps_biff8 import rec
        metadata = rec(0x01c1, struct.pack('<HHI', 0x01c1, 0, 123456))
        sheet = (rec(0x000d, struct.pack('<h', 1)) +
                 rec(0x005f, struct.pack('<H', 1)) +
                 formula(0, 0, struct.pack('<d', 42)))
        parsed = parse_stream(workbook(sheet_records=[sheet], strings=[],
                                       extra_global=metadata), compatibility='data')
        audit = audit_biff8(parsed)
        records = {x['record_id']: x for x in audit['calculation_metadata']['records']}
        self.assertEqual(records['0xd']['calculation_mode'], 'automatic')
        self.assertEqual(records['0x5f']['saved_recalculate_before_save'], True)
        self.assertEqual(records['0x1c1']['engine_build_raw'], 123456)
        self.assertEqual(audit['calculation_metadata']['saved_caches_current'], 'unknown')
        self.assertFalse(audit['calculation_metadata']['formulas_recalculated'])
        self.assertEqual(audit['formula_caches']['total_formulas'], 1)

    def test_invalid_calculation_record_is_uninterpreted_not_executed(self):
        parsed = parse_stream(workbook())
        parsed['uninterpreted_metadata'].append({
            'scope': '中文表1', 'record_id': '0xd', 'offset': 50,
            'payload_hex': 'ff'})
        item = audit_biff8(parsed)['calculation_metadata']['records'][0]
        self.assertEqual(item['status'], 'uninterpreted')
        self.assertFalse(item['recalculation_executed'])

    def test_observed_xf_parent_only_never_applied(self):
        parsed = parse_stream(workbook())
        first = bytearray.fromhex(parsed['styles'][0]['raw_hex'])
        second = bytearray.fromhex(parsed['styles'][1]['raw_hex'])
        struct.pack_into('<H', first, 4, 0xfff4)  # style XF with no parent
        struct.pack_into('<H', second, 4, 0)  # cell XF referencing index zero
        parsed['styles'][0]['raw_hex'] = first.hex()
        parsed['styles'][1]['raw_hex'] = second.hex()
        audit = audit_biff8(parsed)
        self.assertEqual(audit['xf_ancestry'][0]['parent_reference_status'],
                         'style_xf_has_no_parent')
        self.assertEqual(audit['xf_ancestry'][1]['parent_reference_status'],
                         'observed_parent_style_xf_only')
        self.assertEqual(audit['xf_ancestry'][1]['parent_xf_index_raw'], 0)
        self.assertFalse(audit['xf_ancestry'][1]['style_inheritance_applied'])
        self.assertEqual(audit['xf_ancestry'][1]['effective_style'], 'unknown')

    def test_cli_synthetic_cfb_readonly_preserves_exit_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'synthetic.et'
            original = synthetic_cfb({'Workbook': workbook()})
            path.write_bytes(original)
            result = subprocess.run(
                [sys.executable, '-I', '-S', '-B',
                 str(Path(__file__).with_name('wps_biff8_audit.py')), str(path)],
                capture_output=True, text=True, timeout=20)
            self.assertIn(result.returncode, (0, 3), result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['schema_version'], 'biff8_stored_evidence_audit_v1')
            self.assertEqual(data['formula_caches']['total_formulas'], 5)
            self.assertFalse(data['full_format_verified'])
            self.assertEqual(result.returncode, 3 if data['source_read_status'] == 'partial' else 0)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual([x.name for x in Path(folder).iterdir()], ['synthetic.et'])

    def test_cli_fail_preserves_source_status_without_word_routing(self):
        import contextlib
        import io
        with patch('read_wps.read', return_value={'status': 'unsupported', 'location': {'stage': 'container'}}):
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                rc = main(['fixture.et'])
        self.assertEqual(rc, 1)
        self.assertIn('"source_status": "unsupported"', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
