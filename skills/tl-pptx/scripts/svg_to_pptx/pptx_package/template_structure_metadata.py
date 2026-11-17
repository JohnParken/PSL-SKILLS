"""
PPT Master - Template Structure Metadata

Parse and validate explicit SVG metadata consumed by structured PPTX export.

Usage:
    Imported by svg_to_pptx.pptx_package.builder and svg_quality_checker.py.

Examples:
    parse_template_slides([Path("projects/demo/svg_output/01_cover.svg")])

Dependencies:
    None (only uses standard library)
"""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import base64
import binascii
import hashlib
import json
import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from native_payloads import (
    hydrate_native_payload_refs,
)
from pptx_to_svg.preset_authoring import (
    authored_preset_encoding,
    validate_authored_preset_group,
)
from ..drawingml.utils import (
    parse_inline_style,
    parse_opacity,
    parse_project_geometry_length,
)
from ..canvas_contract import (
    parse_project_viewbox,
)

from .template_structure import (
    TemplateElementSpec,
    TemplatePlaceholderBinding,
    TemplateSlideSpec,
)

def is_proxy_placeholder(item: TemplateElementSpec) -> bool:
    """Return whether a visible composite slot uses an invisible binding proxy."""
    return item.placeholder_binding == "proxy"


def template_placeholder_bindings(
    spec: TemplateSlideSpec,
) -> tuple[TemplatePlaceholderBinding, ...]:
    """Assign deterministic, collision-free PowerPoint placeholder identities."""
    from .template_structure import (
        OOXML_UINT32_MAX,
        TEMPLATE_PLACEHOLDER_TYPES,
        TemplatePlaceholderBinding,
        TemplateStructureError,
    )
    next_idx = 1
    used_indices: dict[int, str] = {}
    bindings: list[TemplatePlaceholderBinding] = []
    for item in spec.placeholders:
        placeholder_type = TEMPLATE_PLACEHOLDER_TYPES.get(item.placeholder or "")
        if placeholder_type is None:
            raise TemplateStructureError(
                f"{spec.svg_path.name}: unsupported placeholder type "
                f"{item.placeholder!r}"
            )
        if item.placeholder == "title" and item.placeholder_idx is None:
            assigned_idx = None
        else:
            assigned_idx = (
                item.placeholder_idx
                if item.placeholder_idx is not None
                else next_idx
            )
        effective_idx = assigned_idx if assigned_idx is not None else 0
        if effective_idx > OOXML_UINT32_MAX:
            raise TemplateStructureError(
                f"{spec.svg_path.name}: layout {spec.layout_key!r} placeholder "
                f"{item.element_id!r} idx exceeds the OOXML UInt32 maximum "
                f"{OOXML_UINT32_MAX}"
            )
        previous = used_indices.get(effective_idx)
        if previous is not None:
            raise TemplateStructureError(
                f"{spec.svg_path.name}: layout {spec.layout_key!r} gives "
                f"placeholders {previous!r} and {item.element_id!r} the same "
                f"effective idx {effective_idx}; omitted idx defaults to 0 in OOXML"
            )
        used_indices[effective_idx] = item.element_id
        if assigned_idx is not None:
            next_idx = max(next_idx, assigned_idx + 1)
        bindings.append(TemplatePlaceholderBinding(
            element=item,
            placeholder_type=placeholder_type,
            assigned_idx=assigned_idx,
        ))
    return tuple(bindings)


def _local_tag(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1] if isinstance(elem.tag, str) else ""


def _parse_svg_root(svg_path: Path) -> ET.Element:
    """Parse one SVG and hydrate compact native metadata in memory."""
    root = ET.parse(svg_path).getroot()
    hydrate_native_payload_refs(root, svg_path)
    return root


def _is_authored_preset_atom(elem: ET.Element) -> bool:
    """Return whether one group is a valid compact authored-shape atom."""
    return (
        authored_preset_encoding(elem) == "compact"
        and not validate_authored_preset_group(elem)
    )


def _svg_canvas(root: ET.Element) -> tuple[float, float, float, float]:
    viewbox = parse_project_viewbox(root.get("viewBox"))
    return 0.0, 0.0, float(viewbox.width), float(viewbox.height)


def _is_full_canvas_solid_rect(
    elem: ET.Element,
    canvas: tuple[float, float, float, float],
    *,
    root: ET.Element | None = None,
) -> bool:
    """Return whether a direct rect is eligible for scoped p:bg compilation."""
    if canvas[2] <= 0 or canvas[3] <= 0:
        return False
    if _local_tag(elem) != "rect":
        return False
    if any(elem.get(attr) for attr in ("transform", "filter", "clip-path")):
        return False
    style = parse_inline_style(elem.get("style"))
    root_style = parse_inline_style(root.get("style")) if root is not None else {}

    def root_value(name: str) -> str | None:
        return root_style.get(name, root.get(name)) if root is not None else None

    try:
        fill_opacity = style.get("fill-opacity", elem.get("fill-opacity"))
        if fill_opacity in {None, "inherit"}:
            fill_opacity = root_value("fill-opacity")
        opacity = style.get("opacity", elem.get("opacity"))
        if opacity == "inherit":
            opacity = root_value("opacity")
        if any(parse_opacity(value, allow_percentage=True) < 1
               for value in (fill_opacity, opacity, root_value("opacity"))):
            return False
    except ValueError:
        return False
    try:
        geometry = (
            parse_project_geometry_length(elem.get("x", "0"), "x"),
            parse_project_geometry_length(elem.get("y", "0"), "y"),
            parse_project_geometry_length(elem.get("width", "0"), "width"),
            parse_project_geometry_length(elem.get("height", "0"), "height"),
        )
        corner_radius = (
            parse_project_geometry_length(elem.get("rx", "0"), "rx"),
            parse_project_geometry_length(elem.get("ry", "0"), "ry"),
        )
    except ValueError:
        return False
    if not all(math.isfinite(value) for value in (*geometry, *corner_radius)):
        return False
    if corner_radius != (0.0, 0.0):
        return False
    if any(abs(actual - expected) > 0.5 for actual, expected in zip(geometry, canvas)):
        return False
    fill = (elem.get("fill") or "").strip().lower()
    if not fill or fill == "none" or fill.startswith("url("):
        return False
    stroke = (elem.get("stroke") or "none").strip().lower()
    if stroke != "none":
        try:
            if float(elem.get("stroke-opacity", "1")) != 0:
                return False
        except ValueError:
            return False
    return True


def _portable_project_file(
    project_path: Path,
    raw_value: str,
    field_name: str,
    suffix: str,
) -> Path:
    """Resolve a project-relative structure file without allowing escape."""
    from .template_structure import (
        TemplateStructureError,
    )
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    if not value:
        raise TemplateStructureError(
            f"spec_lock.md pptx_structure.{field_name} cannot be empty"
        )
    candidate = Path(value)
    if candidate.is_absolute():
        raise TemplateStructureError(
            f"spec_lock.md pptx_structure.{field_name} must be project-relative"
        )
    root = project_path.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise TemplateStructureError(
            f"spec_lock.md pptx_structure.{field_name} escapes the project directory"
        ) from exc
    if resolved.suffix.lower() != suffix:
        raise TemplateStructureError(
            f"spec_lock.md pptx_structure.{field_name} must reference a {suffix} file"
        )
    if not resolved.is_file():
        raise TemplateStructureError(
            f"spec_lock.md pptx_structure.{field_name} does not exist: {candidate}"
        )
    return resolved


def _template_replication_mode(template_dir: Path) -> str | None:
    """Read the optional replication mode from template design frontmatter."""
    spec_path = template_dir / "design_spec.md"
    try:
        lines = spec_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return None
        match = re.fullmatch(
            r"replication_mode\s*:\s*[\"']?(standard|fidelity|mirror)[\"']?",
            stripped,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).lower()
    return None


def load_template_source_themes(
    template_dir: Path,
) -> dict[str, bytes] | None:
    """Load exact per-Master themes carried by a Type A mirror workspace."""
    from .template_structure import (
        SOURCE_THEMES_FILENAME,
        SOURCE_THEMES_SCHEMA,
        TemplateStructureError,
        _DML_NS,
        _MASTER_KEY_RE,
        _REL_NS,
    )
    path = template_dir / SOURCE_THEMES_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TemplateStructureError(
            f"Cannot read template source themes {path}: {exc}"
        ) from exc
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or schema != SOURCE_THEMES_SCHEMA:
        raise TemplateStructureError(
            f"Unsupported template source theme schema: {schema!r}"
        )
    masters = payload.get("masters")
    if not isinstance(masters, dict) or not masters:
        raise TemplateStructureError(
            f"{SOURCE_THEMES_FILENAME} requires a non-empty masters object"
        )

    themes: dict[str, bytes] = {}
    for master_key, record in masters.items():
        if (
            not isinstance(master_key, str)
            or not _MASTER_KEY_RE.fullmatch(master_key)
        ):
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} has invalid Master key {master_key!r}"
            )
        if not isinstance(record, dict):
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} Master {master_key!r} must be an object"
            )
        if record.get("encoding") != "base64":
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} Master {master_key!r} must use base64"
            )
        encoded = record.get("payload")
        expected_sha256 = record.get("sha256")
        if (
            not isinstance(encoded, str)
            or not isinstance(expected_sha256, str)
        ):
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} Master {master_key!r} lacks payload/hash"
            )
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} Master {master_key!r} has invalid base64"
            ) from exc
        if base64.b64encode(raw).decode("ascii") != encoded:
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} Master {master_key!r} base64 is not canonical"
            )
        if hashlib.sha256(raw).hexdigest() != expected_sha256.strip().lower():
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} Master {master_key!r} hash differs"
            )
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} Master {master_key!r} theme is malformed"
            ) from exc
        if root.tag != f"{{{_DML_NS}}}theme":
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} Master {master_key!r} is not a:theme"
            )
        if any(
            isinstance(name, str) and name.startswith(f"{{{_REL_NS}}}")
            for node in root.iter()
            for name in node.attrib
        ):
            raise TemplateStructureError(
                f"{SOURCE_THEMES_FILENAME} Master {master_key!r} has relationships"
            )
        themes[master_key] = raw
    return themes


def _template_svg_path(
    template_dir: Path,
    raw_basename: str,
    context: str,
) -> tuple[str, Path]:
    """Resolve one flat template SVG basename inside the project workspace."""
    from .template_structure import (
        TemplateStructureError,
    )
    basename = (
        raw_basename[:-4]
        if raw_basename.lower().endswith(".svg")
        else raw_basename
    )
    if (
        not basename
        or basename in {".", ".."}
        or "/" in basename
        or "\\" in basename
        or any(ord(char) < 0x20 for char in basename)
    ):
        raise TemplateStructureError(
            f"spec_lock.md {context} has invalid template SVG basename "
            f"{raw_basename!r}"
        )
    svg_path = (template_dir / f"{basename}.svg").resolve()
    if svg_path.parent != template_dir or not svg_path.is_file():
        raise TemplateStructureError(
            f"spec_lock.md {context} references missing template SVG "
            f"templates/{basename}.svg"
        )
    return basename, svg_path
