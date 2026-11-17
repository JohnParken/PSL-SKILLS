#!/usr/bin/env python3
"""
PPT Master - PPTX Transition Core

Provide one strict PowerPoint-native transition registry, a compatibility input
map, and shared OOXML read/write helpers for generated and source-preserving
round-trip PPTX files.
See references/animations.md for the public workflow and
scripts/docs/pptx-transitions.md for the OOXML contract.

Usage:
    Import from PPT Master PPTX builders and direct-package workflows.

Examples:
    from pptx_transitions import AdvanceUpdate, EnterUpdate, apply_slide_motion

Dependencies:
    lxml (preferred for prefix-preserving source-PPTX mutation)
"""

from __future__ import annotations

import io
import math
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

try:
    from lxml import etree as LET
except ImportError:
    LET = None


PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"
P15_NS = "http://schemas.microsoft.com/office/powerpoint/2012/main"
P159_NS = "http://schemas.microsoft.com/office/powerpoint/2015/09/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELATIONSHIPS_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)

PRESENTATION_PROPS_PART = "ppt/presProps.xml"
PRESENTATION_RELS_PART = "ppt/_rels/presentation.xml.rels"
CONTENT_TYPES_PART = "[Content_Types].xml"
PRESENTATION_PROPS_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/presProps"
)
PRESENTATION_PROPS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"
)
AUDIO_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/audio"
)
WAV_CONTENT_TYPES = frozenset({"audio/wav", "audio/x-wav"})

DEFAULT_TRANSITION = "fade"
DEFAULT_TRANSITION_DURATION = 0.4
MAX_OOXML_MILLISECONDS = 4_294_967_295
# OOXML stores millisecond values and identifiers (shape ids, time-node ids,
# preset codes) as xsd:unsignedInt, so both ceilings share one value.
MAX_OOXML_UNSIGNED_INT = MAX_OOXML_MILLISECONDS


_TRANSITION_SPECS: dict[str, dict[str, Any]] = {
    # PowerPoint-native public ordering is assembled by gallery category below.
    "fade": {
        "name": "Fade",
        "element": "fade",
        "attrs": {},
    },
    "push": {
        "name": "Push",
        "element": "push",
        "attrs": {"dir": "r"},
    },
    "wipe": {
        "name": "Wipe",
        "element": "wipe",
        "attrs": {"dir": "r"},
    },
    "split": {
        "name": "Split",
        "element": "split",
        "attrs": {"orient": "horz", "dir": "out"},
    },
    "cover": {
        "name": "Cover",
        "element": "cover",
        "attrs": {"dir": "r"},
    },
    "random": {
        "name": "Random",
        "element": "random",
        "attrs": {},
    },
    "blinds": {
        "name": "Blinds",
        "element": "blinds",
        "attrs": {"dir": "vert"},
    },
    "checkerboard": {
        "name": "Checkerboard",
        "element": "checker",
        "attrs": {"dir": "horz"},
    },
    "comb": {
        "name": "Comb",
        "element": "comb",
        "attrs": {"dir": "horz"},
    },
    "cut": {
        "name": "Cut",
        "element": "cut",
        "attrs": {"thruBlk": "0"},
    },
    "dissolve": {
        "name": "Dissolve",
        "element": "dissolve",
        "attrs": {},
    },
    "random_bars": {
        "name": "Random Bars",
        "element": "randomBar",
        "attrs": {"dir": "vert"},
    },
    "zoom": {
        "name": "Zoom",
        "prefix": "p14",
        "element": "warp",
        "attrs": {"dir": "in"},
        "fallback": "fade",
    },
    # Current PowerPoint transition gallery: Subtle.
    "morph": {
        "name": "Morph",
        "prefix": "p159",
        "element": "morph",
        "attrs": {"option": "byObject"},
        "fallback": "fade",
    },
    "reveal": {
        "name": "Reveal",
        "prefix": "p14",
        "element": "reveal",
        "attrs": {"dir": "r"},
        "fallback": "fade",
    },
    "shape": {
        "name": "Shape",
        "element": "circle",
        "attrs": {},
    },
    "uncover": {
        "name": "Uncover",
        "element": "pull",
        "attrs": {"dir": "r"},
    },
    "flash": {
        "name": "Flash",
        "prefix": "p14",
        "element": "flash",
        "attrs": {},
        "fallback": "fade",
    },
    # Current PowerPoint transition gallery: Exciting.
    "fall_over": {
        "name": "Fall Over",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "fallOver", "invX": "1"},
        "fallback": "fade",
    },
    "drape": {
        "name": "Drape",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "drape", "invX": "1"},
        "fallback": "fade",
    },
    "curtains": {
        "name": "Curtains",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "curtains"},
        "fallback": "fade",
    },
    "wind": {
        "name": "Wind",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "wind"},
        "fallback": "fade",
    },
    "prestige": {
        "name": "Prestige",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "prestige"},
        "fallback": "fade",
    },
    "fracture": {
        "name": "Fracture",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "fracture"},
        "fallback": "fade",
    },
    "crush": {
        "name": "Crush",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "crush"},
        "fallback": "fade",
    },
    "peel_off": {
        "name": "Peel Off",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "peelOff", "invX": "1"},
        "fallback": "fade",
    },
    "page_curl": {
        "name": "Page Curl",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "pageCurlSingle", "invX": "1"},
        "fallback": "fade",
    },
    "airplane": {
        "name": "Airplane",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "airplane"},
        "fallback": "fade",
    },
    "origami": {
        "name": "Origami",
        "prefix": "p15",
        "element": "prstTrans",
        "attrs": {"prst": "origami"},
        "fallback": "fade",
    },
    "clock": {
        "name": "Clock",
        "element": "wheel",
        "attrs": {"spokes": "1"},
    },
    "ripple": {
        "name": "Ripple",
        "prefix": "p14",
        "element": "ripple",
        "attrs": {},
        "fallback": "fade",
    },
    "honeycomb": {
        "name": "Honeycomb",
        "prefix": "p14",
        "element": "honeycomb",
        "attrs": {},
        "fallback": "fade",
    },
    "glitter": {
        "name": "Glitter",
        "prefix": "p14",
        "element": "glitter",
        "attrs": {},
        "fallback": "fade",
    },
    "vortex": {
        "name": "Vortex",
        "prefix": "p14",
        "element": "vortex",
        "attrs": {"dir": "r"},
        "fallback": "fade",
    },
    "shred": {
        "name": "Shred",
        "prefix": "p14",
        "element": "shred",
        "attrs": {"dir": "out"},
        "fallback": "fade",
    },
    "switch": {
        "name": "Switch",
        "prefix": "p14",
        "element": "switch",
        "attrs": {"dir": "r"},
        "fallback": "fade",
    },
    "flip": {
        "name": "Flip",
        "prefix": "p14",
        "element": "flip",
        "attrs": {"dir": "r"},
        "fallback": "fade",
    },
    "gallery": {
        "name": "Gallery",
        "prefix": "p14",
        "element": "gallery",
        "attrs": {"dir": "r"},
        "fallback": "fade",
    },
    "cube": {
        "name": "Cube",
        "prefix": "p14",
        "element": "prism",
        "attrs": {"dir": "r"},
        "fallback": "fade",
    },
    "doors": {
        "name": "Doors",
        "prefix": "p14",
        "element": "doors",
        "attrs": {"dir": "vert"},
        "fallback": "fade",
    },
    "box": {
        "name": "Box",
        "element": "zoom",
        "attrs": {},
    },
    # Current PowerPoint transition gallery: Dynamic Content.
    "pan": {
        "name": "Pan",
        "prefix": "p14",
        "element": "pan",
        "attrs": {"dir": "r"},
        "fallback": "fade",
    },
    "ferris_wheel": {
        "name": "Ferris Wheel",
        "prefix": "p14",
        "element": "ferris",
        "attrs": {"dir": "r"},
        "fallback": "fade",
    },
    "conveyor": {
        "name": "Conveyor",
        "prefix": "p14",
        "element": "conveyor",
        "attrs": {"dir": "r"},
        "fallback": "fade",
    },
    "rotate": {
        "name": "Rotate",
        "prefix": "p14",
        "element": "prism",
        "attrs": {"dir": "r", "isContent": "1"},
        "fallback": "fade",
    },
    "window": {
        "name": "Window",
        "prefix": "p14",
        "element": "window",
        "attrs": {},
        "fallback": "fade",
    },
    "orbit": {
        "name": "Orbit",
        "prefix": "p14",
        "element": "prism",
        "attrs": {"dir": "r", "isContent": "1", "isInverted": "1"},
        "fallback": "fade",
    },
    "fly_through": {
        "name": "Fly Through",
        "prefix": "p14",
        "element": "flythrough",
        "attrs": {},
        "fallback": "fade",
    },
}

TRANSITION_CATEGORIES = ("subtle", "exciting", "dynamic_content")
_TRANSITION_KEYS_BY_CATEGORY = {
    "subtle": (
        "morph",
        "fade",
        "push",
        "wipe",
        "split",
        "reveal",
        "cut",
        "random_bars",
        "shape",
        "uncover",
        "cover",
        "flash",
    ),
    "exciting": (
        "fall_over",
        "drape",
        "curtains",
        "wind",
        "prestige",
        "fracture",
        "crush",
        "peel_off",
        "page_curl",
        "airplane",
        "origami",
        "dissolve",
        "checkerboard",
        "blinds",
        "clock",
        "ripple",
        "honeycomb",
        "glitter",
        "vortex",
        "shred",
        "switch",
        "flip",
        "gallery",
        "cube",
        "doors",
        "box",
        "comb",
        "zoom",
        "random",
    ),
    "dynamic_content": (
        "pan",
        "ferris_wheel",
        "conveyor",
        "rotate",
        "window",
        "orbit",
        "fly_through",
    ),
}

TRANSITION_EFFECT_OPTION_FIELDS = (
    "direction",
    "orientation",
    "style",
    "shape",
    "pattern",
    "origin",
    "pages",
    "morph_by",
    "through_black",
    "bounce",
)


def _enum_option(
    default: str,
    values: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "enum",
        "default": default,
        "values": dict(values),
    }


def _attribute_enum(
    default: str,
    attribute: str,
    values: Mapping[str, str | None],
) -> dict[str, Any]:
    overrides: dict[str, dict[str, Any]] = {}
    for name, value in values.items():
        if value is None:
            overrides[name] = {"remove_attrs": (attribute,)}
        else:
            overrides[name] = {"attrs": {attribute: value}}
    return _enum_option(default, overrides)


def _boolean_option(
    default: bool,
    attribute: str,
) -> dict[str, Any]:
    return {
        "type": "boolean",
        "default": default,
        "values": {
            False: {"remove_attrs": (attribute,)},
            True: {"attrs": {attribute: "1"}},
        },
    }


_CARDINAL_DIRECTIONS = {
    "left": None,
    "right": "r",
    "up": "u",
    "down": "d",
}
_CORNER_DIRECTIONS = {
    **_CARDINAL_DIRECTIONS,
    "up_left": "lu",
    "up_right": "ru",
    "down_left": "ld",
    "down_right": "rd",
}
_TRANSITION_EFFECT_OPTIONS: dict[str, dict[str, dict[str, Any]]] = {
    "morph": {
        "morph_by": _attribute_enum(
            "object",
            "option",
            {
                "object": "byObject",
                "word": "byWord",
                "character": "byChar",
            },
        ),
    },
    "fade": {
        "style": _attribute_enum(
            "smoothly",
            "thruBlk",
            {"smoothly": None, "through_black": "1"},
        ),
    },
    "push": {
        "direction": _attribute_enum(
            "right",
            "dir",
            {**_CARDINAL_DIRECTIONS, "left": "l"},
        ),
    },
    "wipe": {
        "direction": _attribute_enum("right", "dir", _CARDINAL_DIRECTIONS),
    },
    "split": {
        "orientation": _attribute_enum(
            "horizontal",
            "orient",
            {"horizontal": None, "vertical": "vert"},
        ),
        "direction": _attribute_enum(
            "out",
            "dir",
            {"in": "in", "out": None},
        ),
    },
    "reveal": {
        "direction": _attribute_enum(
            "right",
            "dir",
            {"left": None, "right": "r"},
        ),
        "through_black": _boolean_option(False, "thruBlk"),
    },
    "cut": {
        "through_black": {
            "type": "boolean",
            "default": False,
            "values": {
                False: {"remove_attrs": ("thruBlk",)},
                True: {"attrs": {"thruBlk": "1"}},
            },
        },
    },
    "random_bars": {
        "orientation": _attribute_enum(
            "vertical",
            "dir",
            {"horizontal": None, "vertical": "vert"},
        ),
    },
    "shape": {
        "shape": _enum_option(
            "circle",
            {
                "circle": {"element": "circle"},
                "diamond": {"element": "diamond"},
                "plus": {"element": "plus"},
            },
        ),
    },
    "uncover": {
        "direction": _attribute_enum("right", "dir", _CORNER_DIRECTIONS),
    },
    "cover": {
        "direction": _attribute_enum("right", "dir", _CORNER_DIRECTIONS),
    },
    "fall_over": {
        "direction": _attribute_enum(
            "right",
            "invX",
            {"left": None, "right": "1"},
        ),
    },
    "drape": {
        "direction": _attribute_enum(
            "right",
            "invX",
            {"left": None, "right": "1"},
        ),
    },
    "wind": {
        "direction": _attribute_enum(
            "right",
            "invX",
            {"left": "1", "right": None},
        ),
    },
    "peel_off": {
        "direction": _attribute_enum(
            "right",
            "invX",
            {"left": None, "right": "1"},
        ),
    },
    "page_curl": {
        "direction": _attribute_enum(
            "right",
            "invX",
            {"left": None, "right": "1"},
        ),
        "pages": _attribute_enum(
            "single",
            "prst",
            {"single": "pageCurlSingle", "double": "pageCurlDouble"},
        ),
    },
    "airplane": {
        "direction": _attribute_enum(
            "right",
            "invX",
            {"left": "1", "right": None},
        ),
    },
    "origami": {
        "direction": _attribute_enum(
            "right",
            "invX",
            {"left": "1", "right": None},
        ),
    },
    "checkerboard": {
        "direction": _attribute_enum(
            "across",
            "dir",
            {"across": None, "down": "vert"},
        ),
    },
    "blinds": {
        "orientation": _attribute_enum(
            "vertical",
            "dir",
            {"horizontal": None, "vertical": "vert"},
        ),
    },
    "clock": {
        "style": _enum_option(
            "clockwise",
            {
                "clockwise": {
                    "element": "wheel",
                    "attrs": {"spokes": "1"},
                },
                "counterclockwise": {
                    "prefix": "p14",
                    "element": "wheelReverse",
                    "attrs": {"spokes": "1"},
                    "fallback": "fade",
                },
                "wedge": {
                    "element": "wedge",
                    "remove_attrs": ("spokes",),
                },
            },
        ),
    },
    "ripple": {
        "origin": _attribute_enum(
            "center",
            "dir",
            {
                "center": None,
                "up_left": "lu",
                "up_right": "ru",
                "down_left": "ld",
                "down_right": "rd",
            },
        ),
    },
    "glitter": {
        "shape": _attribute_enum(
            "diamond",
            "pattern",
            {"diamond": None, "hexagon": "hexagon"},
        ),
        "direction": _attribute_enum(
            "right",
            "dir",
            {
                "left": "r",
                "right": None,
                "up": "d",
                "down": "u",
            },
        ),
    },
    "vortex": {
        "direction": _attribute_enum("right", "dir", _CARDINAL_DIRECTIONS),
    },
    "shred": {
        "pattern": _attribute_enum(
            "strips",
            "pattern",
            {"strips": None, "rectangle": "rectangle"},
        ),
        "direction": _attribute_enum(
            "out",
            "dir",
            {"in": None, "out": "out"},
        ),
    },
    "switch": {
        "direction": _attribute_enum(
            "right",
            "dir",
            {"left": "l", "right": "r"},
        ),
    },
    "flip": {
        "direction": _attribute_enum(
            "right",
            "dir",
            {"left": "l", "right": "r"},
        ),
    },
    "gallery": {
        "direction": _attribute_enum(
            "right",
            "dir",
            {"left": "l", "right": "r"},
        ),
    },
    "cube": {
        "direction": _attribute_enum("right", "dir", _CARDINAL_DIRECTIONS),
    },
    "doors": {
        "orientation": _attribute_enum(
            "vertical",
            "dir",
            {"horizontal": None, "vertical": "vert"},
        ),
    },
    "box": {
        "direction": _attribute_enum(
            "out",
            "dir",
            {"in": "in", "out": None},
        ),
    },
    "comb": {
        "orientation": _attribute_enum(
            "horizontal",
            "dir",
            {"horizontal": None, "vertical": "vert"},
        ),
    },
    "zoom": {
        "direction": _attribute_enum(
            "in",
            "dir",
            {"in": "in", "out": None},
        ),
    },
    "pan": {
        "direction": _attribute_enum("right", "dir", _CARDINAL_DIRECTIONS),
    },
    "ferris_wheel": {
        "direction": _attribute_enum(
            "right",
            "dir",
            {"left": "l", "right": "r"},
        ),
    },
    "conveyor": {
        "direction": _attribute_enum(
            "right",
            "dir",
            {"left": "l", "right": "r"},
        ),
    },
    "rotate": {
        "direction": _attribute_enum("right", "dir", _CARDINAL_DIRECTIONS),
    },
    "window": {
        "orientation": _attribute_enum(
            "horizontal",
            "dir",
            {"horizontal": None, "vertical": "vert"},
        ),
    },
    "orbit": {
        "direction": _attribute_enum("right", "dir", _CARDINAL_DIRECTIONS),
    },
    "fly_through": {
        "direction": _attribute_enum(
            "in",
            "dir",
            {"in": None, "out": "out"},
        ),
        "bounce": _boolean_option(False, "hasBounce"),
    },
}

NATIVE_TRANSITIONS: dict[str, dict[str, Any]] = {}
for _category in TRANSITION_CATEGORIES:
    for _key in _TRANSITION_KEYS_BY_CATEGORY[_category]:
        if _key in NATIVE_TRANSITIONS:
            raise RuntimeError(f"duplicate native transition key: {_key}")
        _spec = dict(_TRANSITION_SPECS[_key])
        _spec["category"] = _category
        _spec["effectOptions"] = _TRANSITION_EFFECT_OPTIONS.get(_key, {})
        NATIVE_TRANSITIONS[_key] = _spec
if set(NATIVE_TRANSITIONS) != set(_TRANSITION_SPECS):
    raise RuntimeError("native transition gallery categories are incomplete")
if len(NATIVE_TRANSITIONS) != 48:
    raise RuntimeError(
        f"native transition gallery count changed: {len(NATIVE_TRANSITIONS)}"
    )
for _key, _spec in NATIVE_TRANSITIONS.items():
    _options = _spec["effectOptions"]
    _unknown_options = set(_options) - set(TRANSITION_EFFECT_OPTION_FIELDS)
    if _unknown_options:
        raise RuntimeError(
            f"native transition {_key!r} has unknown effect option(s): "
            + ", ".join(sorted(_unknown_options))
        )
    for _option_name, _option_spec in _options.items():
        if _option_spec.get("type") not in {"enum", "boolean"}:
            raise RuntimeError(
                f"native transition {_key!r} option {_option_name!r} "
                "must be enum or boolean"
            )
        _values = _option_spec.get("values")
        if not isinstance(_values, dict) or not _values:
            raise RuntimeError(
                f"native transition {_key!r} option {_option_name!r} "
                "must define values"
            )
        if _option_spec.get("default") not in _values:
            raise RuntimeError(
                f"native transition {_key!r} option {_option_name!r} "
                "has an unknown default"
            )

NATIVE_TRANSITION_KEYS = tuple(NATIVE_TRANSITIONS)
# Retained as a module-level compatibility name for existing imports. New code
# should use ``NATIVE_TRANSITIONS`` to distinguish the native registry from
# accepted legacy input names.
CANONICAL_TRANSITIONS = NATIVE_TRANSITIONS

TRANSITION_ALIASES: dict[str, str] = {
    "strips": "wipe",
    "circle": "shape",
    "diamond": "shape",
    "newsflash": "flash",
    "plus": "shape",
    "pull": "uncover",
    "wedge": "clock",
    "wheel": "clock",
}
LEGACY_TRANSITION_KEYS = tuple(TRANSITION_ALIASES)
TRANSITION_ALIAS_OPTIONS: dict[str, dict[str, object]] = {
    "strips": {"direction": "right"},
    "circle": {"shape": "circle"},
    "diamond": {"shape": "diamond"},
    "plus": {"shape": "plus"},
    "wedge": {"style": "wedge"},
    "wheel": {"style": "clockwise"},
}
TRANSITIONS: dict[str, dict[str, Any]] = dict(NATIVE_TRANSITIONS)
for _alias, _canonical_key in TRANSITION_ALIASES.items():
    TRANSITIONS[_alias] = NATIVE_TRANSITIONS[_canonical_key]

TRANSITION_NAMESPACES = {
    "p": PML_NS,
    "p14": P14_NS,
    "p15": P15_NS,
    "p159": P159_NS,
}

for _prefix, _uri in (
    *TRANSITION_NAMESPACES.items(),
    ("mc", MC_NS),
    ("r", RELATIONSHIPS_NS),
):
    try:
        ET.register_namespace(_prefix, _uri)
    except (AttributeError, ValueError):
        pass
