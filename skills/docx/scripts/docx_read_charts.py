"""Read cached values and source ranges from classic DrawingML chart-space XML.

Source ranges are parsed without evaluating formulas. Each series also has
``point_counts`` with the declared ``ptCount`` and observed sparse point count
for its category and value caches; ``None`` means that count was unavailable.
"""

from __future__ import annotations

import re
import unicodedata
from xml.etree.ElementTree import Element


CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAX_OOXML_UINT = 4_294_967_295
UINT_TEXT = re.compile(r"[0-9]+")
NUMBER_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
A1_CELL_TEXT = re.compile(r"(?P<column_absolute>\$?)(?P<column>[A-Za-z]{1,3})"
                          r"(?P<row_absolute>\$?)(?P<row>[1-9][0-9]*)")
MAX_EXCEL_ROW = 1_048_576
MAX_EXCEL_COLUMN = 16_384

SUPPORTED_CHART_TYPES = {
    "pieChart",
    "pie3DChart",
    "doughnutChart",
    "barChart",
    "bar3DChart",
    "lineChart",
    "line3DChart",
    "areaChart",
    "area3DChart",
    "radarChart",
}
UNSUPPORTED_XY_CHART_TYPES = {"scatterChart", "bubbleChart"}
SOURCE_TAGS = {
    "strRef",
    "numRef",
    "strLit",
    "numLit",
    "multiLvlStrRef",
    "multiLvlStrLit",
}
CACHE_TAGS = {"strCache", "numCache"}


def _q(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _children(node: Element | None, local: str, namespace: str = CHART_NS) -> list[Element]:
    if node is None:
        return []
    tag = _q(namespace, local)
    return [child for child in node if child.tag == tag]


def _one_child(
    node: Element | None,
    local: str,
    context: str,
    *,
    required: bool = False,
    namespace: str = CHART_NS,
) -> Element | None:
    matches = _children(node, local, namespace)
    if len(matches) > 1:
        raise ValueError(f"duplicate_{context}_{local}")
    if not matches:
        if required:
            raise ValueError(f"missing_{context}_{local}")
        return None
    return matches[0]


def _text(node: Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text


def _local_name(node: Element) -> str | None:
    if not isinstance(node.tag, str):
        return None
    return node.tag.rsplit("}", 1)[-1]


def _bounded_uint(value: str | None, error_code: str) -> int:
    if value is None or UINT_TEXT.fullmatch(value) is None:
        raise ValueError(error_code)
    digits = value.lstrip("0") or "0"
    if len(digits) > 10:
        raise ValueError(error_code)
    parsed = int(digits)
    if parsed > MAX_OOXML_UINT:
        raise ValueError(error_code)
    return parsed


def _validate_numeric(value: str) -> None:
    if NUMBER_TEXT.fullmatch(value.strip()) is None:
        raise ValueError("invalid_numeric_value")


def _column_number(label: str) -> int | None:
    number = 0
    for character in label.upper():
        number = number * 26 + ord(character) - ord("A") + 1
    return number if number <= MAX_EXCEL_COLUMN else None


def _column_label(number: int) -> str:
    label = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def _parse_a1_cell(value: str) -> tuple[int, int, str] | None:
    match = A1_CELL_TEXT.fullmatch(value)
    if match is None:
        return None
    column = _column_number(match.group("column"))
    row_text = match.group("row")
    if column is None or len(row_text) > 7:
        return None
    row = int(row_text)
    if row > MAX_EXCEL_ROW:
        return None
    rendered = (
        f"{match.group('column_absolute')}{_column_label(column)}"
        f"{match.group('row_absolute')}{row}"
    )
    return row, column, rendered


def _valid_sheet_name(value: str, *, quoted: bool) -> bool:
    if not value or len(value) > 31 or value.startswith("'") or value.endswith("'"):
        return False
    if any(character in value for character in "[]:*?/\\"):
        return False
    if any(unicodedata.category(character) == "Cc" for character in value):
        return False
    return quoted or all(character.isalnum() or character in "_." for character in value)


def _parse_sheet_qualifier(value: str, *, quoted: bool) -> str | None:
    sheet = value
    if value.startswith("["):
        closing = value.find("]")
        if closing < 2 or "]" in value[closing + 1:]:
            return None
        workbook = value[1:closing]
        if any(character in workbook for character in "[]:/?*\\!"):
            return None
        if any(unicodedata.category(character) == "Cc" for character in workbook):
            return None
        sheet = value[closing + 1:]
    elif "[" in value or "]" in value:
        return None
    if not _valid_sheet_name(sheet, quoted=quoted):
        return None
    return sheet


def _split_formula_qualifier(formula: str) -> tuple[str | None, str] | None:
    if formula.startswith("'"):
        qualifier_chars = []
        cursor = 1
        while cursor < len(formula):
            character = formula[cursor]
            if character == "'":
                if cursor + 1 < len(formula) and formula[cursor + 1] == "'":
                    qualifier_chars.append("'")
                    cursor += 2
                    continue
                break
            qualifier_chars.append(character)
            cursor += 1
        if cursor >= len(formula) or formula[cursor + 1:cursor + 2] != "!":
            return None
        qualifier = "".join(qualifier_chars)
        sheet = _parse_sheet_qualifier(qualifier, quoted=True)
        if sheet is None:
            return None
        return sheet, formula[cursor + 2:]

    if "!" not in formula:
        return None, formula
    if formula.count("!") != 1:
        return None
    qualifier, reference = formula.split("!", 1)
    sheet = _parse_sheet_qualifier(qualifier, quoted=False)
    if sheet is None:
        return None
    return sheet, reference


def _parse_reference_formula(formula: str) -> tuple[dict[str, object], dict[str, object] | None]:
    unsupported = {"status": "unsupported"}
    qualified = _split_formula_qualifier(formula)
    if qualified is None:
        return unsupported, None
    sheet, reference = qualified
    parts = reference.split(":")
    if len(parts) > 2:
        return unsupported, None
    first = _parse_a1_cell(parts[0])
    last = first if len(parts) == 1 else _parse_a1_cell(parts[1])
    if first is None or last is None:
        return unsupported, None
    start_row, start_column, first_text = first
    end_row, end_column, last_text = last
    if end_row < start_row or end_column < start_column:
        return unsupported, None
    horizontal = start_row == end_row
    vertical = start_column == end_column
    size = end_column - start_column + 1 if horizontal else end_row - start_row + 1
    if not horizontal and not vertical:
        size = None
    range_text = first_text if len(parts) == 1 else f"{first_text}:{last_text}"
    metadata = {"status": "a1_range", "sheet": sheet, "range": range_text}
    geometry = {
        "start_row": start_row,
        "start_column": start_column,
        "size": size,
        "horizontal": horizontal,
    }
    return metadata, geometry


def _cell_at_index(geometry: dict[str, object], index: int) -> str:
    row = geometry["start_row"]
    column = geometry["start_column"]
    if geometry["horizontal"]:
        column = int(column) + index
    else:
        row = int(row) + index
    return f"{_column_label(int(column))}{row}"


def _attach_value_cells(
    points: list[dict[str, object]],
    indices: list[int],
    categories: dict[str, object],
    values: dict[str, object],
) -> None:
    geometry = values.get("_reference_geometry")
    if not isinstance(geometry, dict) or geometry["size"] is None:
        return
    size = int(geometry["size"])
    declared_counts = [
        count for count in (categories["declared_count"], values["declared_count"])
        if count is not None
    ]
    if any(count != size for count in declared_counts) or any(index >= size for index in indices):
        return
    for point in points:
        if point["value"] is None:
            point["value_cell"] = _cell_at_index(geometry, int(point["index"]))


def _parse_cache(cache: Element, *, numeric: bool) -> dict[str, object]:
    count_node = _one_child(cache, "ptCount", "cache")
    declared_count = None
    if count_node is not None:
        declared_count = _bounded_uint(count_node.get("val"), "invalid_point_count")

    format_node = _one_child(cache, "formatCode", "cache")
    format_code = _text(format_node) if format_node is not None else None
    points: dict[int, str] = {}
    for point in _children(cache, "pt"):
        point_index = _bounded_uint(point.get("idx"), "invalid_point_index")
        if point_index in points:
            raise ValueError("duplicate_point_index")
        value_node = _one_child(point, "v", "point", required=True)
        point_value = _text(value_node)
        if numeric:
            _validate_numeric(point_value)
        points[point_index] = point_value

    if declared_count is not None and any(index >= declared_count for index in points):
        raise ValueError("point_index_out_of_range")

    issues = []
    if not points:
        issues.append("points_missing")
    if declared_count is not None and declared_count != len(points):
        issues.append("point_count_mismatch")
    return {
        "points": points,
        "declared_count": declared_count,
        "actual_count": len(points),
        "format_code": format_code,
        "issues": issues,
    }


def _empty_data(issue: str) -> dict[str, object]:
    return {
        "source": "unavailable",
        "points": {},
        "declared_count": None,
        "actual_count": None,
        "format_code": None,
        "issues": [issue],
    }


def _read_data(parent: Element | None, role: str) -> dict[str, object]:
    if parent is None:
        return _empty_data(f"{role}_source_missing")

    source_nodes = [child for child in parent if _local_name(child) in SOURCE_TAGS
                    and isinstance(child.tag, str) and child.tag.startswith(f"{{{CHART_NS}}}")]
    if len(source_nodes) > 1:
        raise ValueError(f"duplicate_{role}_source")
    if not source_nodes:
        return _empty_data(f"{role}_source_missing")

    source = source_nodes[0]
    source_name = _local_name(source)
    allowed = {"strRef", "numRef", "strLit", "numLit"} if role == "category" else {"numRef", "numLit"}
    if source_name not in allowed:
        return _empty_data(f"unsupported_{role}_source")

    is_reference = source_name.endswith("Ref")
    is_numeric = source_name.startswith("num")
    reference = None
    reference_geometry = None
    if is_reference:
        formula = _one_child(source, "f", f"{role}_reference", required=True)
        reference, reference_geometry = _parse_reference_formula(_text(formula))
        cache_name = "numCache" if is_numeric else "strCache"
        other_cache_names = CACHE_TAGS - {cache_name}
        other_caches = [node for name in other_cache_names for node in _children(source, name)]
        if other_caches:
            raise ValueError(f"invalid_{role}_reference_cache")
        cache = _one_child(source, cache_name, f"{role}_reference")
        if cache is None:
            result = _empty_data(f"{role}_cache_missing")
            result["reference"] = reference
            result["_reference_geometry"] = reference_geometry
            return result
        parsed = _parse_cache(cache, numeric=is_numeric)
        result = {
            "source": "cache",
            **parsed,
        }
    else:
        parsed = _parse_cache(source, numeric=is_numeric)
        result = {
            "source": "literal",
            **parsed,
        }

    result["issues"] = [f"{role}_{issue}" for issue in result["issues"]]
    if role != "value":
        result["format_code"] = None
    if reference is not None:
        result["reference"] = reference
        result["_reference_geometry"] = reference_geometry
    return result


def _rich_text(node: Element) -> str | None:
    paragraphs = _children(node, "p", DRAWING_NS)
    if paragraphs:
        paragraph_text = []
        found_text = False
        for paragraph in paragraphs:
            text_nodes = list(paragraph.iter(_q(DRAWING_NS, "t")))
            found_text = found_text or bool(text_nodes)
            paragraph_text.append("".join(_text(text_node) for text_node in text_nodes))
        return "\n".join(paragraph_text) if found_text else None

    text_nodes = list(node.iter(_q(DRAWING_NS, "t")))
    if not text_nodes:
        return None
    return "".join(_text(text_node) for text_node in text_nodes)


def _read_text_reference(reference: Element, context: str) -> str | None:
    _one_child(reference, "f", f"{context}_reference", required=True)
    other_caches = _children(reference, "numCache")
    if other_caches:
        raise ValueError(f"invalid_{context}_reference_cache")
    cache = _one_child(reference, "strCache", f"{context}_reference")
    if cache is None:
        return None
    parsed = _parse_cache(cache, numeric=False)
    points = parsed["points"]
    if not points:
        return None
    return points[min(points)]


def _read_text_container(container: Element | None, context: str) -> str | None:
    if container is None:
        return None
    candidates = []
    for name in ("v", "strRef", "rich"):
        candidates.extend(_children(container, name))
    if len(candidates) > 1:
        raise ValueError(f"duplicate_{context}_text_source")
    if not candidates:
        return None

    candidate = candidates[0]
    name = _local_name(candidate)
    if name == "v":
        return _text(candidate)
    if name == "strRef":
        return _read_text_reference(candidate, context)
    return _rich_text(candidate)


def _read_title(chart: Element | None, issues: list[str]) -> str | None:
    title = _one_child(chart, "title", "chart")
    if title is None:
        return None
    text_container = _one_child(title, "tx", "title")
    text = _read_text_container(text_container, "title")
    if text is None:
        issues.append("title_unavailable")
    return text


def _read_series_name(series: Element) -> str | None:
    text_container = _one_child(series, "tx", "series")
    return _read_text_container(text_container, "series_name")


def _deduplicate(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _read_series(series: Element, chart_type: str, index: int) -> dict[str, object]:
    category_node = _one_child(series, "cat", "series")
    value_node = _one_child(series, "val", "series")
    categories = _read_data(category_node, "category")
    values = _read_data(value_node, "value")
    category_points = categories["points"]
    value_points = values["points"]
    point_indices = sorted(set(category_points) | set(value_points))
    points = [
        {
            "index": point_index,
            "category": category_points.get(point_index),
            "value": value_points.get(point_index),
        }
        for point_index in point_indices
    ]
    _attach_value_cells(points, point_indices, categories, values)
    series_issues = _deduplicate(categories["issues"] + values["issues"])
    if category_points and value_points and category_points.keys() != value_points.keys():
        series_issues.append("category_value_indices_differ")
    result = {
        "index": index,
        "chart_type": chart_type,
        "name": _read_series_name(series),
        "format_code": values["format_code"],
        "value_source": values["source"],
        "category_source": categories["source"],
        "points": points,
        "issues": series_issues,
        "point_counts": {
            "category": {
                "declared": categories["declared_count"],
                "actual": categories["actual_count"],
            },
            "value": {
                "declared": values["declared_count"],
                "actual": values["actual_count"],
            },
        },
    }
    if "reference" in values:
        result["value_reference"] = values["reference"]
    if "reference" in categories:
        result["category_reference"] = categories["reference"]
    return result


def _has_category_value_shape(series: Element) -> bool:
    return bool(_children(series, "cat") and _children(series, "val"))


def read_chart_cache(root: Element) -> dict[str, object]:
    """Return chart titles and cached series values from a classic chartSpace element.

    Sparse cache points are joined and sorted by their original ``idx``. This
    function never opens related parts, follows relationships, evaluates
    formulas, or exposes formula text.
    """
    if not isinstance(root, Element) or root.tag != _q(CHART_NS, "chartSpace"):
        raise ValueError("wrong_chart_space_root")

    issues: list[str] = []
    chart = _one_child(root, "chart", "chart_space", required=True)
    title = _read_title(chart, issues)
    external_data = _one_child(root, "externalData", "chart_space")
    external_data_id = None
    if external_data is not None:
        external_data_id = external_data.get(_q(REL_NS, "id"))
        if not external_data_id:
            raise ValueError("external_data_relationship_missing")

    plot_area = _one_child(chart, "plotArea", "chart")
    series_results: list[dict[str, object]] = []
    if plot_area is None:
        issues.append("plot_area_missing")
    else:
        chart_nodes = [
            child for child in plot_area
            if isinstance(child.tag, str)
            and child.tag.startswith(f"{{{CHART_NS}}}")
            and (_local_name(child) or "").endswith("Chart")
        ]
        if not chart_nodes:
            issues.append("chart_type_missing")
        for chart_node in chart_nodes:
            chart_type = _local_name(chart_node) or ""
            chart_series = _children(chart_node, "ser")
            if chart_type in UNSUPPORTED_XY_CHART_TYPES:
                issues.append("unsupported_chart_type")
                continue
            if chart_type not in SUPPORTED_CHART_TYPES and (
                not chart_series or not all(_has_category_value_shape(item) for item in chart_series)
            ):
                issues.append("unsupported_chart_type")
                continue
            if not chart_series:
                issues.append("series_missing")
                continue
            for chart_series_node in chart_series:
                series_results.append(
                    _read_series(chart_series_node, chart_type, len(series_results) + 1)
                )

    return {
        "title": title,
        "series": series_results,
        "issues": _deduplicate(issues),
        "external_data_id": external_data_id,
    }
