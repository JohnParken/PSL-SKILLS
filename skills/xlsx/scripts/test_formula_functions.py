#!/usr/bin/env python3
"""Tests for the bounded conditional and array formula functions."""

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from formula_engine import FormulaError, evaluate  # noqa: E402


class FormulaFunctionTests(unittest.TestCase):
    def setUp(self):
        quantities = (5, 4, 15, 3, 22, 12, 10, 33)
        products = (
            "Apples", "Apples", "Artichokes", "Artichokes",
            "Bananas", "Bananas", "Carrots", "Carrots",
        )
        people = ("Tom", "Sarah", "Tom", "Sarah", "Tom", "Sarah", "Tom", "Sarah")
        self.cells = {}
        for row, value in enumerate(quantities, start=1):
            self.cells[("Current", f"A{row}")] = value
            self.cells[("Current", f"B{row}")] = products[row - 1]
            self.cells[("Current", f"C{row}")] = people[row - 1]
        self.cells[("Current", "D1")] = "Apples"
        self.calls = []

        def resolve(sheet, address):
            self.calls.append((sheet, address))
            return self.cells.get((sheet, address))

        self.resolve = resolve

    def value(self, formula):
        return evaluate(formula, "Current", self.resolve)

    def assert_error(self, formula, code="unsupported"):
        with self.assertRaises(FormulaError) as caught:
            self.value(formula)
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(str(caught.exception))
        return caught.exception

    def test_sumif_with_and_without_sum_range_and_no_matches(self):
        self.assertEqual(
            self.value('SUMIF(B1:B8,"Bananas",A1:A8)'),
            Decimal(34),
        )
        self.assertEqual(self.value('SUMIF(A1:A8,">10")'), Decimal(82))
        self.assertEqual(
            self.value('SUMIF(B1:B8,"No fruit",A1:A8)'),
            Decimal(0),
        )

    def test_sumifs_official_style_wildcard_and_inequality_fixtures(self):
        self.assertEqual(
            self.value('SUMIFS(A1:A8,B1:B8,"=A*",C1:C8,"Tom")'),
            Decimal(20),
        )
        self.assertEqual(
            self.value('SUMIFS(A1:A8,B1:B8,"<>Bananas",C1:C8,"Tom")'),
            Decimal(30),
        )
        self.assertEqual(
            self.value('SUMIFS(A1:A8,B1:B8,"Missing",C1:C8,"Tom")'),
            Decimal(0),
        )

    def test_countif_and_countifs_with_multiple_conditions(self):
        self.assertEqual(self.value('COUNTIF(B1:B8,"A*")'), Decimal(4))
        self.assertEqual(self.value('COUNTIF(A1:A8,">=12")'), Decimal(4))
        self.assertEqual(
            self.value('COUNTIFS(B1:B8,"A*",C1:C8,"Tom")'),
            Decimal(2),
        )
        self.assertEqual(
            self.value('COUNTIFS(B1:B8,"Missing",C1:C8,"Tom")'),
            Decimal(0),
        )

    def test_criteria_cell_reference_remains_a_scalar(self):
        self.assertEqual(self.value("COUNTIF(B1:B8,D1)"), Decimal(2))
        self.assertEqual(self.value("SUMIF(B1:B8,D1,A1:A8)"), Decimal(9))
        self.assertEqual(self.calls[-1], ("Current", "D1"))
        self.cells[("Current", "E1")] = 12
        self.assertEqual(self.value("COUNTIF(A1:A8,E1)"), Decimal(1))

    def test_cross_sheet_and_single_cell_ranges(self):
        self.cells[("Data Sheet", "A1")] = "first"
        self.cells[("Data Sheet", "A2")] = "second"
        self.cells[("Data Sheet", "B1")] = Decimal("1.25")
        self.cells[("Data Sheet", "B2")] = Decimal("2.75")
        self.assertEqual(
            self.value("SUMIF('Data Sheet'!A1:A2,\"s*\",'Data Sheet'!B1:B2)"),
            Decimal("2.75"),
        )
        self.assertEqual(self.value('COUNTIF(B1,"Apples")'), Decimal(1))
        self.assertEqual(self.value("SUMIF(A1,5)"), Decimal(5))
        self.assertEqual(
            self.value('COUNTIFS(B1,"Apples",C1,"Tom")'),
            Decimal(1),
        )

    def test_wildcards_are_case_insensitive_and_tilde_escapes(self):
        for row, value in enumerate(("*", "?", "~", "AbC", "abc", "x12"), start=1):
            self.cells[("Current", f"D{row}")] = value
        self.assertEqual(self.value('COUNTIF(D1:D6,"~*")'), Decimal(1))
        self.assertEqual(self.value('COUNTIF(D1:D6,"~?")'), Decimal(1))
        self.assertEqual(self.value('COUNTIF(D1:D6,"~~")'), Decimal(1))
        self.assertEqual(self.value('COUNTIF(D1:D6,"A?C")'), Decimal(2))
        self.assertEqual(self.value('COUNTIF(D1:D6,"a*")'), Decimal(2))

    def test_wildcard_matching_has_a_bounded_work_budget(self):
        self.cells[("Current", "D1")] = "a" * 1000
        self.cells[("Current", "E1")] = "a" * 1000
        pattern = "*" + ("a" * 98) + "b"
        formula = 'COUNTIFS(D1,"' + pattern + '",E1,"' + pattern + '")'
        with patch("formula_engine.MAX_CRITERIA_MATCH_WORK", 150_000):
            self.assert_error(formula, "unsupported")

    def test_empty_and_nonempty_criteria_match_blanks_as_specified(self):
        self.cells.update({
            ("Current", "D1"): None,
            ("Current", "D2"): "",
            ("Current", "D3"): "x",
            ("Current", "D4"): Decimal(1),
        })
        self.assertEqual(self.value('COUNTIF(D1:D4,"")'), Decimal(2))
        self.assertEqual(self.value('COUNTIF(D1:D4,"=")'), Decimal(2))
        self.assertEqual(self.value('COUNTIF(D1:D4,"<>")'), Decimal(2))
        self.assertEqual(self.value('COUNTIF(D1:D3,"<>x")'), Decimal(2))

    def test_missing_cells_differ_from_empty_text_for_wildcards(self):
        self.cells.update({
            ("Current", "D1"): None,
            ("Current", "D2"): "",
            ("Current", "D3"): "abc",
            ("Current", "E1"): Decimal(1),
            ("Current", "E2"): Decimal(2),
            ("Current", "E3"): Decimal(3),
        })
        self.assertEqual(self.value('COUNTIF(D1:D3,"*")'), Decimal(2))
        self.assertEqual(self.value('COUNTIF(D1:D3,"<>*")'), Decimal(1))
        self.assertEqual(self.value('COUNTIF(D1:D3,"=")'), Decimal(2))
        self.assertEqual(self.value('SUMIF(D1:D3,"*",E1:E3)'), Decimal(5))
        self.assertEqual(self.value('SUMIF(D1:D3,"<>*",E1:E3)'), Decimal(1))

    def test_numeric_criteria_operators_and_exponent_syntax(self):
        self.cells[("Current", "D1")] = Decimal("-2")
        self.cells[("Current", "D2")] = Decimal("2.5")
        self.cells[("Current", "D3")] = Decimal("3")
        self.assertEqual(self.value('COUNTIF(D1:D3,">=+2.5e0")'), Decimal(2))
        self.assertEqual(self.value('COUNTIF(D1:D3,"<3e0")'), Decimal(2))
        self.assertEqual(self.value('COUNTIF(D1:D3,"-2.0")'), Decimal(1))
        self.assertEqual(self.value('COUNTIF(D1:D3,"<>2.5")'), Decimal(2))

    def test_255_character_criterion_is_supported(self):
        criterion = "x" * 255
        self.cells[("Current", "D1")] = criterion
        self.assertEqual(
            self.value('COUNTIF(D1,"' + criterion + '")'),
            Decimal(1),
        )

    def test_sumif_preserves_exact_cents_cancellation(self):
        self.cells.update({
            ("Current", "D1"): "keep",
            ("Current", "D2"): "keep",
            ("Current", "D3"): "keep",
            ("Current", "E1"): Decimal("10000000000000000000000000000"),
            ("Current", "E2"): Decimal("0.01"),
            ("Current", "E3"): Decimal("-10000000000000000000000000000"),
        })
        self.assertEqual(self.value('SUMIF(D1:D3,"keep",E1:E3)'), Decimal("0.01"))

    def test_sumproduct_exact_products_and_cancellation(self):
        self.cells.update({
            ("Current", "D1"): Decimal("10000000000000000000000000000"),
            ("Current", "D2"): Decimal("0.01"),
            ("Current", "D3"): Decimal("-10000000000000000000000000000"),
            ("Current", "E1"): Decimal(1),
            ("Current", "E2"): Decimal(1),
            ("Current", "E3"): Decimal(1),
        })
        self.assertEqual(self.value("SUMPRODUCT(D1:D3,E1:E3)"), Decimal("0.01"))
        self.assertEqual(self.value("SUMPRODUCT(D2,E2)"), Decimal("0.01"))

    def test_sumproduct_treats_all_nonnumeric_entries_as_zero(self):
        self.cells.update({
            ("Current", "D1"): Decimal(100),
            ("Current", "D2"): Decimal(2),
            ("Current", "E1"): True,
            ("Current", "E2"): Decimal(3),
        })
        self.assertEqual(self.value("SUMPRODUCT(D1:D2,E1:E2)"), Decimal(6))
        self.assertEqual(self.value("SUMPRODUCT(E1)"), Decimal(0))

    def test_sumproduct_reports_exact_multiplication_and_accumulation_limits(self):
        large_factor = Decimal("9" * 501)
        self.cells[("Current", "D1")] = large_factor
        self.cells[("Current", "E1")] = large_factor
        error = self.assert_error("SUMPRODUCT(D1,E1)", "calculation_error")
        self.assertIn("1000-digit", str(error))

        self.cells[("Current", "D1")] = Decimal("9" * 1000)
        self.cells[("Current", "D2")] = Decimal(2)
        self.cells[("Current", "E1")] = Decimal(1)
        self.cells[("Current", "E2")] = Decimal(1)
        error = self.assert_error("SUMPRODUCT(D1:D2,E1:E2)", "calculation_error")
        self.assertIn("1000-digit", str(error))

    def test_conditional_function_shape_and_cell_count_limits(self):
        self.assert_error(
            "SUMIF(A1:B2,1,A1:A4)",
            "unsupported",
        )
        self.assert_error("COUNTIF(A1:CV1001,1)", "unsupported")
        self.assert_error(
            "SUMIFS(A1:CV1000,A1:CV1000,1,A1:CV1000,1)",
            "unsupported",
        )
        self.assertEqual(
            evaluate(
                "COUNTIFS(A1:CV1000,1,A1:CV1000,1)",
                "Current",
                lambda sheet, address: Decimal(1),
            ),
            Decimal(100_000),
        )

    def test_condition_pair_and_sumproduct_range_count_limits(self):
        valid_pairs = ",".join(["A1", "1"] * 127)
        self.assertEqual(self.value(f"COUNTIFS({valid_pairs})"), Decimal(0))
        too_many_pairs = ",".join(["A1", "1"] * 128)
        self.assert_error(f"COUNTIFS({too_many_pairs})", "unsupported")
        self.assertEqual(
            self.value("SUMIFS(A1," + ",".join(["A1", "5"] * 127) + ")"),
            Decimal(5),
        )
        self.assert_error(
            "SUMIFS(A1," + ",".join(["A1", "5"] * 128) + ")",
            "unsupported",
        )

        self.cells[("Current", "A1")] = 1
        self.assertEqual(
            self.value("SUMPRODUCT(" + ",".join(["A1"] * 255) + ")"),
            Decimal(1),
        )
        self.assert_error(
            "SUMPRODUCT(" + ",".join(["A1"] * 256) + ")",
            "calculation_error",
        )

    def test_range_expressions_and_shape_mismatch_are_rejected(self):
        self.assert_error("COUNTIF(A1+0,1)")
        self.assert_error("SUMPRODUCT(A1+0)")
        self.assert_error("SUMPRODUCT(A1:A2,B1)")

    def test_unsupported_criteria_and_range_type_combinations(self):
        self.cells[("Current", "D1")] = Decimal(1)
        self.cells[("Current", "D2")] = "text"
        self.cells[("Current", "D3")] = None
        self.cells[("Current", "D4")] = True
        self.assert_error('COUNTIF(D1:D2,">100")')
        self.assert_error('COUNTIF(D1,"text")')
        self.assert_error("COUNTIF(D1,TRUE)")
        self.assert_error("COUNTIF(D3,D3)", "unsupported")
        self.assert_error('COUNTIF(D1:D4,"<text")')
        self.assert_error('COUNTIF(D1:D4,"' + "x" * 256 + '")')
        self.assert_error("COUNTIF(A1:A8,B9)")

    def test_sum_ranges_reject_boolean_even_when_unmatched(self):
        self.cells.update({
            ("Current", "D1"): "yes",
            ("Current", "D2"): "no",
            ("Current", "E1"): Decimal(5),
            ("Current", "E2"): True,
        })
        self.assert_error('SUMIF(D1:D2,"yes",E1:E2)')
        self.assert_error('SUMIFS(E1:E2,D1:D2,"yes")')

    def test_resolver_errors_propagate_even_when_conditions_do_not_match(self):
        def resolve(sheet, address):
            if (sheet, address) == ("Current", "B2"):
                raise FormulaError("cycle", "Circular dependency")
            return self.cells.get((sheet, address))

        with self.assertRaises(FormulaError) as caught:
            evaluate('COUNTIFS(A1:A2,"missing",B1:B2,">0")', "Current", resolve)
        self.assertEqual(caught.exception.code, "cycle")

        with self.assertRaises(FormulaError) as caught:
            evaluate("SUMPRODUCT(A1:A2,B1:B2)", "Current", resolve)
        self.assertEqual(caught.exception.code, "cycle")

    def test_nested_if_keeps_lazy_branch_evaluation(self):
        self.assertEqual(
            self.value('IF(TRUE,COUNTIF(B1:B8,"A*"),COUNTIF(Z1,1/0))'),
            Decimal(4),
        )
        self.assert_error('IF(FALSE,COUNTIF(B1:B8,"A*"),1/0)', "calculation_error")


if __name__ == "__main__":
    unittest.main()
