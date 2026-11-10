"""BIFF8 defined-name subset: non-macro static internal 3-D area only.

Synthetic, independent fixed-field expectations. No formula/name execution,
external-file reads or WPS producer acceptance is claimed.
"""
import struct
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_biff8 import workbook, rec
from test_wps_biff8_salvage import values
from test_wps_cfb import synthetic_cfb
from read_et import read_bytes
from wps_biff8 import parse_stream, ResourceLimitError, UnsupportedError
from wps_biff8_names import classify_static_name, MAX_STATIC_NAMES, UnsafeDefinedName


def self_extern_sheet():
    # One SupBook referring to this workbook, XTI index 0 -> first sheet.
    return (rec(0x01AE, b'\x01\x00\x01\x04') +
            rec(0x0017, struct.pack('<H H h h', 1, 0, 0, 0)))


def name_payload(name='核心区域', *, flags=0, shortcut=0, sheet=1,
                 token=0x3B, ref=0, rows=(0, 5), cols=(0, 4),
                 options=1, formula=None):
    encoded = name.encode('utf-16le' if options else 'latin1')
    if formula is None:
        formula = bytes((token,)) + struct.pack('<H4H', ref, *rows, *cols)
    return (struct.pack('<HBBHHH4B', flags, shortcut, len(name), len(formula),
                        0, sheet, 0, 0, 0, 0) +
            bytes((options,)) + encoded + formula)


def named_workbook(payload, *, before=b'', after=b''):
    return workbook(extra_global=self_extern_sheet() + before +
                    rec(0x0018, payload) + after)


class StaticNameTests(unittest.TestCase):
    def test_file_reader_retains_unusable_name_and_palette_and_continues(self):
        macro_name = name_payload('Budget', flags=0x0008)
        palette = b'\x38\x00\x99'
        original = read_bytes(synthetic_cfb({'Workbook': workbook()}))
        for name in (macro_name, b'\x00', name_payload(formula=b'\x21' + bytes(10))):
            with self.subTest(name=name.hex()):
                stream = named_workbook(name, after=rec(0x0092, palette))
                raw = synthetic_cfb({'Workbook': stream})
                result = read_bytes(raw)
                self.assertEqual(values(result), values(original))
                self.assertEqual(result['status'], 'partial')
                self.assertTrue(result['omissions_affect_cell_values'])
                self.assertFalse(result['formulas_executed'])
                self.assertFalse(result['external_links_followed'])
                records = {item['name']: item for item in result['uninterpreted_metadata']}
                self.assertEqual(records['DefinedNameOpaque']['payload_hex'], name.hex())
                self.assertTrue(records['DefinedNameOpaque']['validation_error'])
                self.assertEqual(records['PaletteOpaque']['payload_hex'], palette.hex())
                self.assertFalse(records['DefinedNameOpaque']['semantics_verified'])

    def test_default_file_reader_preserves_cells_and_caches_with_passive_name_fields(self):
        original = name_payload()
        baseline = read_bytes(synthetic_cfb({'Workbook': named_workbook(original)}))
        variants = ((0x0001, b'\0\0', 1), (0, b'\x12\x34', 1), (0, b'\0\0', 0x81), (0x9001, b'\x12\x34', 0x81))
        for flags, reserved, options in variants:
            with self.subTest(flags=flags, reserved=reserved, options=options):
                payload = struct.pack('<H', flags) + original[2:6] + reserved + original[8:14] + bytes((options,)) + original[15:]
                result = read_bytes(synthetic_cfb({'Workbook': named_workbook(payload)}))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'], baseline['sheets'])
                self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')
                self.assertFalse(result['omissions_affect_cell_values'])
                self.assertFalse(result['formulas_executed'])
                self.assertTrue(result['sheets'][0]['cells']['B4']['formula']['cache_may_be_stale'])
                evidence = next(item for item in result['uninterpreted_metadata'] if item['record_id'] == '0x18')
                issue = next(item for item in result['issues'] if item['code'] == 'defined_name_passive_header_fields_ignored')
                self.assertEqual(evidence['payload_hex'], payload.hex())
                self.assertEqual(issue['record_offset'], evidence['offset'])
                self.assertEqual(issue['ignored_flag_bits'], flags)
                self.assertEqual(issue['reserved_fields_raw_hex'], (reserved + bytes(4)).hex())
                self.assertEqual(issue['name_options_raw'], options)

    def test_passive_name_fields_do_not_hide_other_failures(self):
        for payload in (
            name_payload(flags=0x9009),
            name_payload(flags=1, shortcut=65),
            name_payload(flags=1, ref=7),
            name_payload(flags=1, sheet=3),
            name_payload(flags=1)[:-1],
        ):
            with self.subTest(payload=payload.hex()), self.assertRaises(ValueError):
                parse_stream(named_workbook(payload), compatibility='data')
        duplicate = named_workbook(name_payload('Budget', flags=1),
                                   after=rec(0x0018, name_payload('budget')))
        with self.assertRaisesRegex(ValueError, 'Duplicate ordinary static name'):
            parse_stream(duplicate, compatibility='data')

    def test_ordinary_internal_named_range_no_cell_changes(self):
        raw = named_workbook(name_payload('部门甲_统计区域',rows=(2,6),cols=(1,4)))
        base = workbook()
        with self.assertRaises(ValueError):
            parse_stream(raw, compatibility='strict')
        before = parse_stream(base,compatibility='data')
        got = parse_stream(raw,compatibility='data')
        self.assertEqual(values(got),values(before))
        self.assertEqual(got['status'],'partial')
        self.assertEqual(got['cell_value_coverage'],'complete_within_declared_sheets')
        sources=[x for x in got['uninterpreted_metadata'] if x['record_id']=='0x18']
        self.assertEqual(len(sources),1)
        obj=sources[0]['structure']
        self.assertEqual(obj['classification'],'ordinary_static_internal_area_only')
        self.assertEqual(obj['bounds_zero_based'],[2,6,1,4])
        self.assertEqual(obj['scope'],'local')
        self.assertEqual(obj['local_sheet_ordinal_raw'],1)
        self.assertEqual(obj['internal_xti_index_raw'],0)
        self.assertFalse(obj['formula_executed'])
        self.assertFalse(obj['external_link_followed'])
        self.assertFalse(obj['effective_name_binding_applied'])
        self.assertNotIn('name_text',obj)

    def test_zero_macro_bits_and_string_length_required(self):
        for name in ('a', '中文', '_A1.X', '数据2026'):
            with self.subTest(name=name):
                p=name_payload(name)
                ob=classify_static_name(p,1,[(0,0)])
                self.assertEqual(ob['name_character_count'],len(name))
        for name in ('', 'A B', '1Bad', 'A!B', 'A/B', 'A:B', 'A\tB'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                classify_static_name(name_payload(name),1,[(0,0)])

    def test_preserve_three_operand_classes_but_never_interpret_formula(self):
        for token in (0x3B, 0x5B, 0x7B):
            with self.subTest(token=token):
                obj=classify_static_name(name_payload('AB',token=token),1,[(0,0)])
                self.assertEqual(obj['formula_token_raw'],hex(token))
                self.assertFalse(obj['formula_executed'])

    def test_explicit_legacy_retains_its_previous_strict_name_subset(self):
        for compatibility in ('strict','legacy_excel_clsid'):
            with self.subTest(compatibility=compatibility), self.assertRaises(ValueError):
                parse_stream(named_workbook(name_payload('AB')),
                             compatibility=compatibility)

    def test_macro_flags_reject_even_with_safe_range(self):
        for flags in (0x0002,0x0004,0x0008,0x000A,0x000C,0x000E,
                      0x0010,0x0020,0x0040,0x0100):
            with self.subTest(flags=flags),self.assertRaises(UnsafeDefinedName):
                classify_static_name(name_payload(flags=flags),1,[(0,0)])

    def test_functions_names_external_and_formula_tokens_rejected(self):
        wrong_formulas = [
            bytes((0x21,)) + bytes(10), # PtgFunc could invoke an expression
            bytes((0x39,)) + bytes(10), # PtgNameX external defined name
            bytes((0x1E,)) + bytes(10), # PtgInt (wrong cce)
            bytes((0x3B,)) + bytes(10),
        ]
        for formula in wrong_formulas:
            if formula[0] == 0x3B:
                # A valid-looking Area3d with invalid XTI index
                formula = b'\x3b\x07\x00' + bytes(8)
            with self.subTest(token=hex(formula[0])),self.assertRaises(ValueError):
                classify_static_name(name_payload(formula=formula),1,[(0,0)])
        with self.assertRaises(ValueError):
            parse_stream(workbook(extra_global=rec(0x0018,name_payload())),
                         compatibility='data')
        # Having an ExternSheet record is insufficient if its contents refer
        # outside the current workbook; existing SupBook restrictions remain.
        external=rec(0x01AE, b'\x02\x00\x01\x04')
        with self.assertRaises(ValueError):
            parse_stream(workbook(extra_global=external+rec(0x0018,name_payload())),
                         compatibility='data')

    def test_invalid_header_name_flags_truncated_and_variable_tail(self):
        valid=name_payload()
        candidates=[
            valid[:14], valid[:-1], valid+b'\x00',
            valid[:14]+b'\x02'+valid[15:],
            valid[:14]+b'\x09'+valid[15:],
        ]
        for idx,p in enumerate(candidates):
            with self.subTest(idx=idx),self.assertRaises(ValueError):
                classify_static_name(p,1,[(0,0)])
        p=bytearray(valid)
        struct.pack_into('<H',p,6,1)
        with self.assertRaises(ValueError):
            classify_static_name(bytes(p),1,[(0,0)])
        p=bytearray(valid)
        p[10]=0x01
        with self.assertRaises(ValueError):
            classify_static_name(bytes(p),1,[(0,0)])
        p=bytearray(valid)
        p[2]=ord('Z')
        with self.assertRaises(ValueError):
            classify_static_name(bytes(p),1,[(0,0)])

    def test_area_invalid_coordinate_order_relative_and_scope(self):
        for props in (
            {'rows':(8,1)}, {'cols':(9,3)},
            {'cols':(0x4000,1)}, {'cols':(0x8000,1)},
            {'cols':(0x0100,1)}, {'sheet':2}, {'ref':1},
        ):
            with self.subTest(props=props),self.assertRaises(ValueError):
                classify_static_name(name_payload(**props),1,[(0,0)])

    def test_case_insensitive_duplicate_within_scope_refused(self):
        raw=workbook(extra_global=self_extern_sheet() +
                     rec(0x0018,name_payload('Budget'))+
                     rec(0x0018,name_payload('budget')))
        with self.assertRaisesRegex(ValueError,'Duplicate ordinary static name'):
            parse_stream(raw, compatibility='data')

    def test_name_entry_budget_rejected_without_truncating(self):
        raw=workbook(extra_global=self_extern_sheet() +
                     rec(0x0018,name_payload('A'))+
                     rec(0x0018,name_payload('B')))
        with patch('wps_biff8.MAX_STATIC_NAMES',1):
            with self.assertRaises(ResourceLimitError):
                parse_stream(raw,compatibility='data')

    def test_previous_built_in_print_area_remains_valid(self):
        # Exact previous implementation's 27-byte static Print_Area; same
        # self SupBook and direct local XTI with area A1:B3.
        data=(bytes.fromhex('200000010b000000')+struct.pack('<H',1)+
              bytes.fromhex('0000000000063b')+
              struct.pack('<5H',0,0,2,0,1))
        self.assertEqual(len(data),27)
        parsed=parse_stream(workbook(extra_global=self_extern_sheet() +
                           rec(0x0018,data)),compatibility='data')
        label=[x for x in parsed['uninterpreted_metadata'] if x['record_id']=='0x18']
        self.assertEqual(label[0]['name'],'PrintAreaStatic')
        extended = bytearray(data)
        extended[0] |= 1
        extended[10:14] = b'\x01\x02\x03\x04'
        stream = named_workbook(bytes(extended))
        recovered = parse_stream(stream, compatibility='data')
        self.assertEqual(recovered['status'], 'partial')
        self.assertEqual(recovered['sheets'], parsed['sheets'])
        for mode in ('strict', 'legacy_excel_clsid'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                parse_stream(stream, compatibility=mode)
        invalid = data[:17] + b'\x08\0' + data[19:]
        result = read_bytes(synthetic_cfb({'Workbook': named_workbook(invalid)}))
        self.assertEqual(values(result), values(parsed))
        self.assertTrue(result['omissions_affect_cell_values'])
        opaque = next(item for item in result['uninterpreted_metadata'] if item['name'] == 'DefinedNameOpaque')
        self.assertEqual(opaque['payload_hex'], invalid.hex())

    def test_optin_does_not_skip_unsafe_name(self):
        unsafe=name_payload('RunAnything', flags=0x0008)
        with self.assertRaises(ValueError):
            parse_stream(named_workbook(unsafe),compatibility='data',
                         salvage_unknown_metadata=True)


if __name__=='__main__':
    unittest.main()
