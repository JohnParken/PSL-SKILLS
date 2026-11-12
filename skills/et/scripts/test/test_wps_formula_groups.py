"""Synthetic MS-XLS shared/array formula groups; tolerant-contract coverage.

Saved caches are always kept. A malformed or unmatched group definition becomes
a bounded diagnostic; it never discards the value cells and is never
recalculated or executed.
"""
from decimal import Decimal
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wps_biff8 import parse_stream, UnsupportedError, ResourceLimitError
from test_wps_biff8 import rec, formula, workbook, special, string


def exp(row=0, col=0):
    return b'\1'+struct.pack('<HH',row,col)


def group(kind='shared', bounds=(0,1,0,0), uses=2, tokens=b'\x1e\x2a\0'):
    prefix=struct.pack('<HHBB',*bounds)+(bytes([0,uses]) if kind=='shared' else bytes(6))
    return rec(0x4BC if kind=='shared' else 0x221,prefix+struct.pack('<H',len(tokens))+tokens)


def member(row, value=42, shared=True, anchor=(0,0), cache=None):
    return formula(row,0,struct.pack('<d',value) if cache is None else cache,8 if shared else 0,exp(*anchor))


def issue_codes(result):
    return [item.get('code') for item in result.get('issues', ())]


class FormulaGroupTests(unittest.TestCase):
    def parse(self, raw):
        return parse_stream(workbook([raw],strings=[]))

    def test_shared_numeric_cache_and_independent_pointer_metadata(self):
        result=self.parse(member(0,42)+group()+member(1,43))
        sheet=result['sheets'][0]
        self.assertEqual([sheet['cells'][a]['value'] for a in ('A1','A2')],[Decimal(42),Decimal(43)])
        self.assertEqual(sheet['cells']['A2']['formula']['group_anchor'],'A1')
        self.assertEqual(sheet['formula_groups'][0]['bounds_zero_based'],[0,1,0,0])
        self.assertEqual(sheet['formula_groups'][0]['observed_members'],2)
        self.assertFalse(result['formulas_executed'])
        self.assertIn('formula_group_expression_not_decoded',issue_codes(result))

    def test_ptgexp_reserved_high_bit_is_ignored_as_specified(self):
        first=formula(0,0,struct.pack('<d',42),8,b'\x81'+bytes(4))
        result=self.parse(first+group()+member(1))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],42)
        self.assertEqual(len(result['sheets'][0]['formula_groups']),1)

    def test_array_all_cells_have_separate_cached_values(self):
        result=self.parse(member(0,2,False)+group('array')+member(1,3,False))
        sheet=result['sheets'][0]
        self.assertEqual(sheet['cells']['A2']['value'],3)
        self.assertEqual(sheet['formula_groups'][0]['kind'],'array')

    def test_string_after_group_and_boolean_error_caches(self):
        result=self.parse(member(0,cache=special(0))+group()+rec(0x207,string('中文'))+member(1,cache=special(1,1)))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'中文')
        self.assertIs(result['sheets'][0]['cells']['A2']['value'],True)
        result=self.parse(member(0,cache=special(2,7))+group()+member(1,cache=special(3)))
        self.assertEqual(result['sheets'][0]['cells']['A1']['type'],'error')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'],'')

    def test_missing_string_cache_is_explicit_partial(self):
        result=self.parse(member(0,cache=special(0))+group()+member(1))
        self.assertEqual(result['sheets'][0]['cells']['A1']['cache_status'],'missing')
        self.assertIn('missing_formula_string_cache',issue_codes(result))

    def test_group_member_and_anchor_problems_are_diagnostics_not_aborts(self):
        bad=[member(0)+group(), member(0)+member(1), member(0)+group()+member(1,anchor=(9,0)),
             member(0)+group()+member(1,shared=False),member(0)+group()+member(2),
             member(0,shared=False)+group('array')+member(1),
             member(0)+group(uses=1)+member(1), group(),
             member(1,shared=False,anchor=(1,0))+group('array')]
        for raw in bad:
            with self.subTest(raw=raw.hex()):
                result=self.parse(raw)
                self.assertEqual(result['status'],'partial')
                self.assertTrue(any(code in ('formula_group_definition_invalid',
                                             'formula_group_definition_orphan',
                                             'formula_group_member_mismatch',
                                             'formula_group_overlap')
                                    for code in issue_codes(result)))
                # The anchor formula cache itself is never erased.
                retained = list(result['sheets'][0]['cells'].values())
                self.assertTrue(all(cell['value'] == 42 for cell in retained))
                if raw.startswith(rec(0x0006, b'')):
                    self.assertGreaterEqual(len(retained), 1)

    def test_edge_real_zero_use_payload_variant_is_retained(self):
        payload = bytes.fromhex(
            '050005000409000013002c010000c02c0e0000c02c200000c042030400')
        raw = (formula(5, 4, struct.pack('<d', 168259), 8, exp(5, 4))
               + rec(0x4BC, payload)
               + formula(5, 5, struct.pack('<d', 7280), 8, exp(5, 4)))
        result = self.parse(raw)
        sheet = result['sheets'][0]
        self.assertEqual(sheet['cells']['E6']['value'], Decimal(168259))
        self.assertEqual(sheet['cells']['F6']['value'], Decimal(7280))
        self.assertEqual(sheet['cells']['F6']['formula']['group_anchor'], 'E6')
        definition = sheet['formula_groups'][0]
        self.assertEqual(definition['record_offset'], 196)
        self.assertTrue(definition['use_count_unspecified'])
        self.assertEqual(definition['observed_members'], 2)
        self.assertIn('formula_group_use_count_unspecified', issue_codes(result))

    def test_shared_zero_use_count_is_retained_as_unspecified(self):
        result = self.parse(member(0, 42) + group(uses=0) + member(1, 43))
        sheet = result['sheets'][0]
        self.assertEqual(sheet['cells']['A1']['value'], Decimal(42))
        self.assertEqual(sheet['cells']['A2']['value'], Decimal(43))
        self.assertEqual(sheet['cells']['A2']['formula']['group_anchor'], 'A1')
        definition = sheet['formula_groups'][0]
        self.assertTrue(definition['use_count_unspecified'])
        self.assertEqual(definition['expected_members'], 0)
        self.assertEqual(definition['observed_members'], 2)
        self.assertIn('formula_group_use_count_unspecified', issue_codes(result))
        self.assertNotIn('formula_group_definition_invalid', issue_codes(result))
        self.assertNotIn('formula_group_member_mismatch', issue_codes(result))
        self.assertEqual(result['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertFalse(result['omissions_affect_cell_values'])

    def test_zero_use_count_does_not_disable_other_group_checks(self):
        result = self.parse(member(0) + group(bounds=(1, 0, 0, 0), uses=0)
                            + member(1))
        self.assertIn('formula_group_definition_invalid', issue_codes(result))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 42)

    def test_shared_range_can_have_nonmember_cells(self):
        result=self.parse(member(0)+group(bounds=(0,9,0,0))+member(9))
        self.assertEqual(result['sheets'][0]['formula_groups'][0]['observed_members'],2)

    def test_bounds_lengths_and_budget_degrade_without_losing_caches(self):
        for definition in [group(bounds=(1,0,0,0)),rec(0x4BC,bytes(9)),
                           rec(0x4BC,struct.pack('<HHBB',0,1,0,0)+b'\0\2\xff\xff\1')]:
            with self.subTest(definition=definition.hex()):
                result=self.parse(member(0)+definition+member(1))
                self.assertEqual(result['status'],'partial')
                self.assertEqual(result['sheets'][0]['cells']['A1']['value'],42)
                self.assertIn('formula_group_definition_invalid',issue_codes(result))
        with patch('wps_biff8.MAX_CELLS',1):
            result=self.parse(member(0)+group())
            self.assertEqual(result['status'],'partial')
            self.assertIn('formula_group_budget_exceeded',issue_codes(result))
            self.assertEqual(result['sheets'][0]['cells']['A1']['value'],42)
        with patch('wps_biff8.MAX_METADATA_BYTES',1):
            result=self.parse(member(0)+group()+member(1))
            self.assertEqual(result['status'],'partial')
            self.assertIn('metadata_budget_truncated',issue_codes(result))
            self.assertEqual(result['sheets'][0]['cells']['A1']['value'],42)

    def test_overlapping_ranges_and_extended_ptgexp_degrade(self):
        result=self.parse(member(0)+group(bounds=(0,2,0,0),uses=1)+member(1,anchor=(1,0))+group(bounds=(1,3,0,0),uses=1))
        self.assertEqual(result['status'],'partial')
        self.assertIn('formula_group_overlap',issue_codes(result))
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'],42)
        result=self.parse(formula(0,0,struct.pack('<d',1),8,exp()+b'\x1e\0\0'))
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],1)

    def test_all_definition_payload_truncations_are_retained_as_opaque(self):
        for kind in ('shared','array'):
            definition=group(kind)
            for length in range(len(definition)-4):
                truncated=rec(0x4BC if kind=='shared' else 0x221,definition[4:4+length])
                with self.subTest(kind=kind,length=length):
                    result=self.parse(member(0,shared=kind=='shared')+truncated+member(1,shared=kind=='shared'))
                    self.assertEqual(result['status'],'partial')
                    self.assertEqual(result['sheets'][0]['cells']['A1']['value'],42)
                    self.assertTrue(result['uninterpreted_metadata'])

    def test_group_definition_after_interruption_is_retained_raw(self):
        raw=member(0,cache=special(0))+rec(0x207,string('x'))+group()+member(1)
        result=self.parse(raw)
        self.assertEqual(result['status'],'partial')
        self.assertIn('formula_group_definition_orphan',issue_codes(result))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],'x')

    def test_unknown_shared_flags_and_extra_ptg_exp_keep_cache(self):
        result=self.parse(formula(0,0,struct.pack('<d',5),8,b'\x1e\x2a\0'))
        self.assertEqual(result['status'],'partial')
        self.assertIn('formula_token_anomaly',issue_codes(result))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'],5)


if __name__=='__main__':
    unittest.main()
