"""Native PowerPoint table conversion."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET



from .marker_common import (
    _hex_or_none,
    _maybe_number,
    _number,
    _visible_fallback_texts,
)



# Adapted from table.py; upstream MIT, Hugo He, 2025-2026.
def _native_table_border_topology_warnings(
    payload: dict[str, Any],
    table_rows: list[list[Any]],
    column_edges: list[float],
    row_edges: list[float],
    shape_records: list[Any],
) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _cell_payload,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _native_table_body_text_warnings,
        _native_table_fill_warnings,
        _native_table_first_column_warnings,
        _native_table_header_warnings,
        _native_table_inset_graphic_warnings,
        _native_table_metadata_texts,
        _table_fallback_grid,
        _table_header_rows,
        _table_text_cells,
    )
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
