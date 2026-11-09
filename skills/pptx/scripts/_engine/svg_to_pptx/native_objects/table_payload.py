# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Table payload implementation."""
from __future__ import annotations

from .table_shared import (
    Any,
    ThemeColorSpec,
    _TABLE_CANONICAL_SPAN_KEYS,
    _TABLE_MAX_COLUMNS,
    _TABLE_MAX_ROWS,
    _TABLE_TOP_LEVEL_SPAN_KEYS,
    _TABLE_UNSUPPORTED_SPAN_KEYS,
    _TableBorderSpec,
    _TableMergeRegion,
    _TableParagraph,
    _TableRun,
    _bool_attr,
    _font_face_xml,
    _hex_or_none,
    _number,
    _powerpoint_line_width_emu,
    _xml_escape,
    color_node_xml,
    detect_text_lang,
    expand_semantic_table_payload,
    font_px_to_hpt,
    text_has_rtl_characters,
    text_uses_rtl,
)

def _table_text_run(
    text: str,
    *,
    color: str | None,
    bold: bool | None,
    font_size: int | None,
    font_face: str | None,
    language: str | None,
    default_language: str | None,
    theme_color_spec: ThemeColorSpec | None,
    italic: bool | None = None,
    underline: bool | None = None,
    strike: bool | None = None,
    alt_language: str | None = None,
    exact_font_face: bool = False,
    baseline: int | None = None,
    outline: _TableBorderSpec | None = None,
) -> str:
    size_attr = f' sz="{font_size}"' if font_size is not None else ""
    bold_attr = f' b="{_bool_attr(bold)}"' if bold is not None else ""
    italic_attr = f' i="{_bool_attr(italic)}"' if italic is not None else ""
    underline_attr = (
        f' u="{"sng" if underline else "none"}"'
        if underline is not None else ""
    )
    strike_attr = (
        f' strike="{"sngStrike" if strike else "noStrike"}"'
        if strike is not None else ""
    )
    resolved_language = language or detect_text_lang(text, default_language)
    language_attr = f' lang="{_xml_escape(resolved_language)}"'
    alt_language_attr = (
        f' altLang="{_xml_escape(alt_language)}"' if alt_language else ""
    )
    baseline_attr = f' baseline="{baseline}"' if baseline is not None else ""
    color_xml = (
        f'<a:solidFill>{color_node_xml(color, theme_color_spec, "text")}</a:solidFill>'
        if color else ""
    )
    if exact_font_face and font_face:
        escaped_face = _xml_escape(font_face)
        font_xml = (
            f'<a:latin typeface="{escaped_face}"/>'
            f'<a:ea typeface="{escaped_face}"/>'
            f'<a:cs typeface="{escaped_face}"/>'
        )
    else:
        font_xml = _font_face_xml(font_face, default_language)
    outline_xml = ""
    if outline is not None and outline.style == "solid":
        assert outline.color is not None and outline.width is not None
        width = _powerpoint_line_width_emu(
            outline.width,
            "table text outline width",
        )
        outline_xml = (
            f'<a:ln w="{width}">'
            '<a:solidFill>'
            f'{color_node_xml(outline.color, theme_color_spec, "stroke")}'
            '</a:solidFill><a:prstDash val="solid"/></a:ln>'
        )
    rtl_xml = '<a:rtl val="1"/>' if text_has_rtl_characters(text) else ''
    space_attr = ' xml:space="preserve"' if text != text.strip() else ""
    return (
        f'<a:r><a:rPr{language_attr}{alt_language_attr}{size_attr}{bold_attr}'
        f'{italic_attr}{underline_attr}{strike_attr}{baseline_attr}>'
        f'{outline_xml}'
        f'{color_xml}'
        f'{font_xml}'
        f'{rtl_xml}'
        "</a:rPr>"
        f"<a:t{space_attr}>{_xml_escape(text)}</a:t></a:r>"
    )


def _table_paragraph_properties(
    align: str,
    *,
    emit_align: bool,
    text: str,
    language: str | None,
    line_spacing_percent: float | None = None,
) -> str:
    """Build table paragraph properties with project-aware direction."""
    attrs = []
    if emit_align:
        attrs.append(f'algn="{align}"')
    if text_uses_rtl(text, language):
        attrs.append('rtl="1"')
    suffix = f" {' '.join(attrs)}" if attrs else ''
    if line_spacing_percent is None:
        return f'<a:pPr{suffix}/>'
    spacing = round(line_spacing_percent * 1000)
    return (
        f'<a:pPr{suffix}><a:lnSpc><a:spcPct val="{spacing}"/>'
        '</a:lnSpc></a:pPr>'
    )


def _cell_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"text": "" if value is None else str(value)}


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
    from .table_layout import _table_border_override
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
    from .table_layout import _table_weights
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
    from .table_layout import _table_anchor, _table_border_specs, _table_cell_extra_attrs, _table_fill_opacity, _table_padding_value
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
