"""Advanced native table construction for tl-docx creation."""

from __future__ import annotations

import io
import math
import unicodedata
from collections.abc import Callable

from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.image.exceptions import InvalidImageStreamError, UnexpectedEndOfFileError, UnrecognizedImageError
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from tl_create_layout import apply_paragraph_defaults, apply_run_format, sync_complex_size
from tl_create_table_validation import (
    _DEFAULT_EDGE,
    _EDGE_NAMES,
    _EDGE_ORDER,
    _normalized_table,
    validate_table,
    validate_table_format,
)


def _preset_edges(preset: str) -> dict[str, dict]:
    if preset == "three_line":
        return {
            "top": {"style": "single", "width_pt": 1.5, "color": "000000"},
            "left": {"style": "none", "width_pt": 0.5, "color": "000000"},
            "bottom": {"style": "single", "width_pt": 1.5, "color": "000000"},
            "right": {"style": "none", "width_pt": 0.5, "color": "000000"},
            "insideH": {"style": "none", "width_pt": 0.5, "color": "000000"},
            "insideV": {"style": "none", "width_pt": 0.5, "color": "000000"},
        }
    if preset == "zebra":
        return {
            name: {"style": "none", "width_pt": 0.5, "color": "000000"}
            for name in _EDGE_ORDER
        }
    return {name: dict(_DEFAULT_EDGE) for name in _EDGE_NAMES}


def _merge_edge(target: dict, override: dict) -> None:
    target.update(override)


def _format_layers(global_format: dict, table_format: dict, block: dict, header_rows: int) -> tuple[dict, dict]:
    config = {
        "preset": "grid",
        "column_sizing": "equal",
        "align": "left",
        "vertical_align": "center",
        "cell_fill": "none",
        "band_fill": "none",
        "cell_margin_top_mm": 1,
        "cell_margin_bottom_mm": 1,
        "cell_margin_left_mm": 1.5,
        "cell_margin_right_mm": 1.5,
        "repeat_header": True,
        "allow_row_split": False,
    }
    edges = _preset_edges("grid")
    band_fill_explicit = False
    for layer in (global_format, table_format):
        if "preset" in layer:
            config["preset"] = layer["preset"]
            edges = _preset_edges(layer["preset"])
        config.update({key: value for key, value in layer.items() if key != "borders"})
        band_fill_explicit = band_fill_explicit or "band_fill" in layer
        for scalar in ("border_style", "border_width_pt", "border_color"):
            if scalar in layer:
                property_name = {
                    "border_style": "style",
                    "border_width_pt": "width_pt",
                    "border_color": "color",
                }[scalar]
                for edge in edges.values():
                    edge[property_name] = layer[scalar]
        for name, border in layer.get("borders", {}).items():
            _merge_edge(edges[name], border)

    if "repeat_header" in block and "repeat_header" not in table_format:
        config["repeat_header"] = block["repeat_header"]
    band_fill = config.get("band_fill", "none")
    if config["preset"] == "zebra" and not band_fill_explicit:
        band_fill = "F2F4F7"
    config["band_fill"] = band_fill
    if header_rows == 0 and config["repeat_header"]:
        config["repeat_header"] = False
    return config, edges


def _edge_canonical(edge: dict) -> tuple:
    if edge["style"] == "none":
        return ("none",)
    return ("single", int(float(edge["width_pt"]) * 8 + 0.5), edge["color"].upper())


def _cell_edges(row: int, column: int, row_count: int, column_count: int, table_edges: dict) -> dict:
    return {
        "top": dict(table_edges["top"] if row == 0 else table_edges["insideH"]),
        "left": dict(table_edges["left"] if column == 0 else table_edges["insideV"]),
        "bottom": dict(table_edges["bottom"] if row == row_count - 1 else table_edges["insideH"]),
        "right": dict(table_edges["right"] if column == column_count - 1 else table_edges["insideV"]),
    }


def _resolved_cell_edges(rows: list[list], row_count: int, column_count: int, table_edges: dict, merges: list[dict]) -> dict:
    if merges and any(
        isinstance(cell, dict) and cell.get("borders")
        for row in rows
        for cell in row
    ):
        raise ValueError("cell borders are not supported on tables with merges")
    resolved = {}
    explicit = {}
    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row):
            edge_values = _cell_edges(row_index, column_index, row_count, column_count, table_edges)
            cell_borders = cell.get("borders", {}) if isinstance(cell, dict) else {}
            explicit[(row_index, column_index)] = set(cell_borders)
            for edge_name, overrides in cell_borders.items():
                _merge_edge(edge_values[edge_name], overrides)
            resolved[(row_index, column_index)] = edge_values

    for row_index in range(row_count):
        for column_index in range(column_count):
            if column_index + 1 < column_count:
                left_cell = (row_index, column_index)
                right_cell = (row_index, column_index + 1)
                _reconcile_shared_edge(
                    resolved[left_cell],
                    resolved[right_cell],
                    "right",
                    "left",
                    "right" in explicit[left_cell],
                    "left" in explicit[right_cell],
                )
            if row_index + 1 < row_count:
                top_cell = (row_index, column_index)
                bottom_cell = (row_index + 1, column_index)
                _reconcile_shared_edge(
                    resolved[top_cell],
                    resolved[bottom_cell],
                    "bottom",
                    "top",
                    "bottom" in explicit[top_cell],
                    "top" in explicit[bottom_cell],
                )
    return resolved


def _reconcile_shared_edge(
    first: dict,
    second: dict,
    first_name: str,
    second_name: str,
    first_explicit: bool,
    second_explicit: bool,
) -> None:
    first_edge = first[first_name]
    second_edge = second[second_name]
    if first_explicit and second_explicit:
        if _edge_canonical(first_edge) != _edge_canonical(second_edge):
            raise ValueError("adjacent cell borders conflict on a shared edge")
        return
    if first_explicit:
        second[second_name] = dict(first_edge)
    elif second_explicit:
        first[first_name] = dict(second_edge)


def _set_border(parent, edge_name: str, edge: dict) -> None:
    tag = qn(f"w:{edge_name}")
    element = parent.find(tag)
    if element is None:
        element = OxmlElement(f"w:{edge_name}")
        parent.append(element)
    element.set(qn("w:val"), "nil" if edge["style"] == "none" else "single")
    if edge["style"] == "single":
        element.set(qn("w:sz"), str(int(float(edge["width_pt"]) * 8 + 0.5)))
        element.set(qn("w:color"), edge["color"].upper())
    else:
        element.attrib.pop(qn("w:sz"), None)
        element.attrib.pop(qn("w:color"), None)


def _set_table_borders(table, edges: dict) -> None:
    properties = table._tbl.tblPr
    borders = properties.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        properties.insert_element_before(
            borders,
            "w:shd",
            "w:tblLayout",
            "w:tblCellMar",
            "w:tblLook",
        )
    width = OxmlElement("w:tblW")
    width.set(qn("w:w"), str(int(sum(table.columns[i].width for i in range(len(table.columns))) / 635 + 0.5)))
    width.set(qn("w:type"), "dxa")
    existing_width = properties.find(qn("w:tblW"))
    if existing_width is not None:
        existing_width.set(qn("w:w"), width.get(qn("w:w")))
        existing_width.set(qn("w:type"), "dxa")
    else:
        properties.insert_element_before(
            width,
            "w:jc",
            "w:tblCellSpacing",
            "w:tblInd",
            "w:tblBorders",
            "w:shd",
            "w:tblLayout",
            "w:tblLook",
        )
    for name, edge in edges.items():
        _set_border(borders, name, edge)


def _set_cell_edge(cell, edge_name: str, edge: dict) -> None:
    properties = cell._tc.get_or_add_tcPr()
    borders = properties.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        properties.append(borders)
    _set_border(borders, edge_name, edge)


def _set_cell_margins(table, config: dict) -> None:
    properties = table._tbl.tblPr
    margins = properties.find(qn("w:tblCellMar"))
    if margins is None:
        margins = OxmlElement("w:tblCellMar")
        properties.insert_element_before(margins, "w:tblLook")
    for side in ("top", "left", "bottom", "right"):
        element = margins.find(qn(f"w:{side}"))
        if element is None:
            element = OxmlElement(f"w:{side}")
            margins.append(element)
        twips = int(float(config[f"cell_margin_{side}_mm"]) * 1440 / 25.4 + 0.5)
        element.set(qn("w:w"), str(twips))
        element.set(qn("w:type"), "dxa")


def _set_cell_fill(cell, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = properties.find(qn("w:shd"))
    if fill == "none":
        if shading is not None:
            properties.remove(shading)
        return
    if shading is None:
        shading = OxmlElement("w:shd")
        properties.append(shading)
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill.upper())


def _set_table_alignment(table, alignment: str) -> None:
    properties = table._tbl.tblPr
    justification = properties.find(qn("w:jc"))
    if justification is None:
        justification = OxmlElement("w:jc")
        properties.insert_element_before(
            justification,
            "w:tblCellSpacing",
            "w:tblInd",
            "w:tblBorders",
            "w:shd",
            "w:tblLayout",
            "w:tblCellMar",
            "w:tblLook",
        )
    justification.set(qn("w:val"), alignment)


def _set_row_layout(row, allow_split: bool) -> None:
    properties = row._tr.get_or_add_trPr()
    cant_split = properties.find(qn("w:cantSplit"))
    if cant_split is None:
        cant_split = OxmlElement("w:cantSplit")
        properties.append(cant_split)
    cant_split.set(qn("w:val"), "0" if allow_split else "1")


def _weighted_widths(rows: list[list], column_count: int, available_mm: float, config: dict) -> list[float]:
    horizontal_padding = config["cell_margin_left_mm"] + config["cell_margin_right_mm"]
    minimum = 8 + horizontal_padding
    if available_mm < minimum * column_count:
        raise ValueError("available page width cannot provide 8mm of content width per table column")
    weights = []
    for column_index in range(column_count):
        weight = 0.0
        for row in rows:
            cell = row[column_index]
            text = cell if isinstance(cell, str) else cell.get("text", "") if isinstance(cell, dict) else ""
            character_weight = sum(
                1.0 if unicodedata.east_asian_width(character) in {"F", "W", "A"} else 0.5
                for character in text
            )
            weight += character_weight
        weights.append(max(1.0, math.sqrt(weight)))
    remainder = available_mm - minimum * column_count
    total_weight = sum(weights)
    return [minimum + remainder * weight / total_weight for weight in weights]


def _available_dimensions(section) -> tuple[float, float]:
    width = (section.page_width - section.left_margin - section.right_margin) / 36000
    height = (section.page_height - section.top_margin - section.bottom_margin) / 36000
    return float(width), float(height)


def _table_widths(document, block: dict, layout: dict, column_count: int) -> list[float]:
    available_width, _ = _available_dimensions(document.sections[-1])
    if "widths_mm" in block:
        widths = [float(value) for value in block["widths_mm"]]
    elif "col_widths_cm" in block:
        widths = [float(value) * 10 for value in block["col_widths_cm"]]
    elif layout["column_sizing"] == "content":
        widths = _weighted_widths(block["rows"], column_count, available_width, layout)
    else:
        widths = [available_width / column_count for _ in range(column_count)]
    if sum(widths) > available_width + 0.1:
        raise ValueError("table widths exceed the available page width")
    minimum = layout["cell_margin_left_mm"] + layout["cell_margin_right_mm"]
    if any(width <= minimum for width in widths):
        raise ValueError("table column width must exceed its left and right cell margins")
    return widths


def _set_column_widths(table, widths_mm: list[float]) -> None:
    for column_index, width in enumerate(widths_mm):
        table.columns[column_index].width = Cm(width / 10)
        for row in table.rows:
            row.cells[column_index].width = Cm(width / 10)


def _set_vertical_alignment(cell, value: str) -> None:
    alignment = {
        "top": WD_CELL_VERTICAL_ALIGNMENT.TOP,
        "center": WD_CELL_VERTICAL_ALIGNMENT.CENTER,
        "bottom": WD_CELL_VERTICAL_ALIGNMENT.BOTTOM,
    }[value]
    cell.vertical_alignment = alignment


def _set_repeat_header(row) -> None:
    properties = row._tr.get_or_add_trPr()
    marker = properties.find(qn("w:tblHeader"))
    if marker is None:
        marker = OxmlElement("w:tblHeader")
        properties.append(marker)
    marker.set(qn("w:val"), "true")


def _add_caption(document, caption: str, theme: str) -> None:
    if theme == "official":
        name = "Table Caption"
        if name not in document.styles:
            style = document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            style.base_style = document.styles["Normal"]
            style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            style.paragraph_format.keep_with_next = True
            style.paragraph_format.space_after = Pt(6)
            style.font.size = Pt(12)
            style.font.bold = False
            sync_complex_size(style.element.rPr)
        document.add_paragraph(caption, style=name)
        return
    paragraph = document.add_paragraph()
    apply_paragraph_defaults(paragraph, theme, "caption")
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_after = Pt(6)
    run = paragraph.add_run(caption)
    apply_run_format(run, theme, "body")
    run.font.size = Pt(12)


def _cell_spec(cell_value):
    if isinstance(cell_value, str):
        return {"text": cell_value}
    return cell_value


def _populate_cell(cell, value, *, theme: str, is_header: bool, read_image: Callable[[str], bytes], width_mm: float, height_mm: float, layout: dict) -> None:
    spec = _cell_spec(value)
    paragraph = cell.paragraphs[0]
    paragraph.clear()
    apply_paragraph_defaults(paragraph, theme, "table_header" if is_header else "table_body")
    if is_header:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if "text" in spec:
        run = paragraph.add_run(spec["text"])
        apply_run_format(run, theme, "body")
        if is_header:
            run.bold = True
    if "image" in spec:
        image = spec["image"]
        content_width = width_mm - layout["cell_margin_left_mm"] - layout["cell_margin_right_mm"]
        content_height = height_mm - layout["cell_margin_top_mm"] - layout["cell_margin_bottom_mm"]
        if image["width_mm"] > content_width + 0.01:
            raise ValueError(f"table image width exceeds available cell width for {image['path']!r}")
        if image["height_mm"] > content_height + 0.01:
            raise ValueError(f"table image height exceeds available page height for {image['path']!r}")
        image_bytes = read_image(image["path"])
        if not isinstance(image_bytes, (bytes, bytearray, memoryview)):
            raise ValueError(f"read_image must return bytes for {image['path']!r}")
        try:
            picture = paragraph.add_run().add_picture(
                io.BytesIO(bytes(image_bytes)),
                width=Cm(image["width_mm"] / 10),
                height=Cm(image["height_mm"] / 10),
            )
        except (InvalidImageStreamError, UnexpectedEndOfFileError, UnrecognizedImageError) as error:
            raise ValueError(f"image data is not a supported image for {image['path']!r}") from error
        picture._inline.docPr.set("descr", image["alt"])
    _set_vertical_alignment(cell, layout["vertical_align"])


def build_table(
    document,
    block: dict,
    theme: str,
    global_format: dict,
    read_image: Callable[[str], bytes],
) -> None:
    """Append one validated table using native WordprocessingML properties."""
    normalized = _normalized_table(block, "table")
    rows = normalized["rows"]
    row_count = normalized["row_count"]
    column_count = normalized["column_count"]
    header_rows = normalized["header_rows"]
    local_format = block.get("format", {})
    layout, edges = _format_layers(global_format, local_format, block, header_rows)
    widths = _table_widths(document, block, layout, column_count)
    if block.get("caption") is not None:
        _add_caption(document, block["caption"], theme)

    table = document.add_table(rows=row_count, cols=column_count)
    table.style = "Table Grid"
    table.autofit = False
    _set_column_widths(table, widths)
    _set_table_borders(table, edges)
    _set_table_alignment(table, layout["align"])
    _set_cell_margins(table, layout)

    merges = normalized["merges"]
    for merge in merges:
        start = table.cell(merge["row"], merge["col"])
        end = table.cell(
            merge["row"] + merge["rowspan"] - 1,
            merge["col"] + merge["colspan"] - 1,
        )
        start.merge(end)

    for row_index, row in enumerate(rows):
        _set_row_layout(table.rows[row_index], layout["allow_row_split"])
        if row_index < header_rows and layout["repeat_header"]:
            _set_repeat_header(table.rows[row_index])
        for column_index, value in enumerate(row):
            coordinate = (row_index, column_index)
            if value is None:
                continue
            if coordinate in normalized["covered"] and not any(
                merge["row"] == row_index and merge["col"] == column_index
                for merge in merges
            ):
                continue
            anchor_merge = next(
                (
                    merge
                    for merge in merges
                    if merge["row"] == row_index and merge["col"] == column_index
                ),
                None,
            )
            row_span = anchor_merge["rowspan"] if anchor_merge else 1
            col_span = anchor_merge["colspan"] if anchor_merge else 1
            cell_width = sum(widths[column_index : column_index + col_span])
            _populate_cell(
                table.cell(row_index, column_index),
                value,
                theme=theme,
                is_header=row_index < header_rows,
                read_image=read_image,
                width_mm=cell_width,
                height_mm=_available_dimensions(document.sections[-1])[1],
                layout=layout,
            )

    if header_rows and row_count > header_rows and layout["preset"] == "three_line":
        separator = {"style": "single", "width_pt": 0.75, "color": "000000"}
        for column_index in range(column_count):
            _set_cell_edge(table.cell(header_rows - 1, column_index), "bottom", separator)

    cell_border_map = _resolved_cell_edges(rows, row_count, column_count, edges, merges)
    if any(
        isinstance(cell, dict) and cell.get("borders")
        for row in rows
        for cell in row
    ):
        for (row_index, column_index), edge_values in cell_border_map.items():
            if (row_index, column_index) in normalized["covered"] and not any(
                merge["row"] == row_index and merge["col"] == column_index
                for merge in merges
            ):
                continue
            cell = table.cell(row_index, column_index)
            for edge_name, edge in edge_values.items():
                _set_cell_edge(cell, edge_name, edge)

    for row_index, row in enumerate(rows):
        if row_index < header_rows:
            row_fill = layout["cell_fill"]
        else:
            data_index = row_index - header_rows
            row_fill = layout["band_fill"] if data_index % 2 == 1 else layout["cell_fill"]
        for column_index, value in enumerate(row):
            coordinate = (row_index, column_index)
            if value is None:
                continue
            if coordinate in normalized["covered"] and not any(
                merge["row"] == row_index and merge["col"] == column_index
                for merge in merges
            ):
                continue
            _set_cell_fill(table.cell(row_index, column_index), row_fill)


__all__ = ["build_table", "validate_table", "validate_table_format"]
