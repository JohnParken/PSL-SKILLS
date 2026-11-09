"""Render validated document specifications into OOXML package parts."""
from __future__ import annotations

from docx_create_common import (
    quoteattr, ET, append_paragraph_properties, W,
    R, REL, CT, WP,
    A, PIC, XML, _CONTENT_TYPES,
    _REL_TYPES, _MAX_XML_BYTES, _MAX_PACKAGE_BYTES, _MAX_PACKAGE_PARTS,
    _q, _resolved_border, _border_attributes, _normalize_run_fonts,
    _twips,
)

def _half_points(value: float) -> str:
    return str(int(value * 2 + 0.5))


def _points_to_twips(value: float) -> str:
    return str(int(value * 20 + 0.5))


def _characters_to_hundredths(value: float) -> str:
    return str(int(value * 100 + 0.5))


def _append_character_properties(parent: ET.Element, formatting: dict) -> None:
    font_attrs = {}
    if "font_latin" in formatting:
        for key in ("ascii", "hAnsi", "cs"):
            font_attrs[_q(W, key)] = formatting["font_latin"]
    if "font_east_asia" in formatting:
        font_attrs[_q(W, "eastAsia")] = formatting["font_east_asia"]
    if font_attrs:
        ET.SubElement(parent, _q(W, "rFonts"), font_attrs)
    for key, tag in (("bold", "b"), ("italic", "i")):
        if key in formatting:
            ET.SubElement(parent, _q(W, tag), {_q(W, "val"): "1" if formatting[key] else "0"})
    if "color" in formatting:
        ET.SubElement(parent, _q(W, "color"), {_q(W, "val"): formatting["color"]})
    if "size_pt" in formatting:
        half_points = _half_points(formatting["size_pt"])
        ET.SubElement(parent, _q(W, "sz"), {_q(W, "val"): half_points})
        ET.SubElement(parent, _q(W, "szCs"), {_q(W, "val"): half_points})
    if "underline" in formatting:
        ET.SubElement(parent, _q(W, "u"), {
            _q(W, "val"): "single" if formatting["underline"] else "none",
        })


def _add_text_run(parent: ET.Element, text: str, formatting: dict | None = None) -> None:
    run = ET.SubElement(parent, _q(W, "r"))
    if formatting:
        props = ET.Element(_q(W, "rPr"))
        _append_character_properties(props, formatting)
        if len(props):
            run.append(props)
    text_element = ET.SubElement(run, _q(W, "t"))
    if text[:1].isspace() or text[-1:].isspace():
        text_element.set(_q(XML, "space"), "preserve")
    text_element.text = text


def _append_paragraph_properties(props: ET.Element, formatting: dict, *, outline_level: int | None = None,
                                 keep_next: bool = False) -> None:
    append_paragraph_properties(props, formatting, outline_level=outline_level, keep_next=keep_next)


def _paragraph(parent: ET.Element, *, text: str | None = None, runs: list[dict] | None = None,
               style: str | None = None, formatting: dict | None = None,
               num_id: int | None = None, bold: bool = False,
               outline_level: int | None = None, keep_next: bool = False,
               list_level: int = 0, bookmark: tuple[int, str] | None = None) -> ET.Element:
    paragraph = ET.SubElement(parent, _q(W, "p"))
    if style or formatting or num_id is not None or outline_level is not None or keep_next:
        props = ET.SubElement(paragraph, _q(W, "pPr"))
        if style:
            ET.SubElement(props, _q(W, "pStyle"), {_q(W, "val"): style})
        append_paragraph_properties(props, formatting or {}, keep_next=keep_next,
                                    outline_level=outline_level, num_id=num_id, list_level=list_level)
    if bookmark is not None:
        ET.SubElement(paragraph, _q(W, "bookmarkStart"), {
            _q(W, "id"): str(bookmark[0]), _q(W, "name"): bookmark[1],
        })
    if text is not None:
        _add_text_run(paragraph, text, formatting if formatting is not None else ({"bold": True} if bold else None))
    elif runs is not None:
        for run in runs:
            run_format = dict(formatting or {})
            run_format.update(_normalize_run_fonts(run))
            run_format.pop("text", None)
            if run.get("tab"):
                tab_run = ET.SubElement(paragraph, _q(W, "r"))
                props = ET.Element(_q(W, "rPr"))
                _append_character_properties(props, run_format)
                if len(props):
                    tab_run.append(props)
                ET.SubElement(tab_run, _q(W, "tab"))
            else:
                _add_text_run(paragraph, run["text"], run_format)
    if bookmark is not None:
        ET.SubElement(paragraph, _q(W, "bookmarkEnd"), {_q(W, "id"): str(bookmark[0])})
    return paragraph

def _styles_xml(style_formats: dict[str, dict], related_format: dict | None = None,
                list_format: dict | None = None) -> bytes:
    styles = ET.Element(_q(W, "styles"))
    defaults = ET.SubElement(styles, _q(W, "docDefaults"))
    run_default = ET.SubElement(defaults, _q(W, "rPrDefault"))
    run_props = ET.SubElement(run_default, _q(W, "rPr"))
    normal_format = {
        "font_east_asia": "仿宋", "font_latin": "Times New Roman", "size_pt": 16, "bold": False,
    }
    _append_character_properties(run_props, normal_format)
    paragraph_default = ET.SubElement(defaults, _q(W, "pPrDefault"))
    default_ppr = ET.SubElement(paragraph_default, _q(W, "pPr"))
    ET.SubElement(default_ppr, _q(W, "spacing"), {
        _q(W, "before"): "0", _q(W, "after"): "0",
        _q(W, "line"): "276", _q(W, "lineRule"): "auto",
    })

    normal = ET.SubElement(styles, _q(W, "style"), {
        _q(W, "type"): "paragraph", _q(W, "default"): "1", _q(W, "styleId"): "Normal",
    })
    ET.SubElement(normal, _q(W, "name"), {_q(W, "val"): "Normal"})
    ET.SubElement(normal, _q(W, "qFormat"))
    normal_ppr = ET.SubElement(normal, _q(W, "pPr"))
    _append_paragraph_properties(normal_ppr, {
        "space_before_pt": 0, "space_after_pt": 0,
    })
    normal_spacing = normal_ppr.find(_q(W, "spacing"))
    normal_spacing.set(_q(W, "line"), "276")
    normal_spacing.set(_q(W, "lineRule"), "auto")
    normal_ppr.append(ET.Element(_q(W, "jc"), {_q(W, "val"): "left"}))
    normal_rpr = ET.SubElement(normal, _q(W, "rPr"))
    _append_character_properties(normal_rpr, normal_format)

    if related_format is not None:
        for style_id in ("Header", "Footer"):
            style = ET.SubElement(styles, _q(W, "style"), {
                _q(W, "type"): "paragraph", _q(W, "styleId"): style_id,
            })
            ET.SubElement(style, _q(W, "name"), {_q(W, "val"): style_id})
            ET.SubElement(style, _q(W, "basedOn"), {_q(W, "val"): "Normal"})
            _append_paragraph_properties(ET.SubElement(style, _q(W, "pPr")), related_format)
            _append_character_properties(ET.SubElement(style, _q(W, "rPr")), related_format)

    if list_format is not None:
        style = ET.SubElement(styles, _q(W, "style"), {
            _q(W, "type"): "paragraph", _q(W, "styleId"): "ListParagraph",
        })
        ET.SubElement(style, _q(W, "name"), {_q(W, "val"): "List Paragraph"})
        ET.SubElement(style, _q(W, "basedOn"), {_q(W, "val"): "Normal"})
        _append_paragraph_properties(ET.SubElement(style, _q(W, "pPr")), list_format)
        _append_character_properties(ET.SubElement(style, _q(W, "rPr")), list_format)

    body = ET.SubElement(styles, _q(W, "style"), {
        _q(W, "type"): "paragraph", _q(W, "styleId"): "BodyText",
    })
    ET.SubElement(body, _q(W, "name"), {_q(W, "val"): "Body Text"})
    ET.SubElement(body, _q(W, "basedOn"), {_q(W, "val"): "Normal"})
    ET.SubElement(body, _q(W, "next"), {_q(W, "val"): "BodyText"})
    ET.SubElement(body, _q(W, "qFormat"))
    body_ppr = ET.SubElement(body, _q(W, "pPr"))
    _append_paragraph_properties(body_ppr, style_formats["body"])
    body_rpr = ET.SubElement(body, _q(W, "rPr"))
    _append_character_properties(body_rpr, style_formats["body"])

    for style_key, style_name in (("table_body", "TableBody"), ("table_header", "TableHeader")):
        table_style = ET.SubElement(styles, _q(W, "style"), {
            _q(W, "type"): "paragraph", _q(W, "styleId"): style_name,
        })
        ET.SubElement(table_style, _q(W, "name"), {
            _q(W, "val"): "Table Body" if style_key == "table_body" else "Table Header",
        })
        ET.SubElement(table_style, _q(W, "basedOn"), {_q(W, "val"): "Normal"})
        ET.SubElement(table_style, _q(W, "next"), {_q(W, "val"): "Normal"})
        ET.SubElement(table_style, _q(W, "qFormat"))
        table_ppr = ET.SubElement(table_style, _q(W, "pPr"))
        _append_paragraph_properties(table_ppr, style_formats[style_key])
        table_rpr = ET.SubElement(table_style, _q(W, "rPr"))
        _append_character_properties(table_rpr, style_formats[style_key])

    caption_style = ET.SubElement(styles, _q(W, "style"), {
        _q(W, "type"): "paragraph", _q(W, "styleId"): "TableCaption",
    })
    ET.SubElement(caption_style, _q(W, "name"), {_q(W, "val"): "Table Caption"})
    ET.SubElement(caption_style, _q(W, "basedOn"), {_q(W, "val"): "Normal"})
    _append_paragraph_properties(ET.SubElement(caption_style, _q(W, "pPr")), {
        "align": "center", "keep_with_next": True, "space_after_pt": 6,
    })
    _append_character_properties(ET.SubElement(caption_style, _q(W, "rPr")), {
        "size_pt": 12, "bold": False,
    })

    for key, style_id in (("title", "Title"), ("subtitle", "Subtitle")):
        style = ET.SubElement(styles, _q(W, "style"), {
            _q(W, "type"): "paragraph", _q(W, "styleId"): style_id,
        })
        ET.SubElement(style, _q(W, "name"), {_q(W, "val"): style_id})
        ET.SubElement(style, _q(W, "basedOn"), {_q(W, "val"): "Normal"})
        ET.SubElement(style, _q(W, "next"), {_q(W, "val"): "BodyText"})
        _append_paragraph_properties(ET.SubElement(style, _q(W, "pPr")), style_formats[key], keep_next=True)
        _append_character_properties(ET.SubElement(style, _q(W, "rPr")), style_formats[key])

    for level in range(1, 5):
        format_name = f"heading{level}"
        style_name = f"Heading{level}"
        heading = ET.SubElement(styles, _q(W, "style"), {
            _q(W, "type"): "paragraph", _q(W, "styleId"): style_name,
        })
        ET.SubElement(heading, _q(W, "name"), {_q(W, "val"): f"heading {level}"})
        ET.SubElement(heading, _q(W, "basedOn"), {_q(W, "val"): "Normal"})
        ET.SubElement(heading, _q(W, "next"), {_q(W, "val"): "Normal"})
        ET.SubElement(heading, _q(W, "qFormat"))
        ppr = ET.SubElement(heading, _q(W, "pPr"))
        _append_paragraph_properties(ppr, style_formats[format_name],
                                     outline_level=level - 1, keep_next=True)
        rpr = ET.SubElement(heading, _q(W, "rPr"))
        _append_character_properties(rpr, style_formats[format_name])

    default_char = ET.SubElement(styles, _q(W, "style"), {
        _q(W, "type"): "character", _q(W, "default"): "1", _q(W, "styleId"): "DefaultParagraphFont",
    })
    ET.SubElement(default_char, _q(W, "name"), {_q(W, "val"): "Default Paragraph Font"})
    comment_ref = ET.SubElement(styles, _q(W, "style"), {
        _q(W, "type"): "character", _q(W, "styleId"): "CommentReference",
    })
    ET.SubElement(comment_ref, _q(W, "name"), {_q(W, "val"): "Comment Reference"})
    ET.SubElement(comment_ref, _q(W, "basedOn"), {_q(W, "val"): "DefaultParagraphFont"})
    ET.SubElement(comment_ref, _q(W, "semiHidden"))
    ET.SubElement(comment_ref, _q(W, "unhideWhenUsed"))
    return ET.tostring(styles, encoding="UTF-8", xml_declaration=True)


def _numbering_xml(instances: list[dict], heading_numbering: str = "none") -> bytes:
    numbering = ET.Element(_q(W, "numbering"))
    definitions = [(0, False, False), (1, True, False)]
    for ordered in (False, True):
        if any(instance["ordered"] == ordered and instance["multilevel"] for instance in instances):
            definitions.append((3 if ordered else 2, ordered, True))
    for abstract_id, ordered, multilevel in definitions:
        abstract = ET.SubElement(numbering, _q(W, "abstractNum"), {
            _q(W, "abstractNumId"): str(abstract_id),
        })
        ET.SubElement(abstract, _q(W, "multiLevelType"), {
            _q(W, "val"): "multilevel" if multilevel else "singleLevel",
        })
        for index in range(9 if multilevel else 1):
            level = ET.SubElement(abstract, _q(W, "lvl"), {_q(W, "ilvl"): str(index)})
            ET.SubElement(level, _q(W, "start"), {_q(W, "val"): "1"})
            ET.SubElement(level, _q(W, "numFmt"), {
                _q(W, "val"): "decimal" if ordered else "bullet",
            })
            if multilevel and index:
                ET.SubElement(level, _q(W, "lvlRestart"), {_q(W, "val"): str(index)})
            label = ".".join(f"%{position + 1}" for position in range(index + 1)) + "." if ordered else "•"
            ET.SubElement(level, _q(W, "lvlText"), {_q(W, "val"): label})
            ET.SubElement(level, _q(W, "lvlJc"), {_q(W, "val"): "left"})
            ppr = ET.SubElement(level, _q(W, "pPr"))
            tabs = ET.SubElement(ppr, _q(W, "tabs"))
            left = str(720 * (index + 1))
            ET.SubElement(tabs, _q(W, "tab"), {_q(W, "val"): "num", _q(W, "pos"): left})
            ET.SubElement(ppr, _q(W, "ind"), {_q(W, "left"): left, _q(W, "hanging"): "360"})
    if heading_numbering != "none":
        abstract = ET.SubElement(numbering, _q(W, "abstractNum"), {
            _q(W, "abstractNumId"): "4",
        })
        ET.SubElement(abstract, _q(W, "multiLevelType"), {_q(W, "val"): "multilevel"})
        for index in range(4):
            level = ET.SubElement(abstract, _q(W, "lvl"), {_q(W, "ilvl"): str(index)})
            ET.SubElement(level, _q(W, "start"), {_q(W, "val"): "1"})
            if heading_numbering == "decimal":
                fmt = "decimal"
                label = ".".join(f"%{n}" for n in range(1, index + 2)) + " "
            else:
                fmt = "chineseCounting" if index in (0, 1) else "decimal"
                label = ("%1、", "（%2）", "%3.", "（%4）")[index]
            ET.SubElement(level, _q(W, "numFmt"), {_q(W, "val"): fmt})
            ET.SubElement(level, _q(W, "suff"), {_q(W, "val"): "nothing"})
            ET.SubElement(level, _q(W, "lvlText"), {_q(W, "val"): label})
            ET.SubElement(level, _q(W, "lvlJc"), {_q(W, "val"): "left"})
        num = ET.SubElement(numbering, _q(W, "num"), {_q(W, "numId"): "1"})
        ET.SubElement(num, _q(W, "abstractNumId"), {_q(W, "val"): "4"})
    offset = 1 if heading_numbering != "none" else 0
    for num_id, instance in enumerate(instances, start=1 + offset):
        num = ET.SubElement(numbering, _q(W, "num"), {_q(W, "numId"): str(num_id)})
        abstract_id = int(instance["ordered"]) + (2 if instance["multilevel"] else 0)
        ET.SubElement(num, _q(W, "abstractNumId"), {_q(W, "val"): str(abstract_id)})
    return ET.tostring(numbering, encoding="UTF-8", xml_declaration=True)


def _table(parent: ET.Element, block: dict, next_drawing_id: int) -> list[dict]:
    table = ET.SubElement(parent, _q(W, "tbl"))
    props = ET.SubElement(table, _q(W, "tblPr"))
    layout = block["format"]
    total = sum(block["widths_twips"])
    ET.SubElement(props, _q(W, "tblW"), {_q(W, "w"): str(total), _q(W, "type"): "dxa"})
    ET.SubElement(props, _q(W, "jc"), {_q(W, "val"): layout["align"]})
    borders = ET.SubElement(props, _q(W, "tblBorders"))
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        ET.SubElement(borders, _q(W, edge), _border_attributes(_resolved_border(layout, edge)))
    if layout["cell_fill"] != "none":
        ET.SubElement(props, _q(W, "shd"), {
            _q(W, "val"): "clear", _q(W, "fill"): layout["cell_fill"],
        })
    ET.SubElement(props, _q(W, "tblLayout"), {_q(W, "type"): "fixed"})
    cell_margins = ET.SubElement(props, _q(W, "tblCellMar"))
    for side in ("top", "left", "bottom", "right"):
        ET.SubElement(cell_margins, _q(W, side), {
            _q(W, "w"): str(_twips(layout[f"cell_margin_{side}_mm"])),
            _q(W, "type"): "dxa",
        })
    grid = ET.SubElement(table, _q(W, "tblGrid"))
    for width in block["widths_twips"]:
        ET.SubElement(grid, _q(W, "gridCol"), {_q(W, "w"): str(width)})
    images = []
    for row_index, values in enumerate(block["rows"]):
        row = ET.SubElement(table, _q(W, "tr"))
        is_header = row_index < block["header_rows"]
        repeat_header = is_header and layout["repeat_header"]
        if not layout["allow_row_split"] or repeat_header:
            row_props = ET.SubElement(row, _q(W, "trPr"))
            if not layout["allow_row_split"]:
                ET.SubElement(row_props, _q(W, "cantSplit"), {_q(W, "val"): "true"})
            if repeat_header:
                ET.SubElement(row_props, _q(W, "tblHeader"), {_q(W, "val"): "true"})
        for column, content in enumerate(values):
            if content is None:
                continue
            span = content.get("col_span", 1) if isinstance(content, dict) else 1
            width = sum(block["widths_twips"][column:column + span])
            cell = ET.SubElement(row, _q(W, "tc"))
            cell_props = ET.SubElement(cell, _q(W, "tcPr"))
            ET.SubElement(cell_props, _q(W, "tcW"), {
                _q(W, "w"): str(width), _q(W, "type"): "dxa",
            })
            if span > 1:
                ET.SubElement(cell_props, _q(W, "gridSpan"), {_q(W, "val"): str(span)})
            if isinstance(content, dict) and content.get("v_merge"):
                ET.SubElement(cell_props, _q(W, "vMerge"), {_q(W, "val"): content["v_merge"]})
            if isinstance(content, dict) and content.get("borders"):
                cell_edges = ET.SubElement(cell_props, _q(W, "tcBorders"))
                for edge in ("top", "left", "bottom", "right"):
                    if edge in content["borders"]:
                        ET.SubElement(cell_edges, _q(W, edge), _border_attributes(content["borders"][edge]))
            if not is_header and (row_index - block["header_rows"]) % 2 == 1 and layout["band_fill"] != "none":
                ET.SubElement(cell_props, _q(W, "shd"), {
                    _q(W, "val"): "clear", _q(W, "fill"): layout["band_fill"],
                })
            ET.SubElement(cell_props, _q(W, "vAlign"), {_q(W, "val"): layout["vertical_align"]})
            style_name = "TableHeader" if is_header else "TableBody"
            text_format = block["header_format"] if is_header else block["body_format"]
            text = content if isinstance(content, str) else content["text"]
            if isinstance(content, dict) and content.get("v_merge") == "continue":
                paragraph = _paragraph(cell, runs=[], style=style_name, formatting=text_format)
            else:
                paragraph = _paragraph(cell, text=text, style=style_name, formatting=text_format)
            if isinstance(content, dict) and content["image"] is not None:
                image = content["image"]
                _add_picture(paragraph, image, next_drawing_id)
                images.append(image)
                next_drawing_id += 1
    return images


def _add_picture(parent: ET.Element, block: dict, drawing_id: int) -> None:
    run = ET.SubElement(parent, _q(W, "r"))
    drawing = ET.SubElement(run, _q(W, "drawing"))
    inline = ET.SubElement(drawing, _q(WP, "inline"), {
        "distT": "0", "distB": "0", "distL": "0", "distR": "0",
    })
    ET.SubElement(inline, _q(WP, "extent"), {"cx": str(block["cx"]), "cy": str(block["cy"])})
    ET.SubElement(inline, _q(WP, "docPr"), {
        "id": str(drawing_id), "name": f"Picture {drawing_id}", "descr": block["alt"],
    })
    frame = ET.SubElement(inline, _q(WP, "cNvGraphicFramePr"))
    ET.SubElement(frame, _q(A, "graphicFrameLocks"), {"noChangeAspect": "1"})
    graphic = ET.SubElement(inline, _q(A, "graphic"))
    graphic_data = ET.SubElement(graphic, _q(A, "graphicData"), {
        "uri": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    })
    picture = ET.SubElement(graphic_data, _q(PIC, "pic"))
    nonvisual = ET.SubElement(picture, _q(PIC, "nvPicPr"))
    ET.SubElement(nonvisual, _q(PIC, "cNvPr"), {
        "id": str(drawing_id), "name": f"image{drawing_id}.{block['extension']}",
        "descr": block["alt"],
    })
    ET.SubElement(nonvisual, _q(PIC, "cNvPicPr"))
    fill = ET.SubElement(picture, _q(PIC, "blipFill"))
    ET.SubElement(fill, _q(A, "blip"), {_q(R, "embed"): block["rid"]})
    stretch = ET.SubElement(fill, _q(A, "stretch"))
    ET.SubElement(stretch, _q(A, "fillRect"))
    shape = ET.SubElement(picture, _q(PIC, "spPr"))
    transform = ET.SubElement(shape, _q(A, "xfrm"))
    ET.SubElement(transform, _q(A, "off"), {"x": "0", "y": "0"})
    ET.SubElement(transform, _q(A, "ext"), {"cx": str(block["cx"]), "cy": str(block["cy"])})
    geometry = ET.SubElement(shape, _q(A, "prstGeom"), {"prst": "rect"})
    ET.SubElement(geometry, _q(A, "avLst"))


def _related_text_xml(root_name: str, text: str, *, page_numbers: bool = False,
                      style: str | None = None) -> bytes:
    root = ET.Element(_q(W, root_name))
    paragraph = _paragraph(root, style=style)
    if text:
        _add_text_run(paragraph, text)
    if page_numbers:
        _add_text_run(paragraph, " ")
        begin = ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "fldChar"))
        begin.set(_q(W, "fldCharType"), "begin")
        begin.set(_q(W, "dirty"), "true")
        instruction = ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "instrText"))
        instruction.set(_q(XML, "space"), "preserve")
        instruction.text = " PAGE "
        separate = ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "fldChar"))
        separate.set(_q(W, "fldCharType"), "separate")
        _add_text_run(paragraph, "1")
        end = ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "fldChar"))
        end.set(_q(W, "fldCharType"), "end")
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


_CROSSREF_PLACEHOLDER = "（请在 Word/WPS 中更新交叉引用域）"


def _append_cross_reference(paragraph: ET.Element, bookmark: str, kind: str) -> None:
    """Emit a real dirty REF/PAGEREF field; layout needs application-side updates."""
    ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "fldChar"),
                  {_q(W, "fldCharType"): "begin", _q(W, "dirty"): "true"})
    instruction = ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "instrText"))
    instruction.set(_q(XML, "space"), "preserve")
    instruction.text = f" {'REF' if kind == 'text' else 'PAGEREF'} {bookmark} \\h "
    ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "fldChar"),
                  {_q(W, "fldCharType"): "separate"})
    _add_text_run(paragraph, _CROSSREF_PLACEHOLDER)
    ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "fldChar"),
                  {_q(W, "fldCharType"): "end"})


def _section_xml(section: ET.Element, options: dict) -> None:
    # OOXML CT_SectPr ordering: header/footer references, type, pgSz, pgMar,
    # pgNumType, then titlePg. The last section belongs directly to w:body.
    for kind in ("header", "footer"):
        for variant in ("default", "first", "even"):
            rel_id = options.get(kind + "_rids", {}).get(variant)
            if rel_id is not None:
                ET.SubElement(section, _q(W, kind + "Reference"), {
                    _q(W, "type"): variant, _q(R, "id"): rel_id,
                })
    if options.get("break_type"):
        ET.SubElement(section, _q(W, "type"), {_q(W, "val"): options["break_type"]})
    page = options["page_twips"]
    width, height = page["width"], page["height"]
    size = {_q(W, "w"): str(width), _q(W, "h"): str(height)}
    if width > height:
        size[_q(W, "orient")] = "landscape"
    ET.SubElement(section, _q(W, "pgSz"), size)
    ET.SubElement(section, _q(W, "pgMar"), {
        _q(W, "top"): str(page["top"]), _q(W, "right"): str(page["right"]),
        _q(W, "bottom"): str(page["bottom"]), _q(W, "left"): str(page["left"]),
        _q(W, "header"): str(min(720, page["top"])),
        _q(W, "footer"): str(min(720, page["bottom"])), _q(W, "gutter"): "0",
    })
    if options["page_number_start"] is not None or options["page_number_format"] != "decimal":
        attributes = {_q(W, "fmt"): options["page_number_format"]}
        if options["page_number_start"] is not None:
            attributes[_q(W, "start")] = str(options["page_number_start"])
        ET.SubElement(section, _q(W, "pgNumType"), attributes)
    if options["different_first_page"]:
        ET.SubElement(section, _q(W, "titlePg"))


def _document_xml(spec: dict) -> tuple[bytes, list[dict], list[dict]]:
    root = ET.Element(_q(W, "document"))
    body = ET.SubElement(root, _q(W, "body"))
    instances: list[dict] = []
    image_blocks: list[dict] = []
    next_num_id = 2 if spec["heading_numbering"] != "none" else 1
    next_drawing_id = 1
    shared_ids = {}
    current_section = 0
    for block in spec["blocks"]:
        kind = block["type"]
        if kind in {"title", "subtitle"}:
            _paragraph(body, text=block["text"], style="Title" if kind == "title" else "Subtitle",
                       formatting=block["format"])
        elif kind == "heading":
            formatting = block["format"].copy()
            if block["align"] is not None:
                formatting["align"] = block["align"]
            _paragraph(body, text=block["text"], style=f"Heading{block['level']}",
                       formatting=formatting,
                       num_id=1 if spec["heading_numbering"] != "none" else None,
                       list_level=block["level"] - 1,
                       bookmark=(block["bookmark_id"], block["bookmark"]) if block["bookmark"] else None)
        elif kind == "toc":
            _paragraph(body, text=block["title"], style="Title")
            paragraph = ET.SubElement(body, _q(W, "p"))
            ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "fldChar"),
                          {_q(W, "fldCharType"): "begin"})
            run = ET.SubElement(paragraph, _q(W, "r"))
            instruction = ET.SubElement(run, _q(W, "instrText"))
            instruction.set(_q(XML, "space"), "preserve")
            instruction.text = f' TOC \\o "1-{block["depth"]}" \\u '
            ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "fldChar"),
                          {_q(W, "fldCharType"): "separate"})
            _add_text_run(paragraph, "（请在 Word/WPS 中更新目录域）")
            ET.SubElement(ET.SubElement(paragraph, _q(W, "r")), _q(W, "fldChar"),
                          {_q(W, "fldCharType"): "end"})
        elif kind == "paragraph":
            formatting = block["format"].copy()
            if block["align"] is not None:
                formatting["align"] = block["align"]
            if isinstance(block["content"], str):
                _paragraph(body, text=block["content"], style="BodyText", formatting=formatting)
            else:
                _paragraph(body, runs=block["content"], style="BodyText", formatting=formatting)
        elif kind == "list":
            identifier = block["list_id"]
            if identifier is None or identifier not in shared_ids:
                block["num_id"] = next_num_id
                instances.append({"ordered": block["ordered"], "multilevel": identifier is not None})
                if identifier is not None:
                    shared_ids[identifier] = next_num_id
                next_num_id += 1
            else:
                block["num_id"] = shared_ids[identifier]
            for item in block["items"]:
                _paragraph(body, text=item, num_id=block["num_id"], list_level=block["level"],
                           style="ListParagraph" if spec["list_format"] is not None else None)
        elif kind == "table":
            if block["caption"] is not None:
                _paragraph(body, text=block["caption"], style="TableCaption")
            images = _table(body, block, next_drawing_id)
            image_blocks.extend(images)
            next_drawing_id += len(images)
        elif kind == "image":
            paragraph = ET.SubElement(body, _q(W, "p"))
            _add_picture(paragraph, block, next_drawing_id)
            block["drawing_id"] = next_drawing_id
            image_blocks.append(block)
            next_drawing_id += 1
        elif kind == "reference":
            paragraph = ET.SubElement(body, _q(W, "p"))
            if block["prefix"]:
                _add_text_run(paragraph, block["prefix"])
            _append_cross_reference(paragraph, block["bookmark"], block["kind"])
            if block["suffix"]:
                _add_text_run(paragraph, block["suffix"])
        elif kind == "section_break":
            # Empty section-break paragraph owns the preceding section's properties.
            paragraph = ET.SubElement(body, _q(W, "p"))
            props = ET.SubElement(paragraph, _q(W, "pPr"))
            _section_xml(ET.SubElement(props, _q(W, "sectPr")), spec["sections"][current_section])
            current_section = block["section_index"]
        elif kind == "page_break":
            paragraph = ET.SubElement(body, _q(W, "p"))
            run = ET.SubElement(paragraph, _q(W, "r"))
            ET.SubElement(run, _q(W, "br"), {_q(W, "type"): "page"})

    _section_xml(ET.SubElement(body, _q(W, "sectPr")), spec["sections"][current_section])
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True), image_blocks, instances


def _relationship_xml(relationships: list[tuple[str, str, str]]) -> bytes:
    """Write OPC relationships with a *default* namespace, not ``ns0``.

    Both forms are XML-namespace equivalent, but some LibreOffice importers
    reject the prefixed-root form. ElementTree's global namespace registry is
    process-wide, so changing it temporarily would race concurrent builders.
    This tiny fixed-vocabulary writer uses standard-library escaping instead.
    """
    lines = [f'<?xml version="1.0" encoding="UTF-8"?>',
             f'<Relationships xmlns={quoteattr(REL)}>']
    for rel_id, rel_type, target in relationships:
        lines.append('<Relationship Id=' + quoteattr(rel_id) +
                     ' Type=' + quoteattr(rel_type) +
                     ' Target=' + quoteattr(target) + '/>')
    lines.append('</Relationships>')
    return ''.join(lines).encode('utf-8')


def _content_types_xml(extensions: set[str], overrides: list[tuple[str, str]]) -> bytes:
    root = ET.Element(_q(CT, "Types"))
    ET.SubElement(root, _q(CT, "Default"), {
        "Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
    })
    ET.SubElement(root, _q(CT, "Default"), {"Extension": "xml", "ContentType": "application/xml"})
    for extension in sorted(extensions):
        content_type = "image/png" if extension == "png" else "image/jpeg"
        ET.SubElement(root, _q(CT, "Default"), {
            "Extension": extension, "ContentType": content_type,
        })
    for name, content_type in overrides:
        ET.SubElement(root, _q(CT, "Override"), {
            "PartName": f"/{name}", "ContentType": content_type,
        })
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def _package_parts(spec: dict) -> dict[str, bytes]:
    relationships: list[tuple[str, str, str]] = []
    media = {}

    def add_relationship(rel_type: str, target: str) -> str:
        rel_id = f"rId{len(relationships) + 1}"
        relationships.append((rel_id, _REL_TYPES[rel_type], target))
        return rel_id

    def add_image(image: dict, name: str) -> None:
        digest = image["image_digest"]
        if spec["deduplicate_images"] and digest in media:
            image["media_name"], image["rid"] = media[digest]
        else:
            image["media_name"] = name
            image["rid"] = add_relationship("image", f"media/{name}")
            media[digest] = (name, image["rid"])

    add_relationship("styles", "styles.xml")
    add_relationship("numbering", "numbering.xml")
    has_toc = any(block["type"] == "toc" for block in spec["blocks"])
    has_ref = any(block["type"] == "reference" for block in spec["blocks"])
    has_settings = has_toc or has_ref or spec["odd_even_headers"]
    if has_settings:
        add_relationship("settings", "settings.xml")

    # Reuse identical header/footer parts across sections (preserves legacy names).
    related_parts = {}
    related_ids = {}
    for section in spec["sections"]:
        for kind in ("header", "footer"):
            refs = {}
            choices = section[kind + "s"]
            for variant in ("default", "first", "even"):
                if variant == "first" and not section["different_first_page"]:
                    continue
                if variant == "even" and not spec["odd_even_headers"]:
                    continue
                # Even variants default to odd-page text; first variants default to blank.
                text = choices[variant]
                if variant == "even" and text is None:
                    text = choices["default"]
                page_field = (kind == "footer" and section["page_numbers"] and
                              (variant != "first" or section["first_page_numbers"]))
                if text is None and not page_field:
                    # For subsequent sections, explicitly use an empty part to prevent
                    # Word inheriting a previous section's unrelated variant.
                    if (variant == "default"
                            and all(s[kind+'s']["default"] is None for s in spec["sections"])
                            and not (kind == "footer" and any(s["page_numbers"] for s in spec["sections"]))):
                        continue
                    text = ""
                key = (kind, text or "", page_field)
                if key not in related_ids:
                    number = 1 + len([p for p in related_parts if p.startswith('word/'+kind)])
                    part_name = f"word/{kind}{number}.xml"
                    related_ids[key] = (part_name, add_relationship(kind, part_name.removeprefix('word/')))
                    related_parts[part_name] = (kind, text or "", page_field)
                refs[variant] = related_ids[key][1]
            section[kind+"_rids"] = refs

    for index, block in enumerate(spec["blocks"], start=1):
        if block["type"] == "image":
            name = f"image{index}.{block['extension']}"
            add_image(block, name)
        elif block["type"] == "table":
            for row_index, row in enumerate(block["rows"], 1):
                for column_index, cell in enumerate(row, 1):
                    if not isinstance(cell, dict) or cell["image"] is None:
                        continue
                    image = cell["image"]
                    name = f"image{index}_r{row_index}_c{column_index}.{image['extension']}"
                    add_image(image, name)

    document_xml, image_blocks, instances = _document_xml(spec)
    overrides = [("word/document.xml", _CONTENT_TYPES["document"]),
                 ("word/styles.xml", _CONTENT_TYPES["styles"]),
                 ("word/numbering.xml", _CONTENT_TYPES["numbering"])]
    if has_settings:
        overrides.append(("word/settings.xml", _CONTENT_TYPES["settings"]))
    overrides.extend((name, _CONTENT_TYPES[kind]) for name, (kind, _, _) in related_parts.items())
    parts = {
        "[Content_Types].xml": _content_types_xml(
            {block["extension"] for block in image_blocks}, overrides),
        "_rels/.rels": _relationship_xml([("rId1", _REL_TYPES["officeDocument"], "word/document.xml")]),
        "word/document.xml": document_xml,
        "word/_rels/document.xml.rels": _relationship_xml(relationships),
        "word/styles.xml": _styles_xml(spec["styles"], spec["related_format"], spec["list_format"]),
        "word/numbering.xml": _numbering_xml(instances, spec["heading_numbering"]),
    }
    if has_settings:
        settings = ET.Element(_q(W, "settings"))
        if spec["odd_even_headers"]:
            ET.SubElement(settings, _q(W, "evenAndOddHeaders"))
        if has_toc or has_ref:
            ET.SubElement(settings, _q(W, "updateFields"), {_q(W, "val"): "true"})
        parts["word/settings.xml"] = ET.tostring(settings, encoding="UTF-8", xml_declaration=True)
    for name, (kind, value, page_field) in related_parts.items():
        parts[name] = _related_text_xml(
            "hdr" if kind == "header" else "ftr", value,
            page_numbers=page_field,
            style=("Header" if kind == "header" else "Footer") if spec["related_format"] is not None else None,
        )
    spec["related_parts"] = related_parts
    for block in image_blocks:
        parts[f"word/media/{block['media_name']}"] = block["image_data"]
    if len(parts) > _MAX_PACKAGE_PARTS:
        raise ValueError("generated DOCX exceeds the 4096-part limit")
    for name, content in parts.items():
        if name.endswith((".xml", ".rels")) and len(content) > _MAX_XML_BYTES:
            raise ValueError(f"generated XML part {name} exceeds the 16 MiB limit")
    if sum(map(len, parts.values())) > _MAX_PACKAGE_BYTES:
        raise ValueError("generated DOCX exceeds the 128 MiB package limit")
    return parts
