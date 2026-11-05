"""Read and verify the seven public chart types from validated OOXML packages.

All numbers in the returned JSON-friendly data are finite decimal strings;
missing points remain None. Reading never evaluates workbook formulas or
silently substitutes cached values for an unavailable workbook reference.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from .workbook_read import ChartReadError, MAX_POINTS, REL_BASE, R_NS, WorkbookReader, decimal_text, semantic_root, unsigned, values_equal


C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
MAX_SERIES = 50
MAX_TOTAL_POINTS = 10000
SUPPORTED_TYPES = {"column", "bar", "line", "area", "pie", "doughnut", "scatter"}
_PLOT_TYPES = {"barChart": "column", "lineChart": "line", "areaChart": "area", "pieChart": "pie", "doughnutChart": "doughnut", "scatterChart": "scatter"}


def _tag(name: str) -> str:
    return f"{{{C_NS}}}{name}"


def _one(parent: ET.Element, name: str, *, required: bool = True) -> ET.Element | None:
    matches = parent.findall(_tag(name))
    if len(matches) > 1 or (required and not matches):
        raise ChartReadError(f"Expected one chart {name}", field_path="chart." + name)
    return matches[0] if matches else None


def _cache(root: ET.Element, numeric: bool, field: str) -> tuple[list[str | None], dict]:
    allowed = {_tag("ptCount"), _tag("pt"), _tag("formatCode")} if numeric else {_tag("ptCount"), _tag("pt")}
    if any(child.tag not in allowed for child in root):
        raise ChartReadError("Unsupported chart cache content", "unsupported_content", field_path=field)
    count_node = _one(root, "ptCount")
    count = unsigned(count_node.get("val"), MAX_POINTS, field + ".point_count")
    values: list[str | None] = [None] * count
    seen = set()
    formats = {}
    for point in root.findall(_tag("pt")):
        index = unsigned(point.get("idx"), MAX_POINTS - 1, field + ".point.index")
        if index >= count or index in seen:
            raise ChartReadError("Duplicate or out-of-range chart point index", field_path=field, point_index=index)
        seen.add(index)
        if any(child.tag != _tag("v") for child in point):
            raise ChartReadError("Unsupported chart point content", "unsupported_content", field_path=field, point_index=index)
        value_node = _one(point, "v")
        if len(value_node):
            raise ChartReadError("Chart point value must be scalar", "unsupported_content", field_path=field, point_index=index)
        value = value_node.text or ""
        values[index] = decimal_text(value, field) if numeric and value.strip() else None if numeric else value
        if point.get("formatCode") is not None:
            formats[str(index)] = point.get("formatCode")
    format_node = _one(root, "formatCode", required=False) if numeric else None
    return values, {
        "point_count": count,
        "present_indices": sorted(seen),
        "missing_indices": [index for index, value in enumerate(values) if value is None],
        "format_code": format_node.text if format_node is not None else None,
        "point_formats": formats,
    }


class _ChartReader:
    def __init__(self, package, chart_part: str, workbooks: dict) -> None:
        self.root = semantic_root(package, chart_part)
        if self.root is None or self.root.tag != _tag("chartSpace"):
            raise ChartReadError("Unsupported chart XML root", "unsupported_content", part=chart_part)
        self.workbook_part = None
        self.workbook = None
        self.checked_references = 0
        self.literal_sources = 0
        external = _one(self.root, "externalData", required=False)
        if external is not None:
            relation = package.relationships.get(chart_part, {}).get(external.get(f"{{{R_NS}}}id", ""))
            if relation is None or relation.external or relation.kind != REL_BASE + "package":
                raise ChartReadError("Chart workbook relationship is missing or unsupported", "unsupported_content", part=chart_part)
            self.workbook_part = relation.target
            workbook_package = workbooks.get(relation.target)
            if workbook_package is None:
                raise ChartReadError("Chart workbook was not validated", "unsupported_content", part=relation.target)
            self.workbook = WorkbookReader(workbook_package)

    def source(self, parent: ET.Element, field: str, *, numeric: bool | None = None) -> tuple[list[str | None], dict, bool]:
        allowed = {_tag("numRef"), _tag("numLit"), _tag("strRef"), _tag("strLit")}
        if len(parent) != 1 or parent[0].tag not in allowed:
            raise ChartReadError("Unsupported or ambiguous chart data source", "unsupported_content", field_path=field)
        node = parent[0]
        kind = node.tag.rsplit("}", 1)[-1]
        is_numeric = kind.startswith("num")
        if numeric is not None and numeric != is_numeric:
            raise ChartReadError("Chart source has an unexpected numeric/string type", "unsupported_content", field_path=field)
        reference = None
        if kind.endswith("Ref"):
            permitted = {_tag("f"), _tag("numCache" if is_numeric else "strCache")}
            if any(child.tag not in permitted for child in node):
                raise ChartReadError("Unsupported chart reference content", "unsupported_content", field_path=field)
            formula = _one(node, "f")
            if len(formula):
                raise ChartReadError("Chart reference must be scalar", "unsupported_content", field_path=field)
            reference = formula.text or ""
            cache = _one(node, "numCache" if is_numeric else "strCache")
        else:
            cache = node
        values, metadata = _cache(cache, is_numeric, field)
        metadata.update({"kind": kind, "reference": reference, "workbook_status": "not_applicable"})
        if reference is not None:
            if self.workbook is None:
                raise ChartReadError("Referenced chart data requires an embedded workbook", "unsupported_content", field_path=field, reference=reference)
            expected, workbook_metadata = self.workbook.read(reference, is_numeric)
            if len(expected) != len(values):
                raise ChartReadError("Chart cache count differs from its workbook range", field_path=field, reference=reference)
            for index, (cached, cell) in enumerate(zip(values, expected)):
                if not values_equal(cached, cell, is_numeric):
                    raise ChartReadError(
                        "Chart cache differs from its embedded workbook", field_path=field, reference=reference,
                        point_index=index, cached_value=cached, workbook_value=cell,
                    )
            self.checked_references += 1
            metadata.update(workbook_metadata)
            metadata["workbook_status"] = "checked"
        else:
            self.literal_sources += 1
        return values, metadata, is_numeric

    def name(self, series: ET.Element, field: str) -> tuple[str | None, dict]:
        tx = _one(series, "tx", required=False)
        if tx is None:
            self.literal_sources += 1
            return None, {"kind": "absent", "reference": None, "point_count": 0, "workbook_status": "not_applicable"}
        if len(tx) == 1 and tx[0].tag == _tag("v"):
            if len(tx[0]):
                raise ChartReadError("Series name must be scalar", "unsupported_content", field_path=field)
            self.literal_sources += 1
            return tx[0].text or "", {"kind": "direct", "reference": None, "point_count": 1, "workbook_status": "not_applicable"}
        values, metadata, _ = self.source(tx, field, numeric=False)
        if len(values) != 1:
            raise ChartReadError("Series name must contain exactly one point", field_path=field)
        return values[0], metadata


def inspect_chart(package, chart_part: str, workbooks: dict) -> dict:
    """Read one supported plot and verify every numRef/strRef against its workbook.

    The caller must validate the presentation and workbook packages first.
    Literal sources have workbook_status='not_applicable'; no unresolved
    reference is returned as a successfully checked chart.
    """
    reader = _ChartReader(package, chart_part, workbooks)
    chart = _one(reader.root, "chart")
    plot_area = _one(chart, "plotArea")
    plots = [child for child in plot_area if child.tag.rsplit("}", 1)[-1].endswith("Chart")]
    if len(plots) != 1 or plots[0].tag not in {_tag(name) for name in _PLOT_TYPES}:
        raise ChartReadError("Content inspection supports one column/bar/line/area/pie/doughnut/scatter plot", "unsupported_content")
    plot = plots[0]
    kind = _PLOT_TYPES[plot.tag.rsplit("}", 1)[-1]]
    if kind == "column":
        direction = _one(plot, "barDir").get("val")
        if direction not in {"bar", "col"}:
            raise ChartReadError("Unsupported bar direction", "unsupported_content")
        kind = "bar" if direction == "bar" else "column"
    series_nodes = plot.findall(_tag("ser"))
    if not 1 <= len(series_nodes) <= MAX_SERIES:
        raise ChartReadError("Chart must contain between 1 and 50 series", "resource_limit")
    if kind in {"pie", "doughnut"} and len(series_nodes) != 1:
        raise ChartReadError("Pie-family content inspection supports one series", "unsupported_content")
    series = []
    identifiers = set()
    orders = set()
    total_points = 0
    for number, node in enumerate(series_nodes):
        field = f"chart.series[{number}]"
        index = unsigned(_one(node, "idx").get("val"), 4294967295, field + ".index")
        order = unsigned(_one(node, "order").get("val"), 4294967295, field + ".order")
        if index in identifiers or order in orders:
            raise ChartReadError("Duplicate chart series index or order", field_path=field)
        identifiers.add(index)
        orders.add(order)
        name, name_source = reader.name(node, field + ".name")
        item = {"index": index, "order": order, "name": name, "sources": {"name": name_source}}
        if kind == "scatter":
            x_values, x_source, _ = reader.source(_one(node, "xVal"), field + ".x", numeric=True)
            y_values, y_source, _ = reader.source(_one(node, "yVal"), field + ".y", numeric=True)
            if len(x_values) != len(y_values):
                raise ChartReadError("Scatter X/Y point counts differ", field_path=field)
            item.update({"x": x_values, "y": y_values})
            item["sources"].update({"x": x_source, "y": y_source})
            total_points += len(x_values)
        else:
            categories, category_source, category_numeric = reader.source(_one(node, "cat"), field + ".categories")
            values, value_source, _ = reader.source(_one(node, "val"), field + ".values", numeric=True)
            if len(categories) != len(values):
                raise ChartReadError("Category/value point counts differ", field_path=field)
            item.update({"categories": categories, "category_type": "number" if category_numeric else "string", "values": values})
            item["sources"].update({"categories": category_source, "values": value_source})
            total_points += len(values)
        if total_points > MAX_TOTAL_POINTS:
            raise ChartReadError("Chart exceeds 10000 total data points", "resource_limit")
        series.append(item)
    series.sort(key=lambda item: item["order"])
    categories = None
    if kind != "scatter" and all(item["categories"] == series[0]["categories"] for item in series):
        categories = series[0]["categories"]
    return {
        "type": kind, "numeric_encoding": "decimal_string", "categories": categories, "series": series,
        "cache": {"status": "checked", "point_count": total_points},
        "workbook": {
            "status": "checked" if reader.checked_references else "not_applicable",
            "part": reader.workbook_part, "checked_references": reader.checked_references,
            "literal_sources": reader.literal_sources, "unchecked_reasons": [],
        },
    }


def _input_text(value: object) -> str:
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def _input_number(value: object, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ChartReadError("Boolean chart data is unsupported", "unsupported_content", field_path=field)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ChartReadError("Invalid chart input number", field_path=field) from error
    if not math.isfinite(number):
        raise ChartReadError("Chart input number must be finite", field_path=field)
    # The writer transports numbers through float, then emits integral floats
    # as ints. Exact JSON integers and numeric strings must survive that step.
    # For Python float inputs, the original JSON lexeme is no longer available.
    transported = str(int(number)) if number.is_integer() else str(number)
    if isinstance(value, (int, str)):
        try:
            original = Decimal(str(value).strip())
        except InvalidOperation as error:
            raise ChartReadError("Invalid exact chart input number", field_path=field) from error
        if original != Decimal(transported):
            raise ChartReadError(
                "Chart input number loses precision during writer transport", field_path=field,
                expected_value=str(value), transported_value=transported,
            )
    return transported


def _compare_values(expected: list, actual: list, numeric: bool, field: str) -> None:
    if len(expected) != len(actual):
        raise ChartReadError("Chart data count differs from its source input", field_path=field)
    for index, (left, right) in enumerate(zip(expected, actual)):
        if not values_equal(left, right, numeric):
            raise ChartReadError("Chart data differs from its source input", field_path=field, point_index=index, expected_value=left, actual_value=right)


def compare_chart_input(payload: dict, result: dict) -> None:
    """Compare chart type, ordered series names, categories and data to input.

    Styling, drawing layout, axes and number-format rendering are outside this
    content comparison. XML line-ending normalization and the existing numeric
    writer transport are the only value normalizations performed here. Lossy
    transport of exact integers or numeric strings is rejected. Float inputs
    carry their already parsed Python value, not an original JSON number lexeme.
    """
    kind = re.sub(r"[^a-z0-9]", "", str(payload.get("type") or payload.get("chart_type") or "column").lower())
    if kind not in SUPPORTED_TYPES:
        raise ChartReadError("Unsupported source chart type", "unsupported_content", field_path="chart.type")
    if kind != result.get("type"):
        raise ChartReadError("Chart type differs from its source input", field_path="chart.type", expected_value=kind, actual_value=result.get("type"))
    expected_series = payload.get("series", [])
    actual_series = result.get("series", [])
    if len(expected_series) != len(actual_series):
        raise ChartReadError("Chart series count differs from its source input", field_path="chart.series")
    axes = payload.get("axes") or {}
    category_kind = axes.get("category", {}).get("kind") or "text"
    date_categories = kind == "area" and re.sub(r"[^a-z0-9]", "", str(category_kind).lower()) == "date"
    for index, (source, actual) in enumerate(zip(expected_series, actual_series)):
        field = f"chart.series[{index}]"
        name = _input_text(source.get("name") or f"Series {index + 1}")
        if name != actual.get("name"):
            raise ChartReadError("Chart series name differs from its source input", field_path=field + ".name", expected_value=name, actual_value=actual.get("name"))
        if kind == "scatter":
            fields = ("x", "y")
            if source.get("points") is not None:
                points = source["points"]
                expected_values = {
                    "x": [point.get("x") if isinstance(point, dict) else point[0] for point in points],
                    "y": [point.get("y") if isinstance(point, dict) else point[1] for point in points],
                }
            else:
                expected_values = {
                    "x": source.get("x", source.get("xs", [])),
                    "y": source.get("y", source.get("ys", source.get("values", []))),
                }
        else:
            categories = [_input_number(value, "chart.categories") if date_categories else _input_text(value) for value in payload.get("categories", [])]
            if actual.get("category_type") != ("number" if date_categories else "string"):
                raise ChartReadError("Chart category type differs from its source input", field_path=field + ".categories")
            _compare_values(categories, actual.get("categories", []), date_categories, field + ".categories")
            fields = ("values",)
            expected_values = {"values": source.get("values", [])}
        for value_field in fields:
            values = [_input_number(value, field + "." + value_field) for value in expected_values[value_field]]
            _compare_values(values, actual.get(value_field, []), True, field + "." + value_field)
