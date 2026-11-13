"""Bounded task inputs and the standard-library SVG-to-PPTX workflow."""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import math
import os
import platform
import pyexpat
import re
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .images import MAX_IMAGE_BYTES, inspect_image
from .package import ConvertedSlide, build_presentation
from .svg_limits import check_expansion_budget
from .validation import inspect_presentation, safe_xml, validate_presentation


SKILL_ROOT = Path(__file__).resolve().parents[2]
INPUT_SCHEMA = "psl.pptx.v1"
RESULT_SCHEMA = "psl.pptx.result.v1"
MAX_CONFIG_BYTES = 32 * 1024 * 1024
MAX_SVG_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_SVG_BYTES = 128 * 1024 * 1024
MAX_PPTX_BYTES = 128 * 1024 * 1024
MAX_SLIDES = 200
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
SVG_TAGS = frozenset({
    "svg", "g", "defs", "rect", "circle", "ellipse", "line", "path", "polyline", "polygon",
    "text", "tspan", "a", "image", "use", "symbol", "linearGradient", "radialGradient", "stop",
    "clipPath", "title", "desc", "metadata", "marker",
})
PRESENTATION_ATTRIBUTES = frozenset({
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap",
    "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "opacity",
    "display", "visibility", "clip-path", "clip-rule", "font-family", "font-size", "font-weight", "font-style",
    "font-variant", "font-stretch", "letter-spacing", "word-spacing", "text-anchor", "dominant-baseline",
    "alignment-baseline", "baseline-shift", "text-decoration", "direction", "unicode-bidi", "writing-mode",
    "stop-color", "stop-opacity", "marker-start", "marker-mid", "marker-end",
})
SVG_ATTRIBUTES = PRESENTATION_ATTRIBUTES | frozenset({
    "id", "style", "transform", "x", "y", "width", "height", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "d", "points", "viewBox", "preserveAspectRatio", "version", "href", "offset", "gradientUnits", "gradientTransform",
    "spreadMethod", "fx", "fy", "fr", "clipPathUnits", "markerWidth", "markerHeight", "refX", "refY", "orient",
    "markerUnits", "dx", "dy", "rotate", "textLength", "lengthAdjust", "type",
})
NATIVE_ATTRIBUTES = frozenset({
    "data-pptx-replace-with", "data-pptx-native-authority", "data-pptx-json", "data-pptx-data",
    "data-pptx-bounds", "data-pptx-x", "data-pptx-y", "data-pptx-width", "data-pptx-height",
    "data-pptx-kind", "data-pptx-inline-formula", "data-pptx-role", "data-pptx-page-role",
    "data-pptx-shape-hyperlink",
})
DATA_IMAGE_RE = re.compile(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)\Z", re.IGNORECASE)
LOCAL_URL_RE = re.compile(r"url\(\s*['\"]?#[A-Za-z_][A-Za-z0-9_.:-]*['\"]?\s*\)", re.IGNORECASE)
URL_START_RE = re.compile(r"url\s*\(", re.IGNORECASE)
JSON_FIELDS = frozenset({"schema", "title", "width", "height", "text_flow", "native_objects", "slides"})
SLIDE_FIELDS = frozenset({"svg", "svg_path", "notes"})


class TaskError(ValueError):
    """An explicit input, environment, or task-boundary error."""

    def __init__(self, message: str, code: str = "invalid_input") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparedSlide:
    """A checked, self-contained SVG and its plain-text notes."""

    svg: str
    notes: str


@dataclass(frozen=True)
class Deck:
    """The public JSON creation contract after validation."""

    title: str
    width: float
    height: float
    text_flow: str
    native_objects: bool
    slides: tuple[PreparedSlide, ...]


def task_directory() -> Path:
    directory = Path.cwd().resolve()
    if directory.is_relative_to(SKILL_ROOT):
        raise TaskError("Run from the confirmed task directory outside the skill installation", "task_boundary")
    return directory


def task_path(value: str, *, must_exist: bool = True) -> Path:
    """Resolve a portable task-relative path without following boundary escapes."""
    if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
        raise TaskError("File paths must be nonempty task-relative strings", "task_boundary")
    path = Path(value)
    if path.is_absolute() or PureWindowsPath(value).drive or "\\" in value or ".." in path.parts:
        raise TaskError("Absolute, parent-traversal and Windows-style paths are unsupported", "task_boundary")
    root = task_directory()
    resolved = (root / path).resolve(strict=False)
    if not resolved.is_relative_to(root) or resolved.is_relative_to(SKILL_ROOT):
        raise TaskError("A file path escapes the task directory or enters the skill installation", "task_boundary")
    # Refuse symlinks, including in the destination's parent chain. This also
    # avoids turning a path alias into permission to replace a different file.
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise TaskError("Symlink task paths are unsupported", "task_boundary")
    if must_exist and not resolved.is_file():
        raise TaskError("The requested task input is not a regular file", "input_missing")
    if not must_exist and not resolved.parent.is_dir():
        raise TaskError("Create the output parent directory inside the task directory first", "output_parent_missing")
    return resolved


@contextlib.contextmanager
def _parent_descriptor(path: Path):
    """Walk from the confirmed task root with openat and no symlink traversal."""
    root = task_directory()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise TaskError("Task file is outside the confirmed directory", "task_boundary") from error
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise TaskError("Invalid task-relative file path", "task_boundary")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, relative.name
    finally:
        os.close(descriptor)


def _read_at(parent: int, name: str, maximum: int) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise TaskError("Input file is not regular or exceeds its size limit", "resource_limit")
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise TaskError("Input exceeds its size limit", "resource_limit")
    return data


def read_limited(path: Path, maximum: int) -> bytes:
    """Read a bounded regular file without following symlinks at open time."""
    with _parent_descriptor(path) as (parent, name):
        return _read_at(parent, name, maximum)


def _pairs_unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise TaskError("Duplicate JSON keys are unsupported")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise TaskError(f"Non-finite JSON constant is unsupported: {value}")


def _json_value_check(value: object, depth: int = 0) -> None:
    if depth > 64:
        raise TaskError("JSON nesting exceeds 64 levels", "resource_limit")
    if isinstance(value, float) and not math.isfinite(value):
        raise TaskError("JSON numbers must be finite")
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise TaskError("JSON strings must not contain unpaired Unicode surrogates")
    if isinstance(value, dict):
        for key, item in value.items():
            _json_value_check(key, depth + 1)
            _json_value_check(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _json_value_check(item, depth + 1)


def strict_json(data: str | bytes) -> object:
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8-sig")
        value = json.loads(data, object_pairs_hook=_pairs_unique, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        raise TaskError("Input is not bounded, valid UTF-8 JSON") from error
    _json_value_check(value)
    return value


def _unknown_fields(value: dict, allowed: frozenset[str], label: str) -> None:
    if set(value) - allowed:
        raise TaskError(f"{label} contains unsupported fields: {', '.join(sorted(set(value) - allowed))}")


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise TaskError(f"{label} must be a string of at most {maximum} characters")
    if any(not (ord(char) in (9, 10, 13) or 32 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD
                or 0x10000 <= ord(char) <= 0x10FFFF) for char in value):
        raise TaskError(f"{label} contains a character forbidden in XML 1.0")
    return value


def _dimension(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 96 <= value <= 5376:
        raise TaskError(f"{label} must be a number between 96 and 5376 pixels")
    return float(value)


def _image_uri(href: str, *, max_uri_bytes: int) -> str:
    if href.startswith("data:"):
        match = DATA_IMAGE_RE.fullmatch(href)
        if match is None or len(match[2]) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise TaskError("Only bounded base64 PNG/JPEG data images are supported")
        try:
            data = base64.b64decode(match[2], validate=True)
        except (ValueError, binascii.Error) as error:
            raise TaskError("Invalid base64 image data") from error
        declared_format = match[1].lower()
    else:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise TaskError("Image references must be task-relative paths or base64 PNG/JPEG data")
        data = read_limited(task_path(href), MAX_IMAGE_BYTES)
        declared_format = None
    if ((len(data) + 2) // 3) * 4 + 24 > max_uri_bytes:
        raise TaskError("Embedded images exceed the remaining deck byte budget", "resource_limit")
    info = inspect_image(data, declared_format)
    media_type = "jpeg" if info.format == "jpg" else "png"
    return f"data:image/{media_type};base64," + base64.b64encode(data).decode("ascii")


def _formula_expansion_cost(latex: str, *, inline: bool = False, style_bytes: int = 0) -> int:
    """Reserve bounded math structure plus repeated run/control styling."""
    from svg_to_pptx.native_objects.formula_compiler import compile_latex_to_inline_omml, compile_latex_to_omml

    _text(latex, "Formula LaTeX", 8192)
    compiler = compile_latex_to_inline_omml if inline else compile_latex_to_omml
    # The compiler limits macros, AST size and serialized OMML independently.
    # Count the expanded expression, so short macro definitions cannot evade
    # the reservation. Styles are applied later to runs/control properties.
    omml = compiler(latex)
    node_count = sum(1 for _node in ET.fromstring(omml).iter())
    styling_cost = node_count * (4096 + 32 * style_bytes)
    return 65536 + len(omml.encode("utf-8")) + styling_cost


def _inline_formula_style_bytes(element: ET.Element, parents: dict[ET.Element, ET.Element], paint_bytes: int) -> int:
    """Bound inherited source styles and gradient data replicated into math."""
    total = paint_bytes
    current = element
    while current is not None:
        for name, value in current.attrib.items():
            if name in PRESENTATION_ATTRIBUTES or name in {"style", f"{{{XML_NS}}}lang"}:
                total += len(name.encode("utf-8")) + len(value.encode("utf-8"))
        current = parents.get(current)
    return total


def _check_native_payload(value: object, kind: str | None) -> int:
    if not isinstance(value, dict):
        raise TaskError("Native object metadata must be a JSON object")
    if "source_package" in value:
        raise TaskError("Imported OOXML source packages are outside the creation contract")
    encoded_size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    if encoded_size > 1024 * 1024:
        raise TaskError("Native object metadata exceeds 1 MiB", "resource_limit")
    expanded_cost = encoded_size
    if kind == "chart":
        if "chart_type" in value:
            raise TaskError("Use the canonical chart type field; chart_type aliases are unsupported")
        chart_type = str(value.get("type", "column")).lower()
        if chart_type not in {"column", "bar", "line", "area", "pie", "doughnut", "scatter"}:
            raise TaskError("Supported native chart types: column, bar, line, area, pie, doughnut, scatter")
        series = value.get("series", [])
        categories = value.get("categories", [])
        if not isinstance(series, list) or not 1 <= len(series) <= 50 or not isinstance(categories, list) or len(categories) > 2000:
            raise TaskError("Charts require 1–50 series and at most 2000 categories", "resource_limit")
        point_count = 0
        for item in series:
            if not isinstance(item, dict):
                raise TaskError("Each chart series must be a JSON object")
            lengths = [len(categories)]
            for field in ("values", "x", "y", "xs", "ys", "points"):
                values = item.get(field, [])
                if not isinstance(values, list) or len(values) > 2000:
                    raise TaskError("Chart series data must be lists with at most 2000 points", "resource_limit")
                lengths.append(len(values))
            point_count += max(lengths)
        if point_count > 10000:
            raise TaskError("A chart must not exceed 10000 data points", "resource_limit")
        expanded_cost += point_count * 1024 + len(series) * 8192
    if kind == "table":
        rows = value.get("rows", [])
        columns = value.get("columns", [])
        if not isinstance(rows, list) or not isinstance(columns, list) or any(not isinstance(row, list) for row in rows):
            raise TaskError("Native table columns and rows must be lists of cells")
        row_count = len(rows) + bool(columns)
        column_count = max([len(columns)] + [len(row) for row in rows])
        cell_count = row_count * column_count
        if not 1 <= row_count <= 100 or not 1 <= column_count <= 50 or cell_count > 5000:
            raise TaskError("A table must fit within 100 rows, 50 columns and 5000 resolved cells", "resource_limit")
        repeated = {key: value.get(key) for key in ("style", "defaults", "cell_styles")}
        repeated_size = len(json.dumps(repeated, ensure_ascii=False).encode("utf-8"))
        expanded_cost += cell_count * (2048 + repeated_size)
    if kind == "formula":
        style = {key: value.get(key) for key in ("font_size", "color", "language")}
        style_bytes = len(json.dumps(style, ensure_ascii=False).encode("utf-8"))
        expanded_cost += _formula_expansion_cost(value.get("latex"), style_bytes=style_bytes)
    return expanded_cost


def prepare_svg(data: bytes, *, width: float, height: float, native_objects: bool, max_expanded_bytes: int) -> tuple[str, int]:
    """Validate the supported SVG subset and embed local images in memory."""
    if len(data) > MAX_SVG_BYTES:
        raise TaskError("An SVG input exceeds 8 MiB", "resource_limit")
    root = safe_xml(data, "SVG input")
    if root.tag != f"{{{SVG_NS}}}svg":
        raise TaskError("The SVG root must use the standard SVG namespace")
    from svg_to_pptx.canvas_contract import parse_project_svg_root

    viewbox = parse_project_svg_root(root, context="SVG input")
    if viewbox.pixel_dimensions != (width, height):
        raise TaskError("Every SVG viewBox must be 0 0 width height matching the deck canvas")
    check_expansion_budget(root, max_bytes=max_expanded_bytes)
    expanded_bytes = len(data)
    native_cost = 0
    seen_ids = set()
    parent_map = {child: parent for parent in root.iter() for child in parent}
    paint_tags = {f"{{{SVG_NS}}}{name}" for name in ("linearGradient", "radialGradient", "stop")}
    paint_bytes = sum(
        len(name.encode("utf-8")) + len(value.encode("utf-8"))
        for node in root.iter() if node.tag in paint_tags for name, value in node.attrib.items()
    )
    native_reference_ids = set()
    for marked in root.iter():
        if marked.get("data-pptx-replace-with") is not None or marked.get("data-pptx-inline-formula") is not None:
            ancestor = marked
            while ancestor is not None:
                if ancestor.get("id"):
                    native_reference_ids.add(ancestor.get("id"))
                ancestor = parent_map.get(ancestor)
    for element in root.iter():
        tag = element.tag.removeprefix(f"{{{SVG_NS}}}")
        if tag not in SVG_TAGS:
            raise TaskError(f"Unsupported SVG element: {tag}")
        identifier = element.get("id")
        if identifier:
            if identifier in seen_ids:
                raise TaskError("SVG element IDs must be unique within a slide")
            seen_ids.add(identifier)
        if "class" in element.attrib:
            raise TaskError("CSS classes are unsupported; author inline SVG presentation attributes")
        kind = element.get("data-pptx-replace-with")
        if kind is not None and kind not in {"chart", "table", "formula"}:
            raise TaskError("Unsupported native replacement kind")
        if kind is not None:
            ancestor = parent_map.get(element)
            while ancestor is not None:
                if ancestor.get("data-pptx-replace-with") is not None:
                    raise TaskError("Native replacement objects must not be nested")
                ancestor = parent_map.get(ancestor)
        if kind in {"chart", "table"} and not native_objects:
            raise TaskError("Native chart/table markers require native_objects=true")
        if kind in {"chart", "table"} and element.get("data-pptx-native-authority") != "json":
            raise TaskError("Native charts and tables require explicit data-pptx-native-authority=json")
        carriers = int("data-pptx-json" in element.attrib) + int("data-pptx-data" in element.attrib)
        carriers += sum(child.tag == f"{{{SVG_NS}}}metadata" for child in element)
        if kind is not None and carriers != 1:
            raise TaskError("Each native object must have exactly one JSON payload carrier")
        if kind is None and any(name in element.attrib for name in ("data-pptx-json", "data-pptx-data")):
            raise TaskError("Native JSON payload attributes require a native replacement marker")
        for attribute, value in list(element.attrib.items()):
            if attribute.startswith("{") and attribute not in {f"{{{XLINK_NS}}}href", f"{{{XML_NS}}}space", f"{{{XML_NS}}}lang"}:
                raise TaskError("Unsupported namespaced SVG attribute")
            if attribute.lower().startswith("on") or attribute in {"data-icon", "xml:base"}:
                raise TaskError("Scripts, external icon stores and XML base overrides are unsupported")
            if attribute.startswith("data-") and attribute not in NATIVE_ATTRIBUTES:
                raise TaskError(f"Unsupported SVG metadata attribute: {attribute}")
            if not attribute.startswith(("{", "data-")) and attribute not in SVG_ATTRIBUTES:
                raise TaskError(f"Unsupported SVG attribute: {attribute}")
            if attribute in {"filter", "mask"}:
                raise TaskError("SVG filter and mask effects are outside this skill's supported subset")
            if attribute == "style" and ("@" in value or "\\" in value or re.search(r"(?:filter|mask|animation)\s*:", value, re.IGNORECASE)):
                raise TaskError("Unsupported inline CSS effect or escaped declaration")
            if attribute == "style":
                for declaration in value.split(";"):
                    if not declaration.strip():
                        continue
                    property_name, separator, property_value = declaration.partition(":")
                    if not separator or property_name.strip() not in PRESENTATION_ATTRIBUTES or not property_value.strip():
                        raise TaskError("Inline CSS contains an unsupported or malformed presentation property")
                    if "!important" in property_value.lower():
                        raise TaskError("CSS priority declarations are unsupported")
            if attribute in {"fill", "stroke", "clip-path", "style", "marker-start", "marker-mid", "marker-end"} and URL_START_RE.search(value):
                remainder = LOCAL_URL_RE.sub("", value)
                if URL_START_RE.search(remainder):
                    raise TaskError("SVG paint and geometry references must be local fragment URLs")
            if attribute in {"data-pptx-json", "data-pptx-data"}:
                native_cost += _check_native_payload(strict_json(value), kind)
            if attribute == "data-pptx-inline-formula":
                style_bytes = _inline_formula_style_bytes(element, parent_map, paint_bytes)
                native_cost += _formula_expansion_cost(value, inline=True, style_bytes=style_bytes)
        if tag == "metadata":
            parent = parent_map.get(element)
            parent_kind = parent.get("data-pptx-replace-with") if parent is not None else None
            if parent_kind not in {"chart", "table", "formula"} or element.get("type") != "application/json" or list(element):
                raise TaskError("Only JSON metadata inside a native chart/table/formula marker is supported")
            native_cost += _check_native_payload(strict_json(element.text or ""), parent_kind)
        if native_cost >= max_expanded_bytes:
            raise TaskError("Native objects exceed the remaining deck expansion budget", "resource_limit")
        href_values = [element.get(name) for name in ("href", f"{{{XLINK_NS}}}href") if element.get(name) is not None]
        if len(set(href_values)) > 1:
            raise TaskError("href and xlink:href must agree")
        href = href_values[0] if href_values else None
        if tag == "image":
            if not href:
                raise TaskError("An image requires an href")
            element.attrib.pop(f"{{{XLINK_NS}}}href", None)
            uri = _image_uri(href, max_uri_bytes=max_expanded_bytes - expanded_bytes + len(href.encode("utf-8")))
            expanded_bytes += max(0, len(uri) - len(href.encode("utf-8")))
            element.set("href", uri)
        elif href is not None:
            if tag in {"use", "linearGradient", "radialGradient"}:
                if not re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_.:-]*", href):
                    raise TaskError("SVG reuse requires a same-document fragment reference")
                if tag == "use" and href[1:] in native_reference_ids:
                    raise TaskError("SVG use cannot clone native chart/table/formula metadata")
            elif tag != "a":
                raise TaskError("An href is not supported on this SVG element")
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    _nodes, expansion_cost = check_expansion_budget(root, max_bytes=max_expanded_bytes - native_cost)
    result = ET.tostring(root, encoding="unicode")
    cost = max(len(result.encode("utf-8")), expansion_cost) + native_cost
    if cost > max_expanded_bytes:
        raise TaskError("Expanded SVG exceeds the remaining deck byte budget", "resource_limit")
    return result, cost


def load_deck(config: str) -> Deck:
    if config == "-":
        data = sys.stdin.buffer.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            raise TaskError("Configuration exceeds 32 MiB", "resource_limit")
    else:
        data = read_limited(task_path(config), MAX_CONFIG_BYTES)
    payload = strict_json(data)
    if not isinstance(payload, dict):
        raise TaskError("The deck configuration must be a JSON object")
    _unknown_fields(payload, JSON_FIELDS, "Deck configuration")
    if payload.get("schema") != INPUT_SCHEMA:
        raise TaskError(f"Deck schema must equal {INPUT_SCHEMA}")
    title = _text(payload.get("title", "Presentation"), "Title", 512)
    width = _dimension(payload.get("width", 1280), "width")
    height = _dimension(payload.get("height", 720), "height")
    text_flow = payload.get("text_flow", "preserve")
    if not isinstance(text_flow, str) or text_flow not in {"preserve", "reflow", "split"}:
        raise TaskError("text_flow must be preserve, reflow or split")
    native_objects = payload.get("native_objects", True)
    if not isinstance(native_objects, bool):
        raise TaskError("native_objects must be a boolean")
    slides = payload.get("slides")
    if not isinstance(slides, list) or not 1 <= len(slides) <= MAX_SLIDES:
        raise TaskError("A deck must contain 1 to 200 slides")
    prepared = []
    expanded_bytes = 0
    for index, slide in enumerate(slides, 1):
        if not isinstance(slide, dict):
            raise TaskError(f"Slide {index} must be a JSON object")
        _unknown_fields(slide, SLIDE_FIELDS, f"Slide {index}")
        if ("svg" in slide) == ("svg_path" in slide):
            raise TaskError(f"Slide {index} requires exactly one of svg and svg_path")
        notes = _text(slide.get("notes", ""), f"Slide {index} notes", 200000)
        if "svg_path" in slide:
            svg_bytes = read_limited(task_path(slide["svg_path"]), MAX_SVG_BYTES)
        else:
            svg_bytes = _text(slide["svg"], f"Slide {index} SVG", MAX_SVG_BYTES).encode("utf-8")
        svg, expansion_cost = prepare_svg(
            svg_bytes, width=width, height=height, native_objects=native_objects,
            max_expanded_bytes=MAX_EXPANDED_SVG_BYTES - expanded_bytes,
        )
        expanded_bytes += expansion_cost
        if expanded_bytes > MAX_EXPANDED_SVG_BYTES:
            raise TaskError("Expanded SVG inputs exceed 128 MiB", "resource_limit")
        prepared.append(PreparedSlide(svg, notes))
    return Deck(title, width, height, text_flow, native_objects, tuple(prepared))


def render_deck(deck: Deck) -> bytes:
    from pptx_ooxml.package import PackagePartRegistry
    from svg_to_pptx.drawingml.converter import convert_svg_to_slide_shapes

    root = task_directory()
    slides = []
    package_parts = PackagePartRegistry()
    # Preserve diagnostics without contaminating the machine-readable stdout.
    with contextlib.redirect_stdout(sys.stderr):
        for index, source in enumerate(deck.slides, 1):
            converted = convert_svg_to_slide_shapes(
                root / f"slide-{index}.svg", resource_root=root, svg_text=source.svg,
                slide_num=index, slide_count=len(deck.slides), image_optimize=False,
                native_objects=deck.native_objects, text_flow=deck.text_flow,
                package_parts=package_parts,
            )
            slides.append(ConvertedSlide.from_conversion(converted, notes=source.notes))
    return build_presentation(slides, width=deck.width, height=deck.height, title=deck.title)


def publish_exclusive(path: Path, data: bytes) -> tuple[int, int]:
    """Publish only validated bytes, preserving all existing task files."""
    descriptor = None
    owned_identity = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with _parent_descriptor(path) as (parent, name):
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent)
            info = os.fstat(descriptor)
            owned_identity = (info.st_dev, info.st_ino)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if _read_at(parent, name, MAX_PPTX_BYTES) != data:
                raise TaskError("Written output did not match the validated package", "write_failed")
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            if owned_identity is not None:
                try:
                    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == owned_identity:
                        os.unlink(name, dir_fd=parent)
                except OSError:
                    pass
            raise
    return owned_identity


def create(config: str, output: str) -> dict:
    # Check the original string before Path can normalize away './'.
    if (not isinstance(output, str) or not output or output in (".", "..")
            or any(character in output for character in ("/", "\\", ":", "\x00"))):
        raise TaskError("Output must be a bare filename in the confirmed task directory", "task_boundary")
    destination = task_path(output, must_exist=False)
    if destination.suffix.lower() != ".pptx":
        raise TaskError("The output filename must end in .pptx")
    if destination.exists():
        raise TaskError("Output already exists; choose a new filename", "output_exists")
    deck = load_deck(config)
    data = render_deck(deck)
    summary = validate_presentation(data)
    publish_exclusive(destination, data)
    from archive_delivery import archive_delivery
    delivery = archive_delivery([destination.relative_to(task_directory()).as_posix()], [] if config == "-" else [config])
    return {
        "output": destination.relative_to(task_directory()).as_posix(), "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), **summary,
        **delivery, "temporary_files": len(delivery.get("temporary_residuals", [])),
    }


def read_presentation(value: str, *, inspect: bool = False, allow_png_zero_padding: bool = False,
                      inspection_options: dict | None = None) -> dict:
    data = read_limited(task_path(value), MAX_PPTX_BYTES)
    result = inspect_presentation(data, allow_png_zero_padding=allow_png_zero_padding) if inspect else validate_presentation(data)
    result = {"input": value, "sha256": hashlib.sha256(data).hexdigest(), **result}
    if inspection_options is not None:
        from .inspection import bounded_result
        result = bounded_result(result, data, allow_png_zero_padding=allow_png_zero_padding, **inspection_options)
    return result


def environment() -> dict:
    if sys.version_info < (3, 10):
        raise TaskError("Python 3.10 or newer is required", "environment_failed")
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise TaskError("Run the interpreter with all flags: -I -S -B", "environment_failed")
    if pyexpat.version_info < (2, 6, 0):
        raise TaskError("Expat 2.6.0 or newer is required for bounded XML parsing", "environment_failed")
    if not all(hasattr(os, option) for option in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")) or not {os.open, os.stat, os.unlink}.issubset(os.supports_dir_fd):
        raise TaskError("This release requires POSIX descriptor-relative file operations (Linux supported)", "environment_failed")
    task_directory()
    return {
        "python": platform.python_version(), "platform": platform.system(), "expat": pyexpat.EXPAT_VERSION,
        "isolated": True, "site_packages": False, "bytecode_writes": False, "descriptor_relative_io": True,
        "third_party_python_dependencies": [], "external_programs": [],
    }


def check(*, write_probe: bool = False) -> dict:
    details = environment()
    svg = f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"><text x="80" y="120" font-size="32">PPTX 自检</text></svg>'
    data = render_deck(Deck("Environment check", 1280, 720, "preserve", True, (PreparedSlide(svg, ""),)))
    summary = validate_presentation(data)
    if write_probe:
        path = task_directory() / f"pptx-probe-{uuid.uuid4().hex}.tmp"
        created_identity = None
        try:
            created_identity = publish_exclusive(path, b"pptx-write-probe\n")
        finally:
            if created_identity is not None:
                with _parent_descriptor(path) as (parent, name):
                    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == created_identity:
                        os.unlink(name, dir_fd=parent)
    return {
        "status": "environment-ok", "check_kind": "environment", "artifact_validation": "not_run", **details,
        "in_memory_export": "passed", "write_probe": "passed" if write_probe else "not_run", "self_test": summary,
    }
