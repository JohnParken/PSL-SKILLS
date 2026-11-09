# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Marker payload implementation."""
from __future__ import annotations

from .marker_common_shared import (
    Any,
    ConvertContext,
    ET,
    ctx_h,
    ctx_w,
    ctx_x,
    ctx_y,
    font_px_to_hpt,
    json,
    native_metadata_payload_matches,
    re,
)

def _resolved_bounds(
    elem: ET.Element,
    payload: dict[str, Any],
    ctx: ConvertContext,
) -> tuple[float, float, float, float, bool]:
    """Resolve object bounds in SVG px plus whether all bounds were explicit."""
    from .marker_values import _number
    from .marker_geometry import _inferred_bounds
    if ctx.use_transform_matrix:
        raise RuntimeError("Native PPTX replacement markers support translate/scale only")

    raw_x = payload.get("x", elem.get("data-pptx-x"))
    raw_y = payload.get("y", elem.get("data-pptx-y"))
    raw_width = payload.get("width", elem.get("data-pptx-width"))
    raw_height = payload.get("height", elem.get("data-pptx-height"))
    explicit_bounds = all(value is not None for value in (raw_x, raw_y, raw_width, raw_height))
    inferred = None
    if not explicit_bounds:
        inferred = _inferred_bounds(elem)
        if inferred is None:
            raise RuntimeError(
                "Native PPTX object requires x/y/width/height or visible fallback geometry"
            )

    x = _number(raw_x, "x") if raw_x is not None else inferred[0]  # type: ignore[index]
    y = _number(raw_y, "y") if raw_y is not None else inferred[1]  # type: ignore[index]
    width = (
        _number(raw_width, "width")
        if raw_width is not None else inferred[2] - inferred[0]  # type: ignore[index]
    )
    height = (
        _number(raw_height, "height")
        if raw_height is not None else inferred[3] - inferred[1]  # type: ignore[index]
    )
    if width <= 0 or height <= 0:
        raise RuntimeError("Native PPTX object width/height must be positive")

    if explicit_bounds:
        resolved_x = x
        resolved_y = y
        resolved_w = width
        resolved_h = height
    else:
        resolved_x = ctx_x(x, ctx)
        resolved_y = ctx_y(y, ctx)
        resolved_w = ctx_w(width, ctx)
        resolved_h = ctx_h(height, ctx)
    return resolved_x, resolved_y, resolved_w, resolved_h, explicit_bounds


def _bounds(elem: ET.Element, payload: dict[str, Any], ctx: ConvertContext) -> tuple[int, int, int, int]:
    """Return object bounds as DrawingML EMU tuple."""
    from .marker_values import _powerpoint_emu
    x, y, width, height, _ = _resolved_bounds(elem, payload, ctx)
    return (
        _powerpoint_emu(x, "x"),
        _powerpoint_emu(y, "y"),
        _powerpoint_emu(width, "width", positive=True),
        _powerpoint_emu(height, "height", positive=True),
    )


def _validate_bounds_inputs(
    elem: ET.Element,
    payload: dict[str, Any],
    ctx: ConvertContext,
) -> tuple[int, int, int, int, bool]:
    from .marker_values import _powerpoint_emu
    x, y, width, height, explicit_bounds = _resolved_bounds(elem, payload, ctx)
    return (
        _powerpoint_emu(x, "x"),
        _powerpoint_emu(y, "y"),
        _powerpoint_emu(width, "width", positive=True),
        _powerpoint_emu(height, "height", positive=True),
        explicit_bounds,
    )


def _validate_payload_xml_strings(value: Any) -> None:
    """Reject JSON strings that cannot be serialized into PPTX XML 1.0 parts."""
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_payload_xml_strings(key)
            _validate_payload_xml_strings(item)
        return
    if isinstance(value, list):
        for item in value:
            _validate_payload_xml_strings(item)
        return
    if not isinstance(value, str):
        return
    for char in value:
        codepoint = ord(char)
        if (
            codepoint in {0x09, 0x0A, 0x0D}
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        ):
            continue
        raise RuntimeError(
            "Native PPTX metadata contains an XML 1.0-invalid character "
            f"U+{codepoint:04X}"
        )


def _load_payload(elem: ET.Element, kind: str) -> dict[str, Any]:
    from .marker_values import _local_tag
    raw = elem.get("data-pptx-json") or elem.get("data-pptx-data")
    if raw is None:
        for child in elem:
            if _local_tag(child) != "metadata":
                continue
            if native_metadata_payload_matches(child, kind):
                raw = "".join(child.itertext()).strip()
                break

    if not raw:
        raise RuntimeError(
            f"PPTX {kind} replacement marker requires JSON metadata; add "
            '<metadata type="application/json"> for a real data-backed '
            "object, or remove data-pptx-replace-with from SVG-only "
            "KPI/diagram groups"
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Native PPTX {kind} metadata is not valid JSON: {exc.msg}") from exc
    except (ValueError, RecursionError) as exc:
        raise RuntimeError(f"Native PPTX {kind} metadata cannot be decoded") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Native PPTX {kind} metadata must be a JSON object")
    _validate_payload_xml_strings(payload)
    return payload


def _font_size_hpt(value: Any, default_px: int = 18) -> int:
    def convert(raw: Any) -> int | None:
        try:
            px = float(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        try:
            return font_px_to_hpt(px)
        except ValueError:
            return None

    return convert(value) or convert(default_px) or 1350


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _bool_attr(value: bool) -> str:
    return "1" if value else "0"


def _chart_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    key = _compact_key(value)
    if key in {"1", "on", "true", "yes"}:
        return True
    if key in {"0", "false", "no", "off"}:
        return False
    return bool(value)


def _excel_col(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"


def _compact_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
