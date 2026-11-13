"""Synthetic regression tests for bounded classic chart-cache parsing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from wps_binary_chart_cache import (  # noqa: E402
    CHART_NS,
    ChartCacheBudget,
    ChartCacheLimit,
    read_chart_cache,
)


def _chart(body: str, extra_namespaces: str = "") -> ET.Element:
    source = (
        f'<c:chartSpace xmlns:c="{CHART_NS}" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        f"{extra_namespaces}>"
        f"<c:chart>{body}</c:chart>"
        "</c:chartSpace>"
    )
    return ET.fromstring(source)


def _series(
    categories: str,
    values: str,
    *,
    index: int = 0,
    order: int = 0,
    name: str = "Series A",
) -> str:
    return (
        "<c:ser>"
        f'<c:idx val="{index}"/><c:order val="{order}"/>'
        f"<c:tx><c:v>{name}</c:v></c:tx>"
        f"<c:cat>{categories}</c:cat><c:val>{values}</c:val>"
        "</c:ser>"
    )


def _plot(
    group: str, title: str = "", extra_namespaces: str = ""
) -> ET.Element:
    return _chart(
        f"{title}<c:plotArea>{group}</c:plotArea>",
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        + extra_namespaces,
    )


class ChartCacheTests(unittest.TestCase):
    def read(self, root: ET.Element) -> dict[str, object]:
        return read_chart_cache(root, ChartCacheBudget())

    def test_rich_title_and_exact_sparse_numeric_lexical_values(self) -> None:
        categories = (
            "<c:strRef><c:f>ignored-category-formula</c:f><c:strCache>"
            '<c:ptCount val="3"/><c:pt idx="0"><c:v>North</c:v></c:pt>'
            '<c:pt idx="2"><c:v>South</c:v></c:pt>'
            "</c:strCache></c:strRef>"
        )
        values = (
            "<c:numRef><c:f>ignored-value-formula</c:f><c:numCache>"
            '<c:formatCode>0.00</c:formatCode><c:ptCount val="3"/>'
            '<c:pt idx="0"><c:v>01.2300</c:v></c:pt>'
            '<c:pt idx="1"><c:v>-4.50e+2</c:v></c:pt>'
            '<c:pt idx="2"><c:v>3E-1</c:v></c:pt>'
            "</c:numCache></c:numRef>"
        )
        rich_title = (
            "<c:title><c:tx><c:rich><a:p>"
            "<a:r><a:t>Quarter </a:t></a:r>"
            "<a:fld><a:t>results</a:t></a:fld>"
            "</a:p></c:rich></c:tx></c:title>"
        )
        root = _plot(
            "<c:pieChart>"
            + _series(categories, values).replace(
                "<c:tx><c:v>Series A</c:v></c:tx>",
                "<c:tx><c:strRef><c:f>ignored-series-formula</c:f>"
                "<c:strCache><c:ptCount val=\"1\"/>"
                "<c:pt idx=\"0\"><c:v>Series North</c:v></c:pt>"
                "</c:strCache></c:strRef></c:tx>",
            )
            + "</c:pieChart>",
            rich_title,
        )
        root.insert(0, ET.fromstring(
            '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/'
            'markup-compatibility/2006"><mc:Choice Requires="c">'
            "<c:style xmlns:c=\"http://schemas.openxmlformats.org/drawingml/2006/chart\" "
            'val="1"/></mc:Choice></mc:AlternateContent>'
        ))

        result = self.read(root)
        series = result["series"][0]  # type: ignore[index]
        self.assertEqual(result["title"], "Quarter results")
        self.assertEqual(series["chart_type"], "pieChart")  # type: ignore[index]
        self.assertEqual(series["name"], "Series North")  # type: ignore[index]
        self.assertEqual(
            series["categories"]["points"],  # type: ignore[index]
            [{"index": 0, "value": "North"}, {"index": 2, "value": "South"}],
        )
        self.assertEqual(
            series["categories"]["missing_ranges"],  # type: ignore[index]
            [{"first_index": 1, "last_index": 1}],
        )
        self.assertEqual(
            series["values"]["points"],  # type: ignore[index]
            [
                {"index": 0, "value": "01.2300"},
                {"index": 1, "value": "-4.50e+2"},
                {"index": 2, "value": "3E-1"},
            ],
        )
        self.assertEqual(series["values"]["format_code"], "0.00")  # type: ignore[index]
        self.assertNotIn("ignored-value-formula", repr(result))
        self.assertNotIn("ignored-series-formula", repr(result))
        self.assertEqual(result["status"], "partial")

    def test_missing_cache_differs_from_empty_cache(self) -> None:
        categories = (
            "<c:strLit><c:ptCount val=\"0\"/></c:strLit>"
        )
        values = (
            "<c:numRef><c:f>private-formula</c:f></c:numRef>"
        )
        result = self.read(
            _plot(f"<c:pieChart>{_series(categories, values)}</c:pieChart>")
        )
        series = result["series"][0]  # type: ignore[index]
        self.assertEqual(series["categories"]["status"], "complete")  # type: ignore[index]
        self.assertEqual(series["categories"]["points"], [])  # type: ignore[index]
        self.assertEqual(series["categories"]["missing_ranges"], [])  # type: ignore[index]
        self.assertEqual(series["values"]["status"], "unavailable")  # type: ignore[index]
        self.assertEqual(series["values"]["source"], "numRef")  # type: ignore[index]
        self.assertEqual(series["values"]["declared_count"], None)  # type: ignore[index]
        self.assertEqual(series["values"]["missing_ranges"], None)  # type: ignore[index]
        self.assertNotIn("private-formula", repr(result))

    def test_unknown_count_keeps_values_and_huge_gap_is_compact(self) -> None:
        unknown = (
            "<c:strLit><c:pt idx=\"4\"><c:v>x</c:v></c:pt></c:strLit>"
        )
        huge = (
            "<c:numLit><c:ptCount val=\"4000000000\"/>"
            '<c:pt idx="3999999999"><c:v>7.00</c:v></c:pt></c:numLit>'
        )
        result = self.read(
            _plot(f"<c:pieChart>{_series(unknown, huge)}</c:pieChart>")
        )
        series = result["series"][0]  # type: ignore[index]
        self.assertEqual(series["categories"]["status"], "partial")  # type: ignore[index]
        self.assertIsNone(series["categories"]["missing_ranges"])  # type: ignore[index]
        self.assertEqual(
            series["categories"]["points"],  # type: ignore[index]
            [{"index": 4, "value": "x"}],
        )
        self.assertEqual(
            series["values"]["missing_ranges"],  # type: ignore[index]
            [{"first_index": 0, "last_index": 3999999998}],
        )
        self.assertEqual(len(series["values"]["points"]), 1)  # type: ignore[index]

    def test_structural_ambiguity_quarantines_only_affected_channel(self) -> None:
        categories = (
            "<c:strLit><c:ptCount val=\"2\"/>"
            '<c:pt idx="0"><c:v>first</c:v></c:pt>'
            '<c:pt idx="0"><c:v>duplicate</c:v></c:pt>'
            "</c:strLit>"
        )
        values = (
            "<c:numLit><c:ptCount val=\"1\"/>"
            '<c:pt idx="0"><c:v>1.000</c:v></c:pt></c:numLit>'
        )
        second = _series(
            '<c:strLit><c:ptCount val="1"/><c:pt idx="0"><c:v>ok</c:v>'
            "</c:pt></c:strLit>",
            values,
            index=1,
            order=1,
            name="Series B",
        )
        result = self.read(
            _plot(
                f"<c:pieChart>{_series(categories, values)}{second}</c:pieChart>"
            )
        )
        first, retained = result["series"]  # type: ignore[misc]
        self.assertEqual(first["categories"]["status"], "unavailable")
        self.assertEqual(first["categories"]["points"], [])
        self.assertEqual(first["values"]["points"], [{"index": 0, "value": "1.000"}])
        self.assertEqual(retained["name"], "Series B")
        self.assertEqual(retained["categories"]["status"], "complete")
        self.assertEqual(result["status"], "partial")

    def test_ambiguous_source_and_unsupported_chart_keep_valid_series(self) -> None:
        categories = (
            '<c:strLit><c:ptCount val="1"/><c:pt idx="0"><c:v>cat</c:v>'
            "</c:pt></c:strLit>"
        )
        values = (
            '<c:numLit><c:ptCount val="1"/><c:pt idx="0"><c:v>2</c:v>'
            "</c:pt></c:numLit>"
        )
        ambiguous = (
            "<c:cat><c:strLit><c:ptCount val=\"1\"/>"
            '<c:pt idx="0"><c:v>a</c:v></c:pt></c:strLit>'
            "<c:numLit><c:ptCount val=\"1\"/>"
            '<c:pt idx="0"><c:v>1</c:v></c:pt></c:numLit></c:cat>'
        )
        first = _series(categories, values).replace(
            f"<c:cat>{categories}</c:cat>", ambiguous
        )
        result = self.read(_plot(f"<c:scatterChart>{first}</c:scatterChart>"))
        item = result["series"][0]  # type: ignore[index]
        self.assertEqual(item["chart_type"], "scatterChart")  # type: ignore[index]
        self.assertEqual(item["categories"]["status"], "unavailable")  # type: ignore[index]
        self.assertEqual(item["values"]["status"], "complete")  # type: ignore[index]
        self.assertEqual(result["status"], "partial")
        self.assertIn("unsupported_chart_type:scatterChart", result["issues"])

    def test_foreign_plot_markup_is_reported_without_reading_alternate_content(self) -> None:
        categories = '<c:strLit><c:ptCount val="0"/></c:strLit>'
        values = '<c:numLit><c:ptCount val="0"/></c:numLit>'
        known = f"<c:pieChart>{_series(categories, values)}</c:pieChart>"
        alternate = (
            "<mc:AlternateContent><mc:Choice Requires=\"c\">"
            f"<c:lineChart>{_series(categories, values, index=1, order=1)}</c:lineChart>"
            "</mc:Choice><mc:Fallback/></mc:AlternateContent>"
        )
        result = self.read(_plot(known + alternate))
        self.assertEqual(len(result["series"]), 1)  # type: ignore[arg-type]
        self.assertIn("unsupported_plot_markup", result["issues"])
        self.assertEqual(result["status"], "partial")

    def test_unknown_chart_name_is_generic_but_keeps_cache_data(self) -> None:
        categories = (
            '<c:strLit><c:ptCount val="1"/><c:pt idx="0"><c:v>label</c:v>'
            "</c:pt></c:strLit>"
        )
        values = (
            '<c:numLit><c:ptCount val="1"/><c:pt idx="0"><c:v>2.00</c:v>'
            "</c:pt></c:numLit>"
        )
        result = self.read(
            _plot(f"<c:vendorChart>{_series(categories, values)}</c:vendorChart>")
        )
        item = result["series"][0]  # type: ignore[index]
        self.assertEqual(item["chart_type"], "unsupportedChart")  # type: ignore[index]
        self.assertEqual(item["categories"]["points"], [{"index": 0, "value": "label"}])  # type: ignore[index]
        self.assertIn("unsupported_chart_type", result["issues"])
        self.assertNotIn("vendorChart", repr(result["issues"]))

    def test_unsupported_source_and_count_mismatch_are_explicit(self) -> None:
        multilevel = (
            "<c:multiLvlStrRef><c:f>ignored</c:f><c:multiLvlStrCache>"
            '<c:lvl><c:pt idx="0"><c:v>label</c:v></c:pt></c:lvl>'
            "</c:multiLvlStrCache></c:multiLvlStrRef>"
        )
        values = (
            '<c:numLit><c:ptCount val="1"/><c:pt idx="0"><c:v>2</c:v>'
            "</c:pt></c:numLit>"
        )
        unsupported = self.read(
            _plot(f"<c:pieChart>{_series(multilevel, values)}</c:pieChart>")
        )["series"][0]  # type: ignore[index]
        self.assertEqual(unsupported["categories"]["source"], "multiLvlStrRef")
        self.assertIn("source_unsupported", unsupported["categories"]["issues"])

        incompatible = (
            "<c:strRef><c:f>ignored</c:f><c:strCache>"
            '<c:ptCount val="0"/></c:strCache><c:multiLvlStrCache/>'
            "</c:strRef>"
        )
        ambiguous = self.read(
            _plot(f"<c:pieChart>{_series(incompatible, values)}</c:pieChart>")
        )["series"][0]  # type: ignore[index]
        self.assertEqual(ambiguous["categories"]["status"], "unavailable")
        self.assertEqual(ambiguous["categories"]["source"], "strRef")
        self.assertIn("cache_ambiguous", ambiguous["categories"]["issues"])

        categories = (
            '<c:strLit><c:ptCount val="1"/><c:pt idx="0"><c:v>a</c:v>'
            "</c:pt></c:strLit>"
        )
        two_values = (
            '<c:numLit><c:ptCount val="2"/><c:pt idx="0"><c:v>1</c:v>'
            '</c:pt><c:pt idx="1"><c:v>2</c:v></c:pt></c:numLit>'
        )
        mismatch = self.read(
            _plot(f"<c:pieChart>{_series(categories, two_values)}</c:pieChart>")
        )
        mismatched = mismatch["series"][0]  # type: ignore[index]
        self.assertEqual(mismatched["categories"]["status"], "complete")
        self.assertEqual(mismatched["values"]["status"], "complete")
        self.assertEqual(len(mismatched["categories"]["points"]), 1)
        self.assertEqual(len(mismatched["values"]["points"]), 2)
        self.assertIn("series_count_mismatch", mismatched["issues"])
        self.assertEqual(mismatch["status"], "partial")

    def test_source_names_match_the_chart_namespace_exactly(self) -> None:
        categories = (
            '<x:strLit><c:ptCount val="1"/><c:pt idx="0"><c:v>foreign</c:v>'
            "</c:pt></x:strLit>"
            '<c:strLit><c:ptCount val="1"/><c:pt idx="0"><c:v>local</c:v>'
            "</c:pt></c:strLit>"
        )
        values = '<c:numLit><c:ptCount val="1"/></c:numLit>'
        result = self.read(
            _plot(
                f"<c:pieChart>{_series(categories, values)}</c:pieChart>",
                extra_namespaces='xmlns:x="urn:foreign-chart-like-data"',
            )
        )
        category = result["series"][0]["categories"]  # type: ignore[index]
        self.assertEqual(category["status"], "complete")
        self.assertEqual(category["points"], [{"index": 0, "value": "local"}])

    def test_bad_numeric_value_is_a_missing_point_not_zero(self) -> None:
        categories = '<c:strLit><c:ptCount val="0"/></c:strLit>'
        values = (
            "<c:numLit><c:ptCount val=\"3\"/>"
            '<c:pt idx="0"><c:v>1</c:v></c:pt>'
            '<c:pt idx="1"><c:v>#N/A</c:v></c:pt>'
            '<c:pt idx="2"><c:v>3.00</c:v></c:pt>'
            "</c:numLit>"
        )
        result = self.read(
            _plot(f"<c:pieChart>{_series(categories, values)}</c:pieChart>")
        )
        channel = result["series"][0]["values"]  # type: ignore[index]
        self.assertEqual(channel["status"], "partial")
        self.assertEqual(
            channel["points"],
            [{"index": 0, "value": "1"}, {"index": 2, "value": "3.00"}],
        )
        self.assertEqual(
            channel["missing_ranges"], [{"first_index": 1, "last_index": 1}]
        )

    def test_shared_series_budget_and_per_value_limit(self) -> None:
        categories = '<c:strLit><c:ptCount val="0"/></c:strLit>'
        values = '<c:numLit><c:ptCount val="0"/></c:numLit>'
        root = _plot(f"<c:pieChart>{_series(categories, values)}</c:pieChart>")
        budget = ChartCacheBudget()
        with patch.object(ChartCacheBudget, "MAX_SERIES", 1):
            read_chart_cache(root, budget)
            with self.assertRaises(ChartCacheLimit):
                read_chart_cache(root, budget)
        long_category = (
            '<c:strLit><c:ptCount val="1"/><c:pt idx="0">'
            "<c:v>long-value</c:v></c:pt></c:strLit>"
        )
        long_root = _plot(
            f"<c:pieChart>{_series(long_category, values)}</c:pieChart>"
        )
        with patch.object(ChartCacheBudget, "MAX_VALUE_BYTES", 4):
            with self.assertRaises(ChartCacheLimit):
                self.read(long_root)

        malformed_count_values = (
            '<c:numLit><c:ptCount val="not-unsigned"/>'
            '<c:pt idx="0"><c:v>1</c:v></c:pt>'
            '<c:pt idx="1"><c:v>2</c:v></c:pt></c:numLit>'
        )
        malformed_root = _plot(
            f"<c:pieChart>{_series(categories, malformed_count_values)}</c:pieChart>"
        )
        with patch.object(ChartCacheBudget, "MAX_POINTS", 1):
            with self.assertRaises(ChartCacheLimit):
                self.read(malformed_root)

    def test_bad_chart_space_cardinality_raises_value_error(self) -> None:
        root = ET.fromstring(
            f'<c:chartSpace xmlns:c="{CHART_NS}"><c:chart/>'
            "<c:chart/></c:chartSpace>"
        )
        with self.assertRaises(ValueError):
            self.read(root)


if __name__ == "__main__":
    unittest.main()
