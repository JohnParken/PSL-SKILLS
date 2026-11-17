"""
PPT Master - Template Structure Metadata

Parse and validate explicit SVG metadata consumed by structured PPTX export.

Usage:
    Imported by svg_to_pptx.pptx_package.builder and svg_quality_checker.py.

Examples:
    parse_template_slides([Path("projects/demo/svg_output/01_cover.svg")])

Dependencies:
    None (only uses standard library)
"""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import re
from pathlib import Path

from .template_structure import (
    PptxLayoutDefinition,
    PptxLayoutReference,
    PptxMasterReference,
    PptxPrototypeReference,
    PptxStructureLock,
)

def load_pptx_structure_lock(project_path: Path) -> PptxStructureLock | None:
    """Load optional native-structure sections from spec_lock.md."""
    from .template_structure import (
        PPTX_STRUCTURE_MODES,
        PptxLayoutDefinition,
        PptxLayoutReference,
        PptxMasterReference,
        PptxPrototypeReference,
        PptxStructureLock,
        TEMPLATE_ADHERENCE_MODES,
        TEMPLATE_REUSE_SCOPES,
        TemplateStructureError,
        _LAYOUT_KEY_RE,
        _LOCK_PAGE_RE,
        _LOCK_ROW_RE,
        _MASTER_KEY_RE,
        _portable_project_file,
        _template_replication_mode,
        _template_svg_path,
    )
    lock_path = project_path / "spec_lock.md"
    if not lock_path.is_file():
        return None
    try:
        lines = lock_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TemplateStructureError(f"Cannot read {lock_path}: {exc}") from exc

    sections: dict[str, list[tuple[str, str]]] = {}
    current_section: str | None = None
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if line.startswith("## "):
            current_section = line[3:].strip()
            sections.setdefault(current_section, [])
            continue
        if current_section not in {
            "pptx_structure",
            "pptx_masters",
            "pptx_layouts",
            "page_pptx_layouts",
            "page_layouts",
        }:
            continue
        match = _LOCK_ROW_RE.fullmatch(line)
        if match:
            sections[current_section].append((
                match.group(1).strip(),
                match.group(2).strip(),
            ))
        elif re.match(r"^-\s+", line):
            raise TemplateStructureError(
                f"spec_lock.md {current_section} line {line_number} must use "
                "'- <key>: <value>' syntax"
            )

    structure_rows = sections.get("pptx_structure", [])
    master_rows = sections.get("pptx_masters", [])
    layout_rows = sections.get("pptx_layouts", [])
    page_layout_rows = sections.get("page_pptx_layouts", [])
    prototype_rows = sections.get("page_layouts", [])
    structure_section_present = "pptx_structure" in sections
    master_section_present = "pptx_masters" in sections
    layout_section_present = "pptx_layouts" in sections
    page_layout_section_present = "page_pptx_layouts" in sections
    prototype_section_present = "page_layouts" in sections
    if (
        not structure_rows
        and not master_rows
        and not layout_rows
        and not page_layout_rows
        and not prototype_rows
        and not structure_section_present
        and not master_section_present
        and not layout_section_present
        and not page_layout_section_present
        and not prototype_section_present
    ):
        return None
    mode_rows = [value.strip().lower() for key, value in structure_rows if key == "mode"]
    if len(mode_rows) != 1:
        raise TemplateStructureError(
            "spec_lock.md pptx_structure requires exactly one '- mode:' row"
        )
    mode = mode_rows[0]
    if mode not in PPTX_STRUCTURE_MODES:
        allowed = ", ".join(sorted(PPTX_STRUCTURE_MODES))
        raise TemplateStructureError(
            f"spec_lock.md pptx_structure.mode must be one of: {allowed}"
        )

    adherence_rows = [
        value.strip().lower()
        for key, value in structure_rows
        if key == "template_adherence"
    ]
    if len(adherence_rows) > 1:
        raise TemplateStructureError(
            "spec_lock.md pptx_structure allows at most one "
            "'- template_adherence:' row"
        )
    template_adherence = adherence_rows[0] if adherence_rows else None
    if template_adherence and template_adherence not in TEMPLATE_ADHERENCE_MODES:
        allowed = ", ".join(sorted(TEMPLATE_ADHERENCE_MODES))
        raise TemplateStructureError(
            "spec_lock.md pptx_structure.template_adherence must be one of: "
            f"{allowed}"
        )
    if mode == "preserve" and template_adherence == "adaptive":
        raise TemplateStructureError(
            "spec_lock.md preserve mode requires template_adherence: strict; "
            "adaptive template use must export through structured mode"
        )
    if template_adherence and mode not in {"structured", "preserve"}:
        raise TemplateStructureError(
            "spec_lock.md template_adherence is allowed only in structured or "
            "preserve mode"
        )

    reuse_scope_rows = [
        value.strip().lower()
        for key, value in structure_rows
        if key == "template_reuse_scope"
    ]
    if len(reuse_scope_rows) > 1:
        raise TemplateStructureError(
            "spec_lock.md pptx_structure allows at most one "
            "'- template_reuse_scope:' row"
        )
    template_reuse_scope = reuse_scope_rows[0] if reuse_scope_rows else None
    if (
        template_reuse_scope
        and template_reuse_scope not in TEMPLATE_REUSE_SCOPES
    ):
        allowed = ", ".join(sorted(TEMPLATE_REUSE_SCOPES))
        raise TemplateStructureError(
            "spec_lock.md pptx_structure.template_reuse_scope must be one of: "
            f"{allowed}"
        )
    if mode == "preserve" and template_reuse_scope:
        raise TemplateStructureError(
            "spec_lock.md preserve mode does not use template_reuse_scope; "
            "the source PPTX structure is already authoritative"
        )
    if mode == "flat" and template_reuse_scope not in {None, "style"}:
        raise TemplateStructureError(
            "spec_lock.md flat mode permits only template_reuse_scope: style"
        )
    if mode == "structured" and template_reuse_scope == "style":
        raise TemplateStructureError(
            "spec_lock.md template_reuse_scope: style requires mode: flat and "
            "must omit structured template mappings"
        )
    if any(key == "layout_strategy" for key, _value in structure_rows):
        raise TemplateStructureError(
            "spec_lock.md pptx_structure.layout_strategy is obsolete; SVG pages "
            "must declare their final structured Master/Layout contract directly"
        )

    source_rows = [
        value for key, value in structure_rows if key == "source_template"
    ]
    contract_rows = [
        value for key, value in structure_rows if key == "native_structure"
    ]
    source_template = None
    native_structure = None
    if mode == "preserve":
        if len(source_rows) != 1 or len(contract_rows) != 1:
            raise TemplateStructureError(
                "spec_lock.md preserve mode requires exactly one '- source_template:' "
                "row and one '- native_structure:' row"
            )
        source_template = _portable_project_file(
            project_path,
            source_rows[0],
            "source_template",
            ".pptx",
        )
        native_structure = _portable_project_file(
            project_path,
            contract_rows[0],
            "native_structure",
            ".json",
        )
    elif source_rows or contract_rows:
        raise TemplateStructureError(
            "spec_lock.md source_template/native_structure rows are allowed only "
            "when pptx_structure.mode is preserve"
        )

    masters: list[PptxMasterReference] = []
    seen_master_keys: set[str] = set()
    for master_key, raw_name in master_rows:
        master_name = raw_name.strip()
        if not _MASTER_KEY_RE.fullmatch(master_key):
            raise TemplateStructureError(
                f"spec_lock.md has invalid Master key {master_key!r}; use 1-64 "
                "characters, start with an ASCII letter or digit, and use only "
                "ASCII letters, digits, dots, underscores, or hyphens"
            )
        if master_key in seen_master_keys:
            raise TemplateStructureError(
                f"spec_lock.md pptx_masters repeats Master key {master_key!r}"
            )
        if not master_name:
            raise TemplateStructureError(
                f"spec_lock.md Master {master_key!r} has an empty name"
            )
        seen_master_keys.add(master_key)
        masters.append(PptxMasterReference(master_key, master_name))

    if mode == "structured":
        if not master_rows:
            raise TemplateStructureError(
                "spec_lock.md structured mode requires a non-empty pptx_masters section"
            )
    elif master_section_present:
        raise TemplateStructureError(
            "spec_lock.md pptx_masters is allowed only when "
            "pptx_structure.mode is structured"
        )

    prototypes: list[PptxPrototypeReference] = []
    seen_prototype_slides: set[int] = set()
    template_dir = (project_path / "templates").resolve()
    template_replication_mode = _template_replication_mode(template_dir)
    if mode != "structured" and prototype_section_present:
        raise TemplateStructureError(
            "spec_lock.md page_layouts section is allowed only when pptx_structure.mode "
            "is structured"
        )
    for page_key, raw_value in prototype_rows:
        page_match = _LOCK_PAGE_RE.fullmatch(page_key)
        if not page_match or int(page_match.group(1)) <= 0:
            raise TemplateStructureError(
                f"spec_lock.md page_layouts key {page_key!r} must be P<NN>"
            )
        slide_num = int(page_match.group(1))
        if slide_num in seen_prototype_slides:
            raise TemplateStructureError(
                f"spec_lock.md page_layouts repeats page P{slide_num:02d}"
            )
        seen_prototype_slides.add(slide_num)
        raw_basename = raw_value.strip()
        basename, svg_path = _template_svg_path(
            template_dir,
            raw_basename,
            f"page_layouts P{slide_num:02d}",
        )
        if basename.startswith("layout_"):
            raise TemplateStructureError(
                f"spec_lock.md page_layouts P{slide_num:02d} cannot select "
                f"obsolete definition-only template SVG {basename!r}; create "
                "and select a complete Slide prototype"
            )
        prototypes.append(PptxPrototypeReference(
            slide_num=slide_num,
            template_basename=basename,
            svg_path=svg_path,
            replication_mode=template_replication_mode,
        ))

    if mode == "structured" and template_adherence and not prototypes:
        raise TemplateStructureError(
            "spec_lock.md structured template use requires one page_layouts row per page"
        )
    if prototypes and not template_adherence:
        raise TemplateStructureError(
            "spec_lock.md page_layouts requires template_adherence: strict or adaptive"
        )
    if template_reuse_scope in {"mirror", "layout"} and not prototypes:
        raise TemplateStructureError(
            "spec_lock.md template_reuse_scope mirror/layout requires one "
            "page_layouts row per page"
        )
    if template_reuse_scope == "style" and prototypes:
        raise TemplateStructureError(
            "spec_lock.md template_reuse_scope: style must omit page_layouts"
        )
    if template_reuse_scope == "mirror":
        non_mirror = sorted({
            reference.template_basename
            for reference in prototypes
            if reference.replication_mode != "mirror"
        })
        if non_mirror:
            raise TemplateStructureError(
                "spec_lock.md template_reuse_scope: mirror requires a mirror "
                "template workspace; non-mirror prototype(s): "
                + ", ".join(non_mirror)
            )
    if mode == "structured" and template_reuse_scope is None and prototypes:
        # Backward compatibility: projects created before the explicit reuse
        # axis inherit their former behavior. Mirror workspaces stay literal;
        # standard/fidelity workspaces remain structural layout references.
        template_reuse_scope = (
            "mirror"
            if all(
                reference.replication_mode == "mirror"
                for reference in prototypes
            )
            else "layout"
        )

    layout_definitions: list[PptxLayoutDefinition] = []
    references: list[PptxLayoutReference] = []
    if mode == "structured":
        if not layout_rows:
            raise TemplateStructureError(
                "spec_lock.md structured mode requires a non-empty "
                "pptx_layouts definition section"
            )
        seen_layout_keys: set[str] = set()
        for layout_key, raw_value in layout_rows:
            if not _LAYOUT_KEY_RE.fullmatch(layout_key):
                raise TemplateStructureError(
                    f"spec_lock.md has invalid Layout key {layout_key!r}; use 1-64 "
                    "characters, start with an ASCII letter or digit, and use only "
                    "ASCII letters, digits, dots, underscores, or hyphens"
                )
            if layout_key in seen_layout_keys:
                raise TemplateStructureError(
                    f"spec_lock.md pptx_layouts repeats Layout key {layout_key!r}"
                )
            seen_layout_keys.add(layout_key)
            parts = [part.strip() for part in raw_value.split("|")]
            if len(parts) != 3 or not all(parts):
                raise TemplateStructureError(
                    f"spec_lock.md Layout {layout_key!r} must be "
                    "'<master_key> | <PowerPoint layout name> | "
                    "<P<NN> or template:<basename>>'"
                )
            master_key, layout_name, raw_source = parts
            if not _MASTER_KEY_RE.fullmatch(master_key):
                raise TemplateStructureError(
                    f"spec_lock.md Layout {layout_key!r} has invalid Master key "
                    f"{master_key!r}"
                )
            if master_key not in seen_master_keys:
                raise TemplateStructureError(
                    f"spec_lock.md Layout {layout_key!r} references undeclared "
                    f"Master {master_key!r}"
                )
            prototype_slide_num: int | None = None
            prototype_svg_path: Path | None = None
            page_match = _LOCK_PAGE_RE.fullmatch(raw_source)
            if page_match and int(page_match.group(1)) > 0:
                prototype_slide_num = int(page_match.group(1))
            elif raw_source.startswith("template:"):
                raw_basename = raw_source.split(":", 1)[1].strip()
                basename, prototype_svg_path = _template_svg_path(
                    template_dir,
                    raw_basename,
                    f"pptx_layouts Layout {layout_key!r}",
                )
                if basename.startswith("layout_"):
                    raise TemplateStructureError(
                        f"spec_lock.md Layout {layout_key!r} cannot use obsolete "
                        f"definition-only template SVG {basename!r}; use a "
                        "complete Slide prototype"
                    )
            else:
                raise TemplateStructureError(
                    f"spec_lock.md Layout {layout_key!r} prototype source must be "
                    f"P<NN> or template:<basename>; found {raw_source!r}"
                )
            layout_definitions.append(PptxLayoutDefinition(
                layout_key=layout_key,
                master_key=master_key,
                layout_name=layout_name,
                prototype_slide_num=prototype_slide_num,
                prototype_svg_path=prototype_svg_path,
            ))

        seen_slides: set[int] = set()
        for page_key, raw_value in page_layout_rows:
            page_match = _LOCK_PAGE_RE.fullmatch(page_key)
            if not page_match or int(page_match.group(1)) <= 0:
                raise TemplateStructureError(
                    f"spec_lock.md page_pptx_layouts key {page_key!r} must be P<NN>"
                )
            slide_num = int(page_match.group(1))
            if slide_num in seen_slides:
                raise TemplateStructureError(
                    "spec_lock.md page_pptx_layouts repeats page "
                    f"P{slide_num:02d}"
                )
            seen_slides.add(slide_num)
            layout_key = raw_value.strip()
            if not _LAYOUT_KEY_RE.fullmatch(layout_key):
                raise TemplateStructureError(
                    f"spec_lock.md P{slide_num:02d} has invalid Layout key "
                    f"{layout_key!r}"
                )
            if layout_key not in seen_layout_keys:
                raise TemplateStructureError(
                    f"spec_lock.md P{slide_num:02d} references undeclared Layout "
                    f"{layout_key!r}"
                )
            references.append(PptxLayoutReference(
                slide_num=slide_num,
                layout_key=layout_key,
            ))
        if not references:
            raise TemplateStructureError(
                "spec_lock.md structured mode requires one page_pptx_layouts "
                "assignment per generated page"
            )
        unused_masters = sorted(
            seen_master_keys - {
                definition.master_key for definition in layout_definitions
            }
        )
        if unused_masters:
            raise TemplateStructureError(
                "spec_lock.md pptx_masters contains Master key(s) without a "
                "Layout definition: " + ", ".join(unused_masters)
            )
    elif mode == "preserve":
        if page_layout_section_present:
            raise TemplateStructureError(
                "spec_lock.md page_pptx_layouts is reserved for structured mode; "
                "preserve mode maps source Layouts directly in pptx_layouts"
            )
        seen_slides: set[int] = set()
        for page_key, raw_value in layout_rows:
            page_match = _LOCK_PAGE_RE.fullmatch(page_key)
            if not page_match or int(page_match.group(1)) <= 0:
                raise TemplateStructureError(
                    f"spec_lock.md pptx_layouts key {page_key!r} must be P<NN>"
                )
            slide_num = int(page_match.group(1))
            if slide_num in seen_slides:
                raise TemplateStructureError(
                    f"spec_lock.md pptx_layouts repeats page P{slide_num:02d}"
                )
            seen_slides.add(slide_num)
            parts = [part.strip() for part in raw_value.split("|")]
            if len(parts) not in {1, 2} or not parts[0]:
                raise TemplateStructureError(
                    f"spec_lock.md P{slide_num:02d} preserve mapping must be "
                    "'<layout_key>' or '<layout_key> | <PowerPoint layout name>'"
                )
            layout_key = parts[0]
            if not _LAYOUT_KEY_RE.fullmatch(layout_key):
                raise TemplateStructureError(
                    f"spec_lock.md P{slide_num:02d} has invalid Layout key "
                    f"{layout_key!r}"
                )
            references.append(PptxLayoutReference(
                slide_num=slide_num,
                layout_key=layout_key,
                layout_name=parts[1] if len(parts) == 2 else None,
            ))
        if not references:
            raise TemplateStructureError(
                "spec_lock.md preserve mode requires one pptx_layouts row per page"
            )
    else:
        if layout_section_present or page_layout_section_present:
            raise TemplateStructureError(
                "spec_lock.md pptx_layouts/page_pptx_layouts sections are not "
                "allowed when pptx_structure.mode is flat"
            )
    return PptxStructureLock(
        mode=mode,
        template_reuse_scope=template_reuse_scope,
        template_adherence=template_adherence,
        masters=tuple(masters),
        layout_definitions=tuple(layout_definitions),
        layouts=tuple(sorted(references, key=lambda item: item.slide_num)),
        prototypes=tuple(sorted(prototypes, key=lambda item: item.slide_num)),
        source_template=source_template,
        native_structure=native_structure,
    )
