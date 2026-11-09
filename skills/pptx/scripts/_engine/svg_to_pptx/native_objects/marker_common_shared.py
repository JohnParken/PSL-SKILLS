# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
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

TABLE_URI = "http://schemas.openxmlformats.org/drawingml/2006/table"
CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"
CHARTEX_URI = "http://schemas.microsoft.com/office/drawing/2014/chartex"
CHART_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
CHARTEX_REL_TYPE = "http://schemas.microsoft.com/office/2014/relationships/chartEx"
CHART_COLOR_STYLE_REL_TYPE = "http://schemas.microsoft.com/office/2011/relationships/chartColorStyle"
CHART_STYLE_REL_TYPE = "http://schemas.microsoft.com/office/2011/relationships/chartStyle"
PACKAGE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
CHART_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
CHARTEX_CONTENT_TYPE = "application/vnd.ms-office.chartex+xml"
CHART_COLOR_STYLE_CONTENT_TYPE = "application/vnd.ms-office.chartcolorstyle+xml"
CHART_STYLE_CONTENT_TYPE = "application/vnd.ms-office.chartstyle+xml"

_NATIVE_KINDS = {"table", "chart", "formula"}
_POWERPOINT_COORD_MIN = -(2**31)
_POWERPOINT_COORD_MAX = 2**31 - 1
_POWERPOINT_LINE_WIDTH_MAX = 20116800
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_RE = re.compile(r"^rgba?\(([^)]+)\)$", re.IGNORECASE)
_CSS_NAMED_COLORS = {
    "aliceblue": "F0F8FF",
    "black": "000000",
    "blue": "0000FF",
    "brown": "A52A2A",
    "cyan": "00FFFF",
    "darkgray": "A9A9A9",
    "darkgrey": "A9A9A9",
    "gold": "FFD700",
    "gray": "808080",
    "green": "008000",
    "grey": "808080",
    "lightgray": "D3D3D3",
    "lightgrey": "D3D3D3",
    "magenta": "FF00FF",
    "navy": "000080",
    "orange": "FFA500",
    "purple": "800080",
    "red": "FF0000",
    "silver": "C0C0C0",
    "transparent": None,
    "white": "FFFFFF",
    "yellow": "FFFF00",
}


@dataclass(frozen=True)
class _FallbackShapeRecord:
    tag: str
    bounds: tuple[float, float, float, float]
    fill: str | None
    stroke: str | None
    labels: tuple[str, ...]
    fill_opacity: float | None = None


@dataclass(frozen=True)
class _FallbackTextRun:
    text: str
    fill: str | None
    bold: bool


@dataclass(frozen=True)
class _FallbackTextRecord:
    text: str
    x: float | None
    y: float | None
    fill: str | None
    bold: bool
    anchor: str
    labels: tuple[str, ...]
    runs: tuple[_FallbackTextRun, ...] = ()


_INHERITED_TEXT_ATTRS = ("fill", "font-weight", "text-anchor")
# fill / font-weight / text-anchor a marker inherits from outside its own
# subtree (root <svg> and ancestor groups); the record walk starts from them.
_FALLBACK_TEXT_INHERITANCE: contextvars.ContextVar[dict[str, str]] = (
    contextvars.ContextVar("_FALLBACK_TEXT_INHERITANCE", default={})
)
_UNSET: Any = object()
