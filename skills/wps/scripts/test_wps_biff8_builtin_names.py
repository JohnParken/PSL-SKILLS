"""MS-XLS Lbl built-in static Name and PtgUnion bounded subset regressions.

Synthetic-only, stdlib. No Office, formula/name interpretation, filter/print
activation, external link, or WPS producer compatibility claims.
"""
import struct
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_wps_biff8 import workbook, rec
from test_wps_biff8_names import name_payload, self_extern_sheet
from test_wps_biff8_salvage import values
from wps_biff8 import parse_stream, ResourceLimitError
from wps_biff8_names import (
    classify_static_builtin_name, classify_static_name, UnsafeDefinedName,
)


def area(rows, cols, *, token=0x3b, ixti=0):
    return bytes((token,)) + struct.pack('<H4H', ixti, *rows, *cols)


def built_in_name(builtin_id=0x07, *, sheet=1, hidden=False, formula=None,
                  name_option=0, flags_extra=0):
    if formula is None:
        formula = area((0,2),(0,255))
    flags = 0x20 | int(hidden) | flags_extra
    return (struct.pack('<HBBHHH4B',flags,0,1,len(formula),
                        0,sheet,0,0,0,0)
            + bytes((name_option,builtin_id)) + formula)


def built_in_workbook(*payloads, preamble=None):
    return workbook(extra_global=(self_extern_sheet() if preamble is None else preamble)
                    + b''.join(rec(0x0018,p) for p in payloads))


class StaticBuiltInNameTests(unittest.TestCase):
    def test_print_titles_full_rows_and_full_columns(self):
        baseline=values(parse_stream(workbook(), compatibility='data'))
        for formula, expected in (
            (area((0,3),(0,255)), 'full_row'),
            (area((0,65535),(2,4)), 'full_column'),
        ):
            for hidden in (False, True):
                with self.subTest(kind=expected, hidden=hidden):
                    data=built_in_workbook(built_in_name(
                        formula=formula, hidden=hidden))
                    result=parse_stream(data,compatibility='data')
                    self.assertEqual(values(result),baseline)
                    self.assertEqual(result['status'],'partial')
                    items=[x for x in result['uninterpreted_metadata']
                           if x['name']=='PrintTitlesStatic']
                    self.assertEqual(len(items),1)
                    self.assertEqual(items[0]['payload_hex'],
                                     built_in_name(formula=formula,hidden=hidden).hex())
                    source=items[0]['structure']
                    self.assertEqual(source['classification'],
                                     'built_in_print_titles_static_area_only')
                    self.assertEqual(source['regions_kind'],[expected])
                    self.assertEqual(source['name_hidden_raw'],hidden)
                    self.assertFalse(source['print_settings_applied'])
                    self.assertFalse(source['formula_executed'])
                    self.assertFalse(source['external_link_followed'])

    def test_print_titles_row_column_union_is_static_source_only(self):
        first=area((0,5),(0,255))
        second=area((0,65535),(0,2),token=0x5b)
        formula=first+second+b'\x10'
        parsed=parse_stream(built_in_workbook(
            built_in_name(formula=formula)), compatibility='data')
        s=next(x for x in parsed['uninterpreted_metadata']
               if x['name']=='PrintTitlesStatic')['structure']
        self.assertTrue(s['ptg_union_raw_present'])
        self.assertEqual(s['regions_kind'],['full_row','full_column'])
        self.assertEqual(s['regions_zero_based'],
                         [[0,5,0,255],[0,65535,0,2]])
        self.assertFalse(s['print_settings_applied'])
        self.assertFalse(s['filter_applied'])

    def test_filter_database_local_static_range_hidden_or_visible(self):
        baseline=values(parse_stream(workbook(),compatibility='data'))
        for hidden in (False, True):
            payload=built_in_name(0x0d, hidden=hidden,
                                  formula=area((0,30),(0,8)))
            result=parse_stream(built_in_workbook(payload),compatibility='data')
            self.assertEqual(values(result),baseline)
            source=next(x for x in result['uninterpreted_metadata']
                        if x['name']=='FilterDatabaseStatic')['structure']
            self.assertEqual(source['builtin_name_id_raw'],0x0d)
            self.assertEqual(source['regions_zero_based'],[[0,30,0,8]])
            self.assertEqual(source['scope'] if 'scope' in source else
                             source['local_sheet_ordinal_raw'],1)
            self.assertFalse(source['filter_applied'])
            self.assertFalse(source['formula_executed'])

    def test_ordinary_two_area_union_same_self_sheet(self):
        union=(area((2,4),(1,3)) +
               area((10,12),(2,7),token=0x7b) + b'\x10')
        parsed=parse_stream(workbook(
            extra_global=self_extern_sheet()+
                         rec(0x0018,name_payload('区域双联',formula=union))),
            compatibility='data')
        record=next(x for x in parsed['uninterpreted_metadata']
                    if x['name']=='OrdinaryStaticInternalAreaName')
        obj=record['structure']
        self.assertTrue(obj['ptg_union_raw_present'])
        self.assertIsNone(obj['bounds_zero_based'])
        self.assertEqual(obj['regions_zero_based'],
                         [[2,4,1,3],[10,12,2,7]])
        self.assertEqual(obj['internal_xti_index_raw'],0)
        self.assertFalse(obj['formula_executed'])

    def test_built_in_wrong_ids_flags_and_incomplete_unicode(self):
        invalid=[
            built_in_name(0x08),built_in_name(0x06),built_in_name(0x0e),
            built_in_name(0x07,flags_extra=0x02),
            built_in_name(0x07,flags_extra=0x08),
            built_in_name(0x07,flags_extra=0x10),
            built_in_name(0x07,name_option=1),
            built_in_name(0x07,sheet=0),
            built_in_name(0x07,sheet=2),
            built_in_name(0x07)[:-1],
            built_in_name(0x07)+b'\x00',
        ]
        for index,payload in enumerate(invalid):
            with self.subTest(candidate=index),self.assertRaises(ValueError):
                classify_static_builtin_name(payload,1,[(0,0)])

    def test_built_in_rejects_complex_or_external_formula(self):
        wrong=[
            area((0,4),(0,255),ixti=1),
            b'\x39'+bytes(10),   # PtgNameX
            b'\x21'+bytes(10),   # PtgFunc
            b'\x3b'+bytes(9),
            area((0,4),(0,255))+b'\x10',
            area((0,4),(0,255))+area((0,65535),(0,3))+b'\x11',
        ]
        for i,formula in enumerate(wrong):
            with self.subTest(case=i),self.assertRaises(ValueError):
                classify_static_builtin_name(
                    built_in_name(formula=formula),1,[(0,0)])
        external=rec(0x01AE,b'\x01\x00\xff\xff')
        with self.assertRaises(ValueError):
            parse_stream(built_in_workbook(
                built_in_name(),preamble=external),compatibility='data')

    def test_print_title_requires_full_row_or_column_not_arbitrary_area(self):
        invalid=[
            area((0,4),(0,3)),
            area((0,65535),(0,255)),
            area((0,4),(0,255))+area((5,8),(0,255))+b'\x10',
            area((0,65535),(2,4))+area((0,65535),(5,9))+b'\x10',
            area((0,1),(0,255))+area((0,2),(0,255))+b'\x10',
        ]
        for idx,formula in enumerate(invalid):
            with self.subTest(idx=idx),self.assertRaises(ValueError):
                classify_static_builtin_name(built_in_name(formula=formula),
                                             1,[(0,0)])

    def test_filter_database_rejects_union_and_one_row(self):
        for formula in (
            area((3,3),(1,6)),
            area((0,4),(0,5))+area((8,9),(0,3))+b'\x10',
        ):
            with self.subTest(formula_len=len(formula)),self.assertRaises(ValueError):
                classify_static_builtin_name(
                    built_in_name(0x0d,formula=formula),1,[(0,0)])

    def test_cross_sheet_internal_xti_or_relative_area_rejected(self):
        for formula in (
            area((0,8),(0,255),ixti=1),
            area((0,65535),(0x4000,3)),
            area((0,65535),(0x0100,3)),
        ):
            with self.subTest(raw=formula[:4].hex()),self.assertRaises(ValueError):
                classify_static_builtin_name(
                    built_in_name(formula=formula),1,[(0,0)])
        with self.assertRaises(ValueError):
            classify_static_builtin_name(built_in_name(),
                                         1,[(0,1)])
        with self.assertRaises(ValueError):
            classify_static_builtin_name(built_in_name(),
                                         1,[(1,1)])

    def test_duplicate_built_in_by_local_sheet_type_rejected(self):
        payload=built_in_name()
        with self.assertRaisesRegex(ValueError,
                                    'Duplicate built-in static Name'):
            parse_stream(built_in_workbook(payload,payload),
                         compatibility='data')
        # Different types are distinct and may co-exist in the same sheet.
        ok=parse_stream(built_in_workbook(
            payload,built_in_name(0x0d,formula=area((0,30),(0,3)))),
            compatibility='data')
        self.assertEqual(len([x for x in ok['uninterpreted_metadata']
                              if x['name'] in ('PrintTitlesStatic',
                                              'FilterDatabaseStatic')]),2)

    def test_missing_final_BoundSheet8_not_accepted_just_from_header(self):
        # An internal ExternSheet references second sheet; physical BoundSheet8
        # collection has one sheet. Deferred post-Globals check must fail.
        sup=rec(0x01AE,b'\x01\x00\x01\x04')
        extra=rec(0x0017,struct.pack('<H H h h',1,0,1,1))
        raw=workbook(extra_global=sup+extra+rec(0x0018,
                      built_in_name(sheet=2,formula=area(
                          (0,4),(0,255)))))
        with self.assertRaises(ValueError):
            parse_stream(raw,compatibility='data')

    def test_explicit_legacy_only_old_print_area_and_no_skip(self):
        for compatibility in ('strict','legacy_excel_clsid'):
            with self.subTest(compatibility=compatibility),self.assertRaises(ValueError):
                parse_stream(built_in_workbook(built_in_name()),
                             compatibility=compatibility)
        for builtin in (0x07,0x0d):
            unsafe=built_in_name(builtin,flags_extra=0x08)
            with self.subTest(builtin=builtin),self.assertRaises(ValueError):
                parse_stream(built_in_workbook(unsafe),
                             compatibility='data',
                             salvage_unknown_metadata=True)

    def test_ordinary_union_rejects_non_union_op_and_extra_data(self):
        left=area((0,1),(0,2))
        right=area((3,4),(5,7))
        for formula in (
            left+right+b'\x11',  # not PtgUnion
            left+right+b'\x10\x00',
            left+right,           # missing operator
            left+area((0,1),(0,2),ixti=1)+b'\x10',
            left+b'\x39'+bytes(10)+b'\x10',
            left+right+b'\x10'+left+b'\x10',  # more than 2 areas
        ):
            with self.subTest(formula_len=len(formula)),self.assertRaises(ValueError):
                classify_static_name(name_payload(
                    '区域集合', formula=formula),1,[(0,0)])

    def test_combined_name_budget_does_not_allow_flood(self):
        data=built_in_workbook(
            built_in_name(),
            built_in_name(0x0d,formula=area((0,20),(0,2))),
        )
        with patch('wps_biff8.MAX_STATIC_NAMES',1):
            with self.assertRaises(ResourceLimitError):
                parse_stream(data,compatibility='data')


if __name__=='__main__':
    unittest.main()
