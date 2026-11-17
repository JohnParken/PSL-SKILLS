"""Upstream template registration: style responsibilities."""
from __future__ import annotations

import json

from .common import (
    Path,
    SpecParseError,
    _HEX_COLOR_RE,
    _PORTABLE_STYLE_ID_RE,
    _STYLE_ALLOWED_FRONTMATTER_FIELDS,
    _STYLE_CUSTOM_FIELDS,
    _STYLE_FORBIDDEN_FIELDS,
    _STYLE_FORBIDDEN_SECTIONS,
    _STYLE_REQUIRED_FIELDS,
    _STYLE_REQUIRED_SECTIONS,
    _STYLE_REVIEW_TRIGGER_MARKER,
    re,
)

def _validate_style_spec(
    expected_template_id: str | None,
    template_root: Path,
    template_dir: Path,
    frontmatter: dict,
    body: str,
    pages: list[str],
) -> None:
    """Reject Style workspaces outside the roster-free method contract."""
    from .specification import (
        _clean_field_value,
        _extract_section_field,
        _has_qualified_roster_spec,
        _markdown_subsection,
        _markdown_table_rows,
        _numbered_section,
        _style_field_is_declared,
        _style_value_is_substantive,
    )
    errors: list[str] = []

    raw_style_id = frontmatter.get("style_id")
    declared_id = raw_style_id.strip() if isinstance(raw_style_id, str) else ""
    if not declared_id:
        errors.append("frontmatter style_id must be a non-empty string")
    else:
        if (
            _PORTABLE_STYLE_ID_RE.fullmatch(declared_id) is None
            or declared_id in {".", ".."}
            or declared_id.endswith(".")
        ):
            errors.append(
                "frontmatter style_id must be a filesystem-safe portable slug"
            )
        if (
            expected_template_id is not None
            and declared_id != expected_template_id
        ):
            errors.append(
                "frontmatter style_id must match directory "
                f"{expected_template_id!r}, got {declared_id!r}"
            )

    raw_kind = frontmatter.get("kind")
    declared_kind = raw_kind.strip() if isinstance(raw_kind, str) else ""
    if declared_kind != "style":
        errors.append(
            "frontmatter kind must be 'style', "
            f"got {declared_kind!r}"
        )

    raw_summary = frontmatter.get("summary")
    if not isinstance(raw_summary, str) or not _style_value_is_substantive(
        raw_summary
    ):
        errors.append("frontmatter summary must be a non-empty string")

    non_string_fields = [key for key in frontmatter if not isinstance(key, str)]
    if non_string_fields:
        errors.append("style frontmatter field names must be strings")
    unexpected_fields = sorted(
        key
        for key in frontmatter
        if isinstance(key, str) and key not in _STYLE_ALLOWED_FRONTMATTER_FIELDS
    )
    if unexpected_fields:
        errors.append(
            "style frontmatter contains unsupported field(s): "
            + ", ".join(unexpected_fields)
        )

    keywords = frontmatter.get("keywords")
    if (
        not isinstance(keywords, list)
        or not 3 <= len(keywords) <= 5
        or not all(
            isinstance(item, str) and _style_value_is_substantive(item)
            for item in keywords
        )
    ):
        errors.append(
            "frontmatter keywords must contain 3-5 non-empty strings"
        )
    elif len({item.strip().casefold() for item in keywords}) != len(keywords):
        errors.append("frontmatter keywords must be unique")

    if pages and not _has_qualified_roster_spec(template_dir):
        errors.append(
            "style workspaces must not contain page SVGs without a sibling "
            "Layout or Deck owner: "
            + ", ".join(f"{page}.svg" for page in pages)
        )

    # The one-file packaging rule describes a workspace whose templates/ Style
    # owns alone. A project root shares that directory with other kinds, so the
    # rule there is only that Style itself contributes nothing but its spec.
    if (template_dir / "design_spec.md").is_file():
        unexpected_source_entries = sorted(
            path.relative_to(template_dir).as_posix()
            + ("/" if path.is_dir() else "")
            for path in template_dir.rglob("*")
            if path.relative_to(template_dir).as_posix() != "design_spec.md"
        )
        if unexpected_source_entries:
            errors.append(
                "style workspaces must contain only templates/design_spec.md; "
                "unexpected template entry(s): "
                + ", ".join(unexpected_source_entries)
            )

    if expected_template_id is not None:
        unexpected_workspace_entries = sorted(
            path.relative_to(template_root).as_posix()
            + ("/" if path.is_dir() else "")
            for path in template_root.rglob("*")
            if path.relative_to(template_root).as_posix()
            not in {"templates", "templates/design_spec.md"}
        )
        if unexpected_workspace_entries:
            errors.append(
                "library Style workspaces must contain only "
                "templates/design_spec.md; unexpected workspace entry(s): "
                + ", ".join(unexpected_workspace_entries)
            )

    h1_headings = re.findall(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    if len(h1_headings) != 1:
        errors.append(
            "style body must contain exactly one document-title H1; got "
            f"{len(h1_headings)}"
        )

    expected_headings = [
        f"{numeral}. {title}" for numeral, title in _STYLE_REQUIRED_SECTIONS
    ]
    actual_headings = re.findall(r"^##\s+(.+?)\s*$", body, re.MULTILINE)
    if actual_headings != expected_headings:
        errors.append(
            "style body must contain exactly the required I-VII sections in "
            "order; got: " + (", ".join(actual_headings) or "none")
        )

    nested_headings = re.findall(
        r"^(#{3,6})\s+(.+?)\s*$",
        body,
        re.MULTILINE,
    )
    unexpected_nested_headings = [
        f"{marks} {title}"
        for marks, title in nested_headings
        if len(marks) != 3
        or title not in {"Fallback Color Scheme", "Fallback Typography"}
    ]
    if unexpected_nested_headings:
        errors.append(
            "style body contains unsupported nested heading(s): "
            + ", ".join(unexpected_nested_headings)
        )
    allowed_h3 = [title for marks, title in nested_headings if len(marks) == 3]
    if len(allowed_h3) != len(set(allowed_h3)):
        errors.append("style fallback subsections must not be repeated")

    for title in _STYLE_FORBIDDEN_SECTIONS:
        if re.search(
            rf"^#{{1,6}}\s+(?:[IVX]+\.\s+)?{re.escape(title)}\s*$",
            body,
            re.MULTILINE,
        ):
            errors.append(f"style scope must not declare section: {title}")

    for section_title, labels in _STYLE_REQUIRED_FIELDS.items():
        section_name = section_title.split(". ", 1)[1]
        if _numbered_section(body, section_name) is None:
            continue
        for label in labels:
            value = _extract_section_field(body, section_title, [label])
            if not _style_value_is_substantive(value):
                errors.append(
                    f"{section_title} must declare a non-empty {label} field"
                )

    role_section = _numbered_section(body, "Page Role Vocabulary") or ""
    role_rows = [
        row
        for row in _markdown_table_rows(role_section)
        if row and row[0].casefold() != "role"
    ]
    if not any(
        len(row) >= 4
        and all(_style_value_is_substantive(cell) for cell in row[:4])
        for row in role_rows
    ):
        errors.append(
            "III. Page Role Vocabulary must contain at least one complete "
            "four-column role row"
        )

    for (
        section_title,
        preferred_label,
        behavior_label,
        references_label,
        catalog_dir,
    ) in _STYLE_CUSTOM_FIELDS:
        preferred = _extract_section_field(
            body,
            section_title,
            [preferred_label],
        )
        behavior = _extract_section_field(
            body,
            section_title,
            [behavior_label],
        )
        references = _extract_section_field(
            body,
            section_title,
            [references_label],
        )
        preferred_text = _clean_field_value(preferred or "")
        catalog_ids = set(json.loads((catalog_dir / "catalog.json").read_text(encoding="utf-8")))
        is_custom = bool(
            re.match(r"^custom(?:\b|\s*[:—-])", preferred_text, re.IGNORECASE)
        )
        if is_custom and not _style_value_is_substantive(behavior):
            errors.append(
                f"{behavior_label} is required when {preferred_label} is custom"
            )
        if not is_custom and _style_value_is_substantive(behavior):
            errors.append(
                f"{behavior_label} is allowed only when {preferred_label} is custom"
            )
        if not is_custom and _style_value_is_substantive(references):
            errors.append(
                f"{references_label} is allowed only when "
                f"{preferred_label} is custom"
            )
        if (
            _style_value_is_substantive(preferred)
            and not is_custom
            and preferred_text not in catalog_ids
        ):
            errors.append(
                f"{preferred_label} references unknown catalog id "
                f"{preferred_text!r}"
            )
        if is_custom and _style_value_is_substantive(references):
            catalog_references = [
                _clean_field_value(item)
                for item in (references or "").split(",")
            ]
            if any(not item for item in catalog_references):
                errors.append(
                    f"{references_label} must be a comma-separated list of "
                    "catalog ids"
                )
            duplicates = sorted(
                item
                for item in set(catalog_references)
                if catalog_references.count(item) > 1
            )
            if duplicates:
                errors.append(
                    f"{references_label} repeats catalog id(s): "
                    + ", ".join(duplicates)
                )
            unknown_references = sorted(
                item
                for item in set(catalog_references)
                if item == "custom" or item not in catalog_ids
            )
            if unknown_references:
                errors.append(
                    f"{references_label} references unknown catalog id(s): "
                    + ", ".join(unknown_references)
                )

    visual_section = _numbered_section(body, "Visual System Defaults") or ""
    fallback_colors = _markdown_subsection(body, "Fallback Color Scheme")
    if fallback_colors is not None:
        if "### Fallback Color Scheme" not in visual_section:
            errors.append("Fallback Color Scheme must appear under section V")
        color_rows = [
            row
            for row in _markdown_table_rows(fallback_colors)
            if row and row[0].casefold() != "role"
        ]
        if not color_rows:
            errors.append("Fallback Color Scheme must contain at least one row")
        for row in color_rows:
            if (
                len(row) < 3
                or not _style_value_is_substantive(row[0])
                or _HEX_COLOR_RE.fullmatch(row[1].strip("` ")) is None
                or not _style_value_is_substantive(row[2])
            ):
                errors.append(
                    "Fallback Color Scheme rows must be Role | #RRGGBB | Purpose"
                )
                break

    fallback_typography = _markdown_subsection(body, "Fallback Typography")
    if fallback_typography is not None:
        if "### Fallback Typography" not in visual_section:
            errors.append("Fallback Typography must appear under section V")
        typography_rows = [
            row
            for row in _markdown_table_rows(fallback_typography)
            if row and row[0].casefold() != "role"
        ]
        if not any(
            len(row) >= 4
            and all(_style_value_is_substantive(cell) for cell in row[:4])
            for row in typography_rows
        ):
            errors.append(
                "Fallback Typography must contain at least one complete "
                "four-column row"
            )

    review_section = _numbered_section(body, "Review Focus") or ""
    if review_section.count(_STYLE_REVIEW_TRIGGER_MARKER) != 1:
        errors.append(
            "VII. Review Focus must contain exactly one "
            f"{_STYLE_REVIEW_TRIGGER_MARKER} marker"
        )
    review_items = re.findall(r"^[-*]\s+(.+?)\s*$", review_section, re.MULTILINE)
    if not any(_style_value_is_substantive(item) for item in review_items):
        errors.append("VII. Review Focus must contain at least one check")

    for label in _STYLE_FORBIDDEN_FIELDS:
        if _style_field_is_declared(body, label):
            errors.append(f"style scope must not declare field: {label}")

    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise SpecParseError(f"invalid style specification:\n{details}")

