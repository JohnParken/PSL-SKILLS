"""PPT Master project specifications; MIT Hugo He 2025-2026.
Pinned upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
import json
from typing import Mapping

def _validate_spec_lock_relations(
    markdown_path: Path,
    matched: Mapping[str, dict[str, object] | None],
) -> list[str]:
    """Validate cross-section references that JSON field rules cannot express."""
    from .project_specs import (
        CANVAS_FORMATS,
        LEGACY_STRUCTURE_INTENT_KIND,
        Path,
        VISUALIZATION_SVG_KIND,
        VisualizationCatalogError,
        _CUSTOM_REFERENCE_CATALOGS,
        _LEGACY_CANVAS_NAMES,
        _LEGACY_IMAGE_METADATA_KEYS,
        _normalize_schema_value,
        _validate_spec_lock_forbidden,
        get_project_info_common,
        parse_spec_lock_image_value,
        resolve_visualization_reference,
    )
    markdown_name = markdown_path.name
    errors: list[str] = []

    errors.extend(_validate_spec_lock_forbidden(matched.get("forbidden")))

    def fields(section_id: str) -> dict[str, str]:
        section = matched.get(section_id)
        if section is None:
            return {}
        raw_fields = section["fields"]
        assert isinstance(raw_fields, dict)
        return {str(key): str(value) for key, value in raw_fields.items()}

    for section_id, selector_field, references_field, catalog_dir in (
        _CUSTOM_REFERENCE_CATALOGS
    ):
        section_fields = fields(section_id)
        is_custom = (
            _normalize_schema_value(section_fields.get(selector_field, "")) == "custom"
        )
        raw_references = _normalize_schema_value(
            section_fields.get(references_field, "")
        )
        if not is_custom:
            if raw_references:
                errors.append(
                    f"{markdown_name} schema: field '{references_field}' is valid "
                    f"only when '{selector_field}' is custom"
                )
            continue
        if not raw_references:
            continue
        references = [item.strip() for item in raw_references.split(",")]
        duplicates = sorted(
            reference
            for reference in set(references)
            if references.count(reference) > 1
        )
        if duplicates:
            errors.append(
                f"{markdown_name} schema: field '{references_field}' repeats "
                f"catalog id(s) {', '.join(duplicates)}"
            )
        for reference in references:
            try:
                catalog = json.loads((catalog_dir / "catalog.json").read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                catalog = {}
            basename = catalog.get(reference) if isinstance(catalog, dict) else None
            catalog_file = catalog_dir / basename if isinstance(basename, str) else None
            if reference == "custom" or catalog_file is None or not catalog_file.is_file():
                errors.append(
                    f"{markdown_name} schema: field '{references_field}' references "
                    f"unknown catalog id '{reference}'"
                )

    for key, value in fields("images").items():
        if key.strip().casefold() in _LEGACY_IMAGE_METADATA_KEYS:
            continue
        try:
            parse_spec_lock_image_value(key, value)
        except ValueError as exc:
            errors.append(
                f"{markdown_name} schema: images row {key!r} {exc}"
            )

    rhythm = fields("page_rhythm")
    layouts = fields("pptx_layouts")
    page_pptx_layouts = fields("page_pptx_layouts")
    page_layouts = fields("page_layouts")
    page_visualizations = fields("page_visualizations")
    legacy_page_charts = fields("page_charts")
    structure = fields("pptx_structure")

    for layout_key, raw_value in layouts.items():
        parts = [part.strip() for part in raw_value.split("|")]
        if len(parts) != 3:
            continue
        _, _, source = parts
        if source.startswith("template:"):
            basename = source.removeprefix("template:").strip()
            if basename.casefold().endswith(".svg"):
                basename = basename[:-4]
            template_path = markdown_path.parent / "templates" / f"{basename}.svg"
            if not template_path.is_file():
                errors.append(
                    f"{markdown_name} schema: layout '{layout_key}' references "
                    f"missing template SVG '{basename}.svg'"
                )
        elif source not in rhythm:
            errors.append(
                f"{markdown_name} schema: layout '{layout_key}' has unknown "
                f"prototype source '{source}'"
            )

    if structure.get("mode") == "structured":
        expected_pages = set(rhythm)
        for section_id, mapping in (
            ("page_pptx_layouts", page_pptx_layouts),
            ("page_layouts", page_layouts),
        ):
            missing = sorted(expected_pages - set(mapping))
            extra = sorted(set(mapping) - expected_pages)
            if missing:
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' misses pages "
                    f"{', '.join(missing)}"
                )
            if extra:
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' has unknown "
                    f"pages {', '.join(extra)}"
                )

    overlapping_visualization_pages = sorted(
        set(page_visualizations) & set(legacy_page_charts)
    )
    if overlapping_visualization_pages:
        errors.append(
            f"{markdown_name} schema: pages "
            f"{', '.join(overlapping_visualization_pages)} are declared in both "
            "page_visualizations and legacy page_charts; keep only "
            "page_visualizations"
        )

    for section_id, mapping, allow_legacy_bare in (
        ("page_visualizations", page_visualizations, False),
        ("page_charts", legacy_page_charts, True),
    ):
        unknown_pages = sorted(set(mapping) - set(rhythm))
        if unknown_pages:
            errors.append(
                f"{markdown_name} schema: {section_id} has unknown pages "
                f"{', '.join(unknown_pages)}"
            )
        for page_key, raw_reference in mapping.items():
            reference = _normalize_schema_value(raw_reference)
            try:
                entry = resolve_visualization_reference(
                    reference,
                    allow_legacy_bare=allow_legacy_bare,
                )
            except VisualizationCatalogError as exc:
                errors.append(
                    f"{markdown_name} schema: {section_id}.{page_key} "
                    f"cannot resolve visualization {reference!r}: {exc}"
                )
                continue
            if entry.kind == LEGACY_STRUCTURE_INTENT_KIND:
                if section_id != "page_charts" or not allow_legacy_bare:
                    errors.append(
                        f"{markdown_name} schema: {section_id}.{page_key} resolves "
                        "to a legacy Structure intent outside page_charts"
                    )
                if entry.path is not None:
                    errors.append(
                        f"{markdown_name} schema: {section_id}.{page_key} legacy "
                        "Structure intent unexpectedly has an asset path"
                    )
                continue
            if entry.kind != VISUALIZATION_SVG_KIND:
                errors.append(
                    f"{markdown_name} schema: {section_id}.{page_key} resolves "
                    f"to unsupported kind {entry.kind!r}"
                )
                continue
            if entry.path is None:
                errors.append(
                    f"{markdown_name} schema: {section_id}.{page_key} does not "
                    "resolve to an SVG asset path"
                )
                continue
            asset_path = Path(entry.path)
            if asset_path.suffix.casefold() != ".svg" or not asset_path.is_file():
                errors.append(
                    f"{markdown_name} schema: {section_id}.{page_key} does not "
                    f"resolve to an SVG asset at {asset_path}"
                )

    info = get_project_info_common(str(markdown_path.parent))
    format_key = str(info.get("format", "unknown"))
    canvas = CANVAS_FORMATS.get(format_key)
    canvas_fields = fields("canvas")
    if canvas is not None:
        expected_format = str(canvas["name"])
        expected_viewbox = str(canvas["viewbox"])
        accepted_formats = {
            expected_format,
            format_key,
            *_LEGACY_CANVAS_NAMES.get(format_key, ()),
        }
        if (
            "format" in canvas_fields
            and _normalize_schema_value(canvas_fields["format"]) not in accepted_formats
        ):
            errors.append(
                f"{markdown_name} schema: canvas.format must be '{expected_format}'"
            )
        if (
            "viewBox" in canvas_fields
            and _normalize_schema_value(canvas_fields["viewBox"]) != expected_viewbox
        ):
            errors.append(
                f"{markdown_name} schema: canvas.viewBox must be '{expected_viewbox}'"
            )
    return errors


