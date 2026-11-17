"""PPT Master project specifications; MIT Hugo He 2025-2026.
Pinned upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Mapping

def validate_markdown_schema(markdown_path: Path, schema_path: Path) -> list[str]:
    """Validate one existing Markdown artifact against a versioned schema."""
    from .project_specs import (
        json,
        validate_markdown_text,
    )
    try:
        # Candidate validation removes exactly one BOM, as utf-8-sig did here.
        text = markdown_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"Schema validation could not read {markdown_path.name}: {exc}"]

    return validate_markdown_text(text, schema_path, markdown_path=markdown_path)


def validate_markdown_text(
    text: str,
    schema_path: Path,
    *,
    markdown_path: Path = Path("design_spec.md"),
) -> list[str]:
    """Validate a candidate without reading or writing its Markdown file.

    The path supplies diagnostic names and a base for schema-declared asset
    references. A design-spec schema never loads the execution lock.
    """
    from .project_specs import (
        _condition_applies,
        _extract_schema_marker,
        _load_markdown_schema,
        _parse_markdown_sections,
        _validate_condition,
        _validate_references,
        _validate_section,
        _validate_slides,
        _validate_spec_lock_relations,
        _validate_strict_data_surface,
        json,
        re,
    )
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    try:
        schema = _load_markdown_schema(schema_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"Schema validation could not read {markdown_path.name}: {exc}"]

    contract = schema["x-markdown"]
    assert isinstance(contract, dict)
    marker, marker_error = _extract_schema_marker(text)
    if marker_error is not None:
        return [f"{markdown_path.name} schema: {marker_error}"]
    expected_marker = contract.get("marker")
    if isinstance(expected_marker, str):
        if marker is None:
            return [
                f"{markdown_path.name} schema: missing ppt-master-schema marker "
                f"'{expected_marker}'"
            ]
        if marker != expected_marker.casefold():
            return [
                f"{markdown_path.name} schema: marker '{marker}' does not match "
                f"'{expected_marker}'"
            ]
    sections, parse_errors = _parse_markdown_sections(
        text,
        report_duplicate_fields=contract.get("parser") == "heading-data-lines-v1",
    )
    definitions = contract.get("sections", [])
    if not isinstance(definitions, list):
        return [f"Schema validation could not read {schema_path.name}: sections must be a list"]

    markdown_name = markdown_path.name
    errors = [f"{markdown_name} schema: {message}" for message in parse_errors]
    unresolved_patterns = contract.get("unresolved_patterns", [])
    if isinstance(unresolved_patterns, list):
        for pattern in unresolved_patterns:
            if not isinstance(pattern, str):
                continue
            matches = list(re.finditer(pattern, text))
            if matches:
                errors.append(
                    f"{markdown_name} schema: contains {len(matches)} unresolved "
                    f"placeholder(s) matching '{pattern}'"
                )

    matched: dict[str, dict[str, object] | None] = {}
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        section_id = str(definition.get("id", ""))
        pattern = str(definition.get("pattern", ""))
        if not section_id or not pattern:
            continue
        candidates = [
            section
            for section in sections
            if re.fullmatch(pattern, str(section["heading"]))
        ]
        if len(candidates) > 1:
            errors.append(
                f"{markdown_name} schema: section '{section_id}' appears more than once"
            )
        section = candidates[0] if candidates else None
        matched[section_id] = section
        if definition.get("required") is True and section is None:
            errors.append(f"{markdown_name} schema: missing section '{section_id}'")
            continue
        if section is not None:
            errors.extend(
                _validate_section(
                    markdown_name=markdown_name,
                    section_id=section_id,
                    section=section,
                    definition=definition,
                    schema_path=schema_path,
                )
            )

    section_order = contract.get("section_order", [])
    if isinstance(section_order, list):
        ordered_sections = [
            (str(section_id), matched.get(str(section_id)))
            for section_id in section_order
            if matched.get(str(section_id)) is not None
        ]
        offsets = [int(section["offset"]) for _, section in ordered_sections if section]
        if offsets != sorted(offsets):
            expected = " -> ".join(str(section_id) for section_id in section_order)
            errors.append(
                f"{markdown_name} schema: sections are out of order; expected {expected}"
            )

    conditions = contract.get("conditions", [])
    if isinstance(conditions, list):
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            when = condition.get("when", {})
            then = condition.get("then", {})
            if not isinstance(when, dict) or not isinstance(then, dict):
                continue
            if _condition_applies(when, matched):
                errors.extend(
                    _validate_condition(
                        markdown_name=markdown_name,
                        condition_id=str(condition.get("id", "conditional rule")),
                        then=then,
                        matched=matched,
                    )
                )

    errors.extend(
        _validate_references(
            markdown_path=markdown_path,
            markdown_name=markdown_name,
            rules=contract.get("references"),
            matched=matched,
        )
    )

    slide_contract = contract.get("slides")
    if isinstance(slide_contract, dict):
        errors.extend(
            _validate_slides(
                markdown_name=markdown_name,
                slide_contract=slide_contract,
                matched=matched,
            )
        )
    if contract.get("strict_lines") is True:
        errors.extend(
            _validate_strict_data_surface(markdown_name, text, sections, matched)
        )
    if schema.get("$id") == "ppt-master://schemas/spec-lock/v1":
        errors.extend(_validate_spec_lock_relations(markdown_path, matched))
    return errors


def validate_project_artifacts(
    project_path: Path,
    project_info: Mapping[str, object] | None = None,
    *,
    include_design: bool = True,
) -> tuple[list[str], list[str]]:
    """Validate the lock and, when requested, the human-facing design brief."""
    from .project_specs import (
        SCHEMA_DIR,
        _extract_schema_marker,
        get_project_info_common,
        parse_spec_lock_artifact,
        validate_communication_trace,
        validate_markdown_schema,
    )
    info = project_info or get_project_info_common(str(project_path))
    errors: list[str] = []
    warnings: list[str] = []
    artifacts: list[tuple[Path, Path, str]] = []
    spec_name = info.get("spec_file")
    if include_design and isinstance(spec_name, str):
        artifacts.append(
            (
                project_path / spec_name,
                SCHEMA_DIR / "design_spec.schema.json",
                "design",
            )
        )
    lock_path = project_path / "spec_lock.md"
    if lock_path.is_file():
        artifacts.append((lock_path, SCHEMA_DIR / "spec_lock.schema.json", "lock"))
    elif isinstance(spec_name, str):
        errors.append(
            "Communication trace: missing spec_lock.md with a "
            "## communication section."
        )

    legacy_design = False
    legacy_lock = False
    versioned_lock_valid = False
    for artifact_path, schema_path, artifact_kind in artifacts:
        try:
            text = artifact_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            errors.append(f"Schema validation could not read {artifact_path.name}: {exc}")
            continue
        marker, marker_error = _extract_schema_marker(text)
        if marker_error is not None:
            errors.append(f"{artifact_path.name} schema: {marker_error}")
            continue
        if marker is None:
            warnings.append(
                f"{artifact_path.name}: legacy artifact has no ppt-master-schema "
                "marker; skipped versioned schema validation"
            )
            legacy_design = legacy_design or artifact_kind == "design"
            legacy_lock = legacy_lock or artifact_kind == "lock"
            continue
        artifact_errors = validate_markdown_schema(artifact_path, schema_path)
        errors.extend(artifact_errors)
        if artifact_kind == "lock" and not artifact_errors:
            try:
                parse_spec_lock_artifact(
                    artifact_path,
                    compatibility_warnings=warnings,
                )
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append(f"spec_lock.md compatibility parse failed: {exc}")
                continue
            versioned_lock_valid = True
    if versioned_lock_valid:
        try:
            from svg_to_pptx.pptx_package.template_structure import (
                TemplateStructureError,
                load_pptx_structure_lock,
                template_prototype_lock_errors,
            )

            structure_lock = load_pptx_structure_lock(project_path)
            if structure_lock is not None:
                errors.extend(template_prototype_lock_errors(structure_lock))
        except (ImportError, TemplateStructureError) as exc:
            errors.append(f"spec_lock.md structure preflight failed: {exc}")
    if legacy_design or legacy_lock:
        errors.extend(
            validate_communication_trace(
                project_path,
                check_lock=legacy_lock,
                check_design=legacy_design,
            )
        )
    return errors, warnings


def scaffold_project_artifact(project_path: Path, artifact: str) -> str:
    """Render one versioned Markdown scaffold without overwriting user work."""
    from .project_specs import (
        CANVAS_FORMATS,
        SCAFFOLD_DIR,
        _SCAFFOLD_TOKEN_RE,
        get_project_info_common,
    )
    assets = {
        "design_spec": (SCAFFOLD_DIR / "design_spec.txt", "design_spec.md"),
        "spec_lock": (SCAFFOLD_DIR / "spec_lock.txt", "spec_lock.md"),
    }
    if artifact not in assets:
        raise ValueError(f"Unsupported scaffold artifact: {artifact}")
    if not project_path.exists() or not project_path.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {project_path}")

    info = get_project_info_common(str(project_path))
    format_key = str(info.get("format", "unknown"))
    if format_key not in CANVAS_FORMATS:
        raise ValueError(
            "Cannot derive the canvas format from the project directory name. "
            "Use a standard <name>_<format>_<YYYYMMDD> project path."
        )
    canvas = CANVAS_FORMATS[format_key]
    created_date = str(info.get("date_formatted", "Unknown date"))
    if created_date == "Unknown date":
        created_date = "[fill]"
    context = {
        "PROJECT_NAME": str(info.get("name", project_path.name)),
        "CANVAS_NAME": str(canvas["name"]),
        "CANVAS_DIMENSIONS": str(canvas["dimensions"]),
        "VIEWBOX": str(canvas["viewbox"]),
        "CREATED_DATE": created_date,
    }

    scaffold_path, target_name = assets[artifact]
    target_path = project_path / target_name
    existing_spec = info.get("spec_file") if artifact == "design_spec" else None
    if isinstance(existing_spec, str):
        existing_path = project_path / existing_spec
        raise FileExistsError(
            f"Refusing to shadow existing design spec: {existing_path}"
        )
    if target_path.exists() or target_path.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing artifact: {target_path}")
    rendered = scaffold_path.read_text(encoding="utf-8")
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    unresolved = sorted(set(_SCAFFOLD_TOKEN_RE.findall(rendered)))
    if unresolved:
        raise ValueError(
            f"Unresolved scaffold token(s) in {scaffold_path}: {', '.join(unresolved)}"
        )
    with target_path.open("x", encoding="utf-8") as stream:
        stream.write(rendered)
    return str(target_path)
