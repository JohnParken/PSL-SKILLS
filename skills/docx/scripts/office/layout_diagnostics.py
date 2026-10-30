"""Static, non-blocking layout risk checks for normalized DOCX specifications."""

from __future__ import annotations

from typing import Any

MAX_LAYOUT_ISSUES = 100
LAYOUT_TRUNCATION_NOTICE = (
    "layout-warning: additional layout issues exist; showing only the first 100."
)

FIXED_LINE_HEIGHT_RISK = "FIXED_LINE_HEIGHT_RISK"
PARAGRAPH_WIDTH_RISK = "PARAGRAPH_WIDTH_RISK"

_MESSAGES = {
    FIXED_LINE_HEIGHT_RISK:
        "Serialized text size exceeds fixed line spacing; rendered text may be clipped.",
    PARAGRAPH_WIDTH_RISK:
        "Resolved horizontal indents meet or exceed the available paragraph width.",
}


def _points_to_twips(value: float) -> int:
    return int(value * 20 + 0.5)


def _millimeters_to_twips(value: float) -> int:
    return int(value * 1440.0 / 25.4 + 0.5)


def _font_size_twips(value: float) -> int:
    """Return the serialized half-point font size expressed in twips."""
    half_points = int(value * 2 + 0.5)
    return half_points * 10


def _merge_format(base: dict[str, Any], layer: dict[str, Any]) -> dict[str, Any]:
    result = base.copy()
    if "line_spacing_pt" in layer:
        result.pop("line_spacing_multiple", None)
    elif "line_spacing_multiple" in layer:
        result.pop("line_spacing_pt", None)
    result.update(layer)
    return result


def _text_is_substantive(text: str) -> bool:
    return bool(text.strip())


def _paragraph_font_sizes(block: dict[str, Any], formatting: dict[str, Any]) -> list[int]:
    content = block.get("content", block.get("text", ""))
    if isinstance(content, str):
        if not _text_is_substantive(content):
            return []
        return [_font_size_twips(formatting["size_pt"])]

    visible_text = [run.get("text", "") for run in content if run.get("text", "")]
    if not any(_text_is_substantive(text) for text in visible_text):
        return []

    sizes = []
    for run in content:
        if not run.get("text", ""):
            continue
        size_pt = run.get("size_pt", formatting["size_pt"])
        sizes.append(_font_size_twips(size_pt))
    return sizes


def _paragraph_has_content(block: dict[str, Any]) -> bool:
    content = block.get("content", block.get("text", ""))
    if isinstance(content, str):
        return _text_is_substantive(content)
    return any(_text_is_substantive(run.get("text", "")) for run in content)


def _indents_twips(formatting: dict[str, Any]) -> int:
    return (_points_to_twips(formatting.get("left_indent_pt", 0))
            + _points_to_twips(formatting.get("right_indent_pt", 0)))


def _add_issue(issues: list[dict[str, str]], seen: set[tuple[str, str]],
               code: str, location: str) -> bool:
    """Add an issue, returning true when the 100-entry output cap was exceeded."""
    key = (code, location)
    if key in seen:
        return False
    seen.add(key)
    if len(issues) >= MAX_LAYOUT_ISSUES:
        return True
    issues.append({
        "level": "warning",
        "code": code,
        "location": location,
        "message": _MESSAGES[code],
    })
    return False


def _check_paragraph(issues: list[dict[str, str]], seen: set[tuple[str, str]],
                     location: str, formatting: dict[str, Any],
                     available_width_twips: int,
                     font_sizes_twips: list[int], *, has_content: bool) -> bool:
    line_spacing = formatting.get("line_spacing_pt")
    if (font_sizes_twips and line_spacing is not None
            and max(font_sizes_twips) > _points_to_twips(line_spacing)):
        if _add_issue(issues, seen, FIXED_LINE_HEIGHT_RISK, location):
            return True

    if has_content and _indents_twips(formatting) >= available_width_twips:
        if _add_issue(issues, seen, PARAGRAPH_WIDTH_RISK, location):
            return True
    return False


def diagnose_layout(validated: dict[str, Any]) -> tuple[list[dict[str, str]], bool]:
    """Diagnose two known layout risks without changing the normalized spec.

    The input must be the result of ``create_docx._validate_spec``. This function
    uses only normalized fields, never reopens resources, and caps returned
    issues at ``MAX_LAYOUT_ISSUES``.
    """
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    available_width_twips = validated["available_width_twips"]
    styles = validated["styles"]

    for block_index, block in enumerate(validated["blocks"]):
        kind = block["type"]
        location = f"blocks[{block_index}]"

        if kind in {"title", "subtitle"}:
            formatting = _merge_format(styles[kind], block["format"])
            if _check_paragraph(issues, seen, location, formatting,
                                available_width_twips,
                                _paragraph_font_sizes(block, formatting),
                                has_content=_paragraph_has_content(block)):
                return issues, True
        elif kind == "heading":
            formatting = _merge_format(styles[f"heading{block['level']}"], block["format"])
            if _check_paragraph(issues, seen, location, formatting,
                                available_width_twips,
                                _paragraph_font_sizes(block, formatting),
                                has_content=_paragraph_has_content(block)):
                return issues, True
        elif kind == "paragraph":
            formatting = _merge_format(styles["body"], block["format"])
            if _check_paragraph(issues, seen, location, formatting,
                                available_width_twips,
                                _paragraph_font_sizes(block, formatting),
                                has_content=_paragraph_has_content(block)):
                return issues, True
        elif kind == "table":
            layout = block["format"]
            horizontal_margins = (
                _millimeters_to_twips(layout["cell_margin_left_mm"])
                + _millimeters_to_twips(layout["cell_margin_right_mm"])
            )
            for row_index, row in enumerate(block["rows"]):
                for column_index, cell in enumerate(row):
                    cell_text = cell if isinstance(cell, str) else cell["text"]
                    cell_image = None if isinstance(cell, str) else cell["image"]
                    has_text = _text_is_substantive(cell_text)
                    has_content = has_text or cell_image is not None
                    if not has_content:
                        continue

                    is_header = row_index < block["header_rows"]
                    style_name = "table_header" if is_header else "table_body"
                    override_name = "header_format" if is_header else "body_format"
                    formatting = _merge_format(styles[style_name], block[override_name])
                    location = (f"blocks[{block_index}].rows[{row_index}]"
                                f"[{column_index}]")
                    font_sizes = (
                        [_font_size_twips(formatting["size_pt"])] if has_text else []
                    )
                    content_width = block["widths_twips"][column_index] - horizontal_margins
                    if _check_paragraph(issues, seen, location, formatting,
                                        content_width, font_sizes, has_content=True):
                        return issues, True
        # Lists use their own style and images/page breaks have no text paragraph
        # format in the generator. They are deliberately outside these checks.

    return issues, False


def layout_warning_messages(issues: list[dict[str, str]]) -> list[str]:
    """Render the capped issue list using the stable delivery warning format."""
    return [f"layout-warning: {item['code']} at {item['location']}: {item['message']}"
            for item in issues]
