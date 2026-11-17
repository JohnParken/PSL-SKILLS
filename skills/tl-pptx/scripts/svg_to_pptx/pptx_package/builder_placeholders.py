"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import uuid
from pathlib import (
    Path,
)
from xml.etree import ElementTree as ET
from ..drawingml.theme_fonts import (
    ThemeFontSpec,
)
from .template_structure import (
    TEMPLATE_PLACEHOLDER_TYPES,
    TemplateElementSpec,
    TemplateStructureError,
)

def _placeholder_text_body(
    source_shape: ET.Element,
    item: TemplateElementSpec,
) -> ET.Element:
    from .builder import (
        DML_NS,
        PML_NS,
        _TEMPLATE_PLACEHOLDER_PROMPTS,
        _layout_level_one_paragraph_properties,
        _normalize_placeholder_body_properties,
        _set_layout_level_one_default_size,
        _set_no_bullet_paragraph_properties,
        _shape_bounds_emu,
    )
    tx_body = ET.Element(f"{{{PML_NS}}}txBody")
    source_tx_body = source_shape.find(f"{{{PML_NS}}}txBody")
    source_body_pr = (
        source_tx_body.find(f"{{{DML_NS}}}bodyPr")
        if source_tx_body is not None
        else None
    )
    source_lst_style = (
        source_tx_body.find(f"{{{DML_NS}}}lstStyle")
        if source_tx_body is not None
        else None
    )
    source_run_pr = (
        source_tx_body.find(f".//{{{DML_NS}}}rPr")
        if source_tx_body is not None
        else None
    )
    body_pr = (
        ET.fromstring(ET.tostring(source_body_pr, encoding="utf-8"))
        if source_body_pr is not None
        else ET.Element(f"{{{DML_NS}}}bodyPr")
    )
    target_bounds = _shape_bounds_emu(source_shape, item.placeholder_bounds)
    try:
        source_bounds = _shape_bounds_emu(source_shape, None)
    except TemplateStructureError:
        # Composite proxy content may compile to p:grpSp, whose transform is
        # intentionally not reused for the Layout's synthetic p:sp carrier.
        # The explicit design-zone bounds remain the authoritative frame.
        source_bounds = target_bounds
    _normalize_placeholder_body_properties(
        body_pr,
        source_bounds,
        target_bounds,
    )
    tx_body.append(body_pr)
    list_style = (
        ET.fromstring(ET.tostring(source_lst_style, encoding="utf-8"))
        if source_lst_style is not None
        else ET.Element(f"{{{DML_NS}}}lstStyle")
    )
    _set_layout_level_one_default_size(list_style, source_run_pr)
    if item.placeholder in {"body", "subtitle"}:
        _set_no_bullet_paragraph_properties(
            _layout_level_one_paragraph_properties(list_style),
            replace_existing=item.placeholder == "body",
        )
    tx_body.append(list_style)

    paragraph = ET.SubElement(tx_body, f"{{{DML_NS}}}p")
    source_paragraph_props = (
        source_tx_body.find(f"{{{DML_NS}}}p/{{{DML_NS}}}pPr")
        if source_tx_body is not None
        else None
    )
    paragraph_props = (
        ET.fromstring(ET.tostring(source_paragraph_props, encoding="utf-8"))
        if source_paragraph_props is not None
        else None
    )
    if item.placeholder in {"body", "subtitle"}:
        if paragraph_props is None:
            paragraph_props = ET.Element(f"{{{DML_NS}}}pPr")
        _set_no_bullet_paragraph_properties(paragraph_props)
    if paragraph_props is not None:
        paragraph.append(paragraph_props)
    if item.placeholder in {"slide-number", "date"}:
        field_type = (
            "slidenum"
            if item.placeholder == "slide-number"
            else "datetimeFigureOut"
        )
        field = ET.SubElement(
            paragraph,
            f"{{{DML_NS}}}fld",
            {"id": f"{{{str(uuid.uuid4()).upper()}}}", "type": field_type},
        )
        if source_run_pr is not None:
            field.append(ET.fromstring(ET.tostring(source_run_pr, encoding="utf-8")))
        source_text = ""
        if source_tx_body is not None:
            source_text = "".join(
                text.text or ""
                for text in source_tx_body.findall(f".//{{{DML_NS}}}t")
            )
        field_text = (
            "‹#›"
            if item.placeholder == "slide-number"
            else source_text or "Date"
        )
        ET.SubElement(field, f"{{{DML_NS}}}t").text = field_text
    else:
        run = ET.SubElement(paragraph, f"{{{DML_NS}}}r")
        if source_run_pr is not None:
            run.append(ET.fromstring(ET.tostring(source_run_pr, encoding="utf-8")))
        ET.SubElement(run, f"{{{DML_NS}}}t").text = _TEMPLATE_PLACEHOLDER_PROMPTS.get(
            item.placeholder or "",
            "Click to add content",
        )
    ET.SubElement(paragraph, f"{{{DML_NS}}}endParaRPr", {"lang": "en-US"})
    return tx_body


def _set_placeholder_no_inherited_bullets(
    shape: ET.Element,
    item: TemplateElementSpec,
) -> None:
    """Keep prose bullet-free while preserving explicit subtitle bullets."""
    from .builder import (
        DML_NS,
        PML_NS,
        _set_no_bullet_paragraph_properties,
    )
    if item.placeholder not in {"body", "subtitle"}:
        return
    tx_body = shape.find(f"{{{PML_NS}}}txBody")
    if tx_body is None:
        return
    for paragraph in tx_body.findall(f"{{{DML_NS}}}p"):
        paragraph_props = paragraph.find(f"{{{DML_NS}}}pPr")
        if paragraph_props is None:
            paragraph_props = ET.Element(f"{{{DML_NS}}}pPr")
            paragraph.insert(0, paragraph_props)
        _set_no_bullet_paragraph_properties(
            paragraph_props,
            replace_existing=item.placeholder == "body",
        )


def _set_placeholder_theme_font_role(
    shape: ET.Element,
    item: TemplateElementSpec,
    theme_font_spec: ThemeFontSpec | None,
) -> None:
    """Use the placeholder's theme role only when it preserves the actual face."""
    from .builder import (
        DML_NS,
    )
    if theme_font_spec is None:
        return
    if item.placeholder == "title":
        prefix = "+mj"
        target_face = theme_font_spec.major
    elif item.placeholder in TEMPLATE_PLACEHOLDER_TYPES:
        prefix = "+mn"
        target_face = theme_font_spec.minor
    else:
        return
    for props_tag in ("rPr", "defRPr", "endParaRPr"):
        for props in shape.iter(f"{{{DML_NS}}}{props_tag}"):
            for font_tag, suffix in (("latin", "lt"), ("ea", "ea"), ("cs", "cs")):
                font = props.find(f"{{{DML_NS}}}{font_tag}")
                if font is not None:
                    face = font.get("typeface")
                    if face == f"+mj-{suffix}":
                        face = getattr(theme_font_spec.major, font_tag)
                    elif face == f"+mn-{suffix}":
                        face = getattr(theme_font_spec.minor, font_tag)
                    if face and face == getattr(target_face, font_tag):
                        font.set("typeface", f"{prefix}-{suffix}")


def _layout_placeholder_shape(
    source_shape: ET.Element,
    item: TemplateElementSpec,
    placeholder_idx: int | None,
    theme_font_spec: ThemeFontSpec | None = None,
) -> ET.Element:
    """Build one reusable p:sp placeholder from a prototype slide object."""
    from .builder import (
        DML_NS,
        PML_NS,
        _replace_shape_xfrm,
        _shape_bounds_emu,
    )
    placeholder_type = TEMPLATE_PLACEHOLDER_TYPES.get(item.placeholder or "")
    if placeholder_type is None:
        raise TemplateStructureError(
            f"Unsupported placeholder type: {item.placeholder!r}"
        )
    bounds = _shape_bounds_emu(source_shape, item.placeholder_bounds)
    shape = ET.Element(f"{{{PML_NS}}}sp")
    nv_sp_pr = ET.SubElement(shape, f"{{{PML_NS}}}nvSpPr")
    ET.SubElement(
        nv_sp_pr,
        f"{{{PML_NS}}}cNvPr",
        {"id": "2", "name": f"{item.element_id} Placeholder"},
    )
    c_nv_sp_pr = ET.SubElement(nv_sp_pr, f"{{{PML_NS}}}cNvSpPr")
    ET.SubElement(c_nv_sp_pr, f"{{{DML_NS}}}spLocks", {"noGrp": "1"})
    nv_pr = ET.SubElement(nv_sp_pr, f"{{{PML_NS}}}nvPr")
    placeholder_attrs = {"type": placeholder_type}
    if placeholder_idx is not None:
        placeholder_attrs["idx"] = str(placeholder_idx)
    elif item.placeholder != "title":
        raise TemplateStructureError(
            f"Placeholder {item.element_id!r} requires an idx"
        )
    ET.SubElement(nv_pr, f"{{{PML_NS}}}ph", placeholder_attrs)

    source_sp_pr = (
        source_shape.find(f"{{{PML_NS}}}spPr")
        if source_shape.tag == f"{{{PML_NS}}}sp"
        else None
    )
    if source_sp_pr is not None:
        sp_pr = ET.fromstring(ET.tostring(source_sp_pr, encoding="utf-8"))
    else:
        sp_pr = ET.Element(f"{{{PML_NS}}}spPr")
        geometry = ET.SubElement(sp_pr, f"{{{DML_NS}}}prstGeom", {"prst": "rect"})
        ET.SubElement(geometry, f"{{{DML_NS}}}avLst")
        ET.SubElement(sp_pr, f"{{{DML_NS}}}noFill")
        line = ET.SubElement(sp_pr, f"{{{DML_NS}}}ln")
        ET.SubElement(line, f"{{{DML_NS}}}noFill")
    _replace_shape_xfrm(sp_pr, bounds)
    shape.append(sp_pr)
    shape.append(_placeholder_text_body(source_shape, item))
    _set_placeholder_theme_font_role(shape, item, theme_font_spec)
    return shape


def _placeholder_binding_proxy(
    layout_placeholder: ET.Element,
    item: TemplateElementSpec,
) -> ET.Element:
    """Bind a Layout slot invisibly while leaving its visible content ordinary.

    An unbound object placeholder can leak its inherited empty frame into a
    finished Slide in non-PowerPoint renderers. A hidden matching proxy suppresses
    that inheritance. The zero-width transparent run avoids a LibreOffice empty-
    placeholder black fill without adding visible content.
    """
    from .builder import (
        DML_NS,
        PML_NS,
    )
    proxy = ET.fromstring(ET.tostring(layout_placeholder, encoding="utf-8"))
    c_nv_pr = next(proxy.iter(f"{{{PML_NS}}}cNvPr"), None)
    if c_nv_pr is None:
        raise TemplateStructureError(
            f"Cannot create placeholder binding for {item.element_id!r}: "
            "p:cNvPr is missing"
        )
    placeholder = proxy.find(f".//{{{PML_NS}}}ph")
    if placeholder is None:
        raise TemplateStructureError(
            f"Cannot create placeholder binding for {item.element_id!r}: "
            "p:ph is missing"
        )
    c_nv_pr.set(
        "name",
        "Placeholder Binding "
        f"{placeholder.get('type', 'body')} {placeholder.get('idx', '0')}",
    )
    c_nv_pr.set("hidden", "1")
    tx_body = proxy.find(f"{{{PML_NS}}}txBody")
    if tx_body is None:
        raise TemplateStructureError(
            f"Cannot create placeholder binding for {item.element_id!r}: "
            "p:txBody is missing"
        )
    for child in list(tx_body):
        if child.tag == f"{{{DML_NS}}}p":
            tx_body.remove(child)
    paragraph = ET.SubElement(tx_body, f"{{{DML_NS}}}p")
    run = ET.SubElement(paragraph, f"{{{DML_NS}}}r")
    run_props = ET.SubElement(
        run,
        f"{{{DML_NS}}}rPr",
        {"lang": "en-US", "sz": "100"},
    )
    solid_fill = ET.SubElement(run_props, f"{{{DML_NS}}}solidFill")
    color = ET.SubElement(solid_fill, f"{{{DML_NS}}}srgbClr", {"val": "FFFFFF"})
    ET.SubElement(color, f"{{{DML_NS}}}alpha", {"val": "0"})
    ET.SubElement(run, f"{{{DML_NS}}}t").text = "\u200b"
    ET.SubElement(
        paragraph,
        f"{{{DML_NS}}}endParaRPr",
        {"lang": "en-US"},
    )
    return proxy


def _patch_slide_placeholder(
    shape: ET.Element,
    item: TemplateElementSpec,
    placeholder_idx: int | None,
    placeholder_type: str | None = None,
    theme_font_spec: ThemeFontSpec | None = None,
) -> None:
    from .builder import (
        PML_NS,
    )
    resolved_type = placeholder_type or TEMPLATE_PLACEHOLDER_TYPES.get(
        item.placeholder or ""
    )
    if resolved_type is None:
        raise TemplateStructureError(
            f"Unsupported placeholder type: {item.placeholder!r}"
        )
    nv_paths = {
        f"{{{PML_NS}}}sp": f"{{{PML_NS}}}nvSpPr/{{{PML_NS}}}nvPr",
        f"{{{PML_NS}}}pic": f"{{{PML_NS}}}nvPicPr/{{{PML_NS}}}nvPr",
        f"{{{PML_NS}}}graphicFrame": (
            f"{{{PML_NS}}}nvGraphicFramePr/{{{PML_NS}}}nvPr"
        ),
    }
    nv_path = nv_paths.get(shape.tag)
    if nv_path is None:
        raise TemplateStructureError(
            f"Placeholder {item.element_id!r} converted to unsupported "
            f"DrawingML element {shape.tag.rsplit('}', 1)[-1]!r}; text/picture/"
            "native chart/table placeholders must remain one top-level object"
        )
    nv_pr = shape.find(nv_path)
    if nv_pr is None:
        raise TemplateStructureError(
            f"Placeholder {item.element_id!r} has no non-visual properties"
        )
    _set_placeholder_no_inherited_bullets(shape, item)
    _set_placeholder_theme_font_role(shape, item, theme_font_spec)
    for existing in list(nv_pr):
        if existing.tag == f"{{{PML_NS}}}ph":
            nv_pr.remove(existing)
    placeholder_attrs: dict[str, str] = {}
    if (
        placeholder_type is not None
        or resolved_type != "obj"
        or placeholder_idx is None
    ):
        placeholder_attrs["type"] = resolved_type
    if placeholder_idx is not None:
        placeholder_attrs["idx"] = str(placeholder_idx)
    ph = ET.Element(f"{{{PML_NS}}}ph", placeholder_attrs)
    ext_tag = f"{{{PML_NS}}}extLst"
    insert_at = next(
        (idx for idx, child in enumerate(nv_pr) if child.tag == ext_tag),
        len(nv_pr),
    )
    nv_pr.insert(insert_at, ph)


def _set_template_layout_header_footer(
    layout_path: Path,
    placeholders: tuple[TemplateElementSpec, ...],
) -> None:
    """Enable declared footer fields for slides newly created from the layout."""
    from .builder import (
        PML_NS,
        _write_xml_tree,
    )
    kinds = {item.placeholder for item in placeholders}
    if not kinds.intersection({"date", "footer", "slide-number"}):
        return
    tree = ET.parse(layout_path)
    root = tree.getroot()
    hf = root.find(f"{{{PML_NS}}}hf")
    if hf is None:
        hf = ET.Element(f"{{{PML_NS}}}hf")
        trailing_tags = {
            f"{{{PML_NS}}}timing",
            f"{{{PML_NS}}}transition",
            f"{{{PML_NS}}}extLst",
        }
        insert_at = next(
            (idx for idx, child in enumerate(root) if child.tag in trailing_tags),
            len(root),
        )
        root.insert(insert_at, hf)
    hf.set("hdr", "0")
    hf.set("dt", "1" if "date" in kinds else "0")
    hf.set("ftr", "1" if "footer" in kinds else "0")
    hf.set("sldNum", "1" if "slide-number" in kinds else "0")
    _write_xml_tree(layout_path, tree)
