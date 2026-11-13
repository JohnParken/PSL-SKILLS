"""Synthetic tests for the classic OOXML chart cache parser."""

from pathlib import Path
import sys
import unittest
from xml.etree.ElementTree import Element, SubElement

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx_read_charts import CHART_NS, DRAWING_NS, REL_NS, read_chart_cache


def q(namespace, local):
    return f"{{{namespace}}}{local}"


def node(parent, namespace, local, text=None, **attributes):
    element = SubElement(parent, q(namespace, local), attributes)
    if text is not None:
        element.text = text
    return element


def chart_root():
    root = Element(q(CHART_NS, "chartSpace"))
    chart = node(root, CHART_NS, "chart")
    return root, chart


def add_title(root, text):
    title = node(root, CHART_NS, "title")
    tx = node(title, CHART_NS, "tx")
    rich = node(tx, CHART_NS, "rich")
    paragraph = node(rich, DRAWING_NS, "p")
    run = node(paragraph, DRAWING_NS, "r")
    node(run, DRAWING_NS, "t", text)


def add_str_cache(parent, points, count=None):
    cache = node(parent, CHART_NS, "strCache")
    add_cache_points(cache, points, count)
    return cache


def add_cache_points(cache, points, count=None, format_code=None):
    if format_code is not None:
        node(cache, CHART_NS, "formatCode", format_code)
    if count is not None:
        node(cache, CHART_NS, "ptCount", val=str(count))
    for index, value in points:
        point = node(cache, CHART_NS, "pt", idx=str(index))
        node(point, CHART_NS, "v", value)


def add_num_cache(parent, points, count=None, format_code=None):
    cache = node(parent, CHART_NS, "numCache")
    add_cache_points(cache, points, count, format_code)
    return cache


def add_reference(parent, local, formula, cache_builder=None):
    reference = node(parent, CHART_NS, local)
    node(reference, CHART_NS, "f", formula)
    if cache_builder is not None:
        cache_builder(reference)
    return reference


def add_pie_series(chart, name, categories, *, value_points=None, format_code=None):
    series = node(chart, CHART_NS, "ser")
    tx = node(series, CHART_NS, "tx")
    if name is not None:
        name_ref = node(tx, CHART_NS, "strRef")
        node(name_ref, CHART_NS, "f", "Sheet1!$A$1")
        add_str_cache(name_ref, [(0, name)], 1)

    category = node(series, CHART_NS, "cat")
    add_reference(category, "strRef", "Sheet1!$A$2:$A$7",
                   lambda ref: add_str_cache(ref, categories, 6))
    value = node(series, CHART_NS, "val")
    if value_points is not None:
        add_reference(value, "numRef", "Sheet1!$B$2:$B$7",
                      lambda ref: add_num_cache(ref, value_points, 6, format_code))
    else:
        add_reference(value, "numRef", "Sheet1!$B$2:$B$7")
    return series


class ChartCacheTests(unittest.TestCase):
    """Exercise classic chart-space caches without a workbook or ZIP."""

    def test_pie_cache_shape_preserves_categories_and_reference_only_series(self):
        root, chart = chart_root()
        add_title(chart, "Quarterly ")
        external = node(root, CHART_NS, "externalData")
        external.set(q(REL_NS, "id"), "rIdWorkbook")
        plot = node(chart, CHART_NS, "plotArea")
        pie = node(plot, CHART_NS, "pieChart")
        categories = [(index, f"Q{index + 1}") for index in range(6)]
        for series_index in range(4):
            cached_values = series_index == 3
            add_pie_series(
                pie,
                "Revenue" if series_index == 0 else None,
                categories,
                value_points=[(index, f"0.{index + 1}00") for index in range(6)] if cached_values else None,
                format_code="0.00%" if cached_values else None,
            )

        result = read_chart_cache(root)
        self.assertEqual(result["title"], "Quarterly ")
        self.assertEqual(result["external_data_id"], "rIdWorkbook")
        self.assertEqual(len(result["series"]), 4)
        self.assertEqual(result["series"][0]["name"], "Revenue")
        for series in result["series"][:3]:
            self.assertEqual(series["value_source"], "unavailable")
            self.assertEqual(series["category_source"], "cache")
            self.assertEqual(len(series["points"]), 6)
            self.assertTrue(all(point["value"] is None for point in series["points"]))
            self.assertEqual(
                series["value_reference"],
                {"status": "a1_range", "sheet": "Sheet1", "range": "$B$2:$B$7"},
            )
            self.assertEqual(
                [point["value_cell"] for point in series["points"]],
                ["B2", "B3", "B4", "B5", "B6", "B7"],
            )
            self.assertIn("value_cache_missing", series["issues"])
        last = result["series"][3]
        self.assertEqual(last["value_source"], "cache")
        self.assertEqual(last["format_code"], "0.00%")
        self.assertEqual(last["points"][0]["value"], "0.100")
        self.assertNotIn("Sheet1!", repr(result))

    def test_sparse_points_join_by_index_and_preserve_zero_and_count_evidence(self):
        root, chart = chart_root()
        plot = node(chart, CHART_NS, "plotArea")
        line = node(plot, CHART_NS, "lineChart")
        series = node(line, CHART_NS, "ser")
        category = node(series, CHART_NS, "cat")
        add_reference(category, "strRef", "A1:A6", lambda ref: add_str_cache(
            ref, [(4, "E"), (0, "A"), (2, "C")], 6))
        value = node(series, CHART_NS, "val")
        add_reference(value, "numRef", "B1:B6", lambda ref: add_num_cache(
            ref, [(4, "0.000"), (0, "0"), (1, "2.500e-2")], 6, "0.000"))

        parsed = read_chart_cache(root)["series"][0]
        self.assertEqual([point["index"] for point in parsed["points"]], [0, 1, 2, 4])
        self.assertEqual(parsed["points"][0], {"index": 0, "category": "A", "value": "0"})
        self.assertEqual(parsed["points"][1], {"index": 1, "category": None, "value": "2.500e-2"})
        self.assertEqual(
            parsed["points"][2],
            {"index": 2, "category": "C", "value": None, "value_cell": "B3"},
        )
        self.assertEqual(parsed["points"][3]["value"], "0.000")
        self.assertEqual(
            parsed["category_reference"],
            {"status": "a1_range", "sheet": None, "range": "A1:A6"},
        )
        self.assertEqual(
            parsed["value_reference"],
            {"status": "a1_range", "sheet": None, "range": "B1:B6"},
        )
        self.assertEqual(parsed["point_counts"]["category"], {"declared": 6, "actual": 3})
        self.assertIn("category_point_count_mismatch", parsed["issues"])
        self.assertIn("value_point_count_mismatch", parsed["issues"])

    def test_literal_sources_keep_exact_strings_and_format(self):
        root, chart = chart_root()
        plot = node(chart, CHART_NS, "plotArea")
        bar_chart = node(plot, CHART_NS, "barChart")
        series = node(bar_chart, CHART_NS, "ser")
        category = node(series, CHART_NS, "cat")
        str_lit = node(category, CHART_NS, "strLit")
        add_cache_points(str_lit, [(2, "Late"), (0, "Early")], 3)
        value = node(series, CHART_NS, "val")
        num_lit = node(value, CHART_NS, "numLit")
        add_cache_points(num_lit, [(2, "1.2300"), (0, "0.0000")], 3, "0.0000")

        parsed = read_chart_cache(root)["series"][0]
        self.assertEqual(parsed["category_source"], "literal")
        self.assertEqual(parsed["value_source"], "literal")
        self.assertEqual(parsed["format_code"], "0.0000")
        self.assertEqual(parsed["points"][0]["value"], "0.0000")
        self.assertEqual(parsed["points"][1], {"index": 2, "category": "Late", "value": "1.2300"})
        self.assertNotIn("value_reference", parsed)
        self.assertNotIn("category_reference", parsed)

    def test_horizontal_quoted_external_reference_does_not_expose_workbook(self):
        root, chart = chart_root()
        plot = node(chart, CHART_NS, "plotArea")
        line = node(plot, CHART_NS, "lineChart")
        series = node(line, CHART_NS, "ser")
        category = node(series, CHART_NS, "cat")
        add_reference(category, "strRef", "Sheet1!A1:F1", lambda ref: add_str_cache(
            ref, [(0, "First"), (5, "Last")], 6))
        value = node(series, CHART_NS, "val")
        add_reference(
            value,
            "numRef",
            "'[monthly report.xlsx]Quarter''s Data'!$D$3:$I$3",
        )

        result = read_chart_cache(root)
        parsed = result["series"][0]
        self.assertEqual(
            parsed["value_reference"],
            {
                "status": "a1_range",
                "sheet": "Quarter's Data",
                "range": "$D$3:$I$3",
            },
        )
        self.assertEqual(
            [point["value_cell"] for point in parsed["points"]],
            ["D3", "I3"],
        )
        self.assertNotIn("monthly report.xlsx", repr(result))

    def test_unquoted_external_prefix_and_grid_maximum_are_valid(self):
        root, chart = chart_root()
        plot = node(chart, CHART_NS, "plotArea")
        line = node(plot, CHART_NS, "lineChart")
        series = node(line, CHART_NS, "ser")
        category = node(series, CHART_NS, "cat")
        add_reference(category, "strRef", "Sheet1!A1", lambda ref: add_str_cache(
            ref, [(0, "Only")], 1))
        value = node(series, CHART_NS, "val")
        add_reference(value, "numRef", "[monthly.xlsx]Sheet1!XFD1048576")

        result = read_chart_cache(root)
        parsed = result["series"][0]
        self.assertEqual(
            parsed["value_reference"],
            {"status": "a1_range", "sheet": "Sheet1", "range": "XFD1048576"},
        )
        self.assertEqual(parsed["points"][0]["value_cell"], "XFD1048576")
        self.assertNotIn("monthly.xlsx", repr(result))

    def test_unsupported_references_and_count_mismatch_do_not_add_cells(self):
        unsupported_formulas = (
            "Revenue",
            "Sheet1!A1,B2",
            "Sheet1:Sheet2!A1",
            "=SUM(Sheet1!A1)",
            "SUM(Sheet1!A1",
            "Sheet1!XFE1",
            "Sheet1!A1048577",
            "Sheet1!A99999999",
            f"{'S' * 32}!A1",
            "Sheet1!B2:B1",
        )
        for formula in unsupported_formulas:
            with self.subTest(formula=formula):
                root, chart = chart_root()
                plot = node(chart, CHART_NS, "plotArea")
                line = node(plot, CHART_NS, "lineChart")
                series = node(line, CHART_NS, "ser")
                category = node(series, CHART_NS, "cat")
                add_reference(category, "strRef", "A1:A1", lambda ref: add_str_cache(
                    ref, [(0, "A")], 1))
                value = node(series, CHART_NS, "val")
                add_reference(value, "numRef", formula)

                parsed = read_chart_cache(root)["series"][0]
                self.assertEqual(parsed["value_reference"], {"status": "unsupported"})
                self.assertNotIn("value_cell", parsed["points"][0])

        root, chart = chart_root()
        plot = node(chart, CHART_NS, "plotArea")
        line = node(plot, CHART_NS, "lineChart")
        series = node(line, CHART_NS, "ser")
        category = node(series, CHART_NS, "cat")
        add_reference(category, "strRef", "A1:A5", lambda ref: add_str_cache(
            ref, [(0, "A")], 5))
        value = node(series, CHART_NS, "val")
        add_reference(value, "numRef", "$B$2:$B$7")

        parsed = read_chart_cache(root)["series"][0]
        self.assertEqual(
            parsed["value_reference"],
            {"status": "a1_range", "sheet": None, "range": "$B$2:$B$7"},
        )
        self.assertNotIn("value_cell", parsed["points"][0])

    def test_rectangular_reference_is_reported_without_cell_mapping(self):
        root, chart = chart_root()
        plot = node(chart, CHART_NS, "plotArea")
        line = node(plot, CHART_NS, "lineChart")
        series = node(line, CHART_NS, "ser")
        category = node(series, CHART_NS, "cat")
        add_reference(category, "strRef", "A1:A6", lambda ref: add_str_cache(
            ref, [(0, "A"), (5, "F")], 6))
        value = node(series, CHART_NS, "val")
        add_reference(value, "numRef", "$B$2:$C$4")

        parsed = read_chart_cache(root)["series"][0]
        self.assertEqual(
            parsed["value_reference"],
            {"status": "a1_range", "sheet": None, "range": "$B$2:$C$4"},
        )
        self.assertTrue(all("value_cell" not in point for point in parsed["points"]))

    def test_unsupported_scatter_is_diagnosed_without_emitting_series(self):
        root, chart = chart_root()
        plot = node(chart, CHART_NS, "plotArea")
        scatter = node(plot, CHART_NS, "scatterChart")
        node(scatter, CHART_NS, "ser")

        result = read_chart_cache(root)
        self.assertEqual(result["series"], [])
        self.assertIn("unsupported_chart_type", result["issues"])

    def test_wrong_root_and_duplicate_structural_nodes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "wrong_chart_space_root"):
            read_chart_cache(Element(q(CHART_NS, "chart")))

        root, chart = chart_root()
        node(chart, CHART_NS, "plotArea")
        node(chart, CHART_NS, "plotArea")
        with self.assertRaisesRegex(ValueError, "duplicate_chart_plotArea"):
            read_chart_cache(root)

    def test_duplicate_point_index_and_invalid_numeric_are_rejected(self):
        for duplicate, numeric_value, expected in (
            (True, "1", "duplicate_point_index"),
            (False, "one", "invalid_numeric_value"),
        ):
            with self.subTest(expected=expected):
                root, chart = chart_root()
                plot = node(chart, CHART_NS, "plotArea")
                line = node(plot, CHART_NS, "lineChart")
                series = node(line, CHART_NS, "ser")
                category = node(series, CHART_NS, "cat")
                category_ref = node(category, CHART_NS, "strRef")
                node(category_ref, CHART_NS, "f", "A1")
                add_str_cache(category_ref, [(0, "A")], 1)
                value = node(series, CHART_NS, "val")
                value_ref = node(value, CHART_NS, "numRef")
                node(value_ref, CHART_NS, "f", "B1")
                points = [(0, numeric_value), (0, "2")] if duplicate else [(0, numeric_value)]
                add_num_cache(value_ref, points, 1)
                with self.assertRaisesRegex(ValueError, expected):
                    read_chart_cache(root)

    def test_invalid_index_and_point_count_are_rejected(self):
        for attribute, value, expected in (
            ("idx", "-1", "invalid_point_index"),
            ("idx", "4294967296", "invalid_point_index"),
            ("val", "4294967296", "invalid_point_count"),
        ):
            with self.subTest(attribute=attribute, value=value):
                root, chart = chart_root()
                plot = node(chart, CHART_NS, "plotArea")
                pie = node(plot, CHART_NS, "pieChart")
                series = node(pie, CHART_NS, "ser")
                value_parent = node(series, CHART_NS, "val")
                reference = node(value_parent, CHART_NS, "numRef")
                node(reference, CHART_NS, "f", "B1")
                cache = node(reference, CHART_NS, "numCache")
                if attribute == "val":
                    node(cache, CHART_NS, "ptCount", **{attribute: value})
                else:
                    point = node(cache, CHART_NS, "pt", **{attribute: value})
                    node(point, CHART_NS, "v", "1")
                with self.assertRaisesRegex(ValueError, expected):
                    read_chart_cache(root)


if __name__ == "__main__":
    unittest.main()
