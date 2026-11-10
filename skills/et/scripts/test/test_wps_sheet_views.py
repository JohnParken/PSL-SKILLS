"""Independent BIFF8 and OOXML fixtures for saved worksheet view metadata."""
from decimal import Decimal
from pathlib import Path
import struct
import sys
import unittest
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from et_table_preview import preview
from read_et import read_bytes
from test_wps_biff8 import cell, rec, workbook
from test_wps_cfb import synthetic_cfb
from test_wps_ooxml_sheet import S, T, package
from wps_access import ParseError


def window(flags=0):
    return rec(0x023E, struct.pack('<9H', flags, 0, 0, 64, 0, 0, 0, 0, 0))


def pane(columns, rows, active):
    return rec(0x0041, struct.pack('<4H2B', columns, rows, 29, 12, active, 0))


def selection(pane_id, row, col):
    return rec(0x001D, struct.pack('<B6H2B', pane_id, row, col, 0, 1, row, row, col, col))


def biff(views, *, compatibility='data', trailing=b''):
    number = rec(0x0203, cell(0, 0) + struct.pack('<d', 19.25))
    stream = workbook(sheet_records=[number + views + trailing], strings=[])
    return read_bytes(synthetic_cfb({'Workbook': stream}), compatibility=compatibility)


def ooxml(views, *, strict=False):
    namespace = S if strict else T
    sheet = (f'<worksheet xmlns="{namespace}">{views}<sheetData>'
             '<row r="1"><c r="A1"><v>19.25</v></c></row></sheetData></worksheet>')
    return read_bytes(package(strict=strict, overrides={'xl/worksheets/sheet1.xml': sheet}))


class BiffViewTests(unittest.TestCase):
    def test_frozen_view_uses_active_pane_and_split_counts_in_both_modes(self):
        records = (window(0x0108) + rec(0x00A0, struct.pack('<2H', 1, 1)) + pane(2, 3, 0)
                   + selection(1, 0, 4) + selection(2, 4, 1) + selection(0, 7, 3) * 2)
        for compatibility in ('data', 'strict'):
            with self.subTest(compatibility=compatibility):
                result = biff(records, compatibility=compatibility)
                sheet = result['sheets'][0]
                view, = sheet['views']
                self.assertEqual({key: value for key, value in view.items() if key != 'record_offset'},
                                 {'freeze_row': 3, 'freeze_col': 2, 'active_pane': 'bottomRight', 'selected_cell': 'D8'})
                self.assertEqual(sheet['cells']['A1']['value'], Decimal('19.25'))
                self.assertFalse(result['omissions_affect_cell_values'])
                if compatibility == 'data':
                    self.assertFalse(preview(result, {'sheet': '中文表1', 'range': 'A1:A1'})['header_rows_inferred'])

    def test_unfrozen_split_and_single_axis_views_stay_distinct(self):
        records = (window() + pane(2160, 0, 1) + selection(1, 7, 5)
                   + window(0x08) + pane(0, 4, 2) + selection(2, 5, 0)
                   + window(0x08) + pane(2, 0, 1) + selection(1, 0, 3)
                   + window() + selection(3, 0, 255))
        views = biff(records)['sheets'][0]['views']
        self.assertEqual([(view['freeze_row'], view['freeze_col'], view['selected_cell']) for view in views],
                         [(0, 0, 'F8'), (4, 0, 'A6'), (0, 2, 'D1'), (0, 0, 'IV1')])
        self.assertEqual(biff(b'')['sheets'][0]['views'], [])

    def test_selections_outside_window_or_from_another_pane_are_not_borrowed(self):
        records = window(0x08) + pane(2, 3, 0) + selection(1, 1, 3) + rec(0x01AA) + selection(0, 8, 8)
        result = biff(records)
        view, = result['sheets'][0]['views']
        self.assertEqual((view['freeze_row'], view['freeze_col'], view['selected_cell']), (3, 2, None))

    def test_damaged_view_metadata_has_diagnostics_and_retains_cell_values(self):
        records = window(0x08) + pane(2, 3, 0) + selection(0, 6, 2) + rec(0x001D, b'\0')
        result = biff(records)
        view, = result['sheets'][0]['views']
        self.assertEqual((view['freeze_row'], view['freeze_col'], view['selected_cell']), (3, 2, None))
        self.assertIn('invalid_selection_record', view['issues'])
        self.assertIn('saved_sheet_view_partially_read', {item['code'] for item in result['issues']})
        self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], Decimal('19.25'))
        missing_pane, = biff(window(0x08) + selection(3, 1, 1))['sheets'][0]['views']
        self.assertEqual((missing_pane['freeze_row'], missing_pane['freeze_col'], missing_pane['selected_cell']), (None, None, None))

    def test_malformed_pane_or_core_cell_is_rejected(self):
        with self.assertRaisesRegex(ParseError, 'Pane payload') as raised:
            biff(window(0x08) + rec(0x0041, b'\0'))
        self.assertEqual(raised.exception.location['record_id'], '0x41')
        with self.assertRaises(ParseError) as raised:
            biff(window() + selection(3, 0, 0), trailing=rec(0x0203, cell(0, 0)))
        self.assertEqual(raised.exception.location['record_id'], '0x203')


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class OoxmlViewTests(unittest.TestCase):
    def test_frozen_and_frozen_split_views_across_namespaces(self):
        metadata = ('<sheetViews><sheetView workbookViewId="0"><pane state="frozen" xSplit="2.0" ySplit="3" '
                    'topLeftCell="C30" activePane="bottomRight"/><selection pane="topRight" activeCell="D1"/>'
                    '<selection pane="bottomRight" activeCell="F31" sqref="E31:F32"/></sheetView>'
                    '<sheetView workbookViewId="1"><pane state="frozenSplit" ySplit="5" activePane="bottomLeft"/>'
                    '<selection pane="bottomLeft" activeCell="A6"/></sheetView></sheetViews>')
        for strict in (False, True):
            with self.subTest(strict_namespace=strict):
                result = ooxml(metadata, strict=strict)
                self.assertEqual(result['sheets'][0]['views'], [
                    {'workbook_view_id': 0, 'freeze_row': 3, 'freeze_col': 2, 'active_pane': 'bottomRight', 'selected_cell': 'F31'},
                    {'workbook_view_id': 1, 'freeze_row': 5, 'freeze_col': 0, 'active_pane': 'bottomLeft', 'selected_cell': 'A6'},
                ])
                projected = preview(result, {'sheet': 'Data', 'range': 'A1:A2'})
                self.assertFalse(projected['header_rows_inferred'])
                self.assertEqual(projected['rows'][0]['cells']['A']['value'], Decimal('19.25'))

    def test_split_is_not_frozen_and_view_definitions_follow_workbook_ids(self):
        metadata = ('<sheetViews><sheetView workbookViewId="0"><selection activeCell="A1"/></sheetView>'
                    '<sheetView workbookViewId="1"><pane state="frozen" xSplit="2" activePane="topRight"/>'
                    '<selection pane="bottomLeft" activeCell="A4"/></sheetView>'
                    '<sheetView workbookViewId="0"><pane ySplit="1600.5" activePane="bottomLeft"/>'
                    '<selection pane="bottomLeft" activeCell="XFD1048576"/></sheetView>'
                    '<sheetView workbookViewId="2"><selection sqref="D5"/></sheetView></sheetViews>')
        views = ooxml(metadata)['sheets'][0]['views']
        self.assertEqual([(view['workbook_view_id'], view['freeze_row'], view['freeze_col'], view['selected_cell']) for view in views],
                         [(0, 0, 0, 'XFD1048576'), (1, 0, 2, None), (2, 0, 0, None)])
        self.assertEqual(ooxml('')['sheets'][0]['views'], [])

    def test_malformed_optional_view_preserves_cell_data(self):
        metadata = ('<sheetViews><sheetView workbookViewId="0"><pane state="frozen" ySplit="1.5"/>'
                    '<selection activeCell="XFE1"/></sheetView></sheetViews>')
        result = ooxml(metadata)
        sheet, = result['sheets']
        view, = sheet['views']
        self.assertEqual((view['freeze_row'], view['freeze_col'], view['selected_cell']), (None, None, None))
        self.assertEqual(view['issues'], ['invalid_active_cell', 'invalid_frozen_pane'])
        self.assertEqual(result['status'], 'partial')
        self.assertIn('saved_sheet_view_partially_read', {item['code'] for item in result['issues']})
        self.assertEqual(sheet['cells'], ooxml('')['sheets'][0]['cells'])

    def test_conflicting_selections_do_not_report_a_current_cell(self):
        metadata = ('<sheetViews><sheetView workbookViewId="0"><selection activeCell="A1"/>'
                    '<selection activeCell="B2"/></sheetView></sheetViews>')
        view, = ooxml(metadata)['sheets'][0]['views']
        self.assertIsNone(view['selected_cell'])
        self.assertEqual(view['issues'], ['conflicting_active_cell'])


if __name__ == '__main__':
    unittest.main()
