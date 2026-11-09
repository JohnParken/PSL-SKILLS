# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart xml labels implementation."""
from __future__ import annotations

from .chart_xml_shared import (
    Any,
    ET,
    _alpha_xml,
    _bool_attr,
    _chart_bool,
    _chart_list,
    _chart_text_entry_font_face,
    _chart_tx_pr_xml,
    _clean_hex,
    _data_label_point_items,
    _data_label_position,
    _excel_col,
    _first_present,
    _font_face_xml,
    _font_size_hpt,
    _hex_or_none,
    _powerpoint_line_width_emu,
    _xml_escape,
    base64,
    binascii,
    data_labels_show_unlisted,
    detect_text_lang,
    hashlib,
    text_has_rtl_characters,
    text_uses_rtl,
)

def _string_cache(values: list[str]) -> str:
    points = "".join(
        f'<c:pt idx="{idx}"><c:v>{_xml_escape(value)}</c:v></c:pt>'
        for idx, value in enumerate(values)
    )
    return f'<c:strCache><c:ptCount val="{len(values)}"/>{points}</c:strCache>'


def _number_cache(
    values: list[int | float],
    number_format: str = "General",
) -> str:
    points = "".join(
        f'<c:pt idx="{idx}"><c:v>{value}</c:v></c:pt>'
        for idx, value in enumerate(values)
        if value is not None
    )
    return (
        f'<c:numCache><c:formatCode>{_xml_escape(number_format)}</c:formatCode>'
        f'<c:ptCount val="{len(values)}"/>{points}</c:numCache>'
    )


def _category_reference_xml(
    categories: list[Any],
    *,
    column_index: int = 1,
    numeric: bool,
    number_format: str | None = None,
) -> str:
    reference_tag = "numRef" if numeric else "strRef"
    cache = (
        _number_cache(categories, number_format or "General")
        if numeric
        else _string_cache([str(value) for value in categories])
    )
    return (
        f"<c:cat><c:{reference_tag}>"
        f"<c:f>Sheet1!${_excel_col(column_index)}$2:"
        f"${_excel_col(column_index)}${len(categories) + 1}</c:f>"
        f"{cache}"
        f"</c:{reference_tag}></c:cat>"
    )


def _series_color_xml(
    color: str | None,
    *,
    line: bool = True,
    fill_opacity: Any = None,
    line_width: Any = None,
) -> str:
    if not color:
        return ""
    clean = _clean_hex(color, "#4472C4")
    alpha_xml = _alpha_xml(fill_opacity, "series fill_opacity")
    line_width_xml = ""
    if line_width is not None:
        line_width_xml = (
            f' w="{_powerpoint_line_width_emu(line_width, "series line_width")}"'
        )
    line_xml = (
        f'<a:ln{line_width_xml}><a:solidFill><a:srgbClr val="{clean}"/></a:solidFill></a:ln>'
        if line else '<a:ln><a:noFill/></a:ln>'
    )
    return (
        "<c:spPr>"
        f'<a:solidFill><a:srgbClr val="{clean}">{alpha_xml}</a:srgbClr></a:solidFill>'
        f'{line_xml}'
        "</c:spPr>"
    )


def _data_label_flags_xml(config: dict[str, Any]) -> str:
    show_value = _chart_bool(
        _first_present(config.get("show_value"), config.get("showValue"), config.get("value")),
        True,
    )
    show_category = _chart_bool(
        _first_present(config.get("show_category"), config.get("showCategory"), config.get("category")),
        False,
    )
    show_series = _chart_bool(
        _first_present(config.get("show_series"), config.get("showSeries"), config.get("series")),
        False,
    )
    show_percent = _chart_bool(
        _first_present(config.get("show_percent"), config.get("showPercent"), config.get("percent")),
        False,
    )
    return (
        '<c:showLegendKey val="0"/>'
        f'<c:showVal val="{_bool_attr(show_value)}"/>'
        f'<c:showCatName val="{_bool_attr(show_category)}"/>'
        f'<c:showSerName val="{_bool_attr(show_series)}"/>'
        f'<c:showPercent val="{_bool_attr(show_percent)}"/>'
        '<c:showBubbleSize val="0"/>'
    )


def _source_data_labels_xml(config: dict[str, Any]) -> str | None:
    source = config.get("source_ooxml")
    if source is None:
        return None
    if not isinstance(source, dict) or source.get("encoding") != "base64":
        raise RuntimeError(
            "Native PPTX chart data_labels.source_ooxml must be base64 metadata"
        )
    encoded = source.get("payload")
    expected_sha = source.get("sha256")
    if not isinstance(encoded, str) or not isinstance(expected_sha, str):
        raise RuntimeError(
            "Native PPTX chart data_labels.source_ooxml is incomplete"
        )
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(
            "Native PPTX chart data_labels.source_ooxml payload is invalid base64"
        ) from exc
    if len(payload) > 2_000_000:
        raise RuntimeError(
            "Native PPTX chart data_labels.source_ooxml payload is too large"
        )
    if hashlib.sha256(payload).hexdigest() != expected_sha.lower():
        raise RuntimeError(
            "Native PPTX chart data_labels.source_ooxml checksum mismatch"
        )
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError(
            "Native PPTX chart data_labels.source_ooxml is malformed"
        ) from exc
    chart_ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    if root.tag != f"{{{chart_ns}}}dLbls":
        raise RuntimeError(
            "Native PPTX chart data_labels.source_ooxml root must be c:dLbls"
        )
    if any(
        isinstance(name, str) and name.startswith(f"{{{rel_ns}}}")
        for node in root.iter()
        for name in node.attrib
    ):
        raise RuntimeError(
            "Native PPTX chart data_labels.source_ooxml cannot contain relationships"
        )
    return ET.tostring(root, encoding="unicode")


def _data_label_custom_text_xml(
    text: str,
    *,
    font_size: int,
    color: str | None,
    bold: bool,
    font_face: str | None,
    language: str | None,
) -> str:
    fill_xml = (
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        if color else ""
    )
    bold_attr = ' b="1"' if bold else ""
    paragraphs: list[str] = []
    for line in text.split("\n"):
        lang = detect_text_lang(line, language)
        rtl_attr = ' rtl="1"' if text_uses_rtl(line, language) else ""
        run_rtl = '<a:rtl val="1"/>' if text_has_rtl_characters(line) else ""
        paragraphs.append(
            f'<a:p><a:pPr{rtl_attr}/><a:r><a:rPr lang="{lang}" '
            f'sz="{font_size}"{bold_attr}>{fill_xml}{_font_face_xml(font_face, language)}'
            f'{run_rtl}</a:rPr><a:t>{_xml_escape(line)}</a:t></a:r></a:p>'
        )
    return (
        "<c:tx><c:rich><a:bodyPr/><a:lstStyle/>"
        + "".join(paragraphs)
        + "</c:rich></c:tx>"
    )


def _data_labels_xml(
    config: dict[str, Any] | None,
    *,
    chart_type: str,
    grouping: str | None,
    point_count: int,
    font_size: int,
    default_color: str | None,
    default_font_face: str | None,
    language: str | None = None,
) -> str:
    if config is None:
        return ""
    source_xml = _source_data_labels_xml(config)
    if source_xml is not None:
        return source_xml
    show_leader_lines = _chart_bool(
        _first_present(
            config.get("show_leader_lines"),
            config.get("showLeaderLines"),
        ),
        False,
    )
    leader_lines_xml = f'<c:showLeaderLines val="{_bool_attr(show_leader_lines)}"/>'
    position = _data_label_position(config.get("position"), chart_type, grouping)
    raw_font_size = _first_present(config.get("font_size"), config.get("fontSize"))
    label_font_size = _font_size_hpt(raw_font_size, 12) if raw_font_size is not None else font_size
    color = _hex_or_none(config.get("color")) or default_color
    bold = _chart_bool(config.get("bold"), False)
    font_face = _chart_text_entry_font_face(config, default_font_face)
    tx_pr_xml = _chart_tx_pr_xml(
        label_font_size,
        color,
        bold=bold,
        font_face=font_face,
        language=language,
    )
    num_fmt = _first_present(
        config.get("number_format"),
        config.get("numberFormat"),
        config.get("format"),
    )
    num_fmt_xml = (
        f'<c:numFmt formatCode="{_xml_escape(str(num_fmt))}" sourceLinked="0"/>'
        if num_fmt else ""
    )
    flags_xml = _data_label_flags_xml(config)
    point_items = _data_label_point_items(
        config,
        chart_type,
        grouping,
        point_count,
    )
    if point_items:
        selected_items = {int(item["idx"]): item for item in point_items}
        show_unlisted = data_labels_show_unlisted(config)
        point_label_xml = ""
        for idx in range(point_count):
            item = selected_items.get(idx)
            if item is None:
                if not show_unlisted:
                    point_label_xml += f'<c:dLbl><c:idx val="{idx}"/><c:delete val="1"/></c:dLbl>'
                continue
            if item.get("delete") is True:
                point_label_xml += f'<c:dLbl><c:idx val="{idx}"/><c:delete val="1"/></c:dLbl>'
                continue
            item_font_size_raw = _first_present(item.get("font_size"), item.get("fontSize"))
            item_font_size = (
                _font_size_hpt(item_font_size_raw, 12)
                if item_font_size_raw is not None else label_font_size
            )
            item_color = _hex_or_none(item.get("color")) or color
            item_font_face = _chart_text_entry_font_face(item, font_face)
            item_position = _data_label_position(
                _first_present(item.get("position"), config.get("position")),
                chart_type,
                grouping,
            )
            item_num_fmt = _first_present(
                item.get("number_format"),
                item.get("numberFormat"),
                item.get("format"),
                num_fmt,
            )
            item_num_fmt_xml = (
                f'<c:numFmt formatCode="{_xml_escape(str(item_num_fmt))}" sourceLinked="0"/>'
                if item_num_fmt else ""
            )
            item_bold = _chart_bool(item.get("bold"), bold)
            item_position_xml = f'<c:dLblPos val="{item_position}"/>' if item_position else ""
            item_text_properties_xml = _chart_tx_pr_xml(
                item_font_size,
                item_color,
                bold=item_bold,
                font_face=item_font_face,
                language=language,
            )
            custom_text_xml = ""
            if item.get("text") is not None:
                custom_text_xml = _data_label_custom_text_xml(
                    str(item["text"]),
                    font_size=item_font_size,
                    color=item_color,
                    bold=item_bold,
                    font_face=item_font_face,
                    language=language,
                )
            point_label_xml += (
                f'<c:dLbl><c:idx val="{idx}"/>'
                f"{custom_text_xml}"
                f"{item_num_fmt_xml}"
                f"{item_text_properties_xml}"
                f"{item_position_xml}"
                f"{_data_label_flags_xml({**config, **item})}"
                "</c:dLbl>"
            )
        series_label_xml = ""
        if show_unlisted:
            position_xml = f'<c:dLblPos val="{position}"/>' if position else ""
            series_label_xml = f"{num_fmt_xml}{tx_pr_xml}{position_xml}{flags_xml}"
        return f"<c:dLbls>{point_label_xml}{series_label_xml}{leader_lines_xml}</c:dLbls>"

    label_colors = [
        _clean_hex(item, "#404040")
        for item in _chart_list(
            _first_present(config.get("colors"), config.get("label_colors"), config.get("labelColors")),
            "data_labels.colors",
        )
    ]
    if label_colors and len(label_colors) != point_count:
        raise RuntimeError("Native PPTX chart data_labels.colors must match point count")
    point_label_xml = ""
    for idx, label_color in enumerate(label_colors):
        position_xml = f'<c:dLblPos val="{position}"/>' if position else ""
        point_label_xml += (
            f'<c:dLbl><c:idx val="{idx}"/>'
            f"{num_fmt_xml}"
            f"{_chart_tx_pr_xml(label_font_size, label_color, bold=bold, font_face=font_face, language=language)}"
            f"{position_xml}"
            f"{flags_xml}"
            "</c:dLbl>"
        )
    position_xml = f'<c:dLblPos val="{position}"/>' if position else ""
    return (
        "<c:dLbls>"
        f"{point_label_xml}"
        f"{num_fmt_xml}{tx_pr_xml}"
        f"{position_xml}"
        f"{flags_xml}"
        f'{leader_lines_xml}'
        "</c:dLbls>"
    )


def _series_scoped_data_labels(config: dict[str, Any] | None) -> bool:
    """Return whether point-level overrides require a series ``c:dLbls``."""
    if config is None:
        return False
    return any(
        bool(config.get(key))
        for key in ("points", "colors", "label_colors", "labelColors")
    )
