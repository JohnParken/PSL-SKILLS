"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
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

from pptx_transitions_registry import (
    AUDIO_REL_TYPE,
    Any,
    CANONICAL_TRANSITIONS,
    CONTENT_TYPES_NS,
    CONTENT_TYPES_PART,
    DEFAULT_TRANSITION,
    DEFAULT_TRANSITION_DURATION,
    ET,
    Iterable,
    LEGACY_TRANSITION_KEYS,
    LET,
    MAX_OOXML_MILLISECONDS,
    MAX_OOXML_UNSIGNED_INT,
    MC_NS,
    Mapping,
    MutableMapping,
    NATIVE_TRANSITIONS,
    NATIVE_TRANSITION_KEYS,
    P14_NS,
    P159_NS,
    P15_NS,
    PACKAGE_REL_NS,
    PML_NS,
    PRESENTATION_PROPS_CONTENT_TYPE,
    PRESENTATION_PROPS_PART,
    PRESENTATION_PROPS_REL_TYPE,
    PRESENTATION_RELS_PART,
    Path,
    RELATIONSHIPS_NS,
    TRANSITIONS,
    TRANSITION_ALIASES,
    TRANSITION_ALIAS_OPTIONS,
    TRANSITION_CATEGORIES,
    TRANSITION_EFFECT_OPTION_FIELDS,
    TRANSITION_NAMESPACES,
    WAV_CONTENT_TYPES,
    _CARDINAL_DIRECTIONS,
    _CORNER_DIRECTIONS,
    _TRANSITION_EFFECT_OPTIONS,
    _TRANSITION_KEYS_BY_CATEGORY,
    _TRANSITION_SPECS,
    _alias,
    _attribute_enum,
    _boolean_option,
    _canonical_key,
    _category,
    _enum_option,
    _key,
    _option_name,
    _option_spec,
    _options,
    _prefix,
    _spec,
    _unknown_options,
    _uri,
    _values,
    dataclass,
    field,
    io,
    math,
    posixpath,
    quoteattr,
    re,
    zipfile,
)

@dataclass(frozen=True)
class EnterUpdate:
    """Describe how the current slide's visual transition enters."""

    policy: str = "replace"
    effect: str | None = DEFAULT_TRANSITION
    duration: float = DEFAULT_TRANSITION_DURATION
    effect_options: Mapping[str, object] | None = None


@dataclass(frozen=True)
class AdvanceUpdate:
    """Describe how the current slide advances to the next slide."""

    mode: str = "preserve"
    after: float | None = None


@dataclass(frozen=True)
class TransitionSummary:
    """Read-back summary of one slide's logical transition slot."""

    carrier: str
    logical_count: int
    effect: str | None = None
    effect_namespace: str | None = None
    fallback_effect: str | None = None
    fallback_effect_namespace: str | None = None
    duration_ms: int | None = None
    speed: str | None = None
    advance_on_click: bool | None = None
    advance_after_ms: int | None = None
    effect_attributes: Mapping[str, str] = field(default_factory=dict)
    canonical_effect: str | None = None
    effect_options: Mapping[str, object] = field(default_factory=dict)
    sound_relationship_id: str | None = None
    sound_name: str | None = None
    fallback_sound_relationship_id: str | None = None
    fallback_sound_name: str | None = None


@dataclass(frozen=True)
class MorphPairExpectation:
    """One forced-Morph name expected on two adjacent generated slides."""

    source_slide_number: int
    destination_slide_number: int
    key: str

    @property
    def shape_name(self) -> str:
        return f"!!{self.key}"


from pptx_transitions_validation import (
    _qn,
    _local_name,
    _namespace_name,
    _value_repr,
    validate_seconds,
    normalize_transition_effect,
    normalize_transition_effect_options,
    normalize_transition_effect_request,
    describe_transition_effect,
    _seconds_to_ms,
)
from pptx_transitions_creation import (
    _effect_spec,
    _effect_xml,
    _transition_attributes,
    _normalize_transition_sound,
    _transition_sound_xml,
    create_transition_xml,
    _is_lxml_element,
    _new_element,
    _build_transition_element,
)
from pptx_transitions_reading import (
    _transition_elements,
    transition_carriers,
    _primary_and_fallback,
    _effect_identity,
    _sound_identity,
    _effective_transition_options,
    _transition_option_combinations,
    _identify_native_transition,
    _int_attribute,
    _bool_attribute,
    read_slide_transition,
)
from pptx_transitions_mutation import (
    _captured_advance,
    _resolve_advance,
    _set_advance_attributes,
    _insert_transition_carrier,
    _apply_slide_motion_unchecked,
    _visual_identity,
    _expected_visual_identity,
    _validate_applied_motion,
    apply_slide_motion,
)
from pptx_transitions_xml import (
    _xml_declaration,
    _serialize_lxml_like,
    namespace_bindings,
    register_source_namespaces,
    parse_source_xml,
    _required_mce_prefixes,
    _inject_root_namespace_declarations,
    validate_mce_prefixes,
    serialize_source_xml,
    apply_slide_motion_xml,
    read_slide_transition_xml,
    validate_generated_transition_xml,
)
from pptx_transitions_package import (
    _slide_relationships_part,
    _resolve_slide_relationship_target,
    _transition_sound_elements,
    _package_part_content_type,
    _validate_transition_sound_package_parts,
    validate_pptx_transition_package,
)
from pptx_transitions_morph import (
    _top_level_shape_types_by_name,
    validate_pptx_morph_pairs,
    _validate_package_use_timings,
    validate_slide_transition_structure,
    validate_slide_transition_xml,
)
from pptx_transitions_show import (
    _insert_show_properties,
    _next_relationship_id,
    _presentation_props_part,
    _ensure_presentation_props_references,
    set_package_use_timings,
    set_package_kiosk_show,
    set_directory_use_timings,
)

