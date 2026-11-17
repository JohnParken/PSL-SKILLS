"""PPT Master project specifications; MIT Hugo He 2025-2026.
Pinned upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Mapping

def _normalize_schema_value(value: str) -> str:
    """Normalize a Markdown scalar before enum, pattern, and catalog checks."""
    normalized = value.strip()
    if (
        len(normalized) >= 2
        and normalized[0] == normalized[-1]
        and normalized[0] in "'\"`"
    ):
        return normalized[1:-1].strip()
    return normalized


def _extract_schema_marker(text: str) -> tuple[str | None, str | None]:
    """Read the optional version marker from the first non-empty line."""
    from .project_specs import (
        _SCHEMA_MARKER_RE,
    )
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line.startswith("<!--") or "ppt-master-schema:" not in first_line:
        return None, None
    match = _SCHEMA_MARKER_RE.fullmatch(first_line)
    if match is None:
        return None, "has a malformed ppt-master-schema marker"
    return match.group(1).casefold(), None


def _parse_markdown_sections(
    text: str,
    *,
    report_duplicate_fields: bool,
) -> tuple[list[dict[str, object]], list[str]]:
    """Parse H2 sections, data lines, and nested headings from Markdown."""
    from .project_specs import (
        _MARKDOWN_DATA_LINE_RE,
        _MARKDOWN_H2_RE,
        _MARKDOWN_SUBHEADING_RE,
    )
    headings = list(_MARKDOWN_H2_RE.finditer(text))
    sections: list[dict[str, object]] = []
    errors: list[str] = []

    for index, heading_match in enumerate(headings):
        body_start = heading_match.end()
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[body_start:body_end]
        fields: dict[str, str] = {}
        field_names: dict[str, str] = {}

        for field_match in _MARKDOWN_DATA_LINE_RE.finditer(body):
            field_name = field_match.group(1).strip()
            field_key = field_name
            if field_key in fields and report_duplicate_fields:
                errors.append(
                    f"section '{heading_match.group(1).strip()}' repeats data key "
                    f"'{field_name}'"
                )
                continue
            fields[field_key] = field_match.group(2).strip()
            field_names[field_key] = field_name

        sections.append(
            {
                "heading": heading_match.group(1).strip(),
                "offset": heading_match.start(),
                "body": body,
                "fields": fields,
                "field_names": field_names,
                "subheadings": [
                    match.group(1).strip()
                    for match in _MARKDOWN_SUBHEADING_RE.finditer(body)
                ],
            }
        )

    return sections, errors


def parse_markdown_artifact(
    markdown_path: Path,
    *,
    report_duplicate_fields: bool = False,
) -> list[dict[str, object]]:
    """Parse one Markdown planning artifact without changing it.

    This is the public read-only entry point for consumers that need the same
    heading/data-line grammar as schema validation.  Keeping the parser here
    prevents runtime projections from drifting into their own lock grammar.
    """
    from .project_specs import (
        _parse_markdown_sections,
    )
    text = markdown_path.read_text(encoding="utf-8")
    sections, errors = _parse_markdown_sections(
        text,
        report_duplicate_fields=report_duplicate_fields,
    )
    if errors:
        raise ValueError("; ".join(errors))
    return sections


def _looks_like_image_path(raw: str) -> bool:
    """Return whether one lock token looks like a project image path."""
    from .project_specs import (
        Path,
        _IMAGE_PATH_SUFFIXES,
    )
    token = raw.strip().strip("`'\"").replace("\\", "/")
    return bool(token) and Path(token).suffix.casefold() in _IMAGE_PATH_SUFFIXES


def parse_spec_lock_image_value(key: str, value: str) -> dict[str, str]:
    """Parse one image-lock row while preserving supported legacy rows.

    Current rows use ``<path> | source=... | crop=...`` and may retain the
    legacy ``pattern=...`` projection. Legacy rows remain readable, but any row
    that starts using named metadata must provide source and crop.
    """
    from .project_specs import (
        Path,
        _IMAGE_ACQUISITION_SOURCES,
        _IMAGE_CROP_POLICIES,
        _looks_like_image_path,
        re,
    )
    normalized_key = str(key).strip()
    normalized_value = str(value).strip()
    parts = [part.strip() for part in normalized_value.split("|")]
    path_part = parts[0] if parts else ""

    if _looks_like_image_path(normalized_key) and not _looks_like_image_path(path_part):
        parts.insert(0, normalized_key)
        path_part = normalized_key
    elif (
        len(parts) >= 2
        and parts[0].casefold() in _IMAGE_ACQUISITION_SOURCES
        and _looks_like_image_path(parts[1])
    ):
        path_part = parts[1]

    metadata_parts = [part for part in parts[1:] if "=" in part]
    if not metadata_parts:
        legacy_crop = (
            "no-crop"
            if any(
                re.search(r"(?<![a-z])no-crop(?![a-z])", part, re.IGNORECASE)
                for part in parts[1:]
            )
            else ""
        )
        return {
            "path": path_part,
            "source": "",
            "pattern": "",
            "crop": legacy_crop,
            "legacy": "true",
        }

    metadata: dict[str, str] = {}
    unsupported_parts: list[str] = []
    for part in parts[1:]:
        field, separator, raw = part.partition("=")
        if not separator:
            unsupported_parts.append(part)
            continue
        field = field.strip().casefold()
        if field in metadata:
            raise ValueError(f"repeats metadata field {field!r}")
        metadata[field] = raw.strip()

    allowed_fields = {"source", "pattern", "crop"}
    required_fields = {"source", "crop"}
    unknown_fields = sorted(set(metadata) - allowed_fields)
    missing_fields = sorted(required_fields - set(metadata))
    if unsupported_parts:
        shown = ", ".join(repr(part) for part in unsupported_parts)
        raise ValueError(f"has unsupported metadata token(s) {shown}")
    if unknown_fields:
        raise ValueError(
            f"has unknown metadata field(s) {', '.join(unknown_fields)}"
        )
    if missing_fields:
        raise ValueError(
            f"misses metadata field(s) {', '.join(missing_fields)}"
        )
    if not _looks_like_image_path(path_part):
        raise ValueError(f"has invalid image path {path_part!r}")

    normalized_path = path_part.replace("\\", "/")
    path = Path(normalized_path)
    if (
        path.is_absolute()
        or len(path.parts) != 2
        or path.parts[0] != "images"
        or path.name in {"", ".", ".."}
        or ":" in path.name
        or normalized_path != f"images/{path.name}"
    ):
        raise ValueError(
            f"must use canonical project path images/<filename>, got {path_part!r}"
        )

    source = metadata["source"].casefold()
    if source not in _IMAGE_ACQUISITION_SOURCES:
        allowed = ", ".join(sorted(_IMAGE_ACQUISITION_SOURCES))
        raise ValueError(f"source must be one of {allowed}, got {metadata['source']!r}")
    if "pattern" in metadata and not metadata["pattern"]:
        raise ValueError("pattern must be non-empty")
    crop = metadata["crop"].casefold()
    if crop not in _IMAGE_CROP_POLICIES:
        allowed = ", ".join(sorted(_IMAGE_CROP_POLICIES))
        raise ValueError(f"crop must be one of {allowed}, got {metadata['crop']!r}")

    return {
        "path": normalized_path,
        "source": source,
        "pattern": metadata.get("pattern", ""),
        "crop": crop,
        "legacy": "false",
    }


def parse_spec_lock_artifact(
    lock_path: Path,
    *,
    report_duplicate_fields: bool = False,
    compatibility_warnings: list[str] | None = None,
) -> list[dict[str, object]]:
    """Parse one execution lock and normalize supported legacy image rows.

    Current locks use ``- <key>: <path> | source=... | crop=...`` and may retain
    the legacy ``pattern=...`` projection. Some versioned projects instead
    placed the image path before the colon.
    Preserve those projects by projecting the key path back into the value so
    every consumer sees the same path-first image value.
    """
    from .project_specs import (
        _looks_like_image_path,
        parse_markdown_artifact,
    )
    sections = parse_markdown_artifact(
        lock_path,
        report_duplicate_fields=report_duplicate_fields,
    )
    normalized_sections: list[dict[str, object]] = []
    compatibility_keys: list[str] = []

    for section in sections:
        if str(section.get("heading", "")).strip().casefold() != "images":
            normalized_sections.append(section)
            continue
        raw_fields = section.get("fields")
        if not isinstance(raw_fields, dict):
            normalized_sections.append(section)
            continue

        fields: dict[str, str] = {}
        for raw_key, raw_value in raw_fields.items():
            key = str(raw_key)
            value = str(raw_value).strip()
            value_path = value.split("|", 1)[0].strip()
            if _looks_like_image_path(key) and not _looks_like_image_path(value_path):
                value = f"{key} | {value}" if value else key
                compatibility_keys.append(key)
            fields[key] = value

        normalized_section = dict(section)
        normalized_section["fields"] = fields
        normalized_sections.append(normalized_section)

    if compatibility_warnings is not None and compatibility_keys:
        sample = ", ".join(compatibility_keys[:3])
        suffix = "" if len(compatibility_keys) <= 3 else ", ..."
        compatibility_warnings.append(
            f"{lock_path.name} images: normalized {len(compatibility_keys)} legacy "
            "path-as-key row(s); new locks should use '- <key>: <path> | "
            "source=... | crop=...' "
            f"(found: {sample}{suffix})"
        )
    return normalized_sections


def parse_spec_lock(
    lock_path: Path,
    *,
    report_duplicate_fields: bool = False,
    compatibility_warnings: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Return one execution lock as ``{section: {key: value}}`."""
    from .project_specs import (
        parse_spec_lock_artifact,
    )
    sections = parse_spec_lock_artifact(
        lock_path,
        report_duplicate_fields=report_duplicate_fields,
        compatibility_warnings=compatibility_warnings,
    )
    parsed: dict[str, dict[str, str]] = {}
    for section in sections:
        raw_fields = section.get("fields")
        if not isinstance(raw_fields, dict):
            continue
        parsed[str(section.get("heading", "")).strip()] = {
            str(key): str(value) for key, value in raw_fields.items()
        }
    return parsed


def default_spec_lock_forbidden() -> frozenset[str]:
    """Return the versioned scaffold's universal forbidden-item defaults."""
    from .project_specs import (
        SCAFFOLD_DIR,
        _LEGACY_SPEC_LOCK_FORBIDDEN,
        parse_markdown_artifact,
        re,
    )
    sections = parse_markdown_artifact(SCAFFOLD_DIR / "spec_lock.txt")
    section = next(
        (
            item
            for item in sections
            if str(item.get("heading", "")).strip().casefold() == "forbidden"
        ),
        None,
    )
    if section is None:
        raise ValueError("spec-lock scaffold has no forbidden section")
    current = frozenset(
        re.sub(r"^-[ \t]+", "", line.strip())
        for line in str(section.get("body", "")).splitlines()
        if line.strip()
    )
    return current | _LEGACY_SPEC_LOCK_FORBIDDEN


def _normalize_forbidden_row(row: str) -> str:
    """Collapse whitespace for baseline comparison and diagnostics."""
    return " ".join(row.split())


def _validate_spec_lock_forbidden(
    section: Mapping[str, object] | None,
) -> list[str]:
    """Require provenance tags on non-baseline rows in a versioned lock."""
    from .project_specs import (
        _LEGACY_SPEC_LOCK_FORBIDDEN_ANCHORS,
        _MARKDOWN_LIST_ITEM_RE,
        _normalize_forbidden_row,
        default_spec_lock_forbidden,
    )
    if section is None:
        return []

    baseline = {
        _normalize_forbidden_row(row)
        for row in default_spec_lock_forbidden()
    }
    errors: list[str] = []
    row_number = 0
    for line in str(section.get("body", "")).splitlines():
        match = _MARKDOWN_LIST_ITEM_RE.match(line)
        if match is None:
            continue
        row = _normalize_forbidden_row(match.group(1))
        if not row:
            continue
        row_number += 1
        if (
            row in baseline
            or any(
                anchor in row for anchor in _LEGACY_SPEC_LOCK_FORBIDDEN_ANCHORS
            )
            or row.endswith("(user)")
        ):
            continue
        errors.append(
            f"spec_lock.md forbidden: row {row_number} is not a baseline rule "
            f"and lacks the (user) tag: {row[:60]}"
        )
    return errors


