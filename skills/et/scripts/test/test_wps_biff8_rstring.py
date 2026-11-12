#!/usr/bin/env python3
"""RString (0x00D6) value extraction and rich-run diagnostics."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_wps_biff8 import cell, rec, workbook
from wps_biff8 import parse_stream


def number(row, column, value=7):
    return rec(0x0203, cell(row, column) + struct.pack('<d', value))


def rstring(text, runs=1, *, flag=1, run_tail=None):
    run_count = runs
    raw = text.encode('utf-16le' if flag else 'latin1')
    payload = (cell(0, 0) + struct.pack('<HB', len(text), flag) + raw
               + struct.pack('<H', run_count))
    if run_tail is not None:
        payload += run_tail
    else:
        for index in range(run_count):
            payload += struct.pack('<HH', 0, index)
    return rec(0x00D6, payload)


def read(records):
    return parse_stream(workbook([b''.join(records)], strings=[]))


def codes(result):
    return [item['code'] for item in result['issues']]


class RStringTests(unittest.TestCase):
    def test_plain_text_and_runs_are_returned_with_later_cell(self):
        result = read([rstring('Hi', runs=2), number(0, 1)])
        cells = result['sheets'][0]['cells']
        self.assertEqual(cells['A1']['value'], 'Hi')
        self.assertEqual(cells['A1']['rich_text_runs'],
                         [{'character_offset_raw': 0, 'font_index_raw': 0},
                          {'character_offset_raw': 0, 'font_index_raw': 1}])
        self.assertEqual(cells['B1']['value'], 7)
        self.assertEqual(result['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertFalse(result['omissions_affect_cell_values'])
        self.assertIn('rstring_rich_format_not_applied', codes(result))

    def test_malformed_run_tail_keeps_plain_text(self):
        result = read([rstring('Hi', runs=1, run_tail=b'\x00'),
                       number(0, 1)])
        cell_value = result['sheets'][0]['cells']['A1']
        self.assertEqual(cell_value['value'], 'Hi')
        self.assertEqual(cell_value['runs_status'], 'invalid')
        self.assertEqual(result['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertIn('rstring_format_runs_invalid', codes(result))

    def test_compressed_flag_returns_latin1_text(self):
        result = read([rstring('Hi', runs=0, flag=0)])
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 'Hi')

    def test_truncated_text_is_omitted_with_uncertain_coverage(self):
        payload = cell(0, 0) + struct.pack('<HB', 5, 1) + b'\x00\x00'
        result = read([rec(0x00D6, payload), number(0, 1)])
        self.assertNotIn('A1', result['sheets'][0]['cells'])
        self.assertEqual(result['sheets'][0]['cells']['B1']['value'], 7)
        self.assertEqual(result['cell_value_coverage'],
                         'unverified_due_to_best_effort_salvage')
        self.assertIn('rstring_text_invalid', codes(result))


if __name__ == '__main__':
    unittest.main()
