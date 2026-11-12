#!/usr/bin/env python3
"""Known metadata failures must not be reported as saved-cell omissions."""
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from read_et import read_bytes
from test_wps_biff8 import cell, rec, workbook
from test_wps_formula_groups import group, member
from test_wps_ooxml_sheet import package
from wps_biff8 import parse_stream


def number_record(row, column, value=42):
    return rec(0x0203, cell(row, column) + struct.pack('<d', value))


class MetadataValueCoverageTests(unittest.TestCase):
    def read(self, records):
        return parse_stream(workbook([b''.join(records)], strings=[]))

    def assert_metadata_only(self, result, code):
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 42)
        self.assertEqual(result['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertFalse(result['omissions_affect_cell_values'])
        self.assertIn(code, [item['code'] for item in result['issues']])

    def test_invalid_formula_group_metadata_keeps_value_coverage(self):
        result = self.read([member(0), group(bounds=(1, 0, 0, 0))])
        self.assert_metadata_only(result, 'formula_group_definition_invalid')

    def test_invalid_merge_metadata_keeps_value_coverage(self):
        result = self.read([number_record(0, 0), rec(0x00E5, b'\x00')])
        self.assert_metadata_only(result, 'merge_record_invalid')

    def test_orphan_rule_metadata_keeps_value_coverage(self):
        result = self.read([number_record(0, 0), rec(0x01B1, b'\x00')])
        self.assert_metadata_only(result, 'orphan_rule_record')

    def test_invalid_hyperlink_metadata_keeps_value_coverage(self):
        result = self.read([number_record(0, 0), rec(0x01B8, b'\x00' * 10)])
        self.assert_metadata_only(result, 'hlink_invalid')

    def test_invalid_rule_group_metadata_keeps_value_coverage(self):
        result = self.read([number_record(0, 0), rec(0x01B0, b'\x00')])
        self.assert_metadata_only(result, 'bounded_rule_group_validation_failed')

    def test_note_record_outside_drawing_keeps_value_coverage(self):
        result = self.read([number_record(0, 0), rec(0x001C, b'\x00')])
        self.assert_metadata_only(result, 'note_record_outside_drawing')

    def test_ooxml_merge_limit_metadata_keeps_value_coverage(self):
        sheet_extra = ('<mergeCells count="2"><mergeCell ref="A1:B1"/>'
                       '<mergeCell ref="C1:D1"/></mergeCells>')
        data = package(cells='<c r="A1"><v>42</v></c>',
                       sheet_extra=sheet_extra)
        with patch('wps_ooxml_worksheet.MAX_MERGES', 1):
            result = read_bytes(data)
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 42)
        self.assertEqual(result['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertFalse(result['omissions_affect_cell_values'])
        self.assertIn('merge_count_limit_reached',
                      [item['code'] for item in result['issues']])

    def test_unknown_biff_record_remains_value_uncertain(self):
        result = self.read([number_record(0, 0), rec(0x1234, b'x')])
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 42)
        self.assertEqual(result['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')
        self.assertTrue(result['omissions_affect_cell_values'])
        self.assertIn('unknown_biff_record_salvaged',
                      [item['code'] for item in result['issues']])


if __name__ == '__main__':
    unittest.main()
