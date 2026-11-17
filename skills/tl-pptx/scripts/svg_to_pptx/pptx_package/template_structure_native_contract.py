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
import hashlib
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from ..drawingml.utils import (
    is_picture_effect_carrier,
)
from ..native_objects import (
    NativeMarkerAttributeError,
    native_replacement_kind,
)

from .template_structure import (
    NativeLayoutSpec,
    NativePlaceholderSpec,
    NativeSlideSpec,
    NativeStructureContract,
    PptxStructureLock,
)

def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _native_geometry(raw: Any, context: str) -> tuple[float, float, float, float] | None:
    from .template_structure import (
        TemplateStructureError,
    )
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TemplateStructureError(f"{context} geometry must be an object or null")
    try:
        values = tuple(float(raw[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError) as exc:
        raise TemplateStructureError(f"{context} geometry is invalid") from exc
    if not all(math.isfinite(value) for value in values) or values[2] < 0 or values[3] < 0:
        raise TemplateStructureError(f"{context} geometry must be finite and non-negative")
    if values[2] == 0 or values[3] == 0:
        # PowerPoint writes <a:ext cx="0" cy="0"/> for a collapsed placeholder
        # (built-in vertical-text layouts do this); it carries no usable box.
        return None
    return values


def load_native_structure_contract(
    structure_lock: PptxStructureLock,
) -> NativeStructureContract:
    """Load and verify the native structure bundle selected by preserve mode."""
    from .template_structure import (
        NATIVE_STRUCTURE_SCHEMA,
        NativeLayoutSpec,
        NativePlaceholderSpec,
        NativeSlideSpec,
        NativeStructureContract,
        TemplateStructureError,
        _LAYOUT_KEY_RE,
    )
    if structure_lock.mode != "preserve":
        raise TemplateStructureError(
            "native structure contracts are available only in preserve mode"
        )
    source_template = structure_lock.source_template
    contract_path = structure_lock.native_structure
    if source_template is None or contract_path is None:
        raise TemplateStructureError(
            "preserve mode is missing source_template or native_structure"
        )
    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateStructureError(
            f"Cannot read native structure contract {contract_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict) or raw.get("schema") != NATIVE_STRUCTURE_SCHEMA:
        raise TemplateStructureError(
            f"{contract_path.name} must use schema {NATIVE_STRUCTURE_SCHEMA!r}"
        )

    source = raw.get("source")
    expected_sha = source.get("sha256") if isinstance(source, dict) else None
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise TemplateStructureError(
            f"{contract_path.name} source.sha256 must be a lowercase SHA-256 digest"
        )
    actual_sha = _file_sha256(source_template)
    if actual_sha != expected_sha:
        raise TemplateStructureError(
            f"{source_template.name} does not match {contract_path.name} source.sha256"
        )

    slide_size = raw.get("slideSize")
    try:
        slide_size_emu = (
            int(slide_size["width_emu"]),
            int(slide_size["height_emu"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TemplateStructureError(
            f"{contract_path.name} slideSize must contain width_emu/height_emu"
        ) from exc
    if slide_size_emu[0] <= 0 or slide_size_emu[1] <= 0:
        raise TemplateStructureError(
            f"{contract_path.name} slideSize values must be positive"
        )

    raw_layouts = raw.get("layouts")
    if not isinstance(raw_layouts, list) or not raw_layouts:
        raise TemplateStructureError(
            f"{contract_path.name} must contain at least one layout"
        )
    layouts: list[NativeLayoutSpec] = []
    seen_keys: set[str] = set()
    seen_parts: set[str] = set()
    for index, item in enumerate(raw_layouts):
        context = f"{contract_path.name} layouts[{index}]"
        if not isinstance(item, dict):
            raise TemplateStructureError(f"{context} must be an object")
        key = str(item.get("key") or "")
        name = str(item.get("name") or "").strip()
        package_part = str(item.get("packagePart") or "")
        master_key = str(item.get("masterKey") or "")
        if not _LAYOUT_KEY_RE.fullmatch(key):
            raise TemplateStructureError(f"{context} has invalid key {key!r}")
        if key in seen_keys:
            raise TemplateStructureError(f"{context} repeats layout key {key!r}")
        if not name:
            raise TemplateStructureError(f"{context} name cannot be empty")
        if (
            not package_part.startswith("ppt/slideLayouts/")
            or ".." in Path(package_part).parts
            or not package_part.endswith(".xml")
        ):
            raise TemplateStructureError(
                f"{context} packagePart must be a ppt/slideLayouts/*.xml part"
            )
        if package_part in seen_parts:
            raise TemplateStructureError(
                f"{context} repeats package part {package_part!r}"
            )
        if not master_key:
            raise TemplateStructureError(f"{context} masterKey cannot be empty")

        raw_placeholders = item.get("placeholders", [])
        if not isinstance(raw_placeholders, list):
            raise TemplateStructureError(f"{context} placeholders must be a list")
        placeholders: list[NativePlaceholderSpec] = []
        for ph_index, placeholder in enumerate(raw_placeholders):
            ph_context = f"{context} placeholders[{ph_index}]"
            if not isinstance(placeholder, dict):
                raise TemplateStructureError(f"{ph_context} must be an object")
            semantic_role = str(placeholder.get("semanticRole") or "other")
            placeholder_type = str(placeholder.get("type") or "obj")
            raw_idx = placeholder.get("idx")
            try:
                placeholder_idx = int(raw_idx) if raw_idx is not None else None
            except (TypeError, ValueError) as exc:
                raise TemplateStructureError(
                    f"{ph_context} idx must be an integer or null"
                ) from exc
            if placeholder_idx is not None and placeholder_idx < 0:
                raise TemplateStructureError(f"{ph_context} idx cannot be negative")
            placeholders.append(NativePlaceholderSpec(
                semantic_role=semantic_role,
                placeholder_type=placeholder_type,
                idx=placeholder_idx,
                geometry=_native_geometry(placeholder.get("geometry"), ph_context),
            ))
        layouts.append(NativeLayoutSpec(
            key=key,
            name=name,
            package_part=package_part,
            master_key=master_key,
            placeholders=tuple(placeholders),
        ))
        seen_keys.add(key)
        seen_parts.add(package_part)

    raw_slides = raw.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise TemplateStructureError(
            f"{contract_path.name} must contain at least one source slide"
        )
    slides: list[NativeSlideSpec] = []
    seen_slide_indices: set[int] = set()
    slide_parts: set[str] = set()
    for position, item in enumerate(raw_slides, start=1):
        context = f"{contract_path.name} slides[{position}]"
        if not isinstance(item, dict):
            raise TemplateStructureError(f"{context} must be an object")
        try:
            slide_index = int(item.get("index"))
        except (TypeError, ValueError) as exc:
            raise TemplateStructureError(f"{context} index must be an integer") from exc
        if slide_index <= 0 or slide_index in seen_slide_indices:
            raise TemplateStructureError(
                f"{context} index must be unique and positive"
            )
        layout_key = str(item.get("layoutKey") or "")
        if layout_key not in seen_keys:
            raise TemplateStructureError(
                f"{context} references unknown layout key {layout_key!r}"
            )
        raw_package_part = item.get("packagePart")
        if not isinstance(raw_package_part, str) or not raw_package_part:
            raise TemplateStructureError(
                f"{context} packagePart must be a non-empty string"
            )
        package_part = raw_package_part
        if (
            not package_part.startswith("ppt/slides/")
            or ".." in Path(package_part).parts
            or not package_part.endswith(".xml")
            or package_part in slide_parts
        ):
            raise TemplateStructureError(
                f"{context} packagePart must be a unique ppt/slides/*.xml part"
            )
        slides.append(NativeSlideSpec(
            index=slide_index,
            package_part=package_part,
            layout_key=layout_key,
        ))
        seen_slide_indices.add(slide_index)
        slide_parts.add(package_part)
    expected_indices = set(range(1, len(slides) + 1))
    if seen_slide_indices != expected_indices:
        raise TemplateStructureError(
            f"{contract_path.name} source slide indices must be contiguous from 1"
        )

    try:
        with zipfile.ZipFile(source_template, "r") as package:
            package_parts = set(package.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise TemplateStructureError(
            f"Cannot open preserved source template {source_template}: {exc}"
        ) from exc
    missing_parts = sorted((seen_parts | slide_parts) - package_parts)
    if missing_parts:
        raise TemplateStructureError(
            f"{source_template.name} is missing layout part(s): " + ", ".join(missing_parts)
        )

    return NativeStructureContract(
        source_template=source_template,
        contract_path=contract_path,
        source_sha256=expected_sha,
        slide_size_emu=slide_size_emu,
        layouts=tuple(layouts),
        slides=tuple(sorted(slides, key=lambda item: item.index)),
    )


def _parse_placeholder_bounds(
    raw: str | None,
    *,
    svg_path: Path,
    element_id: str,
) -> tuple[float, float, float, float] | None:
    from .template_structure import (
        TemplateStructureError,
    )
    if raw is None:
        return None
    parts = [part for part in re.split(r"[\s,]+", raw.strip()) if part]
    if len(parts) != 4:
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} data-pptx-bounds must be "
            "'x y width height'"
        )
    try:
        x, y, width, height = (float(part) for part in parts)
    except ValueError as exc:
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} placeholder bounds must be numeric"
        ) from exc
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} placeholder bounds must be finite"
        )
    if width <= 0 or height <= 0:
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} placeholder width/height must be positive"
        )
    return x, y, width, height


def _parse_placeholder_idx(
    raw: str | None,
    *,
    svg_path: Path,
    element_id: str,
) -> int | None:
    from .template_structure import (
        OOXML_UINT32_MAX,
        TemplateStructureError,
    )
    if raw is None:
        return None
    value = raw.strip()
    if not value or not value.isdigit():
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} data-pptx-idx must be "
            "a non-negative integer"
        )
    parsed = int(value)
    if parsed > OOXML_UINT32_MAX:
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} data-pptx-idx must be "
            f"at most {OOXML_UINT32_MAX}"
        )
    return parsed


def _validate_placeholder_carrier(
    carrier: ET.Element,
    placeholder: str,
    *,
    svg_path: Path,
    element_id: str,
) -> None:
    from .template_structure import (
        TemplateStructureError,
        _OBJECT_PLACEHOLDER_TAGS,
        _TEXT_PLACEHOLDERS,
        _is_authored_preset_atom,
        _local_tag,
    )
    tag = _local_tag(carrier)
    if placeholder in _TEXT_PLACEHOLDERS and tag != "text":
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} placeholder '{placeholder}' must be "
            "carried by one direct <text> child"
        )
    picture_carrier = (
        tag in {"image", "svg"}
        or (tag == "g" and is_picture_effect_carrier(carrier))
    )
    if placeholder == "picture" and not picture_carrier:
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} picture placeholder must be declared "
            "with one direct <image>, crop <svg>, or exact clipped-picture "
            "effect carrier"
        )
    if placeholder == "media" and not picture_carrier:
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} media placeholder must be declared "
            "with one direct <image>, crop <svg>, or exact clipped-picture "
            "effect carrier"
        )
    if (
        placeholder == "object"
        and tag not in _OBJECT_PLACEHOLDER_TAGS
        and not _is_authored_preset_atom(carrier)
    ):
        raise TemplateStructureError(
            f"{svg_path.name}: {element_id} object placeholder carrier must be "
            "one direct text, image, basic SVG shape, or authored preset atom"
        )
    if placeholder in {"chart", "table"}:
        try:
            native_kind = native_replacement_kind(carrier)
        except NativeMarkerAttributeError as exc:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id} placeholder '{placeholder}' has "
                f"conflicting chart/table replacement metadata: {exc}"
            ) from exc
        if tag != "g" or native_kind != placeholder:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id} placeholder '{placeholder}' must be "
                f"carried by one direct <g data-pptx-replace-with=\"{placeholder}\"> "
                "marker"
            )


def _structure_attrs(elem: ET.Element) -> list[str]:
    from .template_structure import (
        _STRUCTURE_ATTRS,
    )
    return sorted(attr for attr in _STRUCTURE_ATTRS if elem.get(attr) is not None)


def _parse_root_boolean(
    root: ET.Element,
    attribute: str,
    *,
    svg_path: Path,
) -> bool:
    """Parse one optional root boolean with a backward-compatible true default."""
    from .template_structure import (
        TemplateStructureError,
    )
    raw = root.get(attribute)
    if raw is None:
        return True
    if raw not in {"true", "false"}:
        raise TemplateStructureError(
            f"{svg_path.name}: root {attribute} must be exactly 'true' or 'false'"
        )
    return raw == "true"
