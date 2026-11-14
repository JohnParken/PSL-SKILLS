"""Extract stored category and value caches from a native DrawingML chart."""

from collections import Counter


CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _q(local):
    return "{" + CHART_NS + "}" + local


def _children(node, local):
    if node is None:
        return []
    expected = _q(local)
    return [child for child in node if child.tag == expected]


def _one(node, local):
    matches = _children(node, local)
    return matches[0] if matches else None


def _text(node):
    if node is None:
        return ""
    return "".join(node.itertext())


def _path(node):
    parts = []
    current = node
    while current is not None:
        parent = current.getparent()
        matches = [] if parent is None else [
            sibling for sibling in parent if sibling.tag == current.tag
        ]
        index = matches.index(current) + 1 if matches else 1
        parts.append(current.tag.rsplit("}", 1)[-1] + "[" + str(index) + "]")
        current = parent
    return "/" + "/".join(reversed(parts))


def _unsigned(value):
    """Parse an OOXML unsigned index/count without allocating declared ranges."""
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        return None
    # The schema type is unsignedInt; reject huge integer strings before int().
    if len(value) > 10:
        return None
    result = int(value, 10)
    return result if result <= 4294967295 else None


def _cache_points(cache, role):
    """Read each physical point once, preserving saved text and diagnostics."""
    points, issues = {}, []
    for point in _children(cache, "pt"):
        index = _unsigned(point.get("idx"))
        if index is None:
            issues.append(role + "_point_index_invalid")
            continue
        value_nodes = _children(point, "v")
        if len(value_nodes) != 1:
            issues.append(role + "_point_value_missing" if not value_nodes else role + "_point_value_ambiguous")
            continue
        if index in points:
            issues.append(role + "_point_index_duplicate")
            continue
        points[index] = _text(value_nodes[0])
    return points, issues


def _source(parent, role):
    """Read literal/cache values only; never evaluate formulas or follow links."""
    names = {"strRef", "numRef", "strLit", "numLit", "multiLvlStrRef", "multiLvlStrLit"}
    candidates = [child for child in ([] if parent is None else parent) if child.tag in {_q(name) for name in names}]
    empty = {"source": "unavailable", "formula": None, "points": {}, "issues": []}
    if len(candidates) != 1:
        empty["issues"] = [role + ("_source_ambiguous" if candidates else "_source_missing")]
        return empty
    source = candidates[0]
    local = source.tag.rsplit("}", 1)[-1]
    reference = local.endswith("Ref")
    multilevel = local.startswith("multiLvl")
    formula = _text(_one(source, "f")) or None if reference else None
    cache_name = "multiLvlStrCache" if multilevel else "strCache" if local.startswith("str") else "numCache"
    cache = _one(source, cache_name) if reference else source
    if cache is None:
        return dict(empty, formula=formula, issues=[role + "_cache_missing"])
    count_node = _one(cache, "ptCount")
    count = _unsigned(count_node.get("val")) if count_node is not None else None
    issues = [role + "_point_count_invalid"] if count_node is not None and count is None else []
    levels = []
    if multilevel:
        for level_index, level in enumerate(_children(cache, "lvl")):
            level_points, problems = _cache_points(level, role + "_level_" + str(level_index))
            levels.append(level_points)
            issues.extend(problems)
        points = levels[0] if levels else {}
        if not levels:
            issues.append(role + "_levels_missing")
    else:
        points, problems = _cache_points(cache, role)
        issues.extend(problems)
    if count is not None and count != len(points):
        issues.append(role + "_point_count_mismatch")
    if count is not None and any(index >= count for group in (levels or [points]) for index in group):
        issues.append(role + "_point_index_out_of_range")
    if not points:
        issues.append(role + "_points_missing")
    result = {"source": "cache" if reference else "literal", "formula": formula,
              "points": points, "format_code": _text(_one(cache, "formatCode")) or None,
              "declared_count": count, "issues": issues}
    if multilevel:
        result["levels"] = levels
    return result


def _series_name(series):
    container = _one(series, "tx")
    text = _one(container, "strRef")
    if text is not None:
        formula = _text(_one(text, "f")) or None
        cache = _one(text, "strCache")
        points = _source_points(cache)
        return (points[min(points)] if points else None), formula
    value = None if container is None else _one(container, "v")
    if value is not None:
        return _text(value), None
    return None, None


def _source_points(cache):
    result = {}
    for point in _children(cache, "pt"):
        try:
            index = int(point.get("idx"), 10)
        except (TypeError, ValueError):
            continue
        value = _one(point, "v")
        if value is not None and index not in result:
            result[index] = _text(value)
    return result


def _series_nodes(root):
    chart = _one(root, "chart")
    plot = _one(chart, "plotArea")
    if plot is None:
        return [], ["plot_area_missing"]
    chart_nodes = [
        child for child in plot
        if isinstance(child.tag, str)
        and child.tag.startswith("{" + CHART_NS + "}")
        and child.tag.rsplit("}", 1)[-1].endswith("Chart")
    ]
    issues = []
    if not chart_nodes:
        issues.append("chart_type_missing")
    result = []
    for chart_node in chart_nodes:
        chart_type = chart_node.tag.rsplit("}", 1)[-1]
        for series in _children(chart_node, "ser"):
            result.append((chart_type, series))
    return result, issues


def read_chart(root):
    """Return chart title, series cache records and cache coverage diagnostics."""
    if root.tag != _q("chartSpace"):
        return {"title": None, "series": [], "external_data_id": None,
                "issues": ["chart_root_invalid"]}

    chart = _one(root, "chart")
    title_text = _one(_one(chart, "title"), "tx")
    title = None
    if title_text is not None:
        texts = [node for node in title_text.iter() if node.tag in (_q("v"), "{http://schemas.openxmlformats.org/drawingml/2006/main}t")]
        title = "\n".join(filter(None, (_text(node) for node in texts))) or None

    external = _one(root, "externalData")
    external_id = None if external is None else external.get("{" + REL_NS + "}id")
    issues = []
    if external is not None and not external_id:
        issues.append("chart_external_data_relationship_missing")

    series_nodes, chart_issues = _series_nodes(root)
    issues.extend(chart_issues)
    if not series_nodes:
        issues.append("chart_series_missing")

    series_records = []
    seen_indices = Counter()
    for order, (chart_type, series) in enumerate(series_nodes, 1):
        source_index = _one(series, "idx")
        source_index_text = None if source_index is None else source_index.get("val")
        name, name_formula = _series_name(series)
        xy_chart = chart_type in ("scatterChart", "bubbleChart")
        category_parent = _one(series, "xVal" if xy_chart else "cat")
        value_parent = _one(series, "yVal" if xy_chart else "val")
        categories = _source(category_parent, "category")
        values = _source(value_parent, "value")
        category_points = categories["points"]
        value_points = values["points"]
        sizes = _source(_one(series, "bubbleSize"), "bubble_size") if chart_type == "bubbleChart" else None
        levels = categories.get("levels", [])
        point_indices = sorted(set(category_points) | set(value_points)
                               | (set(sizes["points"]) if sizes else set())
                               | {index for level in levels for index in level})
        points = [
            {
                "index": point_index,
                "category": category_points.get(point_index),
                "value": value_points.get(point_index),
            }
            for point_index in point_indices
        ]
        for point in points:
            index = point["index"]
            if xy_chart:
                point.update(x=point["category"], y=point["value"])
            if sizes is not None:
                point["bubble_size"] = sizes["points"].get(index)
            if levels:
                point["category_levels"] = [level.get(index) for level in levels]
        series_issues = list(dict.fromkeys(categories["issues"] + values["issues"] + (sizes["issues"] if sizes else [])))
        if sizes is not None and sizes["points"].keys() != value_points.keys():
            series_issues.append("chart_bubble_value_indices_differ")
        seen_indices[source_index_text] += 1
        if source_index_text is not None and seen_indices[source_index_text] > 1:
            series_issues.append("chart_series_source_index_duplicate")
        if category_points and value_points and category_points.keys() != value_points.keys():
            series_issues.append("chart_category_value_indices_differ")
        record = {
            "path": _path(series),
            "series_index": order,
            "source_index": source_index_text,
            "chart_type": chart_type,
            "name": name,
            "name_formula": name_formula,
            "category_formula": categories["formula"],
            "value_formula": values["formula"],
            "category_source": categories["source"],
            "value_source": values["source"],
            "format_code": values.get("format_code"),
            "points": points,
            "issues": list(dict.fromkeys(series_issues)),
        }
        if xy_chart:
            for role, data in (("x", categories), ("y", values)):
                record.update({role + "_" + field: data.get(field) for field in ("formula", "source", "format_code")})
        if sizes is not None:
            record.update({"bubble_size_" + field: sizes.get(field) for field in ("formula", "source", "format_code")})
        if "levels" in categories:
            record["category_levels"] = [
                {"level_index": index, "points": [{"index": key, "value": value} for key, value in sorted(level.items())]}
                for index, level in enumerate(levels)]
        series_records.append(record)
    return {
        "title": title,
        "series": series_records,
        "external_data_id": external_id,
        "issues": list(dict.fromkeys(issues)),
    }
