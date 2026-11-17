"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import (
    Path,
    PurePosixPath,
)
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import (
    quoteattr,
)
from ..drawingml.theme_fonts import (
    MasterTextStyleSpec,
    apply_master_text_style_spec,
)

from .builder import (
    PptxStructureContext,
)

def _prepare_flat_structure(
    extract_dir: Path,
    structure: PptxStructureContext,
    slide_count: int,
    master_text_style_spec: MasterTextStyleSpec | None,
    structure_name: str | None,
    *,
    verbose: bool = False,
) -> None:
    """Materialize one clean current-deck Master and Blank Layout for flat export."""
    from .builder import (
        PML_NS,
        _FLAT_SYSTEM_PLACEHOLDER_TYPES,
        _TOP_LEVEL_SHAPE_TAGS,
        _clean_flat_structure_part,
        _flat_placeholder_type,
        _flat_structure_name,
        _name_flat_themes,
        _prune_unused_slide_layouts,
        _read_slide_layout_targets,
        _resolve_package_target,
    )
    pruned = _prune_unused_slide_layouts(
        extract_dir,
        structure,
        slide_count,
        verbose=False,
    )
    live_structure = _read_slide_layout_targets(extract_dir, slide_count)
    layout_parts = {
        _resolve_package_target(
            f"ppt/slides/slide{slide_num}.xml",
            live_structure.slide_layout_target(slide_num),
        )
        for slide_num in range(1, slide_count + 1)
    }
    master_parts = set(live_structure.slide_master_parts.values())
    physical_layouts = {
        str(path.relative_to(extract_dir)).replace("\\", "/")
        for path in (extract_dir / "ppt" / "slideLayouts").glob("slideLayout*.xml")
    }
    physical_masters = {
        str(path.relative_to(extract_dir)).replace("\\", "/")
        for path in (extract_dir / "ppt" / "slideMasters").glob("slideMaster*.xml")
    }
    if len(layout_parts) != 1 or layout_parts != physical_layouts:
        raise RuntimeError(
            "Flat export must retain exactly one slide-referenced Blank Layout"
        )
    if len(master_parts) != 1 or master_parts != physical_masters:
        raise RuntimeError(
            "Flat export must retain exactly one slide-referenced Master"
        )

    identity = _flat_structure_name(structure_name)
    theme_name = identity
    master_name = f"{identity} — Master"
    layout_name = f"{identity} — Blank"
    master_path = extract_dir / next(iter(master_parts))
    layout_path = extract_dir / next(iter(layout_parts))
    removed_master_shapes, retained_master_placeholders = _clean_flat_structure_part(
        master_path,
        master_name,
        is_layout=False,
    )
    removed_layout_shapes, retained_layout_placeholders = _clean_flat_structure_part(
        layout_path,
        layout_name,
        is_layout=True,
    )
    theme_count = _name_flat_themes(extract_dir, theme_name)
    master_count = (
        apply_master_text_style_spec(extract_dir, master_text_style_spec)
        if master_text_style_spec is not None
        else 0
    )

    for part_path, expected_name, expected_layout in (
        (master_path, master_name, False),
        (layout_path, layout_name, True),
    ):
        root = ET.parse(part_path).getroot()
        common_slide = root.find(f"{{{PML_NS}}}cSld")
        if common_slide is None or common_slide.get("name") != expected_name:
            raise RuntimeError(
                f"Flat structure identity read-back failed: {part_path}"
            )
        shape_tree = common_slide.find(f"{{{PML_NS}}}spTree")
        if shape_tree is None:
            raise RuntimeError(f"Flat structure shell has no shape tree: {part_path}")
        actual_placeholder_types = tuple(
            sorted(
                _flat_placeholder_type(child) or ""
                for child in shape_tree
                if child.tag in _TOP_LEVEL_SHAPE_TAGS
            )
        )
        expected_placeholder_types = tuple(
            sorted(_FLAT_SYSTEM_PLACEHOLDER_TYPES)
        )
        if actual_placeholder_types != expected_placeholder_types:
            raise RuntimeError(
                "Flat structure shell must retain only one each of the date, "
                f"footer, and slide-number hooks: {part_path}"
            )
        if expected_layout and root.get("type") != "blank":
            raise RuntimeError(f"Flat layout is not typed as Blank: {part_path}")

    if verbose:
        print(
            "  Flat structure: project-owned Master + Blank Layout "
            f"({pruned} stock layout(s), "
            f"{removed_master_shapes + removed_layout_shapes} stock content "
            "shape(s) removed, "
            f"{len(retained_master_placeholders) + len(retained_layout_placeholders)} "
            "system footer hook(s) retained)"
        )
        text_style_status = (
            f"{master_count} master text style(s)"
            if master_text_style_spec is not None
            else "stock text defaults retained (no theme contract)"
        )
        print(f"  Flat theme: {theme_count} theme part(s), {text_style_status}")


def _append_relationship(
    rels_path: Path,
    rel_type: str,
    target: str,
    *,
    target_mode: str | None = None,
) -> str:
    """Append a relationship entry with the next available rId."""
    with open(rels_path, 'r', encoding='utf-8') as f:
        rels_content = f.read()

    rid_numbers = [int(match) for match in re.findall(r'Id="rId(\d+)"', rels_content)]
    next_rid = f'rId{max(rid_numbers, default=0) + 1}'
    mode_attr = (
        f" TargetMode={quoteattr(target_mode)}"
        if target_mode is not None
        else ""
    )
    rel_xml = (
        f"  <Relationship Id={quoteattr(next_rid)} "
        f"Type={quoteattr(rel_type)} Target={quoteattr(target)}{mode_attr}/>"
    )
    rels_content = rels_content.replace(
        '</Relationships>', rel_xml + '\n</Relationships>',
    )

    with open(rels_path, 'w', encoding='utf-8') as f:
        f.write(rels_content)

    return next_rid


def _add_default_content_type(content_types: str, extension: str, content_type: str) -> str:
    """Add a Default content type if it is not already present."""
    ext = extension.lstrip(".")
    if f'Extension="{ext}"' in content_types:
        return content_types
    entry = f'  <Default Extension="{ext}" ContentType="{content_type}"/>'
    override_pos = content_types.find('<Override ')
    if override_pos >= 0:
        return content_types[:override_pos] + entry + '\n' + content_types[override_pos:]
    return content_types.replace('</Types>', entry + '\n</Types>')


def _add_content_type_override(content_types: str, part_name: str, content_type: str) -> str:
    """Add an Override content type if it is not already present."""
    normalized = '/' + part_name.lstrip('/')
    if f'PartName="{normalized}"' in content_types:
        return content_types
    extension = PurePosixPath(part_name).suffix.lstrip('.').casefold()
    try:
        root = ET.fromstring(content_types)
    except ET.ParseError:
        root = None
    if root is not None:
        for child in root:
            if child.tag.rsplit('}', 1)[-1] != 'Default':
                continue
            if (
                child.get('Extension', '').casefold() == extension
                and child.get('ContentType') == content_type
            ):
                return content_types
    entry = f'  <Override PartName="{normalized}" ContentType="{content_type}"/>'
    return content_types.replace('</Types>', entry + '\n</Types>')


def _content_type_contract(payload: bytes) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...] | None:
    """Return the semantic Default/Override roster for exact-byte restoration."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    entries = [
        (
            child.tag.rsplit('}', 1)[-1],
            tuple(sorted(child.attrib.items())),
        )
        for child in root
        if child.tag.rsplit('}', 1)[-1] in {'Default', 'Override'}
    ]
    return tuple(sorted(entries))


def _content_type_for_extension(ext: str) -> str:
    from .builder import (
        _IMAGE_CONTENT_TYPES,
    )
    clean = ext.lower().lstrip('.')
    content_type = _IMAGE_CONTENT_TYPES.get(clean) or mimetypes.guess_type(f'x.{clean}')[0]
    if not content_type:
        raise ValueError(f"Unknown media content type for extension: {ext}")
    return content_type


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _create_writable_work_dir(output_path: Path) -> Path:
    """Create a real writable work directory for PPTX assembly."""
    parents = [output_path.parent, Path.cwd(), Path(tempfile.gettempdir())]
    seen: set[str] = set()
    errors: list[str] = []

    for parent in parents:
        parent = parent if str(parent) else Path(".")
        try:
            key = str(parent.resolve())
        except OSError:
            key = str(parent.absolute())
        if key in seen:
            continue
        seen.add(key)

        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append(f"{parent}: cannot create parent ({exc})")
            continue

        for _ in range(3):
            work_dir = parent / f".pptx-build-{os.getpid()}-{uuid.uuid4().hex}"
            try:
                work_dir.mkdir(mode=0o700)
                probe_path = work_dir / ".write-probe"
                probe_path.write_text("ok", encoding="utf-8")
                probe_path.unlink()
                return work_dir
            except OSError as exc:
                errors.append(f"{work_dir}: {exc}")
                shutil.rmtree(work_dir, ignore_errors=True)

    details = "\n  - ".join(errors) if errors else "no candidate directories available"
    raise PermissionError(
        "Unable to create a writable PPTX work directory. "
        "Set the output path to a writable project directory or adjust sandbox permissions. "
        f"Tried:\n  - {details}"
    )


def _relax_output_permissions(output_path: Path) -> list[str]:
    """Make exported files readable outside the sandbox owner where possible."""
    warnings: list[str] = []

    try:
        current_mode = output_path.stat().st_mode
        readable_mode = (
            current_mode
            | stat.S_IRUSR
            | stat.S_IWUSR
            | stat.S_IRGRP
            | stat.S_IROTH
        )
        os.chmod(output_path, readable_mode)
    except OSError as exc:
        warnings.append(f"chmod skipped for {output_path}: {exc}")

    if os.name != 'nt':
        return warnings

    # Windows ACLs can remain sandbox-only even when the file mode looks sane.
    # Grant the built-in Users SID read access; the SID avoids localization
    # issues on non-English Windows installations.
    try:
        result = subprocess.run(
            ['icacls', str(output_path), '/grant', '*S-1-5-32-545:R'],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        warnings.append(f"icacls skipped for {output_path}: {exc}")
    else:
        if result.returncode != 0:
            # icacls writes in the console OEM code page, so decode lazily
            # and leniently; text mode would decode under the interpreter's
            # encoding (UTF-8 in UTF-8 mode) and raise inside the reader
            # thread on non-ASCII bytes.
            message = (result.stderr or result.stdout or b'').decode(
                'oem', errors='replace',
            ).strip()
            details = f": {message}" if message else ''
            warnings.append(f"icacls failed for {output_path}{details}")

    return warnings
