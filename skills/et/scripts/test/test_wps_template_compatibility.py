"""Synthetic boundaries for the real ETT static-cell/page-break reading gaps."""
import hashlib
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
from read_et import read_bytes
from test_wps_biff8 import workbook, rec, cell, formula, special, string
from test_wps_biff8_names import name_payload, named_workbook, self_extern_sheet
from test_wps_biff8_salvage import values
from test_wps_biff_reading import page_breaks
from test_wps_cfb import synthetic_cfb
from wps_biff8 import parse_stream, ResourceLimitError
from wps_biff8_names import classify_static_name, classify_static_builtin_name


def cell_reference(row=4, column=3, index=0, token=0x3A):
    return bytes((token,)) + struct.pack('<3H', index, row, column)


def template_workbook(*, extra_sheet=b'', extra_global=b''):
    cells = (rec(0xFD, cell(0, 0) + struct.pack('<I', 0)) +
             rec(0x203, cell(1, 0) + struct.pack('<d', 12.5)) +
             formula(2, 0, struct.pack('<d', 25.0)) +
             formula(3, 0, special(0)) + rec(0x207, string('缓存')))
    return workbook([cells + extra_sheet], strings=['00123'], extra_global=extra_global)


class StaticCellNameTests(unittest.TestCase):
    def test_absolute_cell_operand_classes_grid_edges_and_scope(self):
        for token in (0x3A, 0x5A, 0x7A):
            for row, column in ((0, 0), (4, 3), (65535, 255)):
                for scope in (0, 1):
                    with self.subTest(token=token, row=row, column=column, scope=scope):
                        payload = name_payload('Input', sheet=scope, formula=cell_reference(row, column, token=token))
                        item = classify_static_name(payload, 1, [(0, 0)])
                        self.assertEqual(item['classification'], 'ordinary_static_internal_cell_only')
                        self.assertEqual(item['bounds_zero_based'], [row, row, column, column])
                        self.assertEqual(item['regions_zero_based'], [[row, row, column, column]])
                        self.assertEqual(item['formula_token_raw'], hex(token))
                        self.assertFalse(item['formula_executed'])
                        self.assertFalse(item['effective_name_binding_applied'])
                        self.assertFalse(item['external_link_followed'])

    def test_default_reader_preserves_cells_and_exact_source(self):
        payload = name_payload('Input', sheet=0, formula=cell_reference())
        raw = synthetic_cfb({'Workbook': named_workbook(payload)})
        before = hashlib.sha256(raw).hexdigest()
        parsed = read_bytes(raw)
        base = read_bytes(synthetic_cfb({'Workbook': workbook()}))
        self.assertEqual(values(parsed), values(base))
        self.assertEqual(parsed['status'], 'partial')
        self.assertFalse(parsed['reading_assessment']['full_format_verified'])
        source = next(item for item in parsed['uninterpreted_metadata'] if item['record_id'] == '0x18')
        self.assertEqual(source['name'], 'OrdinaryStaticInternalCellName')
        self.assertEqual(source['payload_hex'], payload.hex())
        self.assertEqual(source['structure']['bounds_zero_based'], [4, 4, 3, 3])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), before)

    def test_relative_and_out_of_grid_columns_remain_refused(self):
        for column in (256, 0x3FFF, 0x4000, 0x8000, 0xC003):
            with self.subTest(column=column), self.assertRaises(ValueError):
                classify_static_name(name_payload(formula=cell_reference(column=column)), 1, [(0, 0)])

    def test_external_missing_spanning_and_wrong_local_references_refused(self):
        cases = (([], 0, 0), ([(0, 1)], 0, 0), ([(1, 1)], 0, 1), ([(0, 0)], 1, 0))
        for refs, index, scope in cases:
            with self.subTest(refs=refs, index=index, scope=scope), self.assertRaises(ValueError):
                classify_static_name(name_payload(sheet=scope, formula=cell_reference(index=index)), 2, refs)
        # Final BoundSheet8 reconciliation still rejects a nonexistent sheet.
        links = rec(0x1AE, b'\x01\0\x01\x04') + rec(0x17, struct.pack('<4H', 1, 0, 2, 2))
        with self.assertRaises(ValueError):
            parse_stream(workbook(extra_global=links + rec(0x18, name_payload(sheet=0, formula=cell_reference()))), compatibility='data')

    def test_macro_hidden_function_builtin_and_unknown_flags_refused(self):
        for flags in (1, 2, 4, 8, 0x10, 0x20, 0x40, 0x100):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                classify_static_name(name_payload(flags=flags, formula=cell_reference()), 1, [(0, 0)])
        for builtin in (0x07, 0x0D):
            with self.subTest(builtin=builtin), self.assertRaises(ValueError):
                classify_static_builtin_name(
                    name_payload(chr(builtin), flags=0x20, options=0, formula=cell_reference()), 1, [(0, 0)])

    def test_no_cell_union_or_extra_token_generalization(self):
        reference = cell_reference()
        for formula in (reference[:-1], reference + b'\0', reference + reference + b'\x10',
                        b'\x39' + reference[1:], b'\x3c' + reference[1:]):
            with self.subTest(formula=formula.hex()), self.assertRaises(ValueError):
                classify_static_name(name_payload(formula=formula), 1, [(0, 0)])

    def test_duplicate_area_and_cell_names_share_budget_and_identity(self):
        first = rec(0x18, name_payload('Input', formula=cell_reference()))
        duplicate = rec(0x18, name_payload('input'))
        with self.assertRaisesRegex(ValueError, 'Duplicate ordinary static name'):
            parse_stream(workbook(extra_global=self_extern_sheet() + first + duplicate), compatibility='data')
        with patch('wps_biff8.MAX_STATIC_NAMES', 0), self.assertRaises(ResourceLimitError):
            parse_stream(named_workbook(name_payload(formula=cell_reference())), compatibility='data')

    def test_strict_and_legacy_name_subset_is_unchanged(self):
        for mode in ('strict', 'legacy_excel_clsid'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                parse_stream(named_workbook(name_payload(formula=cell_reference())), compatibility=mode)


class PageBreakDataReadingTests(unittest.TestCase):
    def test_invalid_layout_preserved_without_altering_saved_cells(self):
        cases = (
            (0x1B, [(0, 65535, 255)], 'invalid_bounds'),  # Real template's observed fields.
            (0x1B, [(2, 0, 256)], 'invalid_bounds'),
            (0x1A, [(256, 0, 5)], 'invalid_bounds'),
            (0x1A, [(1, 5, 4)], 'invalid_bounds'),
            (0x1B, [(2, 0, 2), (1, 4, 5)], 'unordered_entries'),
            (0x1A, [(1, 0, 5), (1, 5, 7)], 'overlapping_intervals'),
        )
        base = parse_stream(template_workbook(), compatibility='data')
        for kind, entries, problem in cases:
            payload = page_breaks(entries)
            raw = template_workbook(extra_sheet=rec(kind, payload))
            with self.subTest(kind=kind, entries=entries):
                parsed = parse_stream(raw, compatibility='data')
                self.assertEqual(values(parsed), values(base))
                self.assertEqual(parsed['status'], 'partial')
                self.assertEqual(parsed['cell_value_coverage'], 'complete_within_declared_sheets')
                source = next(item for item in parsed['uninterpreted_metadata'] if item['record_id'] == hex(kind))
                self.assertEqual(source['payload_hex'], payload.hex())
                structure = source['structure']
                self.assertFalse(structure['layout_checks_passed'])
                self.assertFalse(structure['layout_applied'])
                self.assertIn(problem, [item['code'] for item in structure['layout_issues']])
                issue = next(item for item in parsed['issues'] if item['code'] == 'page_break_layout_not_applied')
                self.assertEqual(issue['record_offset'], source['offset'])
                for mode in ('strict', 'legacy_excel_clsid'):
                    with self.assertRaises(ValueError):
                        parse_stream(raw, compatibility=mode)

    def test_order_and_overlap_diagnostics_span_repeated_records(self):
        raw = template_workbook(extra_sheet=rec(0x1B, page_breaks([(2, 0, 10)])) + rec(0x1B, page_breaks([(2, 1, 2), (2, 3, 4)])))
        result = parse_stream(raw, compatibility='data')
        sources = [item for item in result['uninterpreted_metadata'] if item['record_id'] == '0x1b']
        problems = sources[1]['structure']['layout_issues']
        self.assertEqual(problems, [{'code': 'overlapping_intervals', 'message': 'Overlapping page-break intervals on the same axis',
                                     'first_entry_index': 0, 'count': 2}])

    def test_valid_and_empty_layouts_do_not_gain_invalid_layout_diagnostic(self):
        for entries in ([], [(2, 0, 255)], [(2, 0, 2), (2, 3, 4)]):
            result = parse_stream(template_workbook(extra_sheet=rec(0x1B, page_breaks(entries))), compatibility='data')
            source = next(item for item in result['uninterpreted_metadata'] if item['record_id'] == '0x1b')
            self.assertTrue(source['structure']['layout_checks_passed'])
            self.assertFalse(any(item['code'] == 'page_break_layout_not_applied' for item in result['issues']))

    def test_truncated_mismatched_oversized_frames_still_refused(self):
        for kind, payload in ((0x1B, b'\0'), (0x1B, b'\x01\0'),
                              (0x1B, page_breaks([(2, 0, 2)]) + b'\0'),
                              (0x1B, struct.pack('<H', 1027) + bytes(6 * 1027)),
                              (0x1A, struct.pack('<H', 256) + bytes(6 * 256))):
            with self.subTest(kind=kind, size=len(payload)), self.assertRaises(ValueError):
                parse_stream(template_workbook(extra_sheet=rec(kind, payload)), compatibility='data')

    def test_metadata_budget_unknown_records_and_cell_damage_still_refused(self):
        bad_layout = rec(0x1B, page_breaks([(0, 65535, 255)]))
        with patch('wps_biff8.MAX_METADATA_BYTES', 7), self.assertRaises(ResourceLimitError):
            parse_stream(template_workbook(extra_sheet=bad_layout), compatibility='data')
        for tail in (rec(0x7777, b'unknown'), rec(0x203, bytes(7)), rec(0x2F, bytes(2))):
            with self.subTest(tail=tail.hex()), self.assertRaises(ValueError):
                parse_stream(template_workbook(extra_sheet=bad_layout + tail), compatibility='data')

    def test_many_invalid_layout_entries_have_bounded_diagnostics(self):
        payload = page_breaks([(0, 65535, 255)] * 1026)
        result = parse_stream(template_workbook(extra_sheet=rec(0x1B, payload)), compatibility='data')
        source = next(item for item in result['uninterpreted_metadata'] if item['record_id'] == '0x1b')
        self.assertLessEqual(len(source['structure']['layout_issues']), 3)
        self.assertEqual(source['structure']['layout_issues'][0]['count'], 1026)

    def test_combined_cli_preserves_partial_json_input_and_saved_values(self):
        name = name_payload('Input', formula=cell_reference())
        stream = template_workbook(extra_global=self_extern_sheet() + rec(0x18, name),
                                   extra_sheet=rec(0x1B, page_breaks([(0, 65535, 255)])))
        raw = synthetic_cfb({'Workbook': stream})
        # Exclusive task-local directory; only this test's explicitly named file is written.
        with tempfile.TemporaryDirectory(prefix='et-compat-', dir='.') as directory:
            source = Path(directory) / 'synthetic.ett'
            with source.open('xb') as handle:
                handle.write(raw)
            process = subprocess.run([sys.executable, '-I', '-S', '-B', str((Path(__file__).resolve().parents[1] / 'read_et.py')), str(source)],
                                     capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, 3, process.stderr)
            self.assertEqual(process.stderr, '')
            result = json.loads(process.stdout)
            self.assertEqual(result['status'], 'partial')
            self.assertFalse(result['reading_assessment']['full_format_verified'])
            self.assertTrue(any(item['code'] == 'page_break_layout_not_applied' for item in result['issues']))
            self.assertEqual(source.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
