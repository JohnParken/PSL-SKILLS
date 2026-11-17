"""Native PowerPoint table conversion."""

from __future__ import annotations

from typing import Any



from .marker_common import (
    _FallbackTextRecord,
    _hex_or_none,
    _normalized_fallback_text,
)



# Adapted from table.py; upstream MIT, Hugo He, 2025-2026.
def _table_cell_parity_text_style(
    cell: Any,
) -> tuple[bool | None, str | None]:
    """Resolve cell-level parity style, falling back to uniform run values."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _cell_payload,
        _table_cell_paragraphs,
    )
    cell_data = _cell_payload(cell)
    paragraphs = _table_cell_paragraphs(cell_data)
    runs = (
        [run for paragraph in paragraphs for run in paragraph.runs or ()]
        if paragraphs is not None
        and all(paragraph.runs is not None for paragraph in paragraphs)
        else []
    )

    uniform_bold: bool | None = None
    run_bold = {run.bold for run in runs}
    if runs and None not in run_bold and len(run_bold) == 1:
        uniform_bold = run_bold.pop()

    uniform_color: str | None = None
    run_colors = {run.color for run in runs}
    if runs and None not in run_colors and len(run_colors) == 1:
        uniform_color = run_colors.pop()

    # Explicit run properties override the cell's (as the export writes them),
    # so a cell whose every run carries one value renders in that value.
    bold = uniform_bold if uniform_bold is not None else cell_data.get("bold")
    color = (
        uniform_color
        if uniform_color is not None
        else _hex_or_none(cell_data.get("color"))
    )
    return bold, color


def _table_cell_parity_style_gaps(
    cell: Any,
    record: _FallbackTextRecord,
    default_color: str | None = None,
    occurrence: int = 0,
) -> tuple[bool, set[str]]:
    """Compare corresponding visible characters using each run's effective style."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _TableParagraph,
        _TableRun,
        _cell_payload,
        _table_cell_paragraphs,
    )
    cell_data = _cell_payload(cell)
    paragraphs = _table_cell_paragraphs(cell_data)
    if paragraphs is None:
        paragraphs = (_TableParagraph(str(cell_data.get("text") or "")),)
    # A fallback <text> can contain the whole cell or just one paragraph.
    joined_texts = {
        _normalized_fallback_text(separator.join(paragraph.text for paragraph in paragraphs))
        for separator in ("", " ")
    }
    if record.text not in joined_texts:
        matches = tuple(
            paragraph for paragraph in paragraphs
            if _normalized_fallback_text(paragraph.text) == record.text
        )
        paragraphs = matches[occurrence:occurrence + 1]
    cell_bold, cell_color = _table_cell_parity_text_style(cell_data)
    cell_color = cell_color or default_color
    native_styles = [
        (
            run.bold if run.bold is not None else cell_bold,
            run.color or cell_color,
        )
        for paragraph in paragraphs
        for run in (paragraph.runs or (_TableRun(paragraph.text),))
        for character in run.text
        if not character.isspace()
    ]
    fallback_styles = [
        (run.bold, run.fill)
        for run in record.runs
        for character in run.text
        if not character.isspace()
    ]
    missing_bold = False
    missing_colors: set[str] = set()
    for index, (bold, color) in enumerate(fallback_styles):
        native_bold, native_color = (
            native_styles[index] if index < len(native_styles) else (None, None)
        )
        if bold and native_bold is not True:
            missing_bold = True
        if color is not None and color != native_color:
            missing_colors.add(color)
    return missing_bold, missing_colors


def _table_parity_style_gaps(
    table_rows: list[list[Any]],
    text_cells: list[tuple[Any, int, int]],
    default_color: str | None,
):
    """Pair repeated paragraph text with its occurrence inside the same cell."""
    # Resolve shared symbols at call time; retain the public module contract.
    occurrences: dict[tuple[int, int, str], int] = {}
    for record, row_idx, col_idx in text_cells:
        key = (row_idx, col_idx, record.text)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        bold, colors = _table_cell_parity_style_gaps(
            table_rows[row_idx][col_idx], record, default_color, occurrence
        )
        yield record, row_idx, col_idx, bold, colors


def _table_text_cells(
    text_records: list[Any],
    table_rows: list[list[Any]],
    column_edges: list[float],
    row_edges: list[float],
) -> list[tuple[Any, int, int]]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _table_cell_at,
        _table_cell_texts,
    )
    records: list[tuple[Any, int, int]] = []
    for record in text_records:
        position = _table_cell_at(record.x, record.y, column_edges, row_edges)
        if position is None:
            continue
        row_idx, col_idx = position
        if row_idx >= len(table_rows) or col_idx >= len(table_rows[row_idx]):
            continue
        if record.text not in _table_cell_texts(table_rows[row_idx][col_idx]):
            continue
        records.append((record, row_idx, col_idx))
    return records


def _fallback_table_alignment(anchor: str) -> str:
    return {
        "middle": "ctr",
        "end": "r",
        "right": "r",
    }.get(anchor, "l")
