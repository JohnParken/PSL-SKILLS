"""Upstream template registration: svg responsibilities."""
from __future__ import annotations

from .common import (
    CANVAS_FORMATS,
    ET,
    KIND_CONFIG,
    Path,
    SpecParseError,
    _HEX_COLOR_RE,
    _SPEC_NAME_RE,
)

def _validate_svg_template_spec(
    kind: str,
    template_id: str,
    template_dir: Path,
    frontmatter: dict,
    pages: list[str],
    *,
    validate_payload: bool = True,
) -> None:
    """Validate Layout/Deck metadata and, when active, its SVG payload."""
    from .specification import (
        _derive_page_types,
    )
    errors: list[str] = []
    id_key = KIND_CONFIG[kind]["id_key"]
    declared_id = str(frontmatter.get(id_key) or "").strip()
    if declared_id != template_id:
        errors.append(
            f"frontmatter {id_key} must match directory {template_id!r}, "
            f"got {declared_id!r}"
        )
    declared_kind = str(frontmatter.get("kind") or "").strip()
    if declared_kind != kind:
        errors.append(
            f"frontmatter kind must be {kind!r}, got {declared_kind!r}"
        )
    if not str(frontmatter.get("summary") or "").strip():
        errors.append("frontmatter summary must be non-empty")
    if not pages:
        errors.append(f"{kind} workspace must contain at least one template SVG")

    raw_page_count = frontmatter.get("page_count")
    if isinstance(raw_page_count, bool) or not isinstance(raw_page_count, int):
        errors.append("frontmatter page_count must be an integer")
    elif raw_page_count != len(pages):
        errors.append(
            f"frontmatter page_count is {raw_page_count}, but templates/ "
            f"contains {len(pages)} SVG files"
        )

    canvas_format = str(frontmatter.get("canvas_format") or "").strip()
    canvas = CANVAS_FORMATS.get(canvas_format)
    if canvas is None:
        errors.append(
            "frontmatter canvas_format must be one of: "
            + ", ".join(sorted(CANVAS_FORMATS))
        )
    else:
        expected_canvas_fields = {
            "canvas_width": canvas["width"],
            "canvas_height": canvas["height"],
            "canvas_viewbox": canvas["viewbox"],
        }
        for field, expected in expected_canvas_fields.items():
            actual = frontmatter.get(field)
            if str(actual) != str(expected):
                errors.append(
                    f"frontmatter {field} must be {expected!r} for "
                    f"{canvas_format}, got {actual!r}"
                )

    if frontmatter.get("native_structure_mode") != "structured":
        errors.append(
            "frontmatter native_structure_mode must be 'structured'"
        )
    if frontmatter.get("replication_mode") not in {
        "standard",
        "fidelity",
        "mirror",
    }:
        errors.append(
            "frontmatter replication_mode must be standard, fidelity, or mirror"
        )

    if kind == "layout":
        raw_page_types = frontmatter.get("page_types")
        expected_page_types = _derive_page_types(pages)
        if not isinstance(raw_page_types, list) or not all(
            isinstance(item, str) and item.strip()
            for item in raw_page_types
        ):
            errors.append("frontmatter page_types must be a non-empty string list")
        elif raw_page_types != expected_page_types:
            errors.append(
                "frontmatter page_types must exactly match the SVG filename "
                f"roster: expected {expected_page_types!r}, got {raw_page_types!r}"
            )
    else:
        primary_color = str(frontmatter.get("primary_color") or "").strip()
        if _HEX_COLOR_RE.fullmatch(primary_color) is None:
            errors.append(
                "frontmatter primary_color must use #RRGGBB, "
                f"got {primary_color!r}"
            )

    svg_paths = [template_dir / f"{page}.svg" for page in pages]
    if validate_payload and canvas is not None:
        expected_viewbox = str(canvas["viewbox"])
        for svg_path in svg_paths:
            try:
                root = ET.parse(svg_path).getroot()
            except (OSError, ET.ParseError) as exc:
                errors.append(f"{svg_path.name} is not valid SVG XML: {exc}")
                continue
            actual_canvas = (
                root.get("width"),
                root.get("height"),
                root.get("viewBox"),
            )
            expected_canvas = (
                str(canvas["width"]),
                str(canvas["height"]),
                expected_viewbox,
            )
            if actual_canvas != expected_canvas:
                errors.append(
                    f"{svg_path.name} canvas is {actual_canvas!r}, expected "
                    f"{expected_canvas!r}"
                )

    if validate_payload and svg_paths:
        try:
            from svg_to_pptx.pptx_package.template_structure import (
                TemplateStructureError,
                parse_template_slides,
            )
        except ImportError as exc:
            errors.append(f"structured SVG roster is invalid: {exc}")
        else:
            try:
                parse_template_slides(svg_paths)
            except TemplateStructureError as exc:
                errors.append(f"structured SVG roster is invalid: {exc}")

    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise SpecParseError(f"invalid {kind} specification:\n{details}")


def validate_shadowed_deck_spec(
    spec_path: str | Path,
    declared_pages: list[str],
) -> None:
    """Validate a Deck spec whose SVG roster is overridden by Layout."""
    from .specification import (
        validate_qualified_spec_identity,
    )
    path = Path(spec_path)
    match = _SPEC_NAME_RE.fullmatch(path.name)
    if match is None or match.group("kind") != "deck":
        raise SpecParseError(
            "shadowed Deck validation requires design_spec.deck.<id>.md"
        )
    _kind, filename_id, frontmatter, _body = validate_qualified_spec_identity(
        path
    )
    _validate_svg_template_spec(
        "deck",
        filename_id,
        path.parent,
        frontmatter,
        declared_pages,
        validate_payload=False,
    )

