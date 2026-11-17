"""Upstream template registration: spec responsibilities."""
from __future__ import annotations

from .common import (
    KIND_CONFIG,
    Path,
    SpecParseError,
    _SPEC_NAME_RE,
    discover_slide_svgs,
    re,
    yaml,
)

def _read_spec(spec_path: Path) -> tuple[dict | None, str]:
    """Split YAML frontmatter from the body. Returns ``(frontmatter, body)``."""
    text = spec_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    fm_block = text[4:end]
    body = text[end + 5:]
    if yaml is None:
        raise SpecParseError(
            "design_spec.md has YAML frontmatter but PyYAML is not installed; "
            "install pyyaml or remove the frontmatter."
        )
    try:
        data = yaml.safe_load(fm_block) or {}
    except yaml.YAMLError as exc:
        raise SpecParseError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecParseError("YAML frontmatter must be a mapping")
    return data, body


def _extract_section_field(body: str, section_title: str, labels: list[str]) -> str | None:
    section_re = re.compile(
        rf"^##\s+{re.escape(section_title)}\b.*?(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    section_match = section_re.search(body)
    if section_match is None:
        return None
    section = section_match.group(0)

    for label in labels:
        row = re.search(
            rf"^\|\s*\*?\*?{re.escape(label)}\*?\*?\s*\|\s*(.+?)\s*\|",
            section, re.MULTILINE | re.IGNORECASE,
        )
        if row:
            return _clean_field_value(row.group(1))

        bullet = re.search(
            rf"^[-*]\s*\*?\*?{re.escape(label)}\*?\*?\s*[:：]\s*(.+?)\s*$",
            section, re.MULTILINE | re.IGNORECASE,
        )
        if bullet:
            return _clean_field_value(bullet.group(1))
    return None


def _clean_field_value(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^[`*_]+", "", value)
    value = re.sub(r"[`*_]+$", "", value)
    return value.strip()


def _find_first_color(section: str) -> str | None:
    match = re.search(r"`(#[0-9A-Fa-f]{3,8})`", section)
    return match.group(1).upper() if match else None


def _extract_primary_color(body: str) -> str | None:
    section_match = re.search(
        r"^##\s+[IVX]+\.\s+Color Scheme\b.*?(?=^##\s+|\Z)",
        body, re.MULTILINE | re.DOTALL,
    )
    if section_match is None:
        return None
    return _find_first_color(section_match.group(0))


def _summary_from_use_cases(use_cases: str | None) -> str | None:
    if not use_cases:
        return None
    cleaned = use_cases.strip().rstrip(".")
    if not cleaned:
        return None
    return f"{cleaned}."


def _has_kind_qualified_spec(directory: Path) -> bool:
    """Report whether a directory holds any ``design_spec.<kind>.<id>.md``."""
    if not directory.is_dir():
        return False
    return any(
        _SPEC_NAME_RE.fullmatch(path.name)
        for path in directory.glob("design_spec.*.md")
    )


def _qualified_template_specs(directory: Path) -> list[tuple[Path, str]]:
    """Return kind-qualified specs and their filename-declared kinds."""
    if not directory.is_dir():
        return []
    specs = []
    for path in sorted(directory.glob("design_spec.*.md")):
        match = _SPEC_NAME_RE.fullmatch(path.name)
        if match is not None:
            specs.append((path, match.group("kind")))
    return specs


def validate_qualified_spec_identity(
    spec_path: str | Path,
) -> tuple[str, str, dict, str]:
    """Match one qualified filename's kind and id to its frontmatter."""
    path = Path(spec_path)
    match = _SPEC_NAME_RE.fullmatch(path.name)
    if match is None:
        raise SpecParseError(
            "qualified Design Spec must be named "
            "design_spec.<kind>.<id>.md: "
            f"{path.name}"
        )

    filename_kind = match.group("kind")
    filename_id = match.group("id")
    frontmatter, body = _read_spec(path)
    fm = frontmatter or {}
    declared_kind = str(fm.get("kind") or "").strip()
    id_key = KIND_CONFIG[filename_kind]["id_key"]
    declared_id = str(fm.get(id_key) or "").strip()
    errors: list[str] = []
    if declared_kind != filename_kind:
        errors.append(
            f"filename kind {filename_kind!r} must match frontmatter kind "
            f"{declared_kind!r}"
        )
    if declared_id != filename_id:
        errors.append(
            f"filename id {filename_id!r} must match frontmatter {id_key} "
            f"{declared_id!r}"
        )
    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise SpecParseError(
            f"invalid qualified Design Spec identity ({path.name}):\n{details}"
        )
    return filename_kind, filename_id, fm, body


def _validate_spec_shape(template_dir: Path) -> list[tuple[Path, str]]:
    """Reject ambiguous project-spec naming and duplicate kind ownership."""
    exact = template_dir / "design_spec.md"
    qualified = _qualified_template_specs(template_dir)
    if exact.is_file() and qualified:
        raise SpecParseError(
            "design_spec.md and design_spec.<kind>.<id>.md cannot share "
            f"{template_dir}; rename the bare spec to its kind-qualified name"
        )
    kinds = [kind for _path, kind in qualified]
    duplicate_kinds = sorted({
        kind for kind in kinds if kinds.count(kind) > 1
    })
    if duplicate_kinds:
        raise SpecParseError(
            f"{template_dir} declares the same kind more than once: "
            + ", ".join(duplicate_kinds)
        )
    for path, _kind in qualified:
        validate_qualified_spec_identity(path)
    return qualified


def _has_qualified_roster_spec(template_dir: Path) -> bool:
    """Return whether a project directory declares a structural kind."""
    return any(
        kind in {"layout", "deck"}
        for _path, kind in _qualified_template_specs(template_dir)
    )


def _template_content_dir(template_root: Path) -> Path:
    """Resolve the only canonical template source directory."""
    nested = template_root / "templates"
    if (nested / "design_spec.md").is_file() or _has_kind_qualified_spec(nested):
        return nested
    raise SpecParseError(
        "missing templates/design_spec.md or "
        f"templates/design_spec.<kind>.<id>.md in {template_root}"
    )


def _list_pages(template_dir: Path) -> list[str]:
    """Page stems in roster order, the order the preview and export use."""
    return [p.stem for p in discover_slide_svgs(template_dir)]


def _derive_page_types(pages: list[str]) -> list[str]:
    """Derive canonical page-type list from SVG filenames (strips leading 'NN_')."""
    types: list[str] = []
    seen: set[str] = set()
    for p in pages:
        m = re.match(r"^\d+[a-z]?_(.+)$", p)
        role = m.group(1) if m else p
        if role not in seen:
            seen.add(role)
            types.append(role)
    return types


def _numbered_section(body: str, title: str) -> str | None:
    match = re.search(
        rf"^##\s+[IVX]+\.\s+{re.escape(title)}\s*$.*?(?=^##\s+|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(0) if match else None


def _markdown_subsection(body: str, title: str) -> str | None:
    match = re.search(
        rf"^###\s+{re.escape(title)}\s*$.*?(?=^#{{2,3}}\s+|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(0) if match else None


def _markdown_table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def _style_value_is_substantive(value: str | None) -> bool:
    if value is None:
        return False
    cleaned = _clean_field_value(value)
    if not cleaned or re.fullmatch(r"<[^>]+>", cleaned):
        return False
    return cleaned.casefold() not in {"tbd", "todo", "n/a", "none", "-", "—"}


def _style_field_is_declared(body: str, label: str) -> bool:
    escaped = re.escape(label)
    return bool(
        re.search(
            rf"^\s*(?:>\s*)?(?:[-*]\s*)?"
            rf"\*{{0,2}}{escaped}\*{{0,2}}\s*[:：]",
            body,
            re.MULTILINE | re.IGNORECASE,
        )
        or re.search(
            rf"^\|\s*\*{{0,2}}{escaped}\*{{0,2}}\s*\|",
            body,
            re.MULTILINE | re.IGNORECASE,
        )
    )

