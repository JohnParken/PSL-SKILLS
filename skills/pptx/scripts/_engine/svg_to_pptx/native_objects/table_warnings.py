# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Table warnings implementation."""
from __future__ import annotations

from .table_shared import (
    Any,
    ET,
    _clean_hex,
    _hex_or_none,
    _maybe_number,
    _number,
    _visible_fallback_texts,
)

def _native_table_header_warnings(
    payload: dict[str, Any],
    table_rows: list[list[Any]],
    column_edges: list[float],
    row_edges: list[float],
    shape_records: list[Any],
    text_cells: list[tuple[Any, int, int]],
) -> list[str]:
    from .table_payload import _cell_payload, _table_header_rows
    from .table_parity import _fallback_table_alignment, _table_parity_style_gaps
    header_rows = _table_header_rows(payload, len(table_rows))
    if header_rows <= 0:
        return []
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    header_text_cells = [item for item in text_cells if item[1] < header_rows]
    if not header_text_cells:
        return []

    missing: list[str] = []
    table_left, table_right = column_edges[0], column_edges[-1]
    header_top, header_bottom = row_edges[0], row_edges[header_rows]
    header_fill = next(
        (
            record.fill
            for record in sorted(
                shape_records,
                key=lambda item: (item.bounds[2] - item.bounds[0]) * (item.bounds[3] - item.bounds[1]),
                reverse=True,
            )
            if (
                record.tag == "rect"
                and record.fill is not None
                and record.bounds[0] <= table_left + 3
                and record.bounds[2] >= table_right - 3
                and record.bounds[1] <= header_top + 3
                and record.bounds[3] >= header_bottom - 3
            )
        ),
        None,
    )
    if header_fill is not None and style.get("header_fill") is None:
        if not all(
            _hex_or_none(_cell_payload(cell).get("fill")) == header_fill
            for row in table_rows[:header_rows]
            for cell in row
        ):
            missing.append("style.header_fill")

    header_text = _clean_hex(
        style.get("header_text"),
        "#FFFFFF" if _hex_or_none(style.get("header_fill")) is not None
        else _clean_hex(style.get("body_text"), "#1F2937"),
    )
    header_style_gaps = list(_table_parity_style_gaps(table_rows, header_text_cells, header_text))
    if any(colors for _, _, _, _, colors in header_style_gaps):
        missing.append("style.header_text")
    if any(bold for _, _, _, bold, _ in header_style_gaps):
        missing.append("columns[].bold")
    # Header cells export centred unless the payload sets align, so a missing
    # align only matches a fallback whose header text is centred.
    unmatched_anchors = sorted({
        _fallback_table_alignment(record.anchor)
        for record, row_idx, col_idx in header_text_cells
        if (_cell_payload(table_rows[row_idx][col_idx]).get("align") or "ctr")
        != _fallback_table_alignment(record.anchor)
    })
    if unmatched_anchors:
        missing.append(
            "columns[].align "
            + "/".join(f'"{value}"' for value in unmatched_anchors)
            + " (header cells export centred unless align is set)"
        )
    if not missing:
        return []
    return [
        "Native PPTX table header style not projected from fallback: "
        + ", ".join(missing)
    ]


def _native_table_fill_warnings(
    table_rows: list[list[Any]],
    header_rows: int,
    column_edges: list[float],
    row_edges: list[float],
    shape_records: list[Any],
) -> list[str]:
    from .table_payload import _cell_payload
    warnings: list[str] = []
    col_count = len(column_edges) - 1
    row_count = len(row_edges) - 1

    def edge_index(value: float, edges: list[float]) -> int | None:
        return next(
            (idx for idx, edge in enumerate(edges) if abs(edge - value) <= 3),
            None,
        )

    seen: set[tuple[str, int, int, str]] = set()
    for record in shape_records:
        if record.tag != "rect" or record.fill in {None, "FFFFFF"}:
            continue
        x1, y1, x2, y2 = record.bounds
        start_col = edge_index(x1, column_edges)
        end_col = edge_index(x2, column_edges)
        start_row = edge_index(y1, row_edges)
        end_row = edge_index(y2, row_edges)
        if None in {start_col, end_col, start_row, end_row}:
            continue
        assert start_col is not None and end_col is not None
        assert start_row is not None and end_row is not None
        if end_col <= start_col or end_row <= start_row:
            continue

        label: tuple[str, int, int, str] | None = None
        targets: list[tuple[int, int]] = []
        if start_col == 0 and end_col == col_count and start_row >= header_rows:
            label = ("row", start_row, end_row, record.fill)
            targets = [
                (row_idx, col_idx)
                for row_idx in range(start_row, end_row)
                for col_idx in range(col_count)
            ]
        elif (
            start_row <= header_rows
            and end_row == row_count
            and end_col - start_col < col_count
        ):
            body_start = max(start_row, header_rows)
            label = ("column", start_col, end_col, record.fill)
            targets = [
                (row_idx, col_idx)
                for row_idx in range(body_start, end_row)
                for col_idx in range(start_col, end_col)
            ]
        if label is None or label in seen or not targets:
            continue
        seen.add(label)
        if all(
            _hex_or_none(_cell_payload(table_rows[row_idx][col_idx]).get("fill"))
            == record.fill
            for row_idx, col_idx in targets
        ):
            continue
        axis, start, end, color = label
        human_start = start + 1
        human_end = end
        span = str(human_start) if human_start == human_end else f"{human_start}-{human_end}"
        hint = (
            f" (one fallback rect spanning several {axis}s reads as one whole-{axis} fill;"
            " draw one rect per column or row when their payload fills differ)"
            if human_start != human_end
            else ""
        )
        warnings.append(
            f"Native PPTX table whole {axis} {span} fill #{color} is not projected to cell fill{hint}"
        )
    return warnings


def _native_table_body_text_warnings(
    payload: dict[str, Any],
    table_rows: list[list[Any]],
    header_rows: int,
    text_cells: list[tuple[Any, int, int]],
) -> list[str]:
    """Report fallback body text colours the payload would not carry."""
    from .table_parity import _table_parity_style_gaps
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    style_body_text = _hex_or_none(style.get("body_text"))
    body_records = [item for item in text_cells if item[1] >= header_rows]
    if not body_records:
        return []
    missing_colors: set[str] = set()
    for record, _, col_idx, _, colors in _table_parity_style_gaps(
        table_rows, body_records, style_body_text
    ):
        if col_idx == 0:
            continue  # first-column emphasis is reported separately
        missing_colors.update(colors)
    if not missing_colors:
        return []
    return [
        "Native PPTX table body text color "
        + "/".join(f"#{color}" for color in sorted(missing_colors))
        + " is not projected to cell color, defaults.run.color, or style.body_text"
    ]


def _native_table_first_column_warnings(
    table_rows: list[list[Any]],
    header_rows: int,
    text_cells: list[tuple[Any, int, int]],
    *,
    style_body_text: str | None = None,
) -> list[str]:
    """Report first-column emphasis the payload would not carry.

    A first column drawn in the table's own body text colour is carried by
    ``style.body_text`` even when the other columns are coloured per cell, so
    only a colour no layer resolves to is reported.
    """
    from .table_parity import _table_parity_style_gaps
    body_records = [item for item in text_cells if item[1] >= header_rows]
    first_column = [item for item in body_records if item[2] == 0]
    if not first_column:
        return []
    missing: list[str] = []
    first_column_style_gaps = list(_table_parity_style_gaps(table_rows, first_column, style_body_text))
    if any(bold for _, _, _, bold, _ in first_column_style_gaps):
        missing.append("bold")
    missing_colors = sorted({
        color
        for _, _, _, _, colors in first_column_style_gaps
        for color in colors
    })
    if missing_colors:
        missing.append("color " + "/".join(f"#{color}" for color in missing_colors))
    if not missing:
        return []
    return [
        "Native PPTX table first-column text style not projected from fallback: "
        + ", ".join(missing)
    ]


def _native_table_inset_graphic_warnings(
    table_rows: list[list[Any]],
    header_rows: int,
    column_edges: list[float],
    row_edges: list[float],
    shape_records: list[Any],
    text_cells: list[tuple[Any, int, int]],
) -> list[str]:
    for record in shape_records:
        if record.tag != "rect" or record.fill in {None, "FFFFFF"}:
            continue
        x1, y1, x2, y2 = record.bounds
        for row_idx in range(header_rows, len(row_edges) - 1):
            for col_idx in range(len(column_edges) - 1):
                cell_x1, cell_x2 = column_edges[col_idx], column_edges[col_idx + 1]
                cell_y1, cell_y2 = row_edges[row_idx], row_edges[row_idx + 1]
                cell_width = cell_x2 - cell_x1
                cell_height = cell_y2 - cell_y1
                if not (
                    x1 > cell_x1 + max(1, cell_width * 0.02)
                    and x2 < cell_x2 - max(1, cell_width * 0.02)
                    and y1 > cell_y1 + max(1, cell_height * 0.02)
                    and y2 < cell_y2 - max(1, cell_height * 0.02)
                    and x2 - x1 <= cell_width * 0.9
                    and y2 - y1 <= cell_height * 0.9
                ):
                    continue
                has_text = any(
                    text_row == row_idx
                    and text_col == col_idx
                    and text_record.x is not None
                    and text_record.y is not None
                    and x1 <= text_record.x <= x2
                    and y1 <= text_record.y <= y2
                    for text_record, text_row, text_col in text_cells
                )
                if has_text:
                    return [
                        "Native PPTX table contains an inset graphical cell with text; "
                        "the a:tbl schema cannot express it and the object should be Native-ready=no"
                    ]
    return []


def _native_table_border_topology_warnings(
    payload: dict[str, Any],
    table_rows: list[list[Any]],
    column_edges: list[float],
    row_edges: list[float],
    shape_records: list[Any],
) -> list[str]:
    from .table_payload import _cell_payload
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    cells = [_cell_payload(cell) for row in table_rows for cell in row]
    per_side = any(
        isinstance(cell.get("borders"), dict)
        and any(
            side in cell["borders"]
            for side in ("left", "right", "top", "bottom")
        )
        for cell in cells
    )
    style_width = _maybe_number(style.get("border_width"))
    style_uniform = style.get("border_color") is not None or (
        style_width is not None and style_width > 0
    )
    cell_uniform = any(
        cell.get("border_color") is not None
        or (_maybe_number(cell.get("border_width")) or 0) > 0
        for cell in cells
    )
    if per_side or not (style_uniform or cell_uniform):
        return []

    covered: set[tuple[str, int, int]] = set()
    row_count = len(row_edges) - 1
    col_count = len(column_edges) - 1
    for record in shape_records:
        if record.stroke is None:
            continue
        x1, y1, x2, y2 = record.bounds
        if record.tag == "rect":
            # A stroked rect on the table's outer bounds draws the outer frame.
            if (
                abs(x1 - column_edges[0]) <= 2 and abs(x2 - column_edges[-1]) <= 2
                and abs(y1 - row_edges[0]) <= 2 and abs(y2 - row_edges[-1]) <= 2
            ):
                for col_idx in range(col_count):
                    covered.update({("h", 0, col_idx), ("h", row_count, col_idx)})
                for row_idx in range(row_count):
                    covered.update({("v", 0, row_idx), ("v", col_count, row_idx)})
            continue
        if record.tag != "line":
            continue
        if abs(y2 - y1) <= 1:
            boundary = next(
                (idx for idx, edge in enumerate(row_edges) if abs(edge - y1) <= 2),
                None,
            )
            if boundary is None:
                continue
            for col_idx in range(col_count):
                if x1 <= column_edges[col_idx] + 2 and x2 >= column_edges[col_idx + 1] - 2:
                    covered.add(("h", boundary, col_idx))
        elif abs(x2 - x1) <= 1:
            boundary = next(
                (idx for idx, edge in enumerate(column_edges) if abs(edge - x1) <= 2),
                None,
            )
            if boundary is None:
                continue
            for row_idx in range(row_count):
                if y1 <= row_edges[row_idx] + 2 and y2 >= row_edges[row_idx + 1] - 2:
                    covered.add(("v", boundary, row_idx))
    full_count = (row_count + 1) * col_count + (col_count + 1) * row_count
    if not covered or len(covered) >= full_count:
        return []
    return [
        "Native PPTX table border topology not projected: fallback rules cover only "
        f"{len(covered)} of {full_count} cardinal cell edges, but payload has only a uniform border"
    ]


def _native_table_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    table_rows: list[list[Any]],
) -> list[str]:
    from .table_payload import _table_header_rows
    from .table_parity import _native_table_metadata_texts, _table_fallback_grid, _table_text_cells
    fallback_texts = _visible_fallback_texts(elem)
    warnings: list[str] = []
    if fallback_texts:
        metadata_counts = _native_table_metadata_texts(table_rows)
        missing: list[str] = []
        seen_counts: dict[str, int] = {}
        for text in fallback_texts:
            seen_counts[text] = seen_counts.get(text, 0) + 1
            if seen_counts[text] > metadata_counts.get(text, 0):
                missing.append(text)
        if missing:
            sample = ", ".join(repr(text) for text in missing[:5])
            suffix = "" if len(missing) <= 5 else f", and {len(missing) - 5} more"
            warnings.append(
                "Native PPTX table fallback text is missing from metadata columns/rows "
                f"and will disappear with --native-charts-and-tables: {sample}{suffix}"
            )

    grid = _table_fallback_grid(elem, payload, table_rows)
    if grid is None:
        return warnings
    column_edges, row_edges, shape_records, text_records = grid
    text_cells = _table_text_cells(
        text_records,
        table_rows,
        column_edges,
        row_edges,
    )
    fallback_heights = [
        row_edges[idx + 1] - row_edges[idx]
        for idx in range(len(row_edges) - 1)
    ]
    non_uniform = (
        max(fallback_heights) - min(fallback_heights)
        > max(1.0, max(fallback_heights) * 0.02)
    )
    raw_heights = payload.get("row_heights")
    payload_uniform = not isinstance(raw_heights, list) or not raw_heights or (
        max(_number(value, "table row height") for value in raw_heights)
        - min(_number(value, "table row height") for value in raw_heights)
        <= 1e-6
    )
    if non_uniform and payload_uniform:
        warnings.append(
            "Native PPTX table fallback row heights are non-uniform, but payload "
            "row_heights is missing or uniform"
        )

    header_rows = _table_header_rows(payload, len(table_rows))
    warnings.extend(
        _native_table_header_warnings(
            payload,
            table_rows,
            column_edges,
            row_edges,
            shape_records,
            text_cells,
        )
    )
    warnings.extend(
        _native_table_fill_warnings(
            table_rows,
            header_rows,
            column_edges,
            row_edges,
            shape_records,
        )
    )
    table_style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    warnings.extend(
        _native_table_first_column_warnings(
            table_rows,
            header_rows,
            text_cells,
            style_body_text=_hex_or_none(table_style.get("body_text")),
        )
    )
    warnings.extend(
        _native_table_body_text_warnings(
            payload,
            table_rows,
            header_rows,
            text_cells,
        )
    )
    warnings.extend(
        _native_table_border_topology_warnings(
            payload,
            table_rows,
            column_edges,
            row_edges,
            shape_records,
        )
    )
    warnings.extend(
        _native_table_inset_graphic_warnings(
            table_rows,
            header_rows,
            column_edges,
            row_edges,
            shape_records,
            text_cells,
        )
    )
    return warnings
