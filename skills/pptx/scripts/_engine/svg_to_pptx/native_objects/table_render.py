# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Table render implementation."""
from __future__ import annotations

from .table_shared import (
    Any,
    ConvertContext,
    ET,
    ShapeResult,
    TABLE_URI,
    _bool_attr,
    _bounds,
    _clean_hex,
    _fallback_text_attr_values,
    _font_size_hpt,
    _hex_or_none,
    _most_common_value,
    _xml_escape,
    native_import_source,
)

def _build_native_table(elem: ET.Element, ctx: ConvertContext, payload: dict[str, Any]) -> ShapeResult:
    from .table_payload import _cell_payload, _table_bool, _table_cell_paragraphs, _table_header_rows, _table_paragraph_properties, _table_text_run, _validate_table_payload
    from .table_layout import _table_anchor, _table_border_xml, _table_cell_extra_attrs, _table_fill_opacity, _table_fill_xml, _table_merge_attrs, _table_padding_attrs, _weighted_lengths
    payload, table_rows, col_count, merge_layout = _validate_table_payload(payload)
    header_rows = _table_header_rows(payload, len(table_rows))
    preserve_source_style = native_import_source(elem) == "pptx"

    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    # Fills are only what the payload says: a cell that resolves to no fill
    # exports as noFill so the slide background shows through, matching a
    # fallback that draws no cell rect. Text colour keeps a legible default
    # because an invisible run is worse than a wrong one.
    header_fill = _hex_or_none(style.get("header_fill"))
    body_fill = _hex_or_none(style.get("body_fill"))
    band_fill = _hex_or_none(style.get("band_fill"))
    body_text = _clean_hex(style.get("body_text"), "#1F2937")
    header_text = _clean_hex(
        style.get("header_text"),
        "#FFFFFF" if header_fill is not None else body_text,
    )
    font_face = str(style["font_family"]) if style.get("font_family") else None
    if font_face is None and not preserve_source_style:
        # Typography mirrors the fallback: an SVG-first table drawn in one face
        # exports in that face rather than falling to the theme font.
        inherited = getattr(ctx, "inherited_styles", None) or {}
        font_face = _most_common_value(
            _fallback_text_attr_values(elem, "font-family", inherited.get("font-family"))
        )
    body_font_size = _font_size_hpt(style.get("font_size"), 18)
    band_rows_enabled = _table_bool(
        style.get("band_row"),
        "style.band_row",
        default=True,
    )
    header_font_size = _font_size_hpt(
        style.get("header_font_size", style.get("font_size")),
        18,
    )

    off_x, off_y, ext_cx, ext_cy = _bounds(elem, payload, ctx)

    column_widths = payload.get("column_widths")
    grid_widths = _weighted_lengths(
        ext_cx,
        col_count,
        column_widths if isinstance(column_widths, list) else None,
        field_name="column_widths",
    )
    row_heights_raw = payload.get("row_heights")
    row_heights = _weighted_lengths(
        ext_cy,
        len(table_rows),
        row_heights_raw if isinstance(row_heights_raw, list) else None,
        field_name="row_heights",
    )

    grid_xml = "".join(f'<a:gridCol w="{width}"/>' for width in grid_widths)
    rows_xml: list[str] = []
    for row_idx, row in enumerate(table_rows):
        is_header = row_idx < header_rows
        cells_xml: list[str] = []
        for col_idx, cell in enumerate(row):
            cell_data = _cell_payload(cell)
            merge_region = merge_layout.get((row_idx, col_idx))
            merge_attrs = _table_merge_attrs(merge_region, row_idx, col_idx)
            if preserve_source_style:
                fill = (
                    _clean_hex(cell_data.get("fill"), "#FFFFFF")
                    if cell_data.get("fill") is not None else None
                )
            else:
                fill = _hex_or_none(cell_data.get("fill"))
                if fill is None:
                    banded = (
                        band_rows_enabled
                        and band_fill is not None
                        and row_idx % 2 == 0
                        and row_idx
                    )
                    fill = header_fill if is_header else (
                        band_fill if banded else body_fill
                    )
            border_xml = _table_border_xml(cell_data, style, ctx.theme_color_spec)
            fill_xml = (
                "<a:noFill/>"
                if fill is None and not preserve_source_style
                else _table_fill_xml(fill, _table_fill_opacity(cell_data), ctx.theme_color_spec)
            )
            anchor_attr = ""
            if not preserve_source_style or "valign" in cell_data or "valign" in style:
                anchor_attr = f' anchor="{_table_anchor(cell_data, style)}"'
            if merge_region is not None and (
                row_idx != merge_region.row or col_idx != merge_region.col
            ):
                continuation_attrs = (
                    anchor_attr
                    + _table_padding_attrs(cell_data, style)
                    + _table_cell_extra_attrs(cell_data)
                )
                cells_xml.append(
                    f'<a:tc{merge_attrs}>'
                    '<a:txBody><a:bodyPr/><a:lstStyle/><a:p/></a:txBody>'
                    f'<a:tcPr{continuation_attrs}>'
                    f'{border_xml}{fill_xml}'
                    '</a:tcPr>'
                    '</a:tc>'
                )
                continue

            if preserve_source_style:
                color = (
                    _clean_hex(cell_data.get("color"), "#000000")
                    if cell_data.get("color") is not None else None
                )
                align = str(cell_data.get("align") or "l")
            else:
                color = _clean_hex(
                    cell_data.get("color"),
                    header_text if is_header else body_text,
                )
                align = str(cell_data.get("align") or ("ctr" if is_header else "l"))
            if align not in {"l", "ctr", "r", "just"}:
                align = "l"
            paragraphs = _table_cell_paragraphs(cell_data)
            if preserve_source_style:
                bold = (
                    _table_bool(cell_data["bold"], "cell bold", default=False)
                    if "bold" in cell_data else None
                )
                cell_font_size = (
                    _font_size_hpt(cell_data.get("font_size"), 18)
                    if "font_size" in cell_data else None
                )
            else:
                bold = _table_bool(cell_data.get("bold"), "cell bold", default=is_header)
                cell_font_size = (
                    _font_size_hpt(cell_data.get("font_size"), 18)
                    if "font_size" in cell_data
                    else body_font_size
                )
                if is_header and "font_size" not in cell_data:
                    cell_font_size = header_font_size
            language = (
                str(cell_data.get("lang") or style.get("lang") or "").strip()
                or None
            )
            if paragraphs is None:
                text = (
                    "" if cell_data.get("text") is None
                    else str(cell_data.get("text"))
                )
                default_language = language or ctx.primary_language
                paragraph_props = _table_paragraph_properties(
                    align,
                    emit_align=align != "l",
                    text=text,
                    language=default_language,
                )
                text_run_xml = _table_text_run(
                    text,
                    color=color,
                    bold=bold,
                    font_size=cell_font_size,
                    font_face=font_face,
                    language=language,
                    default_language=ctx.primary_language,
                    theme_color_spec=ctx.theme_color_spec,
                )
                paragraphs_xml = (
                    f"<a:p>{paragraph_props}{text_run_xml}</a:p>"
                )
            else:
                paragraph_parts: list[str] = []
                for paragraph in paragraphs:
                    paragraph_align = paragraph.align or align
                    paragraph_text = (
                        paragraph.text
                        if paragraph.runs is None
                        else ''.join(run.text for run in paragraph.runs)
                    )
                    paragraph_props = _table_paragraph_properties(
                        paragraph_align,
                        emit_align=(
                            paragraph.align is not None
                            or paragraph_align != "l"
                        ),
                        text=paragraph_text,
                        language=language or ctx.primary_language,
                        line_spacing_percent=paragraph.line_spacing_percent,
                    )
                    if paragraph.runs is None:
                        text_run_xml = _table_text_run(
                            paragraph.text,
                            color=color,
                            bold=bold,
                            font_size=cell_font_size,
                            font_face=font_face,
                            language=language,
                            default_language=ctx.primary_language,
                            theme_color_spec=ctx.theme_color_spec,
                        )
                    else:
                        text_run_xml = "".join(
                            _table_text_run(
                                run.text,
                                color=run.color or color,
                                bold=run.bold if run.bold is not None else bold,
                                font_size=(
                                    run.font_size
                                    if run.font_size is not None
                                    else cell_font_size
                                ),
                                font_face=run.font_family or font_face,
                                language=run.lang or language,
                                default_language=ctx.primary_language,
                                theme_color_spec=ctx.theme_color_spec,
                                italic=run.italic,
                                underline=run.underline,
                                strike=run.strike,
                                alt_language=run.alt_lang,
                                exact_font_face=run.font_family is not None,
                                baseline=run.baseline,
                                outline=run.outline,
                            )
                            for run in paragraph.runs
                        )
                    paragraph_parts.append(
                        f"<a:p>{paragraph_props}{text_run_xml}</a:p>"
                    )
                paragraphs_xml = "".join(paragraph_parts)
            tc_pr_attrs = (
                f'{anchor_attr}'
                f'{_table_padding_attrs(cell_data, style, row_height=row_heights[row_idx], paragraphs_xml=paragraphs_xml)}'
                f'{_table_cell_extra_attrs(cell_data)}'
            )
            cells_xml.append(
                f"<a:tc{merge_attrs}>"
                "<a:txBody><a:bodyPr/><a:lstStyle/>"
                f"{paragraphs_xml}"
                "</a:txBody>"
                f'<a:tcPr{tc_pr_attrs}>{border_xml}{fill_xml}</a:tcPr>'
                "</a:tc>"
            )
        rows_xml.append(f'<a:tr h="{row_heights[row_idx]}">{"".join(cells_xml)}</a:tr>')

    shape_id = ctx.claim_shape_id(
        elem.get("data-pptx-shape-id"),
        elem.get("data-pptx-shape-scope"),
    )
    first_row = _bool_attr(header_rows > 0)
    band_row = _bool_attr(band_rows_enabled)
    table_style_id = style.get("table_style_id")
    if table_style_id is None and not preserve_source_style:
        table_style_id = "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"
    table_style_xml = (
        f'<a:tableStyleId>{_xml_escape(str(table_style_id))}</a:tableStyleId>'
        if table_style_id else ""
    )
    name = _xml_escape(str(payload.get("name") or elem.get("id") or f"Native Table {shape_id}"))
    xml = f'''<p:graphicFrame>
<p:nvGraphicFramePr>
<p:cNvPr id="{shape_id}" name="{name}"/>
<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr>
<p:nvPr/>
</p:nvGraphicFramePr>
<p:xfrm><a:off x="{off_x}" y="{off_y}"/><a:ext cx="{ext_cx}" cy="{ext_cy}"/></p:xfrm>
<a:graphic>
<a:graphicData uri="{TABLE_URI}">
<a:tbl>
<a:tblPr firstRow="{first_row}" bandRow="{band_row}">
{table_style_xml}
</a:tblPr>
<a:tblGrid>{grid_xml}</a:tblGrid>
{''.join(rows_xml)}
</a:tbl>
</a:graphicData>
</a:graphic>
</p:graphicFrame>'''
    return ShapeResult(xml=xml, bounds_emu=(off_x, off_y, off_x + ext_cx, off_y + ext_cy))
