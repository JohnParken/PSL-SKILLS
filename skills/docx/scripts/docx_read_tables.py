"""Project stored Word table rows onto logical columns without changing the document."""

import re
from itertools import takewhile

from office.validators.advanced import w

MAX_COLUMNS = 4096
MAX_COLUMN_LABEL_CHARS = 256 * 1024
MAX_MERGE_LABEL_CHARS = 128
ON = {"true", "1", "on"}
OFF = {"false", "0", "off"}
DECIMAL = re.compile(r"[+]?[0-9]+")


def _one(parent, name):
    matches = parent.findall(w(name))
    if len(matches) > 1:
        raise ValueError(f"duplicate_{name}")
    return matches[0] if matches else None


def _integer(node, name, default):
    child = _one(node, name)
    if child is None:
        return default
    raw = child.get(w("val"), "").strip()
    if not DECIMAL.fullmatch(raw):
        raise ValueError(f"invalid_{name}")
    significant = raw.lstrip("+").lstrip("0") or "0"
    if len(significant) > 4 or int(significant) > MAX_COLUMNS:
        raise ValueError("table_column_limit_exceeded")
    return int(significant)


def _repeat_header(properties):
    element = _one(properties, "tblHeader")
    if element is None:
        return False
    value = element.get(w("val"), "true")
    if value not in ON | OFF:
        raise ValueError("invalid_tblHeader")
    return value in ON


def _read_row(row, row_index, visible_children, cell_text, previous, issues):
    """Return physical cells with their stored coordinates and active merge starts."""
    props = _one(row, "trPr")
    before = 0 if props is None else _integer(props, "gridBefore", 0)
    after = 0 if props is None else _integer(props, "gridAfter", 0)
    repeated = False if props is None else _repeat_header(props)
    if props is not None and any(props.find(w(name)) is not None for name in ("ins", "del", "trPrChange")):
        issues.add("table_row_revision_unresolved")
    column = before + 1
    cells, sources, active = [], [], {}
    for element in visible_children(row, "tc"):
        props = _one(element, "tcPr")
        span = 1 if props is None else _integer(props, "gridSpan", 1)
        if not span or column + span - 1 > MAX_COLUMNS:
            raise ValueError("invalid_table_cell_span")
        text = cell_text(element)
        item = {"column": column, "column_span": span, "text": text}
        merge = None if props is None else _one(props, "vMerge")
        if merge is not None:
            mode = merge.get(w("val"), "continue")
            item["vertical_merge"] = mode
            key = (column, span)
            if mode == "restart":
                item["row_span"] = 1
                active[key] = (row_index, item)
            elif mode == "continue" and key in previous:
                start_row, origin = previous[key]
                origin["row_span"] += 1
                item["merge_anchor"] = {"row": start_row, "column": column}
                anchor_text = origin["text"]
                if len(anchor_text) <= MAX_MERGE_LABEL_CHARS and anchor_text.strip():
                    item["merge_anchor"]["label"] = anchor_text
                active[key] = previous[key]
                if text.strip():
                    issues.add("nonempty_merge_continuation")
            elif mode == "continue":
                issues.add("unresolved_vertical_merge")
            else:
                raise ValueError("invalid_vMerge")
        if props is not None and _one(props, "hMerge") is not None:
            issues.add("legacy_horizontal_merge_unresolved")
        if props is not None and _one(props, "cellMerge") is not None:
            issues.add("table_merge_revision_unresolved")
        cells.append(item)
        sources.append(element)
        column += span
    end_column = column - 1 + after
    if end_column > MAX_COLUMNS:
        raise ValueError("table_column_limit_exceeded")
    return {
        "type": "table_row", "row_index": row_index, "grid_before": before,
        "grid_after": after, "repeat_header": repeated, "cells": cells,
    }, sources, active, end_column


def _header_spans(rows, count):
    # Store each label once. A column's path follows intersecting spans in row order.
    return [
        {"row": row["row_index"], "column": cell["column"], "column_span": cell["column_span"], "text": cell["text"]}
        for row in rows[:count] for cell in row["cells"]
        if cell["text"].strip() and cell.get("vertical_merge") != "continue"
    ]


def _header_columns(spans, issues):
    """Resolve the ordered labels for each covered grid column once per table."""
    cost = sum(span["column_span"] * (len(span["text"]) + 1) for span in spans)
    if cost > MAX_COLUMN_LABEL_CHARS:
        issues.add("table_header_column_labels_over_limit")
        return []
    by_column = {}
    for span in spans:
        for column in range(span["column"], span["column"] + span["column_span"]):
            by_column.setdefault(column, []).append(span["text"])
    return [{"column": column, "labels": labels} for column, labels in sorted(by_column.items())]


def _merged_groups(row, header_count):
    """Keep row attribution with the row, including when it is on a later page."""
    for cell in row["cells"]:
        anchor = cell.get("merge_anchor")
        if anchor is None:
            if cell.get("row_span", 1) < 2:
                continue
            anchor = {"row": row["row_index"], "column": cell["column"]}
            if len(cell["text"]) <= MAX_MERGE_LABEL_CHARS and cell["text"].strip():
                anchor["label"] = cell["text"]
        if anchor["row"] > header_count:
            yield {**anchor, "column_span": cell["column_span"]}


def read_table(table, visible_children, cell_text, header_count=None):
    """Return table metadata and physical row blocks with logical positions (1-based)."""
    grid = _one(table, "tblGrid")
    columns = 0 if grid is None else len(grid.findall(w("gridCol")))
    if columns > MAX_COLUMNS:
        raise ValueError("table_column_limit_exceeded")
    issues, rows, previous = set(), [], {}
    if not columns:
        issues.add("table_grid_inferred")
    for index, source in enumerate(visible_children(table, "tr"), 1):
        row, cells, previous, width = _read_row(source, index, visible_children, cell_text, previous, issues)
        if columns and width != columns:
            issues.add("table_row_grid_differs")
        columns = max(columns, width)
        rows.append((row, cells))
    row_blocks = [row for row, _ in rows]
    repeated = [row["row_index"] for row in row_blocks if row["repeat_header"]]
    prefix = len(list(takewhile(lambda row: row["repeat_header"], row_blocks)))
    if len(repeated) != prefix:
        issues.add("noncontiguous_repeat_headers")
    ambiguous_grid = issues & {
        "unresolved_vertical_merge", "legacy_horizontal_merge_unresolved", "table_merge_revision_unresolved",
        "table_row_revision_unresolved", "table_row_grid_differs",
    }
    if header_count is not None:
        if header_count > len(rows):
            raise ValueError("requested header rows exceed the table row count")
        source, count = "explicit", header_count
    elif 0 < prefix < len(rows) and len(repeated) == prefix:
        source, count = "repeat_header", prefix
    elif rows:
        # A candidate is useful to the reader but has no confirmed header semantics.
        source, count = "first_row_candidate", 1
        if prefix == len(rows):
            issues.add("all_rows_marked_repeat_header")
    else:
        source, count = "unavailable", 0
    if source == "first_row_candidate" and len(repeated) != len(rows):
        issues.add("table_header_unconfirmed")
    spans = [] if ambiguous_grid else _header_spans(row_blocks, count)
    header = {"source": source, "row_count": count, "spans": spans}
    if spans:
        header["columns"] = _header_columns(spans, issues)
    confirmed_count = 0 if source == "first_row_candidate" else count
    for row in row_blocks[confirmed_count:]:
        groups = list(_merged_groups(row, confirmed_count))
        if groups:
            row["merged_groups"] = groups
    cell_total = sum(len(row["cells"]) for row in row_blocks)
    table_block = {
        "type": "table", "row_count": len(rows), "column_count": columns, "physical_cell_count": cell_total,
        "header": header,
        "repeat_header_count": len(repeated), "issues": sorted(issues),
    }
    return table_block, rows
