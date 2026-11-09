# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart style companions implementation."""
from __future__ import annotations

from .chart_style_shared import (
    Any,
    ConvertContext,
    ET,
    ShapeResult,
    _compact_key,
    _fallback_text_records,
    _first_present,
    _font_size_hpt,
    _hex_or_none,
    _normalized_fallback_text,
    _powerpoint_emu,
    _powerpoint_emu_value,
    _xml_escape,
    ctx_x,
    ctx_y,
    detect_text_lang,
    px_to_emu,
    text_has_rtl_characters,
    text_uses_rtl,
)

def _text_box_xml(
    ctx: ConvertContext,
    *,
    text: str,
    role: str,
    off_x: int,
    off_y: int,
    ext_cx: int,
    ext_cy: int,
    font_size: int,
    color: str | None,
    align: str = "l",
    bold: bool = False,
    font_face: str | None = None,
    anchor: str = "t",
) -> str:
    from .chart_style_text import _font_face_xml
    shape_id = ctx.next_id()
    align_key = _compact_key(align)
    algn = {
        "center": "ctr",
        "centre": "ctr",
        "ctr": "ctr",
        "middle": "ctr",
        "right": "r",
        "r": "r",
        "left": "l",
        "l": "l",
    }.get(align_key, "l")
    fill_xml = (
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        if color else ""
    )
    bold_attr = ' b="1"' if bold else ""
    lang = detect_text_lang(text, ctx.primary_language)
    run_rtl = '<a:rtl val="1"/>' if text_has_rtl_characters(text) else ''
    run_properties_xml = (
        f'{fill_xml}{_font_face_xml(font_face, ctx.primary_language)}'
        f'{run_rtl}'
    )
    rtl_attr = (
        ' rtl="1"'
        if text_uses_rtl(text, ctx.primary_language)
        else ''
    )
    name = _xml_escape(f"Chart {role.title()} {shape_id}")
    return f'''<p:sp>
<p:nvSpPr>
<p:cNvPr id="{shape_id}" name="{name}"/>
<p:cNvSpPr txBox="1"/><p:nvPr/>
</p:nvSpPr>
<p:spPr>
<a:xfrm><a:off x="{off_x}" y="{off_y}"/><a:ext cx="{ext_cx}" cy="{ext_cy}"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
<a:noFill/>
<a:ln><a:noFill/></a:ln>
</p:spPr>
<p:txBody>
<a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="{anchor}" anchorCtr="0"/>
<a:lstStyle/>
<a:p><a:pPr algn="{algn}"{rtl_attr}/>
<a:r><a:rPr lang="{lang}" sz="{font_size}"{bold_attr}>{run_properties_xml}</a:rPr><a:t>{_xml_escape(text)}</a:t></a:r>
</a:p>
</p:txBody>
</p:sp>'''


def _chart_companion_entries(
    payload: dict[str, Any],
    *,
    include_title: bool,
    include_subtitle_as_caption: bool,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def add(role: str, value: Any) -> None:
        if value is None:
            return
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict):
                text = _first_present(item.get("text"), item.get("value"), item.get("content"))
                if text:
                    entries.append({"role": role, **item, "text": str(text)})
            elif str(item).strip():
                entries.append({"role": role, "text": str(item)})

    if include_title:
        add("title", payload.get("title"))
    caption_value = payload.get("caption")
    if caption_value is None and include_subtitle_as_caption:
        caption_value = payload.get("subtitle")
    add("caption", caption_value)
    add("source", payload.get("source"))
    for key in ("note", "notes", "footnote", "footnotes"):
        add("note", payload.get(key))
    return entries


def _chart_companion_width_only(item: dict[str, Any]) -> int | None:
    """Return the authored width when a companion carries only `width`."""
    if "width" in item and not any(key in item for key in ("x", "y", "height")):
        return _powerpoint_emu(item["width"], "companion text width", positive=True)
    return None


def _chart_companion_box(item: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """Validate and resolve an optional explicit companion text box."""
    box_keys = ("x", "y", "width", "height")
    provided_box_keys = [key for key in box_keys if key in item]
    if provided_box_keys == ["width"]:
        # Width alone: the box is placed from the matching fallback text or
        # the default slot; only its width is authored.
        return None
    if provided_box_keys and len(provided_box_keys) != len(box_keys):
        raise RuntimeError(
            "Native PPTX chart companion text boxes require x/y/width/height together"
        )
    if not provided_box_keys:
        return None
    return (
        _powerpoint_emu(item["x"], "companion text x"),
        _powerpoint_emu(item["y"], "companion text y"),
        _powerpoint_emu(item["width"], "companion text width", positive=True),
        _powerpoint_emu(item["height"], "companion text height", positive=True),
    )


def _validate_chart_companion_boxes(
    payload: dict[str, Any],
    *,
    chart_bounds: tuple[int, int, int, int],
    include_title: bool,
    include_subtitle_as_caption: bool,
) -> None:
    """Validate companion boxes without allocating shapes or relationships."""
    from .chart_style_text import _chart_text_entry, _chart_title_is_bounded
    title_bounded = _chart_title_is_bounded(payload)
    if (
        title_bounded
        and not include_subtitle_as_caption
        and _chart_text_entry(payload.get("subtitle")) is not None
    ):
        raise RuntimeError(
            "Native PPTX classic chart bounded title does not support subtitle; "
            "use a separately bounded caption"
        )
    _, chart_off_y, _, chart_ext_cy = chart_bounds
    below_index = 0
    for item in _chart_companion_entries(
        payload,
        include_title=include_title,
        include_subtitle_as_caption=include_subtitle_as_caption,
    ):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if _chart_companion_box(item) is not None:
            continue
        if str(item.get("role") or "note") != "title":
            _powerpoint_emu_value(
                chart_off_y + chart_ext_cy + px_to_emu(4 + below_index * 18),
                "companion text y",
            )
            below_index += 1


def _chart_companion_shapes(
    ctx: ConvertContext,
    payload: dict[str, Any],
    *,
    chart_bounds: tuple[int, int, int, int],
    chart_style: dict[str, str | None],
    note_font_size: int,
    title_font_size: int,
    include_title: bool,
    include_subtitle_as_caption: bool,
    fallback: ET.Element | None = None,
) -> list[ShapeResult]:
    """Build editable companion text with its resolved slide-space bounds.

    With an SVG-first ``fallback``, a companion whose text appears exactly
    once in the fallback takes that text's position. The box is bottom
    anchored with its bottom edge a quarter em under the SVG baseline, so
    the glyph bottom lands where the SVG drew it whatever ascent the
    renderer's font has (a taller face moves the text up, never down onto
    the plot); ``text-anchor`` decides which edge ``x`` names.
    """
    from .chart_style_text import _chart_text_entry_font_face, _chart_title_is_bounded
    if _chart_title_is_bounded(payload):
        include_title = True
    fallback_texts = (
        [
            record
            for record in _fallback_text_records(fallback)
            if record.x is not None and record.y is not None
        ]
        if fallback is not None
        else []
    )
    entries = _chart_companion_entries(
        payload,
        include_title=include_title,
        include_subtitle_as_caption=include_subtitle_as_caption,
    )
    if not entries:
        return []

    chart_off_x, chart_off_y, chart_ext_cx, chart_ext_cy = chart_bounds
    shapes: list[ShapeResult] = []
    below_index = 0
    for item in entries:
        role = str(item.get("role") or "note")
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        font_size = _font_size_hpt(item.get("font_size", item.get("fontSize")), 16 if role == "title" else 12)
        if role == "title" and item.get("font_size") is None and item.get("fontSize") is None:
            font_size = title_font_size
        elif item.get("font_size") is None and item.get("fontSize") is None:
            font_size = note_font_size

        color = _hex_or_none(item.get("color")) or chart_style.get("text_color")
        font_face = _chart_text_entry_font_face(item, chart_style.get("font_face"))
        align = str(item.get("align") or ("ctr" if role == "title" else "l"))
        bold = bool(item.get("bold", role == "title"))
        explicit_box = _chart_companion_box(item)
        width_only = _chart_companion_width_only(item)
        if explicit_box is not None:
            off_x, off_y, ext_cx, ext_cy = explicit_box
        elif role == "title":
            off_x = chart_off_x
            off_y = chart_off_y
            ext_cx = chart_ext_cx
            ext_cy = px_to_emu(28)
        else:
            off_x = chart_off_x
            off_y = _powerpoint_emu_value(
                chart_off_y + chart_ext_cy + px_to_emu(4 + below_index * 18),
                "companion text y",
            )
            ext_cx = chart_ext_cx
            ext_cy = px_to_emu(16)
            below_index += 1
        if width_only is not None:
            ext_cx = width_only
        anchor = "t"
        matches = [
            record for record in fallback_texts
            if record.text == _normalized_fallback_text(text)
        ]
        if len(matches) == 1 and ctx is not None:
            record = matches[0]
            font_px = font_size / 100 / 0.75
            anchor_x = ctx_x(record.x, ctx)
            baseline_y = ctx_y(record.y, ctx)
            width_px = ext_cx / px_to_emu(1)
            left_px = {
                "middle": anchor_x - width_px / 2,
                "end": anchor_x - width_px,
            }.get(record.anchor, anchor_x)
            bottom_px = baseline_y + font_px * 0.25
            off_x = _powerpoint_emu_value(px_to_emu(left_px), "companion text x")
            off_y = _powerpoint_emu_value(px_to_emu(bottom_px - font_px * 1.6), "companion text y")
            ext_cy = px_to_emu(font_px * 1.6)
            anchor = "b"
            align = {"middle": "ctr", "end": "r"}.get(record.anchor, "l")
        text_xml = _text_box_xml(
            ctx,
            text=text,
            role=role,
            off_x=off_x,
            off_y=off_y,
            ext_cx=ext_cx,
            ext_cy=ext_cy,
            font_size=font_size,
            color=color,
            align=align,
            bold=bold,
            font_face=font_face,
            anchor=anchor,
        )
        shapes.append(ShapeResult(
            xml=text_xml,
            bounds_emu=(off_x, off_y, off_x + ext_cx, off_y + ext_cy),
        ))
    return shapes
