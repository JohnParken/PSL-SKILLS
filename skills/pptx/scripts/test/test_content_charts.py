"""Independent readback, corruption and bounded-reference chart content tests."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "_engine"), str(Path(__file__).resolve().parent)]

from pptx_core import runtime
from pptx_core.chart_read import ChartReadError, compare_chart_input
from pptx_core.content import ContentError, expected_slide
from pptx_core.validation import inspect_presentation, validate_presentation
from pptx_core.workbook_read import parse_reference
from test_v680_native import native_marker, svg_page, zip_parts
from test_validation import zipped


C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"c": C_NS, "s": S_NS}
SHEET = "xl/worksheets/sheet1.xml"


def payload(kind: str = "column") -> dict:
    result = {
        "type": kind, "x": 80, "y": 100, "width": 1000, "height": 500,
        "categories": ["A", " B "], "series": [{"name": "Series", "values": [18, 27]}],
    }
    if kind == "scatter":
        result.pop("categories")
        result["series"] = [{"name": "Series", "x": [0.125, 2], "y": [18, 27]}]
    return result


def render(source: dict) -> bytes:
    body = native_marker("chart", source)
    deck = runtime.Deck("Chart content", 1280, 720, "preserve", True, (runtime.PreparedSlide(svg_page(body), ""),))
    return runtime.render_deck(deck)


def source_expectation(source: dict) -> dict:
    return {
        "title": "Chart content", "width_emu": 12192000, "height_emu": 6858000,
        "slides": [expected_slide(svg_page(native_marker("chart", source)), "")],
    }


def read(data: bytes) -> dict:
    return inspect_presentation(data, details=True)["slides"][0]["native_objects"][0]["data"]


def mutate(data: bytes, chart=None, workbook=None) -> bytes:
    parts = zip_parts(data)
    if chart is not None:
        part = next(name for name in parts if name.startswith("ppt/charts/chart") and name.endswith(".xml"))
        root = ET.fromstring(parts[part])
        chart(root)
        parts[part] = ET.tostring(root, encoding="utf-8")
    if workbook is not None:
        part = next(name for name in parts if name.endswith(".xlsx"))
        sheets = zip_parts(parts[part])
        workbook(sheets)
        parts[part] = zipped(sheets)
    return zipped(parts)


def edit_sheet(parts: dict, callback) -> None:
    root = ET.fromstring(parts[SHEET])
    callback(root)
    parts[SHEET] = ET.tostring(root, encoding="utf-8")


def cached_value(root: ET.Element) -> ET.Element:
    return root.find(".//c:val/c:numRef/c:numCache/c:pt[@idx='0']/c:v", NS)


def workbook_value(parts: dict, value: str) -> None:
    edit_sheet(parts, lambda root: setattr(root.find(".//s:c[@r='B2']/s:v", NS), "text", value))


def literal_sources(root: ET.Element) -> None:
    for series in root.findall(".//c:ser", NS):
        tx = series.find("c:tx", NS)
        name = tx.find(".//c:pt/c:v", NS).text
        tx.clear()
        ET.SubElement(tx, f"{{{C_NS}}}v").text = name
        for field in ("cat", "val", "xVal", "yVal"):
            source = series.find("c:" + field, NS)
            if source is None:
                continue
            reference = source[0]
            numeric = reference.tag == f"{{{C_NS}}}numRef"
            cache = reference.find("c:numCache" if numeric else "c:strCache", NS)
            source.remove(reference)
            cache.tag = f"{{{C_NS}}}" + ("numLit" if numeric else "strLit")
            source.append(cache)
    external = root.find("c:externalData", NS)
    if external is not None:
        root.remove(external)


class ChartContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = render(payload())

    def assert_content_error(self, data: bytes, code: str = "content_validation_failed") -> ContentError:
        with self.assertRaises(ContentError) as failure:
            validate_presentation(data, content=True)
        self.assertEqual(failure.exception.code, code)
        self.assertEqual(failure.exception.details["slide_number"], 1)
        self.assertEqual(failure.exception.details["object_kind"], "chart")
        return failure.exception

    def test_seven_types_roundtrip_and_source_comparison(self) -> None:
        for kind in ("column", "bar", "line", "area", "pie", "doughnut", "scatter"):
            with self.subTest(kind=kind):
                source = payload(kind)
                data = render(source)
                result = read(data)
                self.assertEqual(result["type"], kind)
                self.assertEqual(result["numeric_encoding"], "decimal_string")
                self.assertEqual(result["workbook"]["status"], "checked")
                self.assertEqual(result["workbook"]["checked_references"], 3)
                self.assertEqual(result["series"][0]["y" if kind == "scatter" else "values"], ["18", "27"])
                self.assertEqual(result["categories"], None if kind == "scatter" else ["A", " B "])
                compare_chart_input(source, result)
                self.assertEqual(validate_presentation(data, expected=source_expectation(source))["source_comparison"], "passed")
                json.dumps(result, allow_nan=False)

    def test_cache_only_tamper_is_detected_but_structural_validate_stays_structural(self) -> None:
        changed = mutate(self.base, chart=lambda root: setattr(cached_value(root), "text", "99999"))
        self.assertEqual(validate_presentation(changed)["structural_validation"], "passed")
        error = self.assert_content_error(changed)
        self.assertEqual(error.details["cached_value"], "99999")
        self.assertEqual(error.details["workbook_value"], "18")
        self.assertEqual(error.details["point_index"], 0)

    def test_cache_and_workbook_tamper_is_detected_against_input(self) -> None:
        changed = mutate(self.base, chart=lambda root: setattr(cached_value(root), "text", "99999"), workbook=lambda parts: workbook_value(parts, "99999"))
        result = read(changed)
        self.assertEqual(result["series"][0]["values"][0], "99999")
        with self.assertRaises(ChartReadError) as failure:
            compare_chart_input(payload(), result)
        self.assertEqual(failure.exception.details["field_path"], "chart.series[0].values")
        with self.assertRaises(ContentError):
            validate_presentation(changed, expected=source_expectation(payload()))

    def test_sparse_and_out_of_order_points_preserve_missing_values(self) -> None:
        source = payload("line")
        source["categories"] = ["A", "B", "C", "D"]
        source["series"][0]["values"] = [18, None, -0.125, None]

        def reorder(root: ET.Element) -> None:
            cache = root.find(".//c:val/c:numRef/c:numCache", NS)
            points = cache.findall("c:pt", NS)
            for point in points:
                cache.remove(point)
            cache.extend(reversed(points))

        result = read(mutate(render(source), chart=reorder))
        series = result["series"][0]
        self.assertEqual(series["values"], ["18", None, "-0.125", None])
        self.assertEqual(series["sources"]["values"]["missing_indices"], [1, 3])
        self.assertEqual(series["sources"]["values"]["present_indices"], [0, 2])
        compare_chart_input(source, result)

    def test_removed_cache_point_does_not_turn_into_zero_or_escape_check(self) -> None:
        def remove(root: ET.Element) -> None:
            cache = root.find(".//c:val/c:numRef/c:numCache", NS)
            cache.remove(cache.find("c:pt[@idx='0']", NS))

        error = self.assert_content_error(mutate(self.base, chart=remove))
        self.assertIsNone(error.details["cached_value"])
        self.assertEqual(error.details["workbook_value"], "18")

    def test_duplicate_and_out_of_range_cache_points_are_rejected(self) -> None:
        for index in ("0", "2"):
            with self.subTest(index=index):
                def change(root: ET.Element) -> None:
                    root.find(".//c:val/c:numRef/c:numCache/c:pt[@idx='1']", NS).set("idx", index)

                self.assert_content_error(mutate(self.base, chart=change))

    def test_cache_count_limits_are_enforced_before_expansion(self) -> None:
        for count, code in (("3", "content_validation_failed"), ("2001", "resource_limit"), ("999999999999", "resource_limit")):
            with self.subTest(count=count):
                def change(root: ET.Element) -> None:
                    root.find(".//c:val/c:numRef/c:numCache/c:ptCount", NS).set("val", count)

                self.assert_content_error(mutate(self.base, chart=change), code)

    def test_literal_caches_require_no_workbook_and_preserve_gaps(self) -> None:
        source = payload("area")
        source["series"][0]["values"][1] = None
        result = read(mutate(render(source), chart=literal_sources))
        self.assertEqual(result["workbook"]["status"], "not_applicable")
        self.assertEqual(result["workbook"]["checked_references"], 0)
        self.assertEqual(result["series"][0]["sources"]["categories"]["kind"], "strLit")
        self.assertEqual(result["series"][0]["sources"]["values"]["kind"], "numLit")
        self.assertEqual(result["series"][0]["values"], ["18", None])
        compare_chart_input(source, result)

    def test_numeric_literal_category_and_scatter_caches(self) -> None:
        area = payload("area")
        area["categories"] = [45000, 45001]
        area["axes"] = {"category": {"kind": "DATE"}}
        area_result = read(mutate(render(area), chart=literal_sources))
        self.assertEqual(area_result["series"][0]["sources"]["categories"]["kind"], "numLit")
        self.assertEqual(area_result["series"][0]["category_type"], "number")
        compare_chart_input(area, area_result)
        scatter = payload("scatter")
        result = read(mutate(render(scatter), chart=literal_sources))
        self.assertEqual(result["series"][0]["x"], ["0.125", "2"])
        self.assertEqual(result["series"][0]["y"], ["18", "27"])
        compare_chart_input(scatter, result)

    def test_refs_without_embedded_workbook_are_explicitly_unsupported(self) -> None:
        changed = mutate(self.base, chart=lambda root: root.remove(root.find("c:externalData", NS)))
        self.assert_content_error(changed, "unsupported_content")

    def test_unsupported_references_are_never_reported_as_checked(self) -> None:
        references = [
            "[Book.xlsx]Sheet1!$B$2:$B$3", "Sheet1!A2:B3", "Sheet1!B:B", "SUM(Sheet1!B2:B3)",
            "Sheet1!B3:B2", "Sheet1!B2,Sheet1!B3", "Missing!B2:B3", "Sheet1!XFE2:XFE3",
        ]
        for reference in references:
            with self.subTest(reference=reference):
                def change(root: ET.Element) -> None:
                    root.find(".//c:val/c:numRef/c:f", NS).text = reference

                self.assert_content_error(mutate(self.base, chart=change), "unsupported_content")

    def test_reference_size_limit_is_diagnostic(self) -> None:
        changed = mutate(self.base, chart=lambda root: setattr(root.find(".//c:val/c:numRef/c:f", NS), "text", "Sheet1!$B$2:$B$2002"))
        self.assert_content_error(changed, "resource_limit")
        with self.assertRaises(ChartReadError) as failure:
            parse_reference("'" + "A" * 2050 + "'!A1")
        self.assertEqual(failure.exception.code, "resource_limit")

    def test_quoted_sheet_name_apostrophe_case_and_horizontal_a1(self) -> None:
        def rename_chart(root: ET.Element) -> None:
            for formula in root.findall(".//c:f", NS):
                formula.text = formula.text.replace("Sheet1!", "'O''Brien Data'!")

        def rename_workbook(parts: dict) -> None:
            root = ET.fromstring(parts["xl/workbook.xml"])
            root.find("s:sheets/s:sheet", NS).set("name", "O'Brien Data")
            parts["xl/workbook.xml"] = ET.tostring(root, encoding="utf-8")

        result = read(mutate(self.base, chart=rename_chart, workbook=rename_workbook))
        self.assertEqual(result["series"][0]["sources"]["values"]["workbook_sheet"], "O'Brien Data")
        self.assertEqual(parse_reference("'O''Brien Data'!$XFC$1048576:$XFD$1048576"), ("O'Brien Data", ["XFC1048576", "XFD1048576"]))
        self.assertEqual(parse_reference("sheet1!a1:b1"), ("sheet1", ["A1", "B1"]))

    def test_formula_cells_are_rejected_even_with_matching_cached_value(self) -> None:
        def formula(parts: dict) -> None:
            edit_sheet(parts, lambda root: setattr(ET.SubElement(root.find(".//s:c[@r='B2']", NS), f"{{{S_NS}}}f"), "text", "9+9"))

        error = self.assert_content_error(mutate(self.base, workbook=formula), "unsupported_content")
        self.assertEqual(error.details["cell"], "B2")

    def test_unrelated_formula_cells_are_not_evaluated(self) -> None:
        def extra(parts: dict) -> None:
            def add(root: ET.Element) -> None:
                cell = ET.SubElement(root.find(".//s:row[@r='2']", NS), f"{{{S_NS}}}c", {"r": "Z2"})
                ET.SubElement(cell, f"{{{S_NS}}}f").text = "1/0"
                ET.SubElement(cell, f"{{{S_NS}}}v").text = "#DIV/0!"

            edit_sheet(parts, add)

        self.assertEqual(read(mutate(self.base, workbook=extra))["workbook"]["status"], "checked")

    def test_unsupported_referenced_cell_types_are_explicit(self) -> None:
        for kind in ("b", "e", "d", "str", "unknown"):
            with self.subTest(kind=kind):
                def change(parts: dict) -> None:
                    edit_sheet(parts, lambda root: root.find(".//s:c[@r='B2']", NS).set("t", kind))

                self.assert_content_error(mutate(self.base, workbook=change), "unsupported_content")

    def test_duplicate_workbook_cells_are_rejected(self) -> None:
        def duplicate(parts: dict) -> None:
            def add(root: ET.Element) -> None:
                row = root.find(".//s:row[@r='2']", NS)
                row.append(copy.deepcopy(row.find("s:c[@r='B2']", NS)))

            edit_sheet(parts, add)

        self.assert_content_error(mutate(self.base, workbook=duplicate))

    def test_empty_numeric_cells_remain_null(self) -> None:
        source = payload()
        source["series"][0]["values"][1] = None

        def blank(parts: dict) -> None:
            def add(root: ET.Element) -> None:
                cell = ET.SubElement(root.find(".//s:row[@r='3']", NS), f"{{{S_NS}}}c", {"r": "B3"})
                ET.SubElement(cell, f"{{{S_NS}}}v")

            edit_sheet(parts, add)

        result = read(mutate(render(source), workbook=blank))
        self.assertIsNone(result["series"][0]["values"][1])
        self.assertEqual(result["series"][0]["sources"]["values"]["workbook_cells"][1]["type"], "n")

    def test_shared_and_literal_string_cells_preserve_rich_text(self) -> None:
        def shared(parts: dict) -> None:
            strings = ET.Element(f"{{{S_NS}}}sst", {"count": "3", "uniqueCount": "3"})
            for text in ("Series", "A", " B "):
                item = ET.SubElement(strings, f"{{{S_NS}}}si")
                run = ET.SubElement(item, f"{{{S_NS}}}r")
                ET.SubElement(run, f"{{{S_NS}}}t").text = text
                phonetic = ET.SubElement(item, f"{{{S_NS}}}rPh", {"sb": "0", "eb": "1"})
                ET.SubElement(phonetic, f"{{{S_NS}}}t").text = "ignored pronunciation"
            parts["xl/sharedStrings.xml"] = ET.tostring(strings, encoding="utf-8")
            types = ET.fromstring(parts["[Content_Types].xml"])
            ET.SubElement(types, f"{{{CT_NS}}}Override", {
                "PartName": "/xl/sharedStrings.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
            })
            parts["[Content_Types].xml"] = ET.tostring(types, encoding="utf-8")
            relations = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
            ET.SubElement(relations, f"{{{PKG_NS}}}Relationship", {"Id": "rIdStrings", "Type": R_NS + "/sharedStrings", "Target": "sharedStrings.xml"})
            parts["xl/_rels/workbook.xml.rels"] = ET.tostring(relations, encoding="utf-8")

            def cells(root: ET.Element) -> None:
                for index, address in enumerate(("B1", "A2", "A3")):
                    cell = root.find(f".//s:c[@r='{address}']", NS)
                    cell.clear()
                    cell.attrib.update({"r": address, "t": "s"})
                    ET.SubElement(cell, f"{{{S_NS}}}v").text = str(index)

            edit_sheet(parts, cells)

        result = read(mutate(self.base, workbook=shared))
        self.assertEqual(result["categories"], ["A", " B "])
        self.assertEqual(result["series"][0]["name"], "Series")
        self.assertEqual(result["series"][0]["sources"]["categories"]["workbook_cells"][0]["type"], "s")

        def literal(parts: dict) -> None:
            def cells(root: ET.Element) -> None:
                cell = root.find(".//s:c[@r='A2']", NS)
                cell.clear()
                cell.attrib.update({"r": "A2", "t": "str"})
                ET.SubElement(cell, f"{{{S_NS}}}v").text = "A"

            edit_sheet(parts, cells)

        self.assertEqual(read(mutate(self.base, workbook=literal))["categories"], ["A", " B "])

    def test_formats_are_reported_without_changing_numeric_values(self) -> None:
        def styles(parts: dict) -> None:
            root = ET.fromstring(parts["xl/styles.xml"])
            formats = ET.Element(f"{{{S_NS}}}numFmts", {"count": "1"})
            ET.SubElement(formats, f"{{{S_NS}}}numFmt", {"numFmtId": "164", "formatCode": "0.00%"})
            root.insert(0, formats)
            xfs = root.find("s:cellXfs", NS)
            xfs.set("count", "2")
            ET.SubElement(xfs, f"{{{S_NS}}}xf", {"numFmtId": "164", "fontId": "0", "fillId": "0", "borderId": "0"})
            parts["xl/styles.xml"] = ET.tostring(root, encoding="utf-8")
            edit_sheet(parts, lambda sheet: sheet.find(".//s:c[@r='B2']", NS).set("s", "1"))

        result = read(mutate(self.base, workbook=styles))
        source = result["series"][0]["sources"]["values"]
        self.assertEqual(result["series"][0]["values"][0], "18")
        self.assertEqual(source["format_code"], "General")
        self.assertEqual(source["workbook_cells"][0]["number_format_code"], "0.00%")
        self.assertEqual(source["workbook_cells"][0]["number_format_id"], 164)

    def test_decimal_equality_is_exact_and_retains_original_spelling(self) -> None:
        changed = mutate(self.base, chart=lambda root: setattr(cached_value(root), "text", "18.000"), workbook=lambda parts: workbook_value(parts, "1.8E+1"))
        result = read(changed)
        self.assertEqual(result["series"][0]["values"][0], "18.000")
        compare_chart_input(payload(), result)
        precise = "18.0000000000000000000000000000000001"
        self.assert_content_error(mutate(self.base, chart=lambda root: setattr(cached_value(root), "text", precise)))
        matching = mutate(self.base, chart=lambda root: setattr(cached_value(root), "text", precise), workbook=lambda parts: workbook_value(parts, precise))
        self.assertEqual(read(matching)["series"][0]["values"][0], precise)
        with self.assertRaises(ChartReadError):
            compare_chart_input(payload(), read(matching))

    def test_nonfinite_and_excessive_numeric_values_are_rejected(self) -> None:
        for value in ("NaN", "INF", "-Infinity", "not-a-number"):
            with self.subTest(value=value):
                self.assert_content_error(mutate(self.base, chart=lambda root: setattr(cached_value(root), "text", value)))
        for value in ("1e10001", "1e9999999999", "1" * 1025):
            with self.subTest(value=value):
                self.assert_content_error(mutate(self.base, chart=lambda root: setattr(cached_value(root), "text", value)), "resource_limit")

    def test_input_numeric_writer_transport_and_xml_line_endings(self) -> None:
        source = payload()
        source["categories"] = [" First\r\nLine ", "Second\rLine"]
        source["series"][0]["name"] = "  Multi\r\nline\rname "
        source["series"][0]["values"] = [9007199254740992, 0.12345678901234567]
        result = read(render(source))
        self.assertEqual(result["series"][0]["values"][0], "9007199254740992")
        self.assertEqual(result["categories"], [" First\nLine ", "Second\nLine"])
        self.assertEqual(result["series"][0]["name"], "  Multi\nline\nname ")
        compare_chart_input(source, result)

    def test_lossy_integer_and_numeric_string_transport_is_rejected(self) -> None:
        for value in (9007199254740993, "0.1000000000000000001"):
            with self.subTest(value=value):
                source = payload()
                source["series"][0]["values"][0] = value
                data = render(source)
                with self.assertRaises(ChartReadError) as failure:
                    compare_chart_input(source, read(data))
                self.assertEqual(failure.exception.code, "content_validation_failed")
                self.assertIn("loses precision", str(failure.exception))
                with self.assertRaises(ContentError):
                    validate_presentation(data, expected=source_expectation(source))
        source = payload()
        source["categories"] = ["A", "B", "C", "D"]
        source["series"][0]["values"] = [9007199254740992, 9007199254740994, "0.1", "18.0000"]
        compare_chart_input(source, read(render(source)))

    def test_uppercase_type_axes_none_and_existing_scatter_forms(self) -> None:
        source = payload()
        source.update({"type": "COLUMN", "axes": None})
        compare_chart_input(source, read(render(source)))
        forms = [
            {"xs": [0.125, 2], "ys": [18, 27]},
            {"xs": [0.125, 2], "values": [18, 27]},
            {"points": [{"x": 0.125, "y": 18}, {"x": 2, "y": 27}]},
            {"points": [[0.125, 18], [2, 27]]},
        ]
        for series in forms:
            with self.subTest(series=series):
                source = payload("scatter")
                source["series"] = [series]
                compare_chart_input(source, read(render(source)))

    def test_series_order_and_differing_scatter_lengths(self) -> None:
        source = payload("scatter")
        source["series"].append({"name": "Short", "x": [3], "y": [99]})

        def reverse(root: ET.Element) -> None:
            plot = root.find(".//c:scatterChart", NS)
            series = plot.findall("c:ser", NS)
            for item in series:
                plot.remove(item)
            plot.extend(reversed(series))

        result = read(mutate(render(source), chart=reverse))
        self.assertEqual([item["name"] for item in result["series"]], ["Series", "Short"])
        self.assertEqual(result["series"][1]["x"], ["3"])
        compare_chart_input(source, result)

    def test_series_and_total_point_limits(self) -> None:
        source = payload()
        source["categories"] = [str(index) for index in range(201)]
        source["series"][0]["values"] = list(range(201))
        base = render(source)
        for count in (50, 51):
            with self.subTest(count=count):
                def grow(root: ET.Element) -> None:
                    literal_sources(root)
                    plot = root.find(".//c:barChart", NS)
                    first = plot.find("c:ser", NS)
                    for index in range(1, count):
                        item = copy.deepcopy(first)
                        item.find("c:idx", NS).set("val", str(index))
                        item.find("c:order", NS).set("val", str(index))
                        plot.append(item)

                self.assert_content_error(mutate(base, chart=grow), "resource_limit")

    def test_other_plot_types_and_combination_charts_are_explicitly_unsupported(self) -> None:
        def other(root: ET.Element) -> None:
            root.find(".//c:barChart", NS).tag = f"{{{C_NS}}}bubbleChart"

        self.assert_content_error(mutate(self.base, chart=other), "unsupported_content")
        self.assert_content_error(mutate(self.base, chart=lambda root: ET.SubElement(root.find(".//c:plotArea", NS), f"{{{C_NS}}}lineChart")), "unsupported_content")


if __name__ == "__main__":
    unittest.main()
