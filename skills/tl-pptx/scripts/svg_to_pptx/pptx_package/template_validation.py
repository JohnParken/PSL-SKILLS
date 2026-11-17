"""
PPT Master - Template Package Validation

Read a generated structured PPTX back and verify its reusable Master/Layout graph.

Usage:
    Imported by svg_to_pptx.pptx_package.builder.

Examples:
    validate_pptx_template_package(Path("output.pptx"), template_specs)

Dependencies:
    None (only uses standard library and local modules)
"""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import posixpath
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from ..drawingml.utils import EMU_PER_PX
from .template_structure import (
    OOXML_UINT32_MAX,
    TemplatePlaceholderBinding,
    TemplateSlideSpec,
    TemplateStructureError,
    is_proxy_placeholder,
    template_placeholder_bindings,
)

if __name__ == "__main__":
    if any(arg in {"-h", "--help", "help"} for arg in sys.argv[1:]):
        print(__doc__)
        raise SystemExit(0)
    print(
        "Use this validator through the structured SVG-to-PPTX exporter.",
        file=sys.stderr,
    )
    raise SystemExit(1)


PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"


MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


SLIDE_LAYOUT_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
)


SLIDE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)


SLIDE_MASTER_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster"
)


THEME_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
)


SLIDE_LAYOUT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"
)


SLIDE_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
)


SLIDE_MASTER_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"
)


PRESENTATION_COLLECTION_ID_MIN = 1 << 31


PRESENTATION_SLIDE_ID_MIN = 256


_TOP_LEVEL_VISIBLE_TAGS = frozenset({
    f"{{{PML_NS}}}sp",
    f"{{{PML_NS}}}pic",
    f"{{{PML_NS}}}graphicFrame",
    f"{{{PML_NS}}}grpSp",
    f"{{{PML_NS}}}cxnSp",
    f"{{{MC_NS}}}AlternateContent",
})


@dataclass(frozen=True)
class _Relationship:
    rel_id: str
    rel_type: str
    target: str
    target_mode: str | None


@dataclass(frozen=True)
class _Placeholder:
    shape: ET.Element
    placeholder_type: str
    raw_type: str | None
    idx: int
    raw_idx: int | None


class _PackageReader:
    """Cache package XML while accumulating deterministic read-back errors."""

    def __init__(self, package: zipfile.ZipFile, errors: list[str]) -> None:
        self.package = package
        self.errors = errors
        self.names = frozenset(package.namelist())
        self._xml_cache: dict[str, ET.Element | None] = {}
        self._rels_cache: dict[str, tuple[_Relationship, ...]] = {}

    def xml(self, part: str) -> ET.Element | None:
        if part in self._xml_cache:
            return self._xml_cache[part]
        if part not in self.names:
            self.errors.append(f"missing package part {part}")
            self._xml_cache[part] = None
            return None
        try:
            root = ET.fromstring(self.package.read(part))
        except (KeyError, ET.ParseError) as exc:
            self.errors.append(f"{part} is not valid XML: {exc}")
            root = None
        self._xml_cache[part] = root
        return root

    def relationships(self, source_part: str) -> tuple[_Relationship, ...]:
        if source_part in self._rels_cache:
            return self._rels_cache[source_part]
        rels_part = _relationships_part_for(source_part)
        root = self.xml(rels_part)
        relationships: list[_Relationship] = []
        seen_ids: set[str] = set()
        if root is not None:
            for elem in root:
                attrs = {
                    key.rsplit("}", 1)[-1]: value
                    for key, value in elem.attrib.items()
                }
                rel_id = attrs.get("Id", "")
                rel_type = attrs.get("Type", "")
                target = attrs.get("Target", "")
                if not rel_id or not rel_type or not target:
                    self.errors.append(
                        f"{rels_part} contains a relationship without Id, Type, or Target"
                    )
                    continue
                if rel_id in seen_ids:
                    self.errors.append(f"{rels_part} repeats relationship id {rel_id}")
                    continue
                seen_ids.add(rel_id)
                relationships.append(_Relationship(
                    rel_id=rel_id,
                    rel_type=rel_type,
                    target=target,
                    target_mode=attrs.get("TargetMode"),
                ))
        result = tuple(relationships)
        self._rels_cache[source_part] = result
        return result



# Stable explicit facade; algorithms retain their upstream signatures.
from .template_validation_shape_rosters import (
    _validate_unique_creation_ids,
    _top_level_shape_names,
    _validate_named_shape_roster,
    _top_level_shape_by_name,
    _has_explicit_background,
    _xml_payload_signature,
    _background_payload_signatures,
    _expected_background_signature,
)
from .template_validation_placeholder_rosters import (
    _relationships_part_for,
    _resolve_relationship_target,
    _single_relationship_target,
    _placeholder_for_shape,
    _read_placeholders,
    _validate_placeholder_roster,
    _validate_layout_header_footer,
    _shape_bounds,
    _direct_text_body,
    _first_run_size,
    _level_one_default_size,
)
from .template_validation_part_rosters import (
    _content_type_overrides,
    _validate_uint32_id_roster,
    _validate_registered_part_roster,
    _numbered_part_family,
    _validate_physical_part_roster,
    _validate_content_type_part_roster,
    _validate_presentation_master_registration,
    _validate_master_theme_ownership,
    _validate_presentation_slide_registration,
)
from .template_validation_package import (
    validate_pptx_template_package,
)
