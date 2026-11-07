#!/usr/bin/env python3
"""Regression coverage for operation-local ordered worksheet DOM indexes."""
import copy
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stdlib_xlsx import MAIN, Workbook


class ScanWorkbook(Workbook):
    """Pre-index lookup used as a structural parity oracle."""

    def _write_cell_node(self, sheet, ref, indexes):
        return self.cell_node(sheet, ref)


def spec(cells):
    return {"sheets": [{"name": "Data", "cells": cells}]}


class WriteIndexTests(unittest.TestCase):
    def pair(self, cells=None, body=None):
        base = Workbook.create(spec(cells or {}))
        parts = dict(base.parts)
        if body is not None:
            parts['xl/worksheets/sheet1.xml'] = (
                '<worksheet xmlns="' + MAIN + '">' + body + '</worksheet>'
            ).encode()
        return Workbook(copy.deepcopy(parts)), ScanWorkbook(copy.deepcopy(parts))

    def assert_equal(self, books):
        self.assertEqual(books[0].parts, books[1].parts)
        self.assertEqual(books[0].cells, books[1].cells)
        self.assertEqual(books[0].changed, books[1].changed)

    def test_create_order_style_and_boundaries(self):
        cells = {ref: {'value': i, 'style': 1} for i, ref in enumerate(
            ['C7', 'A1', 'XFD1048576', 'B1', 'A7', 'D3', 'C1', 'A2'])}
        books = [cls.create(spec(cells)) for cls in (Workbook, ScanWorkbook)]
        self.assert_equal(books)

    def test_repeated_patch_and_calculation(self):
        books = self.pair({'A1': {'value': 2}, 'B1': {'formula': 'A1*3'}})
        for book in books:
            for value in (4, 7):
                book.patch(spec({'A1': {'value': value}, 'C4': {'value': value}}))
                self.assertEqual(book.calculate()['status'], 'calculated_subset')
                self.assertEqual(book.cells['Data']['B1']['value'], Decimal(value * 3))
            book.calculate(values_only=True)
            self.assertIsNone(book.cells['Data']['B1']['formula'])
        self.assert_equal(books)

    def test_direct_dom_changes_between_operations(self):
        books = self.pair({'A1': {'value': 1}, 'B1': {'value': 2}})
        for book in books:
            book.patch(spec({'A1': {'value': 3}}))
            doc, cell = book.cell_node('Data', 'B1')
            cell.parentNode.removeChild(cell)
            doc, cell = book.cell_node('Data', 'D9')
            book.put_value(doc, cell, 99)
            book.flush()
            book._index()
            book.patch(spec({'B1': {'value': 5}, 'D9': {'formula': 'A1+B1'}}))
            book.calculate()
            self.assertEqual(book.cells['Data']['D9']['value'], Decimal(8))
        self.assert_equal(books)

    def test_unordered_duplicate_rows_and_mismatched_cells(self):
        bodies = [
            '<sheetData><row r="5"><c r="A5"/></row><row r="1"><c r="A1"/></row></sheetData>',
            '<sheetData><row r="1"><c r="A1"/></row><row r="1"><c r="B1"/></row></sheetData>',
            '<sheetData><row r="1"><c r="C1"/><c r="A1"/></row></sheetData>',
            '<sheetData><row r="1"><c r="A2"/></row></sheetData>',
            '<sheetData><row r="1"><c r="a1"/></row></sheetData>',
        ]
        for body in bodies:
            with self.subTest(body=body):
                books = self.pair(body=body)
                for book in books:
                    try:
                        book.patch(spec({'B1': {'value': 4}, 'A3': {'value': 6}}))
                    except ValueError:
                        # Duplicate addresses can arise in already-inconsistent input.
                        pass
                self.assert_equal(books)

    def test_lazy_malformed_row_error_parity(self):
        body = '<sheetData><row r="1"><c r="A1"/></row><row r="bad"/></sheetData>'
        for target in ('A1', 'A2'):
            with self.subTest(target=target):
                books = self.pair(body=body)
                outcomes = []
                for book in books:
                    try:
                        book.patch(spec({target: {'value': 8}}))
                        outcomes.append(None)
                    except ValueError as exc:
                        outcomes.append(str(exc))
                self.assertEqual(outcomes[0], outcomes[1])
                self.assert_equal(books)

    def test_merges_and_untouched_package_parts(self):
        body = ('<sheetData><row r="1"><c r="A1" s="1"/></row></sheetData>'
                '<mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>')
        books = self.pair(body=body)
        for book in books:
            original = dict(book.parts)
            book.patch(spec({'A1': {'value': 'anchor'}}))
            for part, data in original.items():
                if part != 'xl/worksheets/sheet1.xml':
                    self.assertEqual(book.parts[part], data)
            with self.assertRaisesRegex(ValueError, 'merged cell anchor'):
                book.patch(spec({'B1': {'value': 'invalid'}}))
        self.assert_equal(books)

    def test_unknown_formula_and_values_only_failure(self):
        books = self.pair({'A1': {'formula': 'NO_SUCH_FUNCTION(1)'}})
        reports = []
        for book in books:
            reports.append(book.calculate())
            self.assertEqual(reports[-1]['status'], 'incomplete')
            self.assertEqual(book.calculate(values_only=True)['status'], 'failed')
        self.assertEqual(reports[0], reports[1])
        self.assert_equal(books)

    def test_failed_patch_does_not_retain_an_index(self):
        books = self.pair({'A1': {'value': 1}})
        for book in books:
            with self.assertRaisesRegex(ValueError, 'Invalid style'):
                book.patch(spec({'D4': {'value': 2, 'style': 99999}}))
            doc, cell = book.cell_node('Data', 'D4')
            cell.parentNode.removeChild(cell)
            book.patch(spec({'D4': {'value': 7}}))
            self.assertEqual(book.cells['Data']['D4']['value'], Decimal(7))
        self.assert_equal(books)

    def test_dom_comments_extensions_and_insert_order(self):
        body = ('<sheetData><!--before--><row r="2"><!--cell--><c r="B2" s="1"/>'
                '<extLst/></row><row r="4"/></sheetData><extLst/>')
        books = self.pair(body=body)
        for book in books:
            book.patch(spec({'A2': {'value': 1}, 'D2': {'value': 4}, 'C3': {'value': 3}, 'A6': {'value': 6}}))
        self.assert_equal(books)

    def test_invalid_address_and_duplicate_spec_rejected(self):
        for ref in ('XFE1', 'A1048577', 'A0'):
            for cls in (Workbook, ScanWorkbook):
                with self.assertRaises(ValueError):
                    cls.create(spec({ref: {'value': 1}}))
        for cls in (Workbook, ScanWorkbook):
            with self.assertRaises(ValueError):
                cls.create(spec({'a1': {'value': 1}, 'A1': {'value': 2}}))


if __name__ == '__main__':
    unittest.main()
