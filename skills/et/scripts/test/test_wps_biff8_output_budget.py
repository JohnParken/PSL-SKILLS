#!/usr/bin/env python3
"""Value text and display/format text use separate budgets."""
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_wps_biff8 import cell, rec, string, workbook
from test_wps_biff8_hyperlinks import HLINK, hlink, hyperlink_string
from test_wps_ooxml_sheet import (R, STYLES_TYPE, T, build_package, package,
                                  text_parts_of)
from read_et import read_bytes
from wps_biff8 import parse_stream
from wps_result import compact_result


def custom_workbook(values, fmt):
    extra = (rec(0x041E, struct.pack('<H', 165) + string(fmt))
             + rec(0x00E0, bytes(2) + struct.pack('<H', 165) + bytes(16)))
    records = [rec(0x0203, cell(0, column, 2) + struct.pack('<d', value))
               for column, value in enumerate(values)]
    return workbook([b''.join(records)], extra_global=extra)


def many_custom_workbook(count, fmt):
    extra = (rec(0x041E, struct.pack('<H', 165) + string(fmt))
             + rec(0x00E0, bytes(2) + struct.pack('<H', 165) + bytes(16)))
    records = [rec(0x0203, cell(index // 256, index % 256, 2)
                   + struct.pack('<d', index)) for index in range(count)]
    return workbook([b''.join(records)], extra_global=extra)


def ooxml_with_custom_format(fmt, cells='<c r="A1" s="0"><v>1</v></c>'
                                         '<c r="B1" s="0"><v>2</v></c>'):
    styles = (f'<styleSheet xmlns="{T}"><numFmts count="1">'
              f'<numFmt numFmtId="165" formatCode="{fmt}"/></numFmts>'
              '<cellXfs count="1"><xf numFmtId="165"/></cellXfs></styleSheet>')
    parts = text_parts_of(package(cells=cells))
    parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace(
        '</Types>', f'<Override PartName="/xl/styles.xml" '
                    f'ContentType="{STYLES_TYPE}"/></Types>')
    parts['xl/_rels/workbook.xml.rels'] = parts[
        'xl/_rels/workbook.xml.rels'].replace(
            '</Relationships>', f'<Relationship Id="sty" Type="{R}/styles" '
                                'Target="styles.xml"/></Relationships>')
    parts['xl/styles.xml'] = styles
    return build_package(parts)


def codes(result):
    return [item['code'] for item in result['issues']]


class OutputTextBudgetTests(unittest.TestCase):
    def test_repeated_format_is_charged_once_to_display_not_value_budget(self):
        data = custom_workbook([1, 2], 'X' * 255)
        with patch('wps_biff8.MAX_OUTPUT_TEXT_UNITS', 1), \
                patch('wps_biff8.MAX_DISPLAY_TEXT_UNITS', 300):
            result = parse_stream(data)
        cells = result['sheets'][0]['cells']
        self.assertEqual([cells['A1']['value'], cells['B1']['value']], [1, 2])
        self.assertEqual(cells['A1']['custom_format_raw'], 'X' * 255)
        self.assertEqual(cells['B1']['custom_format_raw'], 'X' * 255)
        self.assertEqual(result['output_text_budget']['display_units'], 255)
        self.assertFalse(result['output_text_budget']['display_limited'])
        self.assertNotIn('display_text_budget_truncated', codes(result))

    def test_ooxml_repeated_format_uses_display_budget_not_value_budget(self):
        data = ooxml_with_custom_format('X' * 255)
        with patch('wps_ooxml_text.MAX_DISPLAY_TEXT', 300):
            result = read_bytes(data)
        cells = result['sheets'][0]['cells']
        self.assertEqual([cells['A1']['value'], cells['B1']['value']], [1, 2])
        self.assertEqual(cells['A1']['custom_format_raw'], 'X' * 255)
        self.assertEqual(result['text_budget']['display_units'], 255)
        self.assertFalse(result['text_budget']['display_limited'])

    def test_ooxml_display_limit_omits_format_but_keeps_values(self):
        data = ooxml_with_custom_format('format')
        with patch('wps_ooxml_text.MAX_DISPLAY_TEXT', 1):
            result = read_bytes(data)
        cells = result['sheets'][0]['cells']
        self.assertEqual([cells['A1']['value'], cells['B1']['value']], [1, 2])
        self.assertIsNone(cells['A1']['custom_format_raw'])
        self.assertTrue(cells['A1']['custom_format_raw_omitted'])
        self.assertEqual(result['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertIn('display_text_budget_truncated', codes(result))
        self.assertNotIn('165', result['custom_number_formats'])

    def test_compact_uses_top_level_format_table_for_repeated_formats(self):
        result = parse_stream(custom_workbook([1, 2], 'format'))
        compact = compact_result(result)
        cells = compact['sheets'][0]['cells']
        self.assertNotIn('custom_format_raw', cells['A1'])
        self.assertTrue(cells['A1']['custom_format_raw_omitted'])
        self.assertEqual(compact['custom_number_formats']['165'], 'format')

    def test_hyperlink_text_uses_display_budget_without_dropping_value(self):
        records = [rec(HLINK, hlink(0x0008, hyperlink_string('Sheet1!B2'))),
                   rec(0x0203, cell(0, 0) + struct.pack('<d', 7))]
        with patch('wps_biff8.MAX_DISPLAY_TEXT_UNITS', 1):
            result = parse_stream(workbook([b''.join(records)], strings=[]))
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 7)
        link = result['sheets'][0]['hyperlinks'][0]
        self.assertTrue(link['link_text_omitted'])
        self.assertIsNone(link['location'])
        self.assertEqual(result['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertIn('display_text_budget_truncated', codes(result))

    def test_many_repeated_formats_do_not_fail_the_value_read(self):
        data = many_custom_workbook(32897, 'X' * 255)
        result = parse_stream(data)
        self.assertEqual(result['cell_count'], 32897)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['output_text_budget']['display_units'], 255)
        self.assertFalse(result['output_text_budget']['display_limited'])
        self.assertEqual(result['sheets'][0]['cells']['A1']['custom_format_raw'],
                         'X' * 255)

    def test_display_limit_omits_format_text_but_keeps_all_values(self):
        data = custom_workbook([1, 2], 'format')
        with patch('wps_biff8.MAX_DISPLAY_TEXT_UNITS', 1):
            result = parse_stream(data)
        cells = result['sheets'][0]['cells']
        self.assertEqual([cells['A1']['value'], cells['B1']['value']], [1, 2])
        self.assertIsNone(cells['A1']['custom_format_raw'])
        self.assertTrue(cells['A1']['custom_format_raw_omitted'])
        self.assertEqual(result['cell_value_coverage'],
                         'complete_within_declared_sheets')
        self.assertTrue(codes(result).count('display_text_budget_truncated') >= 1)
        self.assertNotIn('165', result['custom_number_formats'])
        self.assertIn('165', result['custom_number_formats_omitted'])
        self.assertTrue(result['output_text_budget']['display_limited'])


if __name__ == '__main__':
    unittest.main()
