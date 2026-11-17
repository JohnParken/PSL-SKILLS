"""PPT Master project specifications; MIT Hugo He 2025-2026.
Pinned upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Mapping

def _declared_page_count(section: Mapping[str, object] | None) -> int | None:
    """Return the exact integer §I Page Count, or None when absent or not exact."""
    from .project_specs import (
        _PAGE_COUNT_ROW_RE,
    )
    if section is None:
        return None
    match = _PAGE_COUNT_ROW_RE.search(str(section.get("body", "")))
    return int(match.group(1)) if match else None


def _validate_slides(
    *,
    markdown_name: str,
    slide_contract: Mapping[str, object],
    matched: Mapping[str, dict[str, object] | None],
) -> list[str]:
    """Validate repeated slide blocks inside the configured outline section."""
    from .project_specs import (
        _MARKDOWN_SUBHEADING_RE,
        _declared_page_count,
        re,
    )
    outline = matched.get(str(slide_contract.get("section", "")))
    heading_pattern = str(slide_contract.get("heading_pattern", ""))
    if outline is None or not heading_pattern:
        return []

    body = str(outline["body"])
    heading_matches = [
        match
        for match in _MARKDOWN_SUBHEADING_RE.finditer(body)
        if re.match(heading_pattern, match.group(1))
    ]
    if not heading_matches:
        return [f"{markdown_name} schema: content outline has no Slide blocks"]

    errors: list[str] = []
    declared = _declared_page_count(matched.get("project_information"))
    if declared is not None and declared != len(heading_matches):
        errors.append(
            f"{markdown_name} schema: §I Page Count is {declared} but the "
            f"content outline has {len(heading_matches)} Slide block(s); "
            "the two must match"
        )
    required_fields = slide_contract.get("required_fields", [])
    if not isinstance(required_fields, list):
        return errors
    for index, slide_match in enumerate(heading_matches):
        block_end = (
            heading_matches[index + 1].start()
            if index + 1 < len(heading_matches)
            else len(body)
        )
        block = body[slide_match.end():block_end]
        for field_name in required_fields:
            pattern = (
                rf"^[ \t]*-[ \t]+(?:\*\*)?{re.escape(str(field_name))}"
                rf"(?:\*\*)?[ \t]*:"
            )
            if re.search(pattern, block, flags=re.MULTILINE) is None:
                errors.append(
                    f"{markdown_name} schema: '{slide_match.group(1)}' is missing "
                    f"field '{field_name}'"
                )
    return errors


def _validate_references(
    *,
    markdown_path: Path,
    markdown_name: str,
    rules: object,
    matched: Mapping[str, dict[str, object] | None],
) -> list[str]:
    """Validate schema-declared cross-section keys and project assets."""
    from .project_specs import (
        _normalize_schema_value,
        re,
    )
    if not isinstance(rules, list):
        return []
    errors: list[str] = []
    project_root = markdown_path.parent.resolve()

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("id", "reference"))
        source_id = str(rule.get("from_section", ""))
        source = matched.get(source_id)
        if source is None:
            continue
        source_fields = source["fields"]
        assert isinstance(source_fields, dict)

        target_id = rule.get("target_section")
        target_fields: Mapping[str, object] | None = None
        if isinstance(target_id, str):
            target = matched.get(target_id)
            if target is None:
                continue
            raw_target_fields = target["fields"]
            assert isinstance(raw_target_fields, dict)
            target_fields = raw_target_fields

        component = rule.get("value_component")
        asset_pattern = rule.get("asset_path_pattern")
        for source_key, raw_value in source_fields.items():
            value = _normalize_schema_value(str(raw_value))
            reference_value = value
            if isinstance(component, dict):
                separator = str(component.get("separator", "|"))
                index = component.get("index", 0)
                parts = [part.strip() for part in value.split(separator)]
                if not isinstance(index, int) or index >= len(parts):
                    errors.append(
                        f"{markdown_name} schema: reference '{rule_id}' cannot "
                        f"parse value '{value}' from section '{source_id}'"
                    )
                    continue
                reference_value = _normalize_schema_value(parts[index])

            if target_fields is not None:
                if reference_value not in target_fields:
                    errors.append(
                        f"{markdown_name} schema: reference '{rule_id}' value "
                        f"'{reference_value}' from '{source_id}.{source_key}' is not "
                        f"declared in section '{target_id}'"
                    )

            if isinstance(asset_pattern, str):
                asset_value = reference_value
                suffix_match = re.search(r"\{value\}(\.[A-Za-z0-9]+)$", asset_pattern)
                if (
                    suffix_match is not None
                    and asset_value.casefold().endswith(
                        suffix_match.group(1).casefold()
                    )
                ):
                    asset_value = asset_value[: -len(suffix_match.group(1))]
                try:
                    relative_asset = asset_pattern.format(value=asset_value)
                except (KeyError, ValueError) as exc:
                    errors.append(
                        f"{markdown_name} schema: reference '{rule_id}' has invalid "
                        f"asset_path_pattern '{asset_pattern}': {exc}"
                    )
                    continue
                asset_path = (project_root / relative_asset).resolve()
                try:
                    asset_path.relative_to(project_root)
                except ValueError:
                    errors.append(
                        f"{markdown_name} schema: reference '{rule_id}' escapes the "
                        f"project root for value '{reference_value}'"
                    )
                    continue
                if asset_path.suffix.casefold() != ".svg" or not asset_path.is_file():
                    errors.append(
                        f"{markdown_name} schema: reference '{rule_id}' value "
                        f"'{reference_value}' does not resolve to {asset_path}"
                    )
    return errors


def _validate_strict_data_surface(
    markdown_name: str,
    text: str,
    sections: list[dict[str, object]],
    matched: Mapping[str, dict[str, object] | None],
) -> list[str]:
    """Reject unknown lock sections and prose outside the data-line grammar."""
    from .project_specs import (
        _MARKDOWN_DATA_LINE_RE,
    )
    errors: list[str] = []
    section_ids = {
        int(section["offset"]): section_id
        for section_id, section in matched.items()
        if section is not None
    }
    first_offset = min((int(section["offset"]) for section in sections), default=len(text))
    for line in text[:first_offset].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--") or stripped.startswith("# "):
            continue
        errors.append(f"{markdown_name} schema: unsupported preamble line '{stripped}'")

    for section in sections:
        heading = str(section["heading"])
        section_id = section_ids.get(int(section["offset"]))
        if section_id is None:
            errors.append(f"{markdown_name} schema: unknown section '{heading}'")
            continue
        for line in str(section["body"]).splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if section_id == "forbidden" and stripped.startswith("- "):
                continue
            if _MARKDOWN_DATA_LINE_RE.fullmatch(line) is not None:
                continue
            errors.append(
                f"{markdown_name} schema: section '{section_id}' has unsupported "
                f"line '{stripped}'"
            )
    return errors


