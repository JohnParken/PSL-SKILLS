"""Synthetic row-release and security parity tests; no user documents."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_ooxml import IssueLog, XmlBudget
from wps_ooxml_worksheet import read_worksheet
from wps_ooxml_stream import worksheet_rows
from wps_ooxml_text import TRANS


def document(body):
    """Build small independent worksheet XML."""
    return ('<worksheet xmlns="' + TRANS + '">' + body + '</worksheet>').encode()


class StreamingRowsTests(unittest.TestCase):
    def test_rows_consumed_in_order_metadata_retained(self):
        source = document('<cols><col min="1" max="1" width="12"/></cols>'
                          '<sheetData><row r="1"><c r="A1"><v>7</v></c></row>'
                          '<row r="2"><c r="A2"><v>8</v></c></row></sheetData>'
                          '<mergeCells><mergeCell ref="B1:C1"/></mergeCells>')
        seen = []
        root = worksheet_rows(source, lambda row: seen.append((row.get('r'), ''.join(row.itertext()))))
        self.assertEqual(seen, [('1', '7'), ('2', '8')])
        self.assertEqual(len(root.find('{' + TRANS + '}sheetData')), 0)
        self.assertEqual(root.find('{' + TRANS + '}cols')[0].get('width'), '12')
        self.assertEqual(root.find('{' + TRANS + '}mergeCells')[0].get('ref'), 'B1:C1')

    def test_bad_tail_never_calls_consumer(self):
        seen = []
        data = document('<sheetData><row r="1"/></sheetData>') + b'<broken'
        with self.assertRaises(ValueError):
            worksheet_rows(data, seen.append)
        self.assertEqual(seen, [])

    def test_dtd_and_entity_rejected_before_consumer(self):
        seen = []
        data = b'<!DOCTYPE worksheet [<!ENTITY x "boom">]>' + document('<sheetData/>')
        with self.assertRaisesRegex(ValueError, 'DTD'):
            worksheet_rows(data, seen.append)
        self.assertEqual(seen, [])

    def test_shared_budget_consumed_once_and_enforced(self):
        source = document('<sheetData><row r="1"/></sheetData>')
        budget = XmlBudget()
        worksheet_rows(source, lambda row: None, budget=budget)
        self.assertEqual(budget.elements, 3)
        with patch('wps_ooxml.MAX_XML_ELEMENTS', 5):
            with self.assertRaisesRegex(ValueError, 'shared elements'):
                worksheet_rows(source, lambda row: None, budget=budget)

    def test_foreign_wrapped_rows_not_promoted(self):
        seen = []
        source = document('<wrapper><sheetData><row r="1"/></sheetData></wrapper>'
                          '<sheetData><row r="2"/></sheetData>')
        root = worksheet_rows(source, lambda row: seen.append(row.get('r')))
        self.assertEqual(seen, ['2'])
        self.assertEqual(len(root[0][0]), 1)

    def test_existing_pages_reassemble_streamed_cells(self):
        from test_wps_ooxml_sheet import package, direct
        from et_read_view import project
        data = package(cells=''.join(
            '<c r="A' + str(i) + '"><v>' + str(i) + '</v></c>'
            for i in range(1, 8)))
        result = direct(data)
        cursor = None
        coordinates = []
        while True:
            view = project(result, data, {}, page_size=2, cursor=cursor)
            coordinates.extend(item['cell'] for item in view['items'])
            self.assertEqual(view['status'], result['status'])
            cursor = view['next_cursor']
            if cursor is None:
                break
        self.assertEqual(coordinates, ['A' + str(i) for i in range(1, 8)])

    def test_part_read_failure_preserves_diagnostic_contract(self):
        report = IssueLog()
        context = SimpleNamespace(report=report, sheet_name='Data', sheet_part='xl/worksheets/sheet1.xml',
                                  package=None, budget=XmlBudget())
        reason = 'missing part ' + 'x' * 200
        with patch('wps_ooxml_worksheet.read_part', side_effect=KeyError(reason)):
            self.assertIsNone(read_worksheet(context))
        issue = report.issues()[0]
        self.assertEqual(issue['code'], 'worksheet_part_not_read')
        self.assertEqual(issue['message'], 'Part was not read after a bounded read failure')
        self.assertEqual(issue['part'], context.sheet_part)
        self.assertEqual(issue['sheet'], 'Data')
        self.assertEqual(issue['reason'], str(KeyError(reason))[:120])
        self.assertEqual(issue['samples'][0]['part'], context.sheet_part)

    def test_xml_failure_preserves_diagnostic_contract(self):
        report = IssueLog()
        context = SimpleNamespace(report=report, sheet_name='Data', sheet_part='xl/worksheets/sheet1.xml',
                                  package=None, budget=XmlBudget())
        reason = 'Invalid XML: ' + 'x' * 200
        with patch('wps_ooxml_worksheet.read_part', return_value=b'<broken'):
            with patch('wps_ooxml_worksheet.prepare_worksheet', side_effect=ValueError(reason)):
                self.assertIsNone(read_worksheet(context))
        issue = report.issues()[0]
        self.assertEqual(issue['code'], 'worksheet_part_not_read')
        self.assertEqual(issue['message'], 'Part was not interpreted after a bounded XML failure')
        self.assertEqual(issue['part'], context.sheet_part)
        self.assertEqual(issue['sheet'], 'Data')
        self.assertEqual(issue['reason'], reason[:120])
        self.assertEqual(issue['samples'][0]['reason'], reason[:120])

    def test_wrong_root_does_not_consume_rows(self):
        seen = []
        source = document('<sheetData><row r="1"/></sheetData>').replace(b'worksheet', b'wrong')
        worksheet_rows(source, seen.append)
        self.assertEqual(seen, [])


if __name__ == '__main__':
    unittest.main()
