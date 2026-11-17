"""Upstream template registration: brand responsibilities."""
from __future__ import annotations

from .common import (
    Path,
    SpecParseError,
    _BRAND_ALLOWED_FRONTMATTER_FIELDS,
    _BRAND_ASSET_REF_RE,
    _BRAND_FORBIDDEN_SECTIONS,
    _BRAND_PROVENANCE_VALUES,
    _BRAND_REQUIRED_SECTIONS,
    _HEX_COLOR_RE,
    re,
)

def _validate_brand_spec(
    expected_template_id: str | None,
    template_root: Path,
    template_dir: Path,
    frontmatter: dict,
    body: str,
    pages: list[str],
) -> None:
    """Reject brand workspaces that cannot be locked as portable identity truth.

    Args:
        expected_template_id: Registry key to match in library scope. Project
            workspaces pass ``None`` because their root name is the project id,
            not the portable brand id.
        template_root: Brand workspace root containing assets and templates.
        template_dir: Directory containing ``design_spec.md`` and any page SVGs.
        frontmatter: Parsed design-spec frontmatter.
        body: Markdown content after the frontmatter block.
        pages: SVG page stems discovered beside the design spec.
    """
    from .specification import (
        _has_qualified_roster_spec,
        _numbered_section,
    )
    errors: list[str] = []

    declared_id = str(frontmatter.get("brand_id") or "").strip()
    if not declared_id:
        errors.append("frontmatter brand_id must be non-empty")
    elif expected_template_id is not None and declared_id != expected_template_id:
        errors.append(
            "frontmatter brand_id must match directory "
            f"{expected_template_id!r}, "
            f"got {declared_id!r}"
        )

    declared_kind = str(frontmatter.get("kind") or "").strip()
    if declared_kind != "brand":
        errors.append(
            "frontmatter kind must be 'brand', "
            f"got {declared_kind!r}"
        )

    if not str(frontmatter.get("summary") or "").strip():
        errors.append("frontmatter summary must be non-empty")

    unexpected_fields = sorted(
        set(frontmatter) - _BRAND_ALLOWED_FRONTMATTER_FIELDS
    )
    if unexpected_fields:
        errors.append(
            "brand frontmatter contains non-identity field(s): "
            + ", ".join(unexpected_fields)
        )

    # Project pages are valid only when one sibling Layout or Deck owns them.
    if pages and not _has_qualified_roster_spec(template_dir):
        errors.append(
            "brand workspaces must not contain page SVGs under templates/: "
            + ", ".join(f"{page}.svg" for page in pages)
        )

    for numeral, title in _BRAND_REQUIRED_SECTIONS:
        if re.search(
            rf"^##\s+{numeral}\.\s+{re.escape(title)}\s*$",
            body,
            re.MULTILINE,
        ) is None:
            errors.append(f"missing required section: {numeral}. {title}")

    for title in _BRAND_FORBIDDEN_SECTIONS:
        if re.search(
            rf"^##\s+(?:[IVX]+\.\s+)?{re.escape(title)}\s*$",
            body,
            re.MULTILINE,
        ):
            errors.append(f"brand scope must not declare section: {title}")

    declared_primary = str(frontmatter.get("primary_color") or "").strip()
    if _HEX_COLOR_RE.fullmatch(declared_primary) is None:
        errors.append(
            "frontmatter primary_color must use #RRGGBB, "
            f"got {declared_primary!r}"
        )

    color_section = _numbered_section(body, "Color Scheme") or ""
    primary_rows: list[str] = []
    for line in color_section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        role = cells[0].strip("` ")
        raw_color = cells[1].strip("` ")
        if role.lower() == "role" or re.fullmatch(r":?-+:?", role):
            continue
        if _HEX_COLOR_RE.fullmatch(raw_color) is None:
            errors.append(
                f"color row {role!r} must use #RRGGBB, "
                f"got {raw_color!r}"
            )
            continue
        if role.lower() == "primary":
            primary_rows.append(raw_color.upper())
        provenance = cells[2].strip("` ").lower()
        if provenance not in _BRAND_PROVENANCE_VALUES:
            errors.append(
                f"color {raw_color} must declare provenance as "
                "fact, approx, or user"
            )

    if not primary_rows:
        errors.append("Color Scheme must declare one primary color row")
    elif len(primary_rows) > 1:
        errors.append("Color Scheme must declare only one primary color row")
    elif (
        _HEX_COLOR_RE.fullmatch(declared_primary)
        and primary_rows[0] != declared_primary.upper()
    ):
        errors.append(
            "Color Scheme primary must match frontmatter primary_color: "
            f"{primary_rows[0]} != {declared_primary.upper()}"
        )

    root = template_root.resolve()
    for raw_ref in sorted(set(_BRAND_ASSET_REF_RE.findall(body))):
        asset = (template_dir / raw_ref).resolve()
        try:
            asset.relative_to(root)
        except ValueError:
            errors.append(f"asset reference escapes brand workspace: {raw_ref}")
            continue
        if not asset.is_file():
            errors.append(f"referenced brand asset does not exist: {raw_ref}")

    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise SpecParseError(f"invalid brand specification:\n{details}")

