"""Native PowerPoint table conversion."""

from __future__ import annotations

from typing import Any



from .marker_common import (
    _clean_hex,
    _hex_or_none,
)



# Adapted from table.py; upstream MIT, Hugo He, 2025-2026.
def _native_table_header_warnings(
    payload: dict[str, Any],
    table_rows: list[list[Any]],
    column_edges: list[float],
    row_edges: list[float],
    shape_records: list[Any],
    text_cells: list[tuple[Any, int, int]],
) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _cell_payload,
        _fallback_table_alignment,
        _table_header_rows,
        _table_parity_style_gaps,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _cell_payload,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _table_parity_style_gaps,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _table_parity_style_gaps,
    )
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
