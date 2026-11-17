"""PPT Master project specifications; MIT Hugo He 2025-2026.
Pinned upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Mapping

def _load_markdown_schema(schema_path: Path) -> dict[str, object]:
    """Load and sanity-check one versioned Markdown schema."""
    from .project_specs import (
        json,
    )
    with schema_path.open("r", encoding="utf-8") as stream:
        schema = json.load(stream)
    contract = schema.get("x-markdown")
    if not isinstance(contract, dict):
        raise ValueError(f"Schema is missing x-markdown: {schema_path}")
    if contract.get("version") != 1:
        raise ValueError(f"Unsupported Markdown schema version: {schema_path}")
    return schema


def _catalog_values(
    schema_path: Path,
    value_catalog: Mapping[str, object],
) -> tuple[Path, dict[str, object]]:
    """Resolve a schema-declared JSON catalog and object pointer."""
    from .project_specs import (
        json,
    )
    relative_path = value_catalog.get("path")
    pointer = value_catalog.get("pointer", [])
    if not isinstance(relative_path, str) or not isinstance(pointer, list):
        raise ValueError("value_catalog requires string path and list pointer")

    catalog_path = (schema_path.parent / relative_path).resolve()
    with catalog_path.open("r", encoding="utf-8") as stream:
        node: object = json.load(stream)
    for pointer_part in pointer:
        if not isinstance(node, dict):
            raise ValueError(f"pointer enters a non-object at '{pointer_part}'")
        node = node[str(pointer_part)]
    if not isinstance(node, dict):
        raise ValueError("catalog pointer does not resolve to an object")
    return catalog_path, node


def _validate_catalog_values(
    *,
    markdown_name: str,
    section_id: str,
    fields: Mapping[str, object],
    schema_path: Path,
    value_catalog: Mapping[str, object],
) -> list[str]:
    """Validate catalog membership and any schema-declared asset path."""
    from .project_specs import (
        _catalog_values,
        _normalize_schema_value,
        json,
    )
    errors: list[str] = []
    relative_path = value_catalog.get("path")
    try:
        catalog_path, catalog = _catalog_values(schema_path, value_catalog)
    except (OSError, KeyError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [
            f"{markdown_name} schema: cannot read value catalog "
            f"'{relative_path}': {exc}"
        ]

    asset_pattern = value_catalog.get("asset_path_pattern")
    for field_value in fields.values():
        value = _normalize_schema_value(str(field_value))
        if value not in catalog:
            errors.append(
                f"{markdown_name} schema: section '{section_id}' value '{value}' "
                f"is absent from {catalog_path.name}"
            )
            continue
        if not isinstance(asset_pattern, str):
            continue
        try:
            relative_asset_path = asset_pattern.format(key=value, value=value)
        except (KeyError, ValueError) as exc:
            errors.append(
                f"{markdown_name} schema: invalid asset_path_pattern "
                f"'{asset_pattern}': {exc}"
            )
            break
        asset_path = (schema_path.parent / relative_asset_path).resolve()
        if asset_path.suffix.casefold() != ".svg" or not asset_path.is_file():
            errors.append(
                f"{markdown_name} schema: section '{section_id}' value '{value}' "
                f"does not resolve to an SVG asset at {asset_path}"
            )
    return errors


def _validate_section(
    *,
    markdown_name: str,
    section_id: str,
    section: Mapping[str, object],
    definition: Mapping[str, object],
    schema_path: Path,
) -> list[str]:
    """Apply one section definition to one matched Markdown section."""
    from .project_specs import (
        _normalize_schema_value,
        _validate_catalog_values,
        math,
        re,
    )
    errors: list[str] = []
    fields = section["fields"]
    assert isinstance(fields, dict)

    required_fields = definition.get("required_fields", [])
    allow_empty = {
        str(field_name)
        for field_name in definition.get("allow_empty_fields", [])
    }
    if isinstance(required_fields, list):
        for field_name in required_fields:
            field_key = str(field_name)
            if field_key not in fields:
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' is missing field "
                    f"'{field_name}'"
                )
            elif (
                field_key not in allow_empty
                and not _normalize_schema_value(str(fields[field_key]))
            ):
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' field "
                    f"'{field_name}' must not be empty"
                )

    allowed_fields = definition.get("allowed_fields")
    if isinstance(allowed_fields, list):
        allowed = {str(field_name) for field_name in allowed_fields}
        for field_name in fields:
            if field_name not in allowed:
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' has unknown "
                    f"field '{field_name}'"
                )

    field_enums = definition.get("field_enums", {})
    if isinstance(field_enums, dict):
        for field_name, allowed in field_enums.items():
            field_key = str(field_name)
            if field_key not in fields or not isinstance(allowed, list):
                continue
            value = _normalize_schema_value(str(fields[field_key]))
            if value not in [str(item) for item in allowed]:
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' field "
                    f"'{field_name}' has illegal value '{value}'"
                )

    field_patterns = definition.get("field_patterns", {})
    if isinstance(field_patterns, dict):
        for field_name, pattern in field_patterns.items():
            field_key = str(field_name)
            if field_key not in fields:
                continue
            value = _normalize_schema_value(str(fields[field_key]))
            if re.fullmatch(str(pattern), value) is None:
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' field "
                    f"'{field_name}' does not match '{pattern}'"
                )

    field_value_rules = definition.get("field_value_rules", [])
    if isinstance(field_value_rules, list):
        for rule in field_value_rules:
            if not isinstance(rule, dict):
                continue
            key_pattern = rule.get("key_pattern")
            value_pattern = rule.get("value_pattern")
            if not isinstance(key_pattern, str) or not isinstance(
                value_pattern, str
            ):
                continue
            requirement = str(rule.get("requirement", "match its value grammar"))
            for field_name, raw_value in fields.items():
                if re.fullmatch(key_pattern, str(field_name)) is None:
                    continue
                value = str(raw_value).strip()
                if bool(rule.get("normalize", True)):
                    value = _normalize_schema_value(value)
                value_matches = re.fullmatch(value_pattern, value) is not None
                if value_matches and rule.get("numeric") == "positive_finite":
                    try:
                        number = float(value)
                    except ValueError:
                        value_matches = False
                    else:
                        value_matches = math.isfinite(number) and number > 0
                if not value_matches:
                    errors.append(
                        f"{markdown_name} schema: section '{section_id}' field "
                        f"'{field_name}' must {requirement}; found '{value}'"
                    )

    minimum = definition.get("min_entries")
    if isinstance(minimum, int) and len(fields) < minimum:
        errors.append(
            f"{markdown_name} schema: section '{section_id}' needs at least "
            f"{minimum} data line(s)"
        )

    min_body_chars = definition.get("min_body_chars")
    if isinstance(min_body_chars, int) and len(str(section["body"]).strip()) < min_body_chars:
        errors.append(
            f"{markdown_name} schema: section '{section_id}' must contain content"
        )

    entry_key_pattern = definition.get("entry_key_pattern")
    if isinstance(entry_key_pattern, str):
        field_names = section["field_names"]
        assert isinstance(field_names, dict)
        for field_name in field_names.values():
            if re.fullmatch(entry_key_pattern, str(field_name)) is None:
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' has malformed "
                    f"entry key '{field_name}'"
                )

    value_enum = definition.get("value_enum")
    if isinstance(value_enum, list):
        allowed_values = [str(item) for item in value_enum]
        for field_value in fields.values():
            value = _normalize_schema_value(str(field_value))
            if value not in allowed_values:
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' has illegal "
                    f"value '{value}'"
                )

    value_pattern = definition.get("value_pattern")
    if isinstance(value_pattern, str):
        for field_value in fields.values():
            value = _normalize_schema_value(str(field_value))
            if re.fullmatch(value_pattern, value) is None:
                errors.append(
                    f"{markdown_name} schema: section '{section_id}' has malformed "
                    f"value '{value}'"
                )

    value_catalog = definition.get("value_catalog")
    if isinstance(value_catalog, dict):
        errors.extend(
            _validate_catalog_values(
                markdown_name=markdown_name,
                section_id=section_id,
                fields=fields,
                schema_path=schema_path,
                value_catalog=value_catalog,
            )
        )
    return errors


def _condition_applies(
    when: Mapping[str, object],
    matched: Mapping[str, dict[str, object] | None],
) -> bool:
    """Return whether a schema condition applies to the matched document."""
    from .project_specs import (
        _normalize_schema_value,
        re,
    )
    section = matched.get(str(when.get("section", "")))
    if section is None:
        return False
    applies = True
    field_name = when.get("field")
    if field_name is not None:
        fields = section["fields"]
        assert isinstance(fields, dict)
        field_value = fields.get(str(field_name))
        applies = field_value is not None
        if applies and "equals" in when:
            applies = _normalize_schema_value(str(field_value)) == str(when["equals"])
    body_regex = when.get("body_regex")
    if applies and isinstance(body_regex, str):
        applies = re.search(body_regex, str(section["body"])) is not None
    return applies


def _validate_condition(
    *,
    markdown_name: str,
    condition_id: str,
    then: Mapping[str, object],
    matched: Mapping[str, dict[str, object] | None],
) -> list[str]:
    """Apply one active cross-section condition."""
    from .project_specs import (
        _normalize_schema_value,
    )
    errors: list[str] = []

    required_sections = then.get("required_sections", [])
    if isinstance(required_sections, list):
        for section_id in required_sections:
            if matched.get(str(section_id)) is None:
                errors.append(
                    f"{markdown_name} schema: condition '{condition_id}' requires "
                    f"section '{section_id}'"
                )

    forbidden_sections = then.get("forbidden_sections", [])
    if isinstance(forbidden_sections, list):
        for section_id in forbidden_sections:
            if matched.get(str(section_id)) is not None:
                errors.append(
                    f"{markdown_name} schema: condition '{condition_id}' forbids "
                    f"section '{section_id}'"
                )

    field_groups = then.get("required_fields", [])
    if isinstance(field_groups, list):
        for group in field_groups:
            if not isinstance(group, dict):
                continue
            target_id = str(group.get("section", ""))
            target = matched.get(target_id)
            if target is None:
                continue
            target_fields = target["fields"]
            assert isinstance(target_fields, dict)
            for field_name in group.get("fields", []):
                field_key = str(field_name)
                if field_key not in target_fields:
                    errors.append(
                        f"{markdown_name} schema: condition '{condition_id}' requires "
                        f"field '{field_name}' in section '{target_id}'"
                    )
                elif not _normalize_schema_value(str(target_fields[field_key])):
                    errors.append(
                        f"{markdown_name} schema: condition '{condition_id}' requires "
                        f"non-empty field '{field_name}' in section '{target_id}'"
                    )

    field_values = then.get("field_values", [])
    if isinstance(field_values, list):
        for value_rule in field_values:
            if not isinstance(value_rule, dict):
                continue
            target_id = str(value_rule.get("section", ""))
            target = matched.get(target_id)
            if target is None:
                continue
            target_fields = target["fields"]
            assert isinstance(target_fields, dict)
            target_field = str(value_rule.get("field", ""))
            value = target_fields.get(target_field)
            allowed = value_rule.get("enum", [])
            if value is None or not isinstance(allowed, list):
                continue
            normalized = _normalize_schema_value(str(value))
            if normalized not in [str(item) for item in allowed]:
                errors.append(
                    f"{markdown_name} schema: condition '{condition_id}' requires "
                    f"'{target_id}.{target_field}' to be one of {allowed}"
                )

    subheading_rules = then.get("required_subheadings", [])
    if isinstance(subheading_rules, list):
        for rule in subheading_rules:
            if not isinstance(rule, dict):
                continue
            target_id = str(rule.get("section", ""))
            target = matched.get(target_id)
            if target is None:
                continue
            heading = str(rule.get("heading", ""))
            subheadings = target["subheadings"]
            assert isinstance(subheadings, list)
            if not any(
                str(item).startswith(heading)
                for item in subheadings
            ):
                errors.append(
                    f"{markdown_name} schema: condition '{condition_id}' requires "
                    f"subheading '{heading}' in section '{target_id}'"
                )
    return errors


