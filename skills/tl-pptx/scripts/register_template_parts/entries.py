"""Upstream template registration: entries responsibilities."""
from __future__ import annotations

from .common import (
    KIND_CONFIG,
    OrderedDict,
    Path,
    SpecParseError,
)

def _extract_entry(
    kind: str,
    template_id: str | None,
    template_dir: Path,
) -> dict:
    """Build the index entry + extras for a single template."""
    from .brand import (
        _validate_brand_spec,
    )
    from .specification import (
        _extract_primary_color,
        _extract_section_field,
        _list_pages,
        _read_spec,
        _summary_from_use_cases,
        _template_content_dir,
    )
    from .style import (
        _validate_style_spec,
    )
    from .svg import (
        _validate_svg_template_spec,
    )
    template_root = template_dir
    template_dir = _template_content_dir(template_root)
    if template_id is not None:
        exact_spec = template_dir / "design_spec.md"
        if not exact_spec.is_file():
            raise SpecParseError(
                "library workspaces require templates/design_spec.md; "
                "kind-qualified specs belong only to shared project roots"
            )
    spec_path = _resolve_spec_path(template_dir, kind)

    frontmatter, body = _read_spec(spec_path)
    fm = frontmatter or {}

    if fm.get(f"{kind}_id") == "TODO" or "<!-- TODO -->" in body:
        raise SpecParseError(
            "incomplete Design Spec skeleton: finish the TODO identity and design "
            "sections before registration, then rerun --dry-run"
        )

    declared_kind = fm.get("kind")
    if declared_kind not in (None, kind):
        raise SpecParseError(
            f"design_spec.md frontmatter declares kind={declared_kind!r}; "
            f"expected kind={kind!r} — use --kind {declared_kind} instead"
        )

    raw_summary = fm.get("summary")
    summary = raw_summary.strip() if isinstance(raw_summary, str) else ""
    if not summary:
        section_title = (
            "I. Brand Overview" if kind == "brand" else "I. Template Overview"
        )
        summary = (_summary_from_use_cases(
            _extract_section_field(body, section_title, ["Use Cases", "Use cases"])
        ) or "").strip()

    pages = _list_pages(template_dir)
    primary_color = fm.get("primary_color") or _extract_primary_color(body) or ""
    resolved_template_id = (
        template_id
        or str(fm.get(KIND_CONFIG[kind]["id_key"]) or "").strip()
        or template_root.name
    )

    if kind == "brand":
        _validate_brand_spec(
            template_id,
            template_root,
            template_dir,
            fm,
            body,
            pages,
        )
        entry = OrderedDict(
            summary=summary,
            primary_color=str(primary_color),
        )
    elif kind == "style":
        _validate_style_spec(
            template_id,
            template_root,
            template_dir,
            fm,
            body,
            pages,
        )
        entry = OrderedDict(
            summary=summary,
            keywords=[item.strip() for item in fm["keywords"]],
        )
    elif kind == "layout":
        if template_id is None:
            raise SpecParseError("layout validation requires an expected layout_id")
        _validate_svg_template_spec(
            kind,
            template_id,
            template_dir,
            fm,
            pages,
        )
        page_types = fm["page_types"]
        entry = OrderedDict(
            summary=summary,
            canvas_format=str(fm["canvas_format"]),
            page_count=int(fm["page_count"]),
            page_types=list(page_types),
        )
    elif kind == "deck":
        if template_id is None:
            raise SpecParseError("deck validation requires an expected deck_id")
        _validate_svg_template_spec(
            kind,
            template_id,
            template_dir,
            fm,
            pages,
        )
        entry = OrderedDict(
            summary=summary,
            canvas_format=str(fm["canvas_format"]),
            page_count=int(fm["page_count"]),
            primary_color=str(primary_color),
        )
    else:
        raise SpecParseError(f"unknown kind {kind!r}")

    extras = OrderedDict(
        pages=pages,
        primary_color=str(primary_color),
        page_prefix="templates/",
        preview=(
            f"exports/{resolved_template_id}_template_preview.pptx"
            if (
                template_root
                / "exports"
                / f"{resolved_template_id}_template_preview.pptx"
            ).is_file()
            else ""
        ),
    )
    return {"entry": entry, "extras": extras}


def _resolve_spec_path(template_dir: Path, kind: str) -> Path:
    """Return the Design Spec one kind owns inside a template source directory.

    A library workspace keeps the exact ``design_spec.md`` because
    ``<kind_dir>/<template_id>/`` already names its kind and id. A project
    workspace root has no such parent, so it keeps
    ``design_spec.<kind>.<id>.md`` and may hold one spec per kind side by side.
    """
    from .specification import (
        _validate_spec_shape,
    )
    qualified = _validate_spec_shape(template_dir)
    exact = template_dir / "design_spec.md"
    if exact.is_file():
        return exact
    matches = sorted(
        path for path, declared_kind in qualified if declared_kind == kind
    )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SpecParseError(
            f"missing design_spec.md or design_spec.{kind}.<id>.md in {template_dir}"
        )
    raise SpecParseError(
        f"{template_dir} declares kind {kind!r} more than once: "
        + ", ".join(path.name for path in matches)
    )


def validate_brand_workspace(template_root: str | Path) -> dict:
    """Validate a portable Brand workspace without registering it.

    This is the project-scope entry used by ``svg_quality_checker.py
    --template-mode``. Library registration calls the same extraction path with
    an expected directory id, so both scopes share one Brand schema authority.
    """
    return _extract_entry("brand", None, Path(template_root))


def validate_style_workspace(template_root: str | Path) -> dict:
    """Validate a portable Style workspace without registering it.

    Global library roots also enforce directory identity and the one-file
    package boundary. Project roots keep their unrelated initialized-project
    scaffolding out of the Style contract.
    """
    from .library import (
        get_library_scope,
    )
    root = Path(template_root)
    expected_id = root.name if get_library_scope(root, "style") is not None else None
    return _extract_entry("style", expected_id, root)

