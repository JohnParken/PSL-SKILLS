# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Table parity implementation."""
from __future__ import annotations

from .table_shared import (
    Any,
    ET,
    _FallbackTextRecord,
    _TableParagraph,
    _TableRun,
    _fallback_shape_records,
    _fallback_text_records,
    _hex_or_none,
    _maybe_number,
    _normalized_fallback_text,
    _number,
)

def _table_cell_text_parts(cell: Any) -> list[str]:
    """Return normalized paragraph lines for one table cell."""
    from .table_payload import _cell_payload, _table_cell_paragraphs
    cell_data = _cell_payload(cell)
    paragraphs = _table_cell_paragraphs(cell_data)
    values = (
        [paragraph.text for paragraph in paragraphs]
        if paragraphs is not None
        else [cell_data.get("text")]
    )
    return [
        text
        for text in (
            _normalized_fallback_text(value)
            for value in values
            if value is not None
        )
        if text
    ]


def _native_table_metadata_texts(table_rows: list[list[Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in table_rows:
        for cell in row:
            parts = _table_cell_text_parts(cell)
            variants = list(parts)
            if len(parts) > 1:
                variants.extend({
                    _normalized_fallback_text("".join(parts)),
                    _normalized_fallback_text(" ".join(parts)),
                })
            for text in variants:
                counts[text] = counts.get(text, 0) + 1
    return counts


def _table_fallback_frame(
    elem: ET.Element,
    payload: dict[str, Any],
) -> tuple[float, float, float, float] | None:
    values = tuple(_maybe_number(payload.get(key)) for key in ("x", "y", "width", "height"))
    if all(value is not None for value in values):
        x, y, width, height = values
        assert x is not None and y is not None and width is not None and height is not None
        if width > 0 and height > 0:
            return x, y, width, height

    raw_bounds = elem.get("data-pptx-bounds")
    if raw_bounds:
        parts = [_maybe_number(value) for value in raw_bounds.replace(",", " ").split()]
        if len(parts) == 4 and all(value is not None for value in parts):
            x, y, width, height = parts
            assert x is not None and y is not None and width is not None and height is not None
            if width > 0 and height > 0:
                return x, y, width, height
    return None


def _dedupe_table_edges(values: list[float], tolerance: float = 2.0) -> list[float]:
    edges: list[float] = []
    for value in sorted(values):
        if edges and abs(edges[-1] - value) <= tolerance:
            edges[-1] = (edges[-1] + value) / 2
        else:
            edges.append(value)
    return edges


def _table_fallback_grid(
    elem: ET.Element,
    payload: dict[str, Any],
    table_rows: list[list[Any]],
) -> tuple[list[float], list[float], list[Any], list[Any]] | None:
    frame = _table_fallback_frame(elem, payload)
    if frame is None or not table_rows:
        return None
    x, y, width, height = frame
    col_count = max(len(row) for row in table_rows)
    raw_widths = payload.get("column_widths")
    weights = (
        [_number(value, "table fallback column width") for value in raw_widths]
        if isinstance(raw_widths, list) and len(raw_widths) == col_count
        else [1.0] * col_count
    )
    weight_total = sum(weights)
    if weight_total <= 0:
        return None
    payload_column_edges = [x]
    for weight in weights:
        payload_column_edges.append(
            payload_column_edges[-1] + width * weight / weight_total
        )

    shape_records = _fallback_shape_records(elem)
    vertical_candidates = [x, x + width]
    for record in shape_records:
        x1, y1, x2, y2 = record.bounds
        if (
            record.tag == "line"
            and record.stroke is not None
            and abs(x2 - x1) <= 1
            and y2 - y1 >= height * 0.5
        ):
            vertical_candidates.append((x1 + x2) / 2)
    fallback_column_edges = _dedupe_table_edges(vertical_candidates)
    column_edges = (
        fallback_column_edges
        if len(fallback_column_edges) == col_count + 1
        else payload_column_edges
    )

    row_candidates: list[float] = []
    for record in shape_records:
        x1, y1, x2, y2 = record.bounds
        if record.tag == "rect" and record.fill is not None and x2 - x1 >= width * 0.85:
            if x1 <= x + width * 0.1 and x2 >= x + width * 0.9:
                row_candidates.extend((y1, y2))
        elif (
            record.tag == "line"
            and record.stroke is not None
            and abs(y2 - y1) <= 1
            and x2 - x1 >= width * 0.75
        ):
            row_candidates.append((y1 + y2) / 2)
    row_candidates = [
        value
        for value in row_candidates
        if y - 5 <= value <= y + height + 5
    ]
    row_edges = _dedupe_table_edges(row_candidates)
    expected_count = len(table_rows) + 1
    if len(row_edges) < expected_count:
        row_edges = _dedupe_table_edges(row_edges + [y, y + height])
    if len(row_edges) != expected_count:
        return None
    return column_edges, row_edges, shape_records, _fallback_text_records(elem)


def _table_cell_at(
    x: float | None,
    y: float | None,
    column_edges: list[float],
    row_edges: list[float],
) -> tuple[int, int] | None:
    if x is None or y is None:
        return None
    col_idx = next(
        (
            idx
            for idx in range(len(column_edges) - 1)
            if column_edges[idx] - 1 <= x <= column_edges[idx + 1] + 1
        ),
        None,
    )
    row_idx = next(
        (
            idx
            for idx in range(len(row_edges) - 1)
            if row_edges[idx] - 1 <= y <= row_edges[idx + 1] + 1
        ),
        None,
    )
    if row_idx is None or col_idx is None:
        return None
    return row_idx, col_idx


def _table_cell_texts(cell: Any) -> set[str]:
    parts = _table_cell_text_parts(cell)
    texts = set(parts)
    if len(parts) > 1:
        texts.add(_normalized_fallback_text("".join(parts)))
        texts.add(_normalized_fallback_text(" ".join(parts)))
    return texts


def _table_cell_parity_text_style(
    cell: Any,
) -> tuple[bool | None, str | None]:
    """Resolve cell-level parity style, falling back to uniform run values."""
    from .table_payload import _cell_payload, _table_cell_paragraphs
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
    from .table_payload import _cell_payload, _table_cell_paragraphs
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
