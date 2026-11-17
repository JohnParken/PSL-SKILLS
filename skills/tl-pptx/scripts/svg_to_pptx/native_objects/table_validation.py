"""Native PowerPoint table conversion."""

from __future__ import annotations

from .table import _TableMergeRegion

from typing import Any

from semantic_table import expand_semantic_table_payload


from .marker_common import (
    _number,
    _powerpoint_line_width_emu,
)



# Adapted from table.py; upstream MIT, Hugo He, 2025-2026.
def _table_span_value(
    cell_data: dict[str, Any],
    key: str,
    *,
    row_idx: int,
    col_idx: int,
) -> int:
    value = cell_data.get(key, 1)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeError(
            f"Native PPTX table cell R{row_idx}C{col_idx} {key} must be a "
            "positive JSON integer"
        )
    return value


def _merge_covered_cell_is_blank(value: Any) -> bool:
    if value is None or value == "":
        return True
    if not isinstance(value, dict):
        return False
    # The semantic-table schema has already validated formatting fields.
    # Covered cells may retain them, but cannot carry a text body.
    if "paragraphs" in value:
        return False
    if (
        "merge_continuation" in value
        and value["merge_continuation"] is not True
    ):
        return False
    return value.get("text") in (None, "")


def _resolve_table_merge_layout(
    payload: dict[str, Any],
    table_rows: list[list[Any]],
    col_count: int,
) -> dict[tuple[int, int], _TableMergeRegion]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _TABLE_CANONICAL_SPAN_KEYS,
        _TABLE_TOP_LEVEL_SPAN_KEYS,
        _TABLE_UNSUPPORTED_SPAN_KEYS,
        _TableMergeRegion,
        _cell_payload,
    )
    for key in _TABLE_TOP_LEVEL_SPAN_KEYS:
        if key in payload:
            raise RuntimeError(
                f"Native PPTX table uses unsupported top-level merged-cell field: {key}"
            )

    owners: dict[tuple[int, int], _TableMergeRegion] = {}
    for row_idx, row in enumerate(table_rows, start=1):
        for col_idx, cell in enumerate(row, start=1):
            if isinstance(cell, dict):
                used_keys = sorted(
                    key for key in _TABLE_UNSUPPORTED_SPAN_KEYS if key in cell
                )
                if used_keys:
                    keys = ", ".join(used_keys)
                    raise RuntimeError(
                        f"Native PPTX table cell R{row_idx}C{col_idx} uses "
                        f"unsupported merged-cell field(s): {keys}; use row_span/col_span "
                        "on the merge anchor"
                    )

            position = (row_idx - 1, col_idx - 1)
            owner = owners.get(position)
            if owner is not None:
                if isinstance(cell, dict) and any(
                    key in cell for key in _TABLE_CANONICAL_SPAN_KEYS
                ):
                    raise RuntimeError(
                        f"Native PPTX table merge anchor R{row_idx}C{col_idx} overlaps "
                        f"merge rooted at R{owner.row + 1}C{owner.col + 1}"
                    )
                if not _merge_covered_cell_is_blank(cell):
                    raise RuntimeError(
                        f"Native PPTX table merge-covered cell R{row_idx}C{col_idx} "
                        "must be blank"
                    )
                continue

            cell_data = _cell_payload(cell)
            row_span = _table_span_value(
                cell_data, "row_span", row_idx=row_idx, col_idx=col_idx,
            )
            col_span = _table_span_value(
                cell_data, "col_span", row_idx=row_idx, col_idx=col_idx,
            )
            if row_span == 1 and col_span == 1:
                continue
            if (
                row_idx - 1 + row_span > len(table_rows)
                or col_idx - 1 + col_span > col_count
            ):
                raise RuntimeError(
                    f"Native PPTX table merge rooted at R{row_idx}C{col_idx} exceeds "
                    f"the resolved {len(table_rows)}x{col_count} grid"
                )

            region = _TableMergeRegion(
                row=row_idx - 1,
                col=col_idx - 1,
                row_span=row_span,
                col_span=col_span,
            )
            for covered_row in range(region.row, region.row + region.row_span):
                for covered_col in range(region.col, region.col + region.col_span):
                    covered_position = (covered_row, covered_col)
                    prior = owners.get(covered_position)
                    if prior is not None:
                        raise RuntimeError(
                            f"Native PPTX table merge rooted at R{row_idx}C{col_idx} "
                            f"overlaps merge rooted at R{prior.row + 1}C{prior.col + 1}"
                        )
                    owners[covered_position] = region
    return owners


def _grid_is_strict(payload: dict[str, Any]) -> bool:
    # Resolve shared symbols at call time; retain the public module contract.
    value = payload.get("strict_grid")
    return _table_bool(value, "strict_grid", default=False)


def _table_bool(value: Any, field_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise RuntimeError(
        f"Native PPTX table {field_name} must be a JSON boolean"
    )


def _table_header_rows(payload: dict[str, Any], row_count: int) -> int:
    default = 1 if payload.get("columns") else 0
    value = _number(payload.get("header_rows", default), "table header_rows")
    if not value.is_integer():
        raise RuntimeError("Native PPTX table header_rows must be an integer")
    header_rows = int(value)
    if not 0 <= header_rows <= row_count:
        raise RuntimeError(
            "Native PPTX table header_rows must be between zero and the resolved row count"
        )
    return header_rows


def _validate_table_lengths(payload: dict[str, Any], table_rows: list[list[Any]]) -> int:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _TABLE_MAX_COLUMNS,
        _TABLE_MAX_ROWS,
        _table_weights,
    )
    if not table_rows:
        raise RuntimeError("Native PPTX table requires at least one row")
    col_count = max(len(row) for row in table_rows)
    if col_count <= 0:
        raise RuntimeError("Native PPTX table requires at least one column")
    if len(table_rows) > _TABLE_MAX_ROWS or col_count > _TABLE_MAX_COLUMNS:
        raise RuntimeError("Native PPTX table supports at most 1000 rows and columns")
    if _grid_is_strict(payload) and any(len(row) != col_count for row in table_rows):
        raise RuntimeError("Native PPTX table strict_grid requires every row to have the same length")

    column_widths = payload.get("column_widths")
    if column_widths is not None:
        if not isinstance(column_widths, list) or len(column_widths) != col_count:
            raise RuntimeError(
                "Native PPTX table column_widths must match the resolved column count "
                f"(expected {col_count} entries)"
            )
        _table_weights(column_widths, "column_widths")

    row_heights = payload.get("row_heights")
    if row_heights is not None:
        if not isinstance(row_heights, list) or len(row_heights) != len(table_rows):
            raise RuntimeError(
                "Native PPTX table row_heights must match the resolved row count "
                f"(expected {len(table_rows)} entries, header rows included)"
            )
        _table_weights(row_heights, "row_heights")

    return col_count


def _validate_table_cell_formatting(payload: dict[str, Any], table_rows: list[list[Any]]) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _cell_payload,
        _table_anchor,
        _table_border_specs,
        _table_cell_extra_attrs,
        _table_cell_paragraphs,
        _table_fill_opacity,
        _table_padding_value,
    )
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    _table_bool(style.get("band_row"), "style.band_row", default=True)
    if "borders" in style:
        raise RuntimeError(
            "Native PPTX table per-side borders are supported on cells only"
        )
    for row in table_rows:
        for cell in row:
            cell_data = _cell_payload(cell)
            if "merge_continuation" in cell_data and not isinstance(
                cell_data["merge_continuation"],
                bool,
            ):
                raise RuntimeError(
                    "Native PPTX table merge_continuation must be a JSON boolean"
                )
            _table_cell_paragraphs(cell_data)
            if "bold" in cell_data:
                _table_bool(cell_data["bold"], "cell bold", default=False)
            for side in ("left", "right", "top", "bottom"):
                _table_padding_value(cell_data, style, side)
            for border in _table_border_specs(cell_data, style).values():
                if border is None or border.style == "none":
                    continue
                assert border.width is not None
                _powerpoint_line_width_emu(
                    border.width,
                    "table border_width",
                )
            _table_anchor(cell_data, style)
            _table_cell_extra_attrs(cell_data)
            _table_fill_opacity(cell_data)


def _validate_table_payload(
    payload: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[list[Any]],
    int,
    dict[tuple[int, int], _TableMergeRegion],
]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _table_rows,
    )
    authored_payload = payload
    authored_rows = _table_rows(authored_payload)
    payload = expand_semantic_table_payload(payload)
    table_rows = _table_rows(payload)
    col_count = _validate_table_lengths(payload, table_rows)
    for row in table_rows:
        row.extend([""] * (col_count - len(row)))
    for row in authored_rows:
        row.extend([""] * (col_count - len(row)))
    merge_layout = _resolve_table_merge_layout(
        authored_payload,
        authored_rows,
        col_count,
    )
    _table_header_rows(payload, len(table_rows))
    _validate_table_cell_formatting(payload, table_rows)
    return payload, table_rows, col_count, merge_layout
