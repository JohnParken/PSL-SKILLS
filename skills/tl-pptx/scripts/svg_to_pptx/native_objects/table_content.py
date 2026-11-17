"""Native PowerPoint table conversion."""

from __future__ import annotations

from .table import _TableBorderSpec, _TableParagraph, _TableRun

from typing import Any



from ..drawingml.utils import (
    font_px_to_hpt,
)
from .marker_common import (
    _hex_or_none,
    _number,
)



# Adapted from table.py; upstream MIT, Hugo He, 2025-2026.
def _table_rows(payload: dict[str, Any]) -> list[list[Any]]:
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise RuntimeError("Native PPTX table requires columns/rows lists")
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, list):
            raise RuntimeError(f"Native PPTX table row {idx} must be a list")

    table_rows = [list(columns)] if columns else []
    table_rows.extend(list(row) for row in rows)
    return table_rows


def _table_cell_paragraphs(
    cell_data: dict[str, Any],
) -> tuple[_TableParagraph, ...] | None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _TableParagraph,
    )
    if "paragraphs" not in cell_data:
        return None
    if "text" in cell_data:
        raise RuntimeError(
            "Native PPTX table cell text and paragraphs are mutually exclusive"
        )
    raw_paragraphs = cell_data.get("paragraphs")
    if not isinstance(raw_paragraphs, list) or not raw_paragraphs:
        raise RuntimeError(
            "Native PPTX table cell paragraphs must be a non-empty list"
        )

    paragraphs: list[_TableParagraph] = []
    for idx, value in enumerate(raw_paragraphs, start=1):
        if isinstance(value, str):
            paragraphs.append(_TableParagraph(value))
            continue
        if not isinstance(value, dict):
            raise RuntimeError(
                f"Native PPTX table paragraph {idx} must be a string or object"
            )
        if set(value) - {"text", "runs", "align", "line_spacing_percent"}:
            raise RuntimeError(
                "Native PPTX table paragraph "
                f"{idx} accepts text/runs/align/line_spacing_percent only"
            )
        has_text = "text" in value
        has_runs = "runs" in value
        if has_text == has_runs:
            raise RuntimeError(
                f"Native PPTX table paragraph {idx} requires exactly one of text/runs"
            )
        align = value.get("align")
        if align is not None and align not in {"l", "ctr", "r", "just"}:
            raise RuntimeError(
                "Native PPTX table paragraph "
                f"{idx} align must be l, ctr, r, or just"
            )
        line_spacing_percent: float | None = None
        if value.get("line_spacing_percent") is not None:
            line_spacing_percent = _number(
                value["line_spacing_percent"],
                f"table paragraph {idx} line_spacing_percent",
            )
            if not 0 < line_spacing_percent <= 1000:
                raise RuntimeError(
                    "Native PPTX table paragraph "
                    f"{idx} line_spacing_percent must be in (0, 1000]"
                )
        if has_text:
            text = value.get("text")
            if not isinstance(text, str):
                raise RuntimeError(
                    f"Native PPTX table paragraph {idx} text must be a string"
                )
            paragraphs.append(
                _TableParagraph(
                    text,
                    align,
                    line_spacing_percent=line_spacing_percent,
                )
            )
            continue

        raw_runs = value.get("runs")
        if not isinstance(raw_runs, list) or not raw_runs:
            raise RuntimeError(
                f"Native PPTX table paragraph {idx} runs must be a non-empty list"
            )
        runs = tuple(
            _table_run(run, paragraph_idx=idx, run_idx=run_idx)
            for run_idx, run in enumerate(raw_runs, start=1)
        )
        paragraphs.append(
            _TableParagraph(
                "".join(run.text for run in runs),
                align,
                runs,
                line_spacing_percent,
            )
        )
    return tuple(paragraphs)


def _table_run(
    value: Any,
    *,
    paragraph_idx: int,
    run_idx: int,
) -> _TableRun:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _TableRun,
        _table_border_override,
    )
    label = f"paragraph {paragraph_idx} run {run_idx}"
    if not isinstance(value, dict) or "text" not in value:
        raise RuntimeError(f"Native PPTX table {label} must be a text object")
    allowed = {
        "text", "bold", "italic", "underline", "strike", "color",
        "font_size", "font_family", "lang", "alt_lang",
        "baseline_percent", "outline",
    }
    unknown = set(value) - allowed
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise RuntimeError(
            f"Native PPTX table {label} contains unsupported field(s): {fields}"
        )
    text = value.get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"Native PPTX table {label} text must be a string")

    booleans: dict[str, bool | None] = {}
    for field in ("bold", "italic", "underline", "strike"):
        raw = value.get(field)
        if raw is not None and not isinstance(raw, bool):
            raise RuntimeError(
                f"Native PPTX table {label} {field} must be a JSON boolean"
            )
        booleans[field] = raw

    color: str | None = None
    if value.get("color") is not None:
        if not isinstance(value["color"], str):
            raise RuntimeError(f"Native PPTX table {label} color must be a string")
        color = _hex_or_none(value["color"])
        if color is None:
            raise RuntimeError(f"Native PPTX table {label} color is unsupported")

    font_size: int | None = None
    if value.get("font_size") is not None:
        font_size_px = _number(value["font_size"], f"table {label} font_size")
        if not 100 / 75 <= font_size_px <= 400000 / 75:
            raise RuntimeError(
                f"Native PPTX table {label} font_size is outside DrawingML range"
            )
        font_size = font_px_to_hpt(font_size_px)
        if not 100 <= font_size <= 400000:
            raise RuntimeError(
                f"Native PPTX table {label} font_size is outside DrawingML range"
            )

    font_family: str | None = None
    if value.get("font_family") is not None:
        if not isinstance(value["font_family"], str):
            raise RuntimeError(
                f"Native PPTX table {label} font_family must be a string"
            )
        font_family = value["font_family"].strip()
        if not font_family or "," in font_family:
            raise RuntimeError(
                f"Native PPTX table {label} font_family must be one typeface"
            )

    languages: dict[str, str | None] = {}
    for field in ("lang", "alt_lang"):
        raw = value.get(field)
        if raw is None:
            languages[field] = None
            continue
        if not isinstance(raw, str) or not raw.strip():
            raise RuntimeError(
                f"Native PPTX table {label} {field} must be a non-empty string"
            )
        languages[field] = raw.strip()

    baseline: int | None = None
    if value.get("baseline_percent") is not None:
        baseline_percent = _number(
            value["baseline_percent"],
            f"table {label} baseline_percent",
        )
        if not -100 <= baseline_percent <= 100:
            raise RuntimeError(
                f"Native PPTX table {label} baseline_percent must be in [-100, 100]"
            )
        baseline = round(baseline_percent * 1000)

    outline: _TableBorderSpec | None = None
    if value.get("outline") is not None:
        outline = _table_border_override(value["outline"], "text outline")

    return _TableRun(
        text=text,
        bold=booleans["bold"],
        italic=booleans["italic"],
        underline=booleans["underline"],
        strike=booleans["strike"],
        color=color,
        font_size=font_size,
        font_family=font_family,
        lang=languages["lang"],
        alt_lang=languages["alt_lang"],
        baseline=baseline,
        outline=outline,
    )
