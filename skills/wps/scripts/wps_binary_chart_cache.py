"""Read bounded values from an already-parsed classic DrawingML chart."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree.ElementTree import Element


CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_MAX_UINT32 = 4_294_967_295
_NUMBER = re.compile(
    r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?\Z"
)
_SUPPORTED_CHARTS = frozenset(
    {
        "pieChart", "pie3DChart", "doughnutChart", "ofPieChart", "barChart",
        "bar3DChart", "lineChart", "line3DChart", "areaChart", "area3DChart",
    }
)
_KNOWN_UNSUPPORTED_CHARTS = frozenset(
    {
        "bubbleChart", "radarChart", "scatterChart", "stockChart",
        "surfaceChart", "surface3DChart",
    }
)
_PLOT_METADATA = frozenset(
    {"layout", "dTable", "spPr", "extLst", "catAx", "dateAx", "serAx", "valAx"}
)
_CACHE_NAMES = frozenset({"strCache", "numCache", "strLit", "numLit"})
_REFERENCE_CACHE_NAMES = _CACHE_NAMES | {"multiLvlStrCache"}
_REFERENCE_NAMES = frozenset({"strRef", "numRef"})
_SOURCE_NAMES = _CACHE_NAMES | _REFERENCE_NAMES
_UNSUPPORTED_SOURCE_NAMES = frozenset({"multiLvlStrRef", "multiLvlStrCache"})
_SOURCE_CANDIDATES = _SOURCE_NAMES | _UNSUPPORTED_SOURCE_NAMES


class ChartCacheLimit(ValueError):
    """Raised when a chart cache exceeds a resource bound."""


class ChartCacheBudget:
    """Cumulative limits; reuse one instance across a document's chart parts.

    Caps are 256 series, 25,000 cache point candidates, 1 MiB output text,
    4,096 UTF-8 bytes per value, 1,024 source/cache candidates, and 100,000
    relevant XML nodes. Point candidates include invalid data and title caches.
    """

    MAX_SERIES = 256
    MAX_POINTS = 25_000
    MAX_TEXT_BYTES = 1_048_576
    MAX_VALUE_BYTES = 4_096
    MAX_SOURCE_CANDIDATES = 1_024
    MAX_RELEVANT_NODES = 100_000

    def __init__(self) -> None:
        self._series = 0
        self._points = 0
        self._text_bytes = 0
        self._sources = 0
        self._nodes = 0

    def _charge(self, field: str, amount: int, cap: int, label: str) -> None:
        current = getattr(self, field)
        if amount < 0 or current + amount > cap:
            raise ChartCacheLimit(f"Chart cache {label} limit exceeded")
        setattr(self, field, current + amount)

    def charge_series(self) -> None:
        self._charge("_series", 1, self.MAX_SERIES, "series")

    def charge_points(self) -> None:
        self._charge("_points", 1, self.MAX_POINTS, "point")

    def charge_sources(self) -> None:
        self._charge("_sources", 1, self.MAX_SOURCE_CANDIDATES, "source")

    def charge_nodes(self) -> None:
        self._charge("_nodes", 1, self.MAX_RELEVANT_NODES, "markup")

    def charge_text(self, amount: int) -> None:
        self._charge("_text_bytes", amount, self.MAX_TEXT_BYTES, "text")


def _tag(local: str, namespace: str = CHART_NS) -> str:
    return f"{{{namespace}}}{local}"


def _name(element: Element) -> str | None:
    tag = element.tag
    if not isinstance(tag, str) or not tag.startswith("{"):
        return None
    namespace, separator, local = tag[1:].partition("}")
    return local if separator and namespace == CHART_NS else None


def _children(parent: Element, tag: str) -> list[Element]:
    return [child for child in parent if child.tag == tag]


def _one(parent: Element, tag: str) -> tuple[Element | None, bool]:
    found: Element | None = None
    for child in parent:
        if child.tag == tag:
            if found is not None:
                return None, True
            found = child
    return found, False


def _uint32(raw: Any) -> int | None:
    if not isinstance(raw, str) or not raw or len(raw) > 10:
        return None
    if not raw.isascii() or not raw.isdigit():
        return None
    value = int(raw)
    return value if value <= _MAX_UINT32 else None


def _text_size(text: str, budget: ChartCacheBudget) -> int:
    if len(text) > budget.MAX_VALUE_BYTES:
        raise ChartCacheLimit("Chart cache value limit exceeded")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ChartCacheLimit("Chart cache text is not valid UTF-8") from exc
    if size > budget.MAX_VALUE_BYTES:
        raise ChartCacheLimit("Chart cache value limit exceeded")
    return size


def _emit_text(text: str, budget: ChartCacheBudget) -> None:
    budget.charge_text(_text_size(text, budget))


def _unique_issues(issues: list[str], budget: ChartCacheBudget) -> list[str]:
    unique = list(dict.fromkeys(issues))
    for issue in unique:
        budget.charge_text(len(issue.encode("utf-8")))
    return unique


def _read_count(cache: Element) -> tuple[int | None, str | None]:
    node, duplicate = _one(cache, _tag("ptCount"))
    if duplicate:
        return None, "pt_count_ambiguous"
    if node is None:
        return None, "pt_count_missing"
    count = _uint32(node.attrib.get("val"))
    return (count, None) if count is not None else (None, "pt_count_invalid")


def _precount_cache(
    cache: Element, budget: ChartCacheBudget, *, nested: bool = False,
    charge_source: bool = False,
) -> None:
    if charge_source:
        budget.charge_sources()
    for child in cache:
        budget.charge_nodes()
        points = child if nested else (child,)
        for point in points:
            if nested:
                budget.charge_nodes()
            if not nested and point.tag != _tag("pt"):
                continue
            if point.tag == _tag("pt"):
                budget.charge_points()
            for value in point:
                budget.charge_nodes()


def _precount_source(source: Element, budget: ChartCacheBudget) -> None:
    name = _name(source)
    budget.charge_sources()
    if name in _REFERENCE_NAMES:
        for cache in source:
            budget.charge_nodes()
            if _name(cache) in _CACHE_NAMES:
                _precount_cache(cache, budget, charge_source=True)
            elif _name(cache) == "multiLvlStrCache":
                _precount_cache(
                    cache, budget, nested=True, charge_source=True
                )
    elif name in _CACHE_NAMES:
        _precount_cache(source, budget)
    elif name == "multiLvlStrRef":
        for cache in source:
            budget.charge_nodes()
            if _name(cache) == "multiLvlStrCache":
                _precount_cache(
                    cache, budget, nested=True, charge_source=True
                )
    elif name == "multiLvlStrCache":
        _precount_cache(source, budget, nested=True)


def _precount_wrapper(wrapper: Element, budget: ChartCacheBudget) -> None:
    for source in wrapper:
        budget.charge_nodes()
        if _name(source) in _SOURCE_CANDIDATES:
            _precount_source(source, budget)


def _precount_title(chart: Element, budget: ChartCacheBudget) -> None:
    for title in chart:
        if title.tag != _tag("title"):
            continue
        for tx in title:
            if tx.tag == _tag("tx"):
                _precount_wrapper(tx, budget)
                for rich in tx:
                    if _name(rich) == "rich":
                        for paragraph in rich:
                            budget.charge_nodes()
                            for run in paragraph:
                                budget.charge_nodes()
                                for text_node in run:
                                    budget.charge_nodes()


def _plot_groups(
    plot_area: Element, budget: ChartCacheBudget
) -> tuple[list[tuple[str, Element, list[Element]]], list[str]]:
    groups: list[tuple[str, Element, list[Element]]] = []
    issues: list[str] = []
    for child in plot_area:
        budget.charge_nodes()
        name = _name(child)
        if name is None:
            issues.append("unsupported_plot_markup")
            continue
        series = []
        for item in child:
            budget.charge_nodes()
            if item.tag == _tag("ser"):
                budget.charge_series()
                series.append(item)
                for part in item:
                    budget.charge_nodes()
                    if part.tag in {_tag("tx"), _tag("cat"), _tag("val")}:
                        _precount_wrapper(part, budget)
        is_group = name.endswith("Chart") or bool(series)
        if is_group:
            supported = name in _SUPPORTED_CHARTS
            if supported or name in _KNOWN_UNSUPPORTED_CHARTS:
                chart_type = name
            else:
                chart_type = "unsupportedChart"
            groups.append((chart_type, child, series))
            if not supported:
                issue = (
                    f"unsupported_chart_type:{name}"
                    if name in _KNOWN_UNSUPPORTED_CHARTS
                    else "unsupported_chart_type"
                )
                issues.append(issue)
        elif name not in _PLOT_METADATA:
            issues.append("unsupported_plot_markup")
    return groups, issues


def _missing_ranges(
    count: int | None, indices: list[int]
) -> list[dict[str, int]] | None:
    if count is None:
        return None
    gaps = []
    start = 0
    for index in indices:
        if index > start:
            gaps.append({"first_index": start, "last_index": index - 1})
        start = index + 1
    if start < count:
        gaps.append({"first_index": start, "last_index": count - 1})
    return gaps


def _channel(
    status: str,
    source: str | None,
    count: int | None,
    points: list[dict[str, Any]],
    missing: list[dict[str, int]] | None,
    issues: list[str],
    budget: ChartCacheBudget,
    format_code: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "source": source,
        "declared_count": count,
        "points": points,
        "missing_ranges": missing,
        "issues": _unique_issues(issues, budget),
    }
    if format_code is not None:
        result["format_code"] = format_code
    return result


def _unavailable(source: str | None, issue: str, budget: ChartCacheBudget):
    return _channel("unavailable", source, None, [], None, [issue], budget)


def _format_code(cache: Element, budget: ChartCacheBudget) -> tuple[str | None, str | None]:
    node, duplicate = _one(cache, _tag("formatCode"))
    if duplicate:
        return None, "format_code_ambiguous"
    if node is None:
        return None, None
    if len(node) or node.text is None:
        return None, "format_code_invalid"
    _emit_text(node.text, budget)
    return node.text, None


def _numeric_value(text: str) -> bool:
    if not _NUMBER.fullmatch(text):
        return False
    try:
        return Decimal(text).is_finite()
    except InvalidOperation:
        return False


def _parse_cache(
    cache: Element, source: str, kind: str, budget: ChartCacheBudget
) -> dict[str, Any]:
    count, count_issue = _read_count(cache)
    if count_issue in {"pt_count_ambiguous", "pt_count_invalid"}:
        return _channel("unavailable", source, None, [], None, [count_issue], budget)

    format_code = None
    issues: list[str] = []
    if kind == "num":
        format_code, format_issue = _format_code(cache, budget)
        if format_issue:
            issues.append(format_issue)

    parsed = []
    seen: set[int] = set()
    for point in _children(cache, _tag("pt")):
        index = _uint32(point.attrib.get("idx"))
        if index is None:
            point_issue = "point_index_invalid"
            break
        if index in seen:
            point_issue = "point_index_duplicate"
            break
        if count is not None and index >= count:
            point_issue = "point_index_out_of_range"
            break
        seen.add(index)
        value, duplicate = _one(point, _tag("v"))
        if duplicate or value is None or len(value):
            point_issue = "point_structure_invalid"
            break
        parsed.append((index, value))
    else:
        point_issue = None

    if point_issue is not None:
        return _channel(
            "unavailable", source, count, [], _missing_ranges(count, []),
            issues + [point_issue], budget, format_code
        )

    points = []
    for index, element in sorted(parsed, key=lambda pair: pair[0]):
        text = element.text or ""
        size = _text_size(text, budget)
        if kind == "num" and not _numeric_value(text):
            issues.append("point_value_invalid")
            continue
        budget.charge_text(size)
        points.append({"index": index, "value": text})

    if count_issue == "pt_count_missing":
        issues.append(count_issue)
    indices = [point["index"] for point in points]
    missing = _missing_ranges(count, indices)
    partial = bool(issues) or bool(missing)
    return _channel(
        "partial" if partial else "complete", source, count, points, missing,
        issues, budget, format_code
    )


def _data_source(wrapper: Element | None, duplicate: bool, allowed: frozenset[str],
                 budget: ChartCacheBudget) -> dict[str, Any]:
    if duplicate:
        return _unavailable(None, "wrapper_ambiguous", budget)
    if wrapper is None:
        return _unavailable(None, "source_missing", budget)
    sources = [child for child in wrapper if _name(child) in _SOURCE_CANDIDATES]
    if not sources:
        return _unavailable(None, "source_missing", budget)
    if len(sources) != 1:
        return _unavailable(None, "source_ambiguous", budget)

    source_node = sources[0]
    source_name = _name(source_node)
    if source_name in _UNSUPPORTED_SOURCE_NAMES:
        return _unavailable(source_name, "source_unsupported", budget)
    if source_name not in allowed:
        caches = [
            child for child in source_node
            if _name(child) in _REFERENCE_CACHE_NAMES
        ]
        source = _name(caches[0]) if len(caches) == 1 else source_name
        return _unavailable(source, "source_unsupported", budget)
    if source_name in {"strLit", "numLit"}:
        kind = "str" if source_name == "strLit" else "num"
        return _parse_cache(source_node, source_name, kind, budget)
    if source_name not in _REFERENCE_NAMES:
        return _unavailable(source_name, "source_unsupported", budget)

    expected = "strCache" if source_name == "strRef" else "numCache"
    kind = "str" if expected == "strCache" else "num"
    caches = [
        child for child in source_node
        if _name(child) in _REFERENCE_CACHE_NAMES
    ]
    if not caches:
        return _unavailable(source_name, "cache_missing", budget)
    if len(caches) != 1:
        return _unavailable(source_name, "cache_ambiguous", budget)
    cache_name = _name(caches[0])
    if cache_name == "multiLvlStrCache":
        return _unavailable(cache_name, "source_unsupported", budget)
    if cache_name != expected:
        return _unavailable(cache_name, "cache_type_mismatch", budget)
    return _parse_cache(caches[0], cache_name, kind, budget)


def _title_ref(source: Element, budget: ChartCacheBudget) -> tuple[str | None, str | None]:
    caches = [
        child for child in source
        if _name(child) in _REFERENCE_CACHE_NAMES
    ]
    if not caches:
        return None, None
    if len(caches) != 1 or _name(caches[0]) != "strCache":
        return None, "title_cache_invalid"
    cache = caches[0]
    count, count_issue = _read_count(cache)
    if count_issue in {"pt_count_ambiguous", "pt_count_invalid"}:
        return None, "title_cache_invalid"
    seen: set[int] = set()
    title = None
    for point in _children(cache, _tag("pt")):
        index = _uint32(point.attrib.get("idx"))
        value, duplicate = _one(point, _tag("v"))
        if (
            index is None
            or index in seen
            or (count is not None and index >= count)
            or duplicate
            or value is None
            or len(value)
        ):
            return None, "title_cache_invalid"
        seen.add(index)
        if index == 0:
            title = value.text or ""
    return title, None


def _cached_name(tx: Element, budget: ChartCacheBudget) -> tuple[str | None, str | None]:
    choices = [child for child in tx if _name(child) in {"v", "strRef", "rich"}]
    if len(choices) > 1:
        return None, "series_title_ambiguous"
    if not choices:
        return None, None
    choice = choices[0]
    name = _name(choice)
    if name == "v":
        if len(choice):
            return None, "series_title_invalid"
        return choice.text or "", None
    if name != "strRef":
        return None, "series_title_unsupported"
    return _title_ref(choice, budget)


def _rich_title(rich: Element, budget: ChartCacheBudget) -> str:
    pieces = []
    size = 0
    for paragraph in rich:
        if paragraph.tag != _tag("p", DRAWING_NS):
            continue
        for run in paragraph:
            if run.tag not in {_tag("r", DRAWING_NS), _tag("fld", DRAWING_NS)}:
                continue
            for node in run:
                if node.tag == _tag("t", DRAWING_NS):
                    value = node.text or ""
                    size += _text_size(value, budget)
                    if size > budget.MAX_VALUE_BYTES:
                        raise ChartCacheLimit("Chart cache value limit exceeded")
                    pieces.append(value)
    budget.charge_text(size)
    return "".join(pieces)


def _chart_title(chart: Element, budget: ChartCacheBudget) -> tuple[str | None, list[str]]:
    title, duplicate = _one(chart, _tag("title"))
    if duplicate:
        return None, ["title_ambiguous"]
    if title is None:
        return None, []
    tx, duplicate = _one(title, _tag("tx"))
    if duplicate:
        return None, ["title_ambiguous"]
    if tx is None:
        return None, []
    choices = [child for child in tx if _name(child) in {"rich", "strRef", "v"}]
    if len(choices) != 1:
        return None, ["title_unsupported" if not choices else "title_ambiguous"]
    child = choices[0]
    name = _name(child)
    if name == "rich":
        return _rich_title(child, budget), []
    if name == "v":
        if len(child):
            return None, ["title_invalid"]
        value = child.text or ""
        _emit_text(value, budget)
        return value, []
    value, issue = _title_ref(child, budget)
    if value is not None:
        _emit_text(value, budget)
    return (value, ["title_invalid"]) if issue else (value, [])


def _series_metadata(series: Element) -> tuple[int | None, int | None, list[str]]:
    values = []
    issues = []
    for name in ("idx", "order"):
        node, duplicate = _one(series, _tag(name))
        if duplicate:
            values.append(None)
            issues.append(f"series_{name}_ambiguous")
        elif node is None:
            values.append(None)
            issues.append(f"series_{name}_missing")
        else:
            value = _uint32(node.attrib.get("val"))
            values.append(value)
            if value is None:
                issues.append(f"series_{name}_invalid")
    return values[0], values[1], issues


def _series_title(series: Element, budget: ChartCacheBudget) -> tuple[str | None, list[str]]:
    tx, duplicate = _one(series, _tag("tx"))
    if duplicate:
        return None, ["series_title_ambiguous"]
    if tx is None:
        return None, []
    name, issue = _cached_name(tx, budget)
    if name is not None:
        _emit_text(name, budget)
    return name, [issue] if issue else []


def _parse_series(
    chart_type: str, element: Element, budget: ChartCacheBudget
) -> dict[str, Any]:
    index, order, issues = _series_metadata(element)
    name, title_issues = _series_title(element, budget)
    issues.extend(title_issues)
    cat, cat_duplicate = _one(element, _tag("cat"))
    categories = _data_source(
        cat, cat_duplicate, frozenset({"strRef", "numRef", "strLit", "numLit"}), budget
    )
    val, val_duplicate = _one(element, _tag("val"))
    values = _data_source(val, val_duplicate, frozenset({"numRef", "numLit"}), budget)
    if categories["status"] != "complete":
        issues.append("categories_not_complete")
    if values["status"] != "complete":
        issues.append("values_not_complete")
    category_count = categories["declared_count"]
    value_count = values["declared_count"]
    if category_count is not None and value_count is not None and category_count != value_count:
        issues.append("series_count_mismatch")
    budget.charge_text(len(chart_type.encode("utf-8")))
    return {
        "chart_type": chart_type,
        "index": index,
        "order": order,
        "name": name,
        "categories": categories,
        "values": values,
        "issues": _unique_issues(issues, budget),
    }


def read_chart_cache(root: Element, budget: ChartCacheBudget) -> dict[str, Any]:
    """Return stored cache values without resolving formulas or relationships."""
    if not isinstance(root, Element) or root.tag != _tag("chartSpace"):
        raise ValueError("Invalid chartSpace element")
    if not isinstance(budget, ChartCacheBudget):
        raise ValueError("Invalid chart cache budget")
    chart, duplicate = _one(root, _tag("chart"))
    if duplicate or chart is None:
        raise ValueError("Invalid chartSpace chart cardinality")
    plot, duplicate = _one(chart, _tag("plotArea"))
    if duplicate or plot is None:
        raise ValueError("Invalid chart plotArea cardinality")

    groups, issues = _plot_groups(plot, budget)
    _precount_title(chart, budget)
    title, title_issues = _chart_title(chart, budget)
    issues.extend(title_issues)
    series = [
        _parse_series(chart_type, item, budget)
        for chart_type, _group, items in groups
        for item in items
    ]
    if not series:
        issues.append("no_usable_series")
    if any(item["issues"] for item in series):
        issues.append("series_metadata_or_channel_incomplete")
    final_issues = _unique_issues(issues, budget)
    return {
        "status": "partial" if final_issues else "stored_data",
        "title": title,
        "series": series,
        "issues": final_issues,
    }
