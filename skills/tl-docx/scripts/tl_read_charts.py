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


def _source(parent, role):
    """Return one reference or literal source without resolving its formula."""
    names = {"strRef", "numRef", "strLit", "numLit", "multiLvlStrRef", "multiLvlStrLit"}
    children = [] if parent is None else parent
    candidates = [child for child in children if child.tag in {_q(name) for name in names}]
    if len(candidates) > 1:
        return {"source": "unavailable", "formula": None, "points": {},
                "issues": [role + "_source_ambiguous"]}
    if not candidates:
        return {"source": "unavailable", "formula": None, "points": {},
                "issues": [role + "_source_missing"]}

    source_node = candidates[0]
    local = source_node.tag.rsplit("}", 1)[-1]
    is_reference = local.endswith("Ref")
    if local.startswith("multiLvl"):
        return {"source": "unsupported", "formula": _text(_one(source_node, "f")) or None,
                "points": {}, "issues": [role + "_multilevel_source_unsupported"]}
    if local not in ("strRef", "numRef", "strLit", "numLit"):
        return {"source": "unsupported", "formula": None, "points": {},
                "issues": [role + "_source_unsupported"]}

    formula_node = _one(source_node, "f") if is_reference else None
    formula = _text(formula_node) or None
    cache_name = ("strCache" if local.startswith("str") else "numCache")
    cache_node = _one(source_node, cache_name) if is_reference else source_node
    if cache_node is None:
        return {"source": "unavailable", "formula": formula, "points": {},
                "format_code": None, "declared_count": None,
                "issues": [role + "_cache_missing"]}

    points = {}
    issues = []
    for point in _children(cache_node, "pt"):
        index_node = point.get("idx")
        try:
            index = int(index_node, 10)
        except (TypeError, ValueError):
            issues.append(role + "_point_index_invalid")
            continue
        value_node = _one(point, "v")
        if value_node is None:
            issues.append(role + "_point_value_missing")
            continue
        if index in points:
            issues.append(role + "_point_index_duplicate")
            continue
        points[index] = _text(value_node)

    count_node = _one(cache_node, "ptCount")
    declared_count = None
    if count_node is not None:
        try:
            declared_count = int(count_node.get("val"), 10)
        except (TypeError, ValueError):
            issues.append(role + "_point_count_invalid")
    if declared_count is not None and declared_count != len(points):
        issues.append(role + "_point_count_mismatch")
    if not points:
        issues.append(role + "_points_missing")
    return {
        "source": "cache" if is_reference else "literal",
        "formula": formula,
        "points": points,
        "format_code": _text(_one(cache_node, "formatCode")) or None,
        "declared_count": declared_count,
        "issues": issues,
    }


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
        category_parent = _one(series, "cat")
        value_parent = _one(series, "val")
        categories = _source(category_parent, "category")
        values = _source(value_parent, "value")
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
        series_issues = list(dict.fromkeys(categories["issues"] + values["issues"]))
        seen_indices[source_index_text] += 1
        if source_index_text is not None and seen_indices[source_index_text] > 1:
            series_issues.append("chart_series_source_index_duplicate")
        if category_points and value_points and category_points.keys() != value_points.keys():
            series_issues.append("chart_category_value_indices_differ")
        series_records.append({
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
        })
    return {
        "title": title,
        "series": series_records,
        "external_data_id": external_id,
        "issues": list(dict.fromkeys(issues)),
    }
