"""Register a brand / style / layout / deck template in a library index.

Four kinds, each with built-in and optional local indexes. The shared model lives
in ``templates/README.md``; each kind's schema lives in its directory README:

Registration defaults to the ignored ``templates/local/`` library. Pass
``--builtin`` to maintain the distributed roots and indexes listed below.

| --kind  | Workspace roots         | Index file                    |
|---------|-------------------------|-------------------------------|
| brand   | ``templates/brands/``   | ``brands_index.json``         |
| style   | ``templates/styles/``   | ``styles_index.json``         |
| layout  | ``templates/layouts/``  | ``layouts_index.json``        |
| deck    | ``templates/decks/``    | ``decks_index.json``          |

Current workspaces keep ``design_spec.md`` and any SVG roster under
``<workspace>/templates/``. Assets live in optional ``images/`` / ``icons/``
directories. Explicitly generated review artifacts go to the optional, ignored
``exports/`` directory. Every kind uses this nested workspace contract.

Index entry schemas (the JSON file is the single source of truth — README
files describe the kind and usage in prose but do **not** enumerate templates;
discovery happens exclusively against the index file):

- brand:  ``{ summary, primary_color }``
- style:  ``{ summary, keywords[] }``
- layout: ``{ summary, canvas_format, page_count, page_types[] }``
- deck:   ``{ summary, canvas_format, page_count, primary_color }``

Usage::

    python3 scripts/register_template.py <id> --kind deck     # default kind=deck
    python3 scripts/register_template.py <id> --kind layout
    python3 scripts/register_template.py <id> --kind brand
    python3 scripts/register_template.py <id> --kind style
    python3 scripts/register_template.py --rebuild-all --kind deck
    python3 scripts/register_template.py <id> --dry-run

``--rebuild-all`` rebuilds every entry from scratch within the chosen kind;
recommended for repairing index drift across many templates at once.

Project-scoped Brand and Style workspaces are validated, not registered,
through ``svg_quality_checker.py <workspace>/templates --template-mode``. That
entry reuses :func:`validate_brand_workspace` or
:func:`validate_style_workspace`, so each schema has one authority.
"""


from __future__ import annotations


import argparse


import json


import re


import sys


from collections import OrderedDict


from pathlib import Path


from xml.etree import ElementTree as ET


from template_import.canvas_formats import CANVAS_FORMATS


from slide_roster import discover_slide_svgs


try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


SCRIPT_DIR = Path(__file__).resolve().parents[1]


SKILL_DIR = SCRIPT_DIR.parent


TEMPLATES_DIR = SKILL_DIR / "assets" / "templates"


KIND_CONFIG = {
    "brand": {
        "dir": TEMPLATES_DIR / "brands",
        "index": TEMPLATES_DIR / "brands" / "brands_index.json",
        "id_key": "brand_id",
        "needs_svg_roster": False,
    },
    "style": {
        "dir": TEMPLATES_DIR / "styles",
        "index": TEMPLATES_DIR / "styles" / "styles_index.json",
        "id_key": "style_id",
        "needs_svg_roster": False,
    },
    "layout": {
        "dir": TEMPLATES_DIR / "layouts",
        "index": TEMPLATES_DIR / "layouts" / "layouts_index.json",
        "id_key": "layout_id",
        "needs_svg_roster": True,
    },
    "deck": {
        "dir": TEMPLATES_DIR / "decks",
        "index": TEMPLATES_DIR / "decks" / "decks_index.json",
        "id_key": "deck_id",
        "needs_svg_roster": True,
    },
}


_BRAND_REQUIRED_SECTIONS = (
    ("I", "Brand Overview"),
    ("II", "Color Scheme"),
    ("III", "Typography"),
    ("IV", "Logo"),
    ("V", "Voice & Tone"),
    ("VI", "Icon Style"),
)


_BRAND_FORBIDDEN_SECTIONS = (
    "Page Roster",
    "Signature Design Elements",
)


_BRAND_ALLOWED_FRONTMATTER_FIELDS = frozenset({
    "brand_id",
    "kind",
    "summary",
    "primary_color",
})


_BRAND_PROVENANCE_VALUES = {"fact", "approx", "user"}


_BRAND_ASSET_REF_RE = re.compile(
    r"`((?:\.\./)+(?:images|icons)/[^`]+)`"
)


_STYLE_REQUIRED_SECTIONS = (
    ("I", "Style Overview"),
    ("II", "Communication Method"),
    ("III", "Page Role Vocabulary"),
    ("IV", "Evidence & Data Expression"),
    ("V", "Visual System Defaults"),
    ("VI", "Image & Icon Direction"),
    ("VII", "Review Focus"),
)


_STYLE_FORBIDDEN_SECTIONS = (
    "Brand Overview",
    "Template Overview",
    "Color Scheme",
    "Typography",
    "Logo",
    "Voice & Tone",
    "Icon Style",
    "Assets",
    "Signature Design Elements",
    "Page Roster",
    "Placeholder Overrides",
)


_STYLE_REQUIRED_FIELDS = {
    "I. Style Overview": (
        "Style Name",
        "Best Fit",
        "Reusable Intent",
        "Sources",
    ),
    "II. Communication Method": (
        "Argument Flow",
        "Page Message Discipline",
        "Claim Discipline",
    ),
    "IV. Evidence & Data Expression": (
        "Argument Trace",
        "Charts",
        "Tables",
        "Sources",
        "Native Editability",
    ),
    "V. Visual System Defaults": (
        "Composition",
        "Density",
        "Decoration",
        "Color Behavior",
        "Typography Character",
    ),
    "VI. Image & Icon Direction": (
        "Image Usage",
        "Image Treatment",
        "Icon Treatment",
    ),
}


_STYLE_CUSTOM_FIELDS = (
    (
        "II. Communication Method",
        "Preferred Mode",
        "Mode Behavior",
        "Mode References",
        SKILL_DIR / "assets" / "template-catalogs" / "modes",
    ),
    (
        "V. Visual System Defaults",
        "Preferred Visual Style",
        "Visual Style Behavior",
        "Visual Style References",
        SKILL_DIR / "assets" / "template-catalogs" / "visual-styles",
    ),
    (
        "VI. Image & Icon Direction",
        "Preferred Image Rendering",
        "Image Rendering Behavior",
        "Image Rendering References",
        SKILL_DIR / "assets" / "template-catalogs" / "image-renderings",
    ),
)


_STYLE_FORBIDDEN_FIELDS = (
    "Brand Overview",
    "Template Overview",
    "Color Scheme",
    "Typography",
    "Signature Design Elements",
    "Page Roster",
    "Placeholder Overrides",
    "Target Audience",
    "Communication Objective",
    "Desired Outcome",
    "Core Message",
    "Delivery Context",
    "Artifact Afterlife",
    "Application Context",
    "Content Outline",
    "Page Count",
    "Page Types",
    "Page Order",
    "Page Sequence",
    "Canvas Format",
    "Canvas Width",
    "Canvas Height",
    "Canvas ViewBox",
    "Replication Mode",
    "Native Structure Mode",
    "Master",
    "Layout",
    "Placeholders",
    "Page Assignment",
    "Icon Inventory",
    "Image Resources",
    "Primary Color",
    "Logo",
    "Voice & Tone",
    "Icon Style",
    "Assets",
)


_STYLE_ALLOWED_FRONTMATTER_FIELDS = frozenset({
    "style_id",
    "kind",
    "summary",
    "keywords",
})


_STYLE_REVIEW_TRIGGER_MARKER = "<!-- visual-review-trigger: explicit-user-only -->"


_HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")


_PORTABLE_STYLE_ID_RE = re.compile(r"^\w[\w.-]*$")


class SpecParseError(RuntimeError):
    """Raised when a design_spec.md cannot be turned into an index entry."""


_SPEC_NAME_RE = re.compile(
    r"design_spec\.(?P<kind>brand|style|layout|deck)\.(?P<id>[^/\\]+)\.md"
)

