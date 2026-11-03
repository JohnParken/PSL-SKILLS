"""Synthetic MS-XLS group records; no real producer compatibility evidence."""
from decimal import Decimal
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_biff8 import parse_stream, UnsupportedError, ResourceLimitError
from test_wps_biff8 import rec, formula, workbook, special, string


def exp(row=0, col=0):
    return b'\1'+struct.pack('<HH',row,col)


def group(kind='shared', bounds=(0,1,0,0), uses=2, tokens=b'\x1e\x2a\0'):
    prefix=struct.pack('<HHBB',*bounds)+(bytes([0,uses]) if kind=='shared' else bytes(6))
    return rec(0x4BC if kind=='shared' else 0x221,prefix+struct.pack('<H',len(tokens))+tokens)


def member(row, value=42, shared=True, anchor=(0,0), cache=None):
    return formula(row,0,struct.pack('<d',value) if cache is None else cache,8 if shared else 0,exp(*anchor))


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
        self.assertEqual(result['issues'][0]['code'],'formula_group_expression_not_decoded')

    def test_ptgexp_reserved_high_bit_is_ignored_as_specified(self):
        first=formula(0,0,struct.pack('<d',42),8,b'\x81'+bytes(4))
        result=self.parse(first+group()+member(1))
        self.assertEqual(result['sheets'][0]['cells']['A1']['formula']['group_anchor'],'A1')

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
        self.assertIn('missing_formula_string_cache',[x['code'] for x in result['issues']])

    def test_missing_members_anchor_and_flag_conflicts_rejected(self):
        bad=[member(0)+group(), member(0)+member(1), member(0)+group()+member(1,anchor=(9,0)),
             member(0)+group()+member(1,shared=False),member(0)+group()+member(2),
             member(0,shared=False)+group('array')+member(1),
             member(0)+group(uses=1)+member(1), group(),
             member(1,shared=False,anchor=(1,0))+group('array')]
        for raw in bad:
            with self.subTest(raw=raw.hex()), self.assertRaises(ValueError): self.parse(raw)

    def test_shared_range_can_have_nonmember_cells(self):
        result=self.parse(member(0)+group(bounds=(0,9,0,0))+member(9))
        self.assertEqual(result['sheets'][0]['formula_groups'][0]['observed_members'],2)

    def test_bounds_lengths_and_budget_rejected(self):
        for definition in [group(bounds=(1,0,0,0)),group(uses=0),rec(0x4BC,bytes(9)),
                           rec(0x4BC,struct.pack('<HHBB',0,1,0,0)+b'\0\2\xff\xff\1')]:
            with self.assertRaises(ValueError): self.parse(member(0)+definition+member(1))
        with patch('wps_biff8.MAX_CELLS',1),self.assertRaises(ResourceLimitError):
            self.parse(member(0)+group())
        with patch('wps_biff8.MAX_METADATA_BYTES',1),self.assertRaises(ResourceLimitError):
            self.parse(member(0)+group()+member(1))

    def test_overlapping_ranges_and_extended_ptgexp_refused(self):
        with self.assertRaises(UnsupportedError):
            self.parse(member(0)+group(bounds=(0,2,0,0),uses=1)+member(1,anchor=(1,0))+group(bounds=(1,3,0,0),uses=1))
        with self.assertRaises(UnsupportedError):
            self.parse(formula(0,0,struct.pack('<d',1),8,exp()+b'\x1e\0\0'))

    def test_all_definition_payload_truncations_fail_without_partial_cells(self):
        for kind in ('shared','array'):
            definition=group(kind)
            for length in range(len(definition)-4):
                truncated=rec(0x4BC if kind=='shared' else 0x221,definition[4:4+length])
                with self.subTest(kind=kind,length=length),self.assertRaises(ValueError):
                    self.parse(member(0,shared=kind=='shared')+truncated+member(1,shared=kind=='shared'))

    def test_group_must_immediately_follow_anchor_formula(self):
        raw=member(0,cache=special(0))+rec(0x207,string('x'))+group()+member(1)
        with self.assertRaises(ValueError): self.parse(raw)

if __name__=='__main__': unittest.main()
