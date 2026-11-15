"""Public BIFF5 JSON adaptation preserves old grid and source evidence gates."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stdlib_xlsx import address
from xlsx_wps_result import load_result_bytes


class Biff5ResultTests(unittest.TestCase):
    def source(self):
        return {'schema_version': 'biff5_readonly_v1', 'biff_parsed': True,
                'date_system': '1900', 'status': 'partial', 'issues': [{'code': 'biff5_subset'}],
                'cell_value_coverage': 'complete_within_declared_sheets',
                'omissions_affect_cell_values': False,
                'sheets': [{'name': 'S', 'cells': {'IV16384': {'type': 'number', 'value': '1.25000000000000001'}}}]}

    def load(self, value):
        return load_result_bytes(json.dumps(value).encode('utf-8'), address)

    def test_distinct_schema_grid_precision_and_evidence(self):
        source = self.source()
        book = self.load(source)
        self.assertEqual(book.wps_input_schema, 'biff5_readonly_v1')
        self.assertEqual(str(book.cells['S']['IV16384']['value']), '1.25000000000000001')
        self.assertEqual(book.wps_source_status, 'partial')
        self.assertEqual(book.wps_issues, source['issues'])
        for coordinate in ('A16385', 'IW1'):
            bad = copy.deepcopy(source)
            bad['sheets'][0]['cells'] = {coordinate: {'type': 'number', 'value': '1'}}
            with self.assertRaises(ValueError):
                self.load(bad)

    def test_invalid_evidence_and_missing_date_are_rejected(self):
        for updates in ({'biff_parsed': False}, {'date_system': 'unknown'},
                        {'omissions_affect_cell_values': True}, {'status': 'corrupt'}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.load({**self.source(), **updates})

    def test_uncertain_coverage_is_preserved_not_promoted(self):
        source = self.source()
        source.update(cell_value_coverage='unverified_due_to_best_effort_salvage',
                      omissions_affect_cell_values=True)
        book = self.load(source)
        self.assertTrue(book.wps_source_coverage['omissions_affect_cell_values'])
        self.assertEqual(book.wps_source_coverage['cell_value_coverage'], source['cell_value_coverage'])

    def test_analysis_selection_obeys_legacy_grid_including_empty_cells(self):
        from xlsx_analysis import region_from_book
        book = self.load(self.source())
        table = region_from_book(book, 'S', 'IV16384', header=False)
        self.assertEqual(str(table[2][0][1][0][1]), '1.25000000000000001')
        for cell_range in ('IW1', 'A16385', 'IV16384:IW16384', 'A16384:A16385'):
            with self.subTest(cell_range=cell_range), self.assertRaisesRegex(ValueError, 'BIFF5 analysis range'):
                region_from_book(book, 'S', cell_range, header=False)


if __name__ == '__main__':
    unittest.main()
