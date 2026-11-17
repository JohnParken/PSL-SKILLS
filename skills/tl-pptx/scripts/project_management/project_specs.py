#!/usr/bin/env python3
"""
PPT Master - Project Specification Helpers

Scaffold and validate the Markdown planning artifacts used by project_manager.py.
The module keeps schema parsing and deterministic scaffold rendering independent
from the broader project-management command surface.

Usage:
    Import validate_project_artifacts() or scaffold_project_artifact().

Examples:
    from project_management.project_specs import validate_markdown_schema

Dependencies:
    None (only uses the standard library and local project modules)
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Mapping

from .paths import (
    SCAFFOLD_DIR,
    SCHEMA_DIR,
    SCRIPTS_DIR,
    SKILL_DIR,
)

try:
    from project_utils import (
        CANVAS_FORMATS,
        get_project_info as get_project_info_common,
        validate_communication_trace,
    )
except ImportError:
    import sys

    tools_dir = SCRIPTS_DIR
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    from project_utils import (  # type: ignore
        CANVAS_FORMATS,
        get_project_info as get_project_info_common,
        validate_communication_trace,
    )

try:
    from visualization_catalog import (
        LEGACY_STRUCTURE_INTENT_KIND,
        VISUALIZATION_SVG_KIND,
        VisualizationCatalogError,
        resolve_visualization_reference,
    )
except ImportError:
    import sys

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from visualization_catalog import (  # type: ignore
        LEGACY_STRUCTURE_INTENT_KIND,
        VISUALIZATION_SVG_KIND,
        VisualizationCatalogError,
        resolve_visualization_reference,
    )


TOOLS_DIR = SCRIPTS_DIR

_CUSTOM_REFERENCE_CATALOGS = (
    ("mode", "mode", "mode_references", SKILL_DIR / "assets" / "template-catalogs" / "modes"),
    (
        "visual_style",
        "visual_style",
        "visual_style_references",
        SKILL_DIR / "assets" / "template-catalogs" / "visual-styles",
    ),
    (
        "colors",
        "image_rendering",
        "image_rendering_references",
        SKILL_DIR / "assets" / "template-catalogs" / "image-renderings",
    ),
)

# Display names the registry carried before it was aligned with
# references/canvas-formats.md; locks written against them still validate.
_LEGACY_CANVAS_NAMES = {
    "xiaohongshu": ("小红书",),
    "moments": ("Moments/Instagram",),
    "story": ("Story/Vertical",),
    "banner": ("Horizontal Banner",),
}

_MARKDOWN_H2_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_MARKDOWN_SUBHEADING_RE = re.compile(r"^#{3,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_MARKDOWN_DATA_LINE_RE = re.compile(
    r"^[ \t]*-[ \t]+(?:\*\*)?([^:\n*]+?)(?:\*\*)?[ \t]*:[ \t]*(.*)$",
    re.MULTILINE,
)
_MARKDOWN_LIST_ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(.*)$")
_IMAGE_PATH_SUFFIXES = frozenset(
    {
        ".bmp",
        ".emf",
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".svg",
        ".tif",
        ".tiff",
        ".webp",
        ".wmf",
    }
)
_IMAGE_ACQUISITION_SOURCES = frozenset(
    {"ai", "web", "user", "formula", "placeholder", "slice"}
)
_IMAGE_CROP_POLICIES = frozenset({"adaptive", "no-crop"})
_LEGACY_IMAGE_METADATA_KEYS = frozenset(
    {
        "image_rendering",
        "image_rendering_behavior",
        "image_rendering_references",
    }
)
_LEGACY_SPEC_LOCK_FORBIDDEN = frozenset({"Mixing icon libraries"})
_LEGACY_SPEC_LOCK_FORBIDDEN_ANCHORS = (
    "<style>",
    "<foreignObject>",
    "HTML named entities",
    "Mixing icon libraries",
    "rgba()",
    "<g opacity",
)
_SCAFFOLD_TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")
_SCHEMA_MARKER_RE = re.compile(
    r"^<!--[ \t]+ppt-master-schema:[ \t]*([a-z0-9-]+/v[1-9][0-9]*)[ \t]+-->$",
    re.IGNORECASE,
)

_PAGE_COUNT_ROW_RE = re.compile(
    r"^\|[ \t]*Page Count[ \t]*\|[ \t]*([0-9]+)[ \t]*\|",
    flags=re.IGNORECASE | re.MULTILINE,
)


from .project_specs_parsing import (
    _normalize_schema_value,
    _extract_schema_marker,
    _parse_markdown_sections,
    parse_markdown_artifact,
    _looks_like_image_path,
    parse_spec_lock_image_value,
    parse_spec_lock_artifact,
    parse_spec_lock,
    default_spec_lock_forbidden,
    _normalize_forbidden_row,
    _validate_spec_lock_forbidden,
)
from .project_specs_schema import (
    _load_markdown_schema,
    _catalog_values,
    _validate_catalog_values,
    _validate_section,
    _condition_applies,
    _validate_condition,
)
from .project_specs_references import (
    _declared_page_count,
    _validate_slides,
    _validate_references,
    _validate_strict_data_surface,
)
from .project_specs_relations import (
    _validate_spec_lock_relations,
)
from .project_specs_artifacts import (
    validate_markdown_schema,
    validate_markdown_text,
    validate_project_artifacts,
    scaffold_project_artifact,
)
