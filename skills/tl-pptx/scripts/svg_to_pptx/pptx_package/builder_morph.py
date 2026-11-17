"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
from pathlib import (
    Path,
)
from xml.etree import ElementTree as ET
from xml.parsers import expat
from xml.sax.saxutils import (
    escape,
)
from ..animation_config import (
    MorphPair,
)
from .template_structure import (
    TemplateStructureError,
)

def _patch_morph_name_bytes(
    source: bytes,
    assignments: dict[str, tuple[str, str]],
    slide_number: int,
) -> bytes:
    """Replace only cNvPr name values, retaining all other source XML bytes."""
    from .builder import (
        PML_NS,
        _XML_ATTRIBUTE_RE,
        _XML_START_TAG_RE,
    )
    parser = expat.ParserCreate(namespace_separator='}')
    offsets: dict[str, list[tuple[int, str | None]]] = {}
    encoding = 'utf-8'

    def declaration(_version: str, declared_encoding: str | None, _standalone: int) -> None:
        nonlocal encoding
        encoding = declared_encoding or encoding

    def start(name: str, attributes: dict[str, str]) -> None:
        shape_id = attributes.get('id')
        if name == f'{PML_NS}}}cNvPr' and shape_id in assignments:
            offsets.setdefault(shape_id, []).append((parser.CurrentByteIndex, attributes.get('name')))

    parser.XmlDeclHandler = declaration
    parser.StartElementHandler = start
    parser.Parse(source, True)
    # Byte offsets come from Expat; encode individual spans without a BOM.
    if source.startswith((b'\xff\xfe', b'<\x00')):
        encoding = 'utf-16-le'
    elif source.startswith((b'\xfe\xff', b'\x00<')):
        encoding = 'utf-16-be'
    replacements: list[tuple[int, int, bytes]] = []
    for shape_id, (name, group_id) in assignments.items():
        locations = offsets.get(shape_id, [])
        error = (
            f'Morph target "{group_id}" on slide {slide_number} cannot patch '
            f'cNvPr@name for source shape {shape_id} without rewriting XML; '
            'edit this endpoint page as well, or pair groups on two edited pages'
        )
        if len(locations) != 1:
            raise TemplateStructureError(error)
        offset, existing_name = locations[0]
        if existing_name == name:
            continue
        tag = _XML_START_TAG_RE.match(source[offset:].decode(encoding))
        attribute = next(
            (match for match in _XML_ATTRIBUTE_RE.finditer(tag.group()) if match[1] == 'name'),
            None,
        ) if tag is not None else None
        if attribute is None:
            raise TemplateStructureError(error)
        start_offset = offset + len(tag.group()[:attribute.start(3)].encode(encoding))
        end_offset = offset + len(tag.group()[:attribute.end(3)].encode(encoding))
        escaped = escape(name, {'"': '&quot;', "'": '&apos;'})
        replacements.append((start_offset, end_offset, escaped.encode(encoding, errors='xmlcharrefreplace')))
    for start_offset, end_offset, value in sorted(replacements, reverse=True):
        source = source[:start_offset] + value + source[end_offset:]
    return source


def _apply_morph_shape_names(
    extract_dir: Path,
    pairs: tuple[MorphPair, ...],
    slide_numbers: dict[str, int],
    shape_ids: dict[tuple[str, str], int],
    *,
    preserved_slides: set[int],
) -> dict[int, dict[str, str]]:
    """Write forced-Morph names after all structure transformations finish."""
    from .builder import (
        PML_NS,
        _set_shape_name,
        _write_xml_tree,
    )
    assignments: dict[int, dict[str, tuple[str, str]]] = {}
    names_by_slide: dict[int, dict[str, str]] = {}
    for pair in pairs:
        for slide_name, group_id in (
            (pair.source_slide, pair.source_group_id),
            (pair.destination_slide, pair.destination_group_id),
        ):
            slide_number = slide_numbers[slide_name]
            resolved_id = shape_ids.get((slide_name, group_id))
            if resolved_id is None:
                raise TemplateStructureError(
                    f'Morph target "{slide_name}/{group_id}" has no exported '
                    'Slide-local shape identity; edit this endpoint page as well, '
                    'or pair groups on two edited pages'
                )
            shape_id = str(resolved_id)
            slide_assignments = assignments.setdefault(slide_number, {})
            previous = slide_assignments.setdefault(
                shape_id,
                (pair.shape_name, group_id),
            )
            if previous[0] != pair.shape_name:
                raise TemplateStructureError(
                    f'Morph target "{slide_name}/{group_id}" resolves to shape '
                    f'{shape_id} with conflicting names "{previous[0]}" and '
                    f'"{pair.shape_name}"'
                )
            slide_names = names_by_slide.setdefault(slide_number, {})
            previous_group = slide_names.setdefault(pair.shape_name, group_id)
            if previous_group != group_id:
                raise TemplateStructureError(
                    f'Morph name "{pair.shape_name}" maps to multiple objects '
                    f'on slide "{slide_name}"'
                )

    trace_names: dict[int, dict[str, str]] = {}
    for slide_number, slide_assignments in sorted(assignments.items()):
        slide_path = (
            extract_dir / "ppt" / "slides" / f"slide{slide_number}.xml"
        )
        tree = ET.parse(slide_path)
        root = tree.getroot()
        top_level_shapes = _top_level_shapes_by_id(root)
        desired_names = {
            shape_name
            for shape_name, _group_id in slide_assignments.values()
        }
        for shape_id, shape in top_level_shapes.items():
            if shape_id in slide_assignments:
                continue
            c_nv_pr = next(shape.iter(f"{{{PML_NS}}}cNvPr"), None)
            existing_name = (
                c_nv_pr.get("name") if c_nv_pr is not None else None
            )
            if existing_name in desired_names:
                raise TemplateStructureError(
                    f'Morph name "{existing_name}" already belongs to an '
                    f'unmapped object on slide {slide_number}'
                )

        for shape_id, (shape_name, group_id) in slide_assignments.items():
            shape = top_level_shapes.get(shape_id)
            if shape is None:
                raise TemplateStructureError(
                    f'Morph target "{group_id}" no longer resolves to a '
                    f'Slide-local shape on slide {slide_number}'
                )
            _set_shape_name(shape, shape_name)
            trace_names.setdefault(slide_number, {})[group_id] = shape_name
        if slide_number in preserved_slides:
            slide_path.write_bytes(_patch_morph_name_bytes(
                slide_path.read_bytes(), slide_assignments, slide_number,
            ))
        else:
            _write_xml_tree(slide_path, tree)
    return trace_names


def _top_level_shape_name_roster(root: ET.Element) -> tuple[str, ...]:
    """Return the exact visible top-level shape-name sequence for read-back."""
    from .builder import (
        PML_NS,
        _TOP_LEVEL_SHAPE_TAGS,
    )
    sp_tree = root.find(f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree")
    if sp_tree is None:
        raise TemplateStructureError("Structured part has no p:cSld/p:spTree")
    names: list[str] = []
    for child in sp_tree:
        if child.tag not in _TOP_LEVEL_SHAPE_TAGS:
            continue
        c_nv_pr = next(child.iter(f"{{{PML_NS}}}cNvPr"), None)
        name = c_nv_pr.get("name") if c_nv_pr is not None else None
        if not name:
            raise TemplateStructureError(
                "Structured part contains a top-level shape without a name"
            )
        names.append(name)
    return tuple(names)


def _top_level_shapes_by_id(root: ET.Element) -> dict[str, ET.Element]:
    from .builder import (
        PML_NS,
        _TOP_LEVEL_SHAPE_TAGS,
        _shape_id,
    )
    sp_tree = root.find(f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree")
    if sp_tree is None:
        return {}
    shapes: dict[str, ET.Element] = {}
    for child in list(sp_tree):
        if child.tag not in _TOP_LEVEL_SHAPE_TAGS:
            continue
        shape_id = _shape_id(child)
        if shape_id:
            shapes[shape_id] = child
    return shapes


def _timing_shape_ids(root: ET.Element) -> set[str]:
    """Return slide-local shape ids referenced by animation timing."""
    from .builder import (
        PML_NS,
    )
    return {
        elem.attrib["spid"]
        for elem in root.findall(f".//{{{PML_NS}}}timing//{{{PML_NS}}}spTgt")
        if elem.attrib.get("spid")
    }


def _relationship_ids_in_shape(elem: ET.Element) -> set[str]:
    from .builder import (
        _REL_ATTRS,
    )
    rel_ids: set[str] = set()
    for node in elem.iter():
        for attr_name, value in node.attrib.items():
            if attr_name in _REL_ATTRS and value:
                rel_ids.add(value)
    return rel_ids
