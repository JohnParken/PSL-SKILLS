"""Shared helpers for native PowerPoint object conversion."""

from __future__ import annotations

import contextvars
import json
import math
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from ..drawingml.context import ConvertContext, IDENTITY_MATRIX
from ..drawingml.paths import (
    parse_svg_path,
    parse_svg_points,
    svg_path_to_absolute,
)
from ..drawingml.utils import (
    EMU_PER_PX,
    ctx_h,
    ctx_w,
    ctx_x,
    ctx_y,
    font_px_to_hpt,
    matrix_multiply,
    parse_project_geometry_length,
    parse_transform_operations,
    parse_transform_matrix,
    transform_point,
)
from .marker_attributes import (
    NativeMarkerAttributeError,
    native_metadata_payload_matches,
)


# Adapted from marker_common.py; upstream MIT, Hugo He, 2025-2026.
def _local_tag(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag


def _clean_hex(value: Any, default: str) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .marker_common import (
        _hex_or_none,
    )
    return _hex_or_none(value) or _hex_or_none(default) or "000000"


def _hex_or_none(value: Any) -> str | None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .marker_common import (
        _CSS_NAMED_COLORS,
        _HEX_RE,
        _RGB_RE,
    )
    raw = str(value or "").strip()
    if not raw:
        return None
    named = _CSS_NAMED_COLORS.get(raw.lower())
    if named is not None or raw.lower() in _CSS_NAMED_COLORS:
        return named

    match = _HEX_RE.match(raw)
    if match:
        color = match.group(1).upper()
        if len(color) == 3:
            return "".join(channel * 2 for channel in color)
        return color

    match = _RGB_RE.match(raw)
    if not match:
        return None
    parts = [part.strip() for part in match.group(1).split(",")]
    if len(parts) not in {3, 4}:
        return None
    channels: list[int] = []
    for part in parts[:3]:
        try:
            if part.endswith("%"):
                value_float = float(part[:-1]) * 255.0 / 100.0
            else:
                value_float = float(part)
        except ValueError:
            return None
        if not math.isfinite(value_float):
            return None
        channels.append(max(0, min(255, int(round(value_float)))))
    return "".join(f"{channel:02X}" for channel in channels)


def _style_attr(elem: ET.Element, name: str) -> str | None:
    if elem.get(name) is not None:
        return elem.get(name)
    style = elem.get("style")
    if not style:
        return None
    for part in style.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        if key.strip() == name:
            return value.strip()
    return None


def _own_fill_opacity(elem: ET.Element) -> float | None:
    """The element's own ``opacity`` × ``fill-opacity``, or None when neither is set."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .marker_common import (
        _style_attr,
    )
    result: float | None = None
    for name in ("opacity", "fill-opacity"):
        raw = _style_attr(elem, name)
        if raw is None:
            continue
        try:
            value = float(str(raw).strip().rstrip("%")) / (100.0 if str(raw).strip().endswith("%") else 1.0)
        except ValueError:
            continue
        value = max(0.0, min(1.0, value))
        result = value if result is None else result * value
    return result


def _paint_visible(elem: ET.Element, paint: str) -> bool:
    # Resolve shared symbols at call time; retain the public module contract.
    from .marker_common import (
        _style_attr,
    )
    for name in ("opacity", f"{paint}-opacity"):
        raw = _style_attr(elem, name)
        if raw is None:
            continue
        try:
            if float(raw) <= 0:
                return False
        except ValueError:
            continue
    return True


def _normalized_fallback_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _visible_fallback_texts(elem: ET.Element, *, include_metadata: bool = False) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .marker_common import (
        _local_tag,
        _normalized_fallback_text,
        _style_attr,
    )
    texts: list[str] = []

    def visit(node: ET.Element, hidden: bool) -> None:
        tag = _local_tag(node)
        if tag in {"defs", "clipPath", "mask", "filter", "style"}:
            return
        if tag == "metadata" and not include_metadata:
            return
        node_hidden = (
            hidden
            or _style_attr(node, "display") == "none"
            or _style_attr(node, "visibility") == "hidden"
        )
        if tag == "text" and not node_hidden:
            text = _normalized_fallback_text("".join(node.itertext()))
            if text:
                texts.append(text)
            return
        for child in node:
            visit(child, node_hidden)

    visit(elem, False)
    return texts


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"Native PPTX object requires numeric {field_name}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Native PPTX object requires numeric {field_name}") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"Native PPTX object requires finite numeric {field_name}")
    return number


def _maybe_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _project_geometry_number(
    elem: ET.Element,
    attribute: str,
    default: float = 0.0,
) -> float:
    """Read one fallback geometry value through the project length contract."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .marker_common import (
        _local_tag,
    )
    raw = elem.get(attribute)
    if raw is None:
        return default
    try:
        return parse_project_geometry_length(raw, attribute)
    except ValueError as exc:
        tag = _local_tag(elem)
        elem_id = elem.get("id")
        label = f"<{tag} id={elem_id!r}>" if elem_id else f"<{tag}>"
        raise RuntimeError(
            f"Native PPTX fallback {label} {attribute}={raw!r}: {exc}"
        ) from exc


def _powerpoint_emu_value(
    emu: int,
    field_name: str,
    *,
    positive: bool = False,
) -> int:
    """Validate an already-resolved EMU value for a PowerPoint coordinate."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .marker_common import (
        _POWERPOINT_COORD_MAX,
        _POWERPOINT_COORD_MIN,
    )
    lower_bound = 1 if positive else _POWERPOINT_COORD_MIN
    if emu < lower_bound or emu > _POWERPOINT_COORD_MAX:
        qualifier = "positive " if positive else ""
        raise RuntimeError(
            f"Native PPTX object {field_name} must resolve to a {qualifier}"
            "32-bit PowerPoint coordinate"
        )
    return emu


def _powerpoint_emu(value: Any, field_name: str, *, positive: bool = False) -> int:
    """Convert SVG px to an EMU value that PowerPoint can represent."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .marker_common import (
        _number,
        _powerpoint_emu_value,
    )
    number = _number(value, field_name)
    scaled = number * EMU_PER_PX
    if not math.isfinite(scaled):
        raise RuntimeError(f"Native PPTX object {field_name} exceeds PowerPoint coordinates")
    return _powerpoint_emu_value(round(scaled), field_name, positive=positive)


def _powerpoint_line_width_emu(value: Any, field_name: str) -> int:
    """Convert SVG px to a legal DrawingML ``ST_LineWidth`` value."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .marker_common import (
        _POWERPOINT_LINE_WIDTH_MAX,
        _powerpoint_emu,
    )
    emu = _powerpoint_emu(value, field_name, positive=True)
    if emu > _POWERPOINT_LINE_WIDTH_MAX:
        raise RuntimeError(
            f"Native PPTX object {field_name} exceeds DrawingML line-width range"
        )
    return emu


def native_marker_transform(transform: str | None) -> tuple[float, float, float, float]:
    """Return a strict native-marker transform as ``dx, dy, sx, sy``."""
    raw = (transform or "").strip()
    if not raw:
        return 0.0, 0.0, 1.0, 1.0

    try:
        operations = parse_transform_operations(raw)
    except ValueError as exc:
        raise RuntimeError(
            "Native PPTX replacement markers support translate/scale transforms only"
        ) from exc
    if any(name not in {"translate", "scale"} for name, _args in operations):
        raise RuntimeError(
            "Native PPTX replacement markers support translate/scale transforms only"
        )

    a, b, c, d, e, f = parse_transform_matrix(raw)
    components = (a, b, c, d, e, f)
    if not all(math.isfinite(value) for value in components):
        raise RuntimeError("Native PPTX marker transform exceeds finite coordinates")
    if b != 0.0 or c != 0.0:
        raise RuntimeError(
            "Native PPTX replacement markers support translate/scale transforms only"
        )
    return e, f, a, d


