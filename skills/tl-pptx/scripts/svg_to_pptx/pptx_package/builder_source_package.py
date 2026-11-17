"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import re
import shutil
import zipfile
from pathlib import (
    Path,
    PurePosixPath,
)
from xml.etree import ElementTree as ET
from pptx import Presentation
from pptx_embedded_fonts import (
    FONT_CONTENT_TYPE,
    FONT_REL_TYPE,
    PML_NS as EMBEDDED_FONT_PML_NS,
    REL_NS as EMBEDDED_FONT_REL_NS,
    EmbeddedFontBundle,
    EmbeddedFontError,
    embedded_font_typefaces,
)
from pptx_ooxml.clone import clone_presentation_slides
from ..drawingml.theme_colors import (
    ThemeColorError,
)
from .template_structure import (
    NativeStructureContract,
    TemplateSlideSpec,
    TemplateStructureError,
)

from .builder import (
    RoundtripSlidePatch,
)

def _create_preserved_base_pptx(
    contract: NativeStructureContract,
    specs: list[TemplateSlideSpec],
    output_path: Path,
    slide_size_emu: tuple[int, int],
    *,
    roundtrip_page_sources: tuple[int, ...] | None = None,
    package_overrides: dict[str, bytes] | None = None,
    slide_patches: dict[int, RoundtripSlidePatch] | None = None,
) -> bool:
    """Create the preserve base and report whether source Slides were retained."""
    from .builder import (
        _slide_ref_shape_ids,
    )
    if roundtrip_page_sources is not None:
        if len(roundtrip_page_sources) != len(specs):
            raise TemplateStructureError(
                "Round-trip page plan length does not match the SVG roster"
            )
        if contract.slide_size_emu != slide_size_emu:
            raise TemplateStructureError(
                "Generated SVG canvas does not match the preserved source template size"
            )
        for spec, source_index in zip(specs, roundtrip_page_sources):
            source_slide = contract.slide(source_index)
            if spec.layout_key != source_slide.layout_key:
                raise TemplateStructureError(
                    f"{spec.svg_path.name} declares layout {spec.layout_key!r}, "
                    f"but source slide {source_index} uses "
                    f"{source_slide.layout_key!r}"
                )
        try:
            clone_presentation_slides(
                contract.source_template,
                roundtrip_page_sources,
                output_path,
                package_overrides=package_overrides,
                discarded_shape_links={
                    index: _slide_ref_shape_ids(patch.edited_ref_ids | patch.deleted_ref_ids)
                    for index, patch in (slide_patches or {}).items()
                },
            )
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise TemplateStructureError(
                f"Round-trip page-plan cloning failed: {exc}"
            ) from exc
        return True

    source_roster_matches = (
        len(specs) == len(contract.slides)
        and all(
            spec.slide_num == source_slide.index
            and spec.layout_key == source_slide.layout_key
            for spec, source_slide in zip(specs, contract.slides)
        )
    )
    if source_roster_matches:
        shutil.copy2(contract.source_template, output_path)
        return True

    presentation = Presentation(str(contract.source_template))
    actual_size = (int(presentation.slide_width), int(presentation.slide_height))
    if actual_size != contract.slide_size_emu:
        raise TemplateStructureError(
            f"{contract.source_template.name} slide size does not match "
            f"{contract.contract_path.name}"
        )
    if actual_size != slide_size_emu:
        raise TemplateStructureError(
            "Generated SVG canvas does not match the preserved source template size"
        )

    layouts_by_part = {
        str(layout.part.partname).lstrip("/"): layout
        for master in presentation.slide_masters
        for layout in master.slide_layouts
    }
    slide_ids = presentation.slides._sldIdLst
    for slide_id in list(slide_ids):
        presentation.part.drop_rel(slide_id.rId)
        slide_ids.remove(slide_id)

    for spec in specs:
        layout_contract = contract.layout(spec.layout_key)
        layout = layouts_by_part.get(layout_contract.package_part)
        if layout is None:
            raise TemplateStructureError(
                f"Preserved source package did not load layout part "
                f"{layout_contract.package_part!r}"
            )
        presentation.slides.add_slide(layout)
    presentation.save(str(output_path))
    return False


def _clear_preserved_slide_collections(extract_dir: Path) -> None:
    """Remove source slide-order metadata that cannot apply to generated pages."""
    from .builder import (
        PML_NS,
        _write_xml_tree,
    )
    presentation_path = extract_dir / "ppt" / "presentation.xml"
    tree = ET.parse(presentation_path)
    root = tree.getroot()
    custom_shows = root.find(f"{{{PML_NS}}}custShowLst")
    if custom_shows is not None:
        root.remove(custom_shows)
    for extension_list in root.findall(f".//{{{PML_NS}}}extLst"):
        for extension in list(extension_list):
            if any(
                child.tag.rsplit("}", 1)[-1] == "sectionLst"
                for child in extension.iter()
                if isinstance(child.tag, str)
            ):
                extension_list.remove(extension)
    _write_xml_tree(presentation_path, tree)


def _validate_source_theme_xml(payload: bytes) -> None:
    """Validate one imported Theme part before package installation."""
    from .builder import (
        DML_NS,
        REL_NS,
    )
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ThemeColorError(f"Imported source theme is malformed: {exc}") from exc
    if root.tag != f"{{{DML_NS}}}theme":
        raise ThemeColorError("Imported source theme root must be a:theme")
    if any(
        isinstance(name, str) and name.startswith(f"{{{REL_NS}}}")
        for node in root.iter()
        for name in node.attrib
    ):
        raise ThemeColorError("Imported source theme cannot contain relationships")


def _install_source_theme_xml(extract_dir: Path, payload: bytes) -> None:
    """Install a validated imported theme into the generated flat package."""
    _validate_source_theme_xml(payload)

    theme_paths = sorted((extract_dir / "ppt" / "theme").glob("theme*.xml"))
    if not theme_paths:
        raise ThemeColorError("Generated PPTX package has no theme part")
    for theme_path in theme_paths:
        theme_path.write_bytes(payload)


def _install_source_themes_by_master(
    extract_dir: Path,
    master_parts_by_key: dict[str, str],
    payloads_by_key: dict[str, bytes],
) -> None:
    """Install one exact source Theme into each structured mirror Master."""
    from .builder import (
        THEME_REL_TYPE,
        _read_relationships,
        _relationships_path_for_part,
        _resolve_package_target,
    )
    expected = set(master_parts_by_key)
    actual = set(payloads_by_key)
    if actual != expected:
        raise ThemeColorError(
            "Structured source Theme roster differs from Master roster; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    for master_key, master_part in master_parts_by_key.items():
        payload = payloads_by_key[master_key]
        _validate_source_theme_xml(payload)
        master_rels = _relationships_path_for_part(extract_dir, master_part)
        targets = [
            attrs["Target"]
            for attrs in _read_relationships(master_rels).values()
            if attrs.get("Type") == THEME_REL_TYPE and attrs.get("Target")
        ]
        if len(targets) != 1:
            raise ThemeColorError(
                f"Structured Master {master_key!r} must own one Theme relationship"
            )
        theme_part = _resolve_package_target(master_part, targets[0])
        theme_path = extract_dir / theme_part
        if not theme_path.is_file():
            raise ThemeColorError(
                f"Structured Master {master_key!r} Theme part is missing: {theme_part}"
            )
        theme_path.write_bytes(payload)


def _install_source_embedded_fonts(
    extract_dir: Path,
    bundle: EmbeddedFontBundle,
) -> tuple[str, ...]:
    """Install validated source font parts into the generated PPTX package."""
    from .builder import (
        PACKAGE_REL_NS,
        _add_default_content_type,
        _write_xml_tree,
    )
    typefaces = embedded_font_typefaces(bundle)
    try:
        font_list = ET.fromstring(bundle.font_list_xml)
    except ET.ParseError as exc:
        raise EmbeddedFontError(
            f"Embedded font list XML is malformed: {exc}"
        ) from exc

    ppt_dir = extract_dir / "ppt"
    presentation_path = ppt_dir / "presentation.xml"
    presentation_rels_path = ppt_dir / "_rels" / "presentation.xml.rels"
    presentation_tree = ET.parse(presentation_path)
    presentation_root = presentation_tree.getroot()
    rels_tree = ET.parse(presentation_rels_path)
    rels_root = rels_tree.getroot()

    fonts_dir = ppt_dir / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    for relationship in list(rels_root):
        if relationship.attrib.get("Type") != FONT_REL_TYPE:
            continue
        target = relationship.attrib.get("Target", "")
        target_path = PurePosixPath(target)
        if (
            not target_path.is_absolute()
            and target_path.parts[:1] == ("fonts",)
            and target_path.suffix.lower() == ".fntdata"
        ):
            candidate = ppt_dir.joinpath(*target_path.parts)
            if candidate.is_file():
                candidate.unlink()
        rels_root.remove(relationship)

    rid_numbers = [
        int(match.group(1))
        for relationship in rels_root
        if (match := re.fullmatch(
            r"rId(\d+)",
            relationship.attrib.get("Id", ""),
        ))
    ]
    next_rid = max(rid_numbers, default=0) + 1
    relationship_mapping: dict[str, str] = {}
    for index, part in enumerate(bundle.parts, start=1):
        filename = f"font{index}.fntdata"
        (fonts_dir / filename).write_bytes(part.payload)
        relationship_id = f"rId{next_rid}"
        next_rid += 1
        ET.SubElement(
            rels_root,
            f"{{{PACKAGE_REL_NS}}}Relationship",
            {
                "Id": relationship_id,
                "Type": FONT_REL_TYPE,
                "Target": f"fonts/{filename}",
            },
        )
        relationship_mapping[part.relationship_id] = relationship_id

    for node in font_list.iter():
        relationship_attr = f"{{{EMBEDDED_FONT_REL_NS}}}id"
        original_id = node.attrib.get(relationship_attr)
        if original_id is None:
            continue
        replacement_id = relationship_mapping.get(original_id)
        if replacement_id is None:
            raise EmbeddedFontError(
                f"Embedded font list references an unknown part: {original_id}"
            )
        node.set(relationship_attr, replacement_id)

    existing = presentation_root.find(
        f"{{{EMBEDDED_FONT_PML_NS}}}embeddedFontLst"
    )
    if existing is not None:
        insertion_index = list(presentation_root).index(existing)
        presentation_root.remove(existing)
    else:
        default_text_style = presentation_root.find(
            f"{{{EMBEDDED_FONT_PML_NS}}}defaultTextStyle"
        )
        insertion_index = (
            list(presentation_root).index(default_text_style)
            if default_text_style is not None
            else len(presentation_root)
        )
    presentation_root.insert(insertion_index, font_list)
    _write_xml_tree(presentation_path, presentation_tree)
    _write_xml_tree(presentation_rels_path, rels_tree)

    content_types_path = extract_dir / "[Content_Types].xml"
    content_types_path.write_text(
        _add_default_content_type(
            content_types_path.read_text(encoding="utf-8"),
            "fntdata",
            FONT_CONTENT_TYPE,
        ),
        encoding="utf-8",
    )
    return typefaces
