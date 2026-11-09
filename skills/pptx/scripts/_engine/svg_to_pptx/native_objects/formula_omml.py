# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Compatibility facade for formula omml helpers.

Implementation is separated by responsibility; public and private import names
remain available here for existing callers.
"""
from __future__ import annotations

from .formula_omml_shared import (
    Accent,
    AlignmentPoint,
    Bar,
    BorderBox,
    DRAWING_NS,
    Delimiter,
    ET,
    EquationArray,
    FormulaCompileError,
    Fraction,
    Function,
    GroupChar,
    Limit,
    MATH_NS,
    Matrix,
    Nary,
    Node,
    OperatorEmulator,
    Phantom,
    Prescript,
    Radical,
    RunStyle,
    Script,
    Sequence,
    Styled,
    Text,
    XML_NS,
    _ARGUMENT_CONTAINERS,
    _ARGUMENT_ELEMENTS,
    _DRAWING_ATTRIBUTES,
    _DRAWING_ELEMENTS,
    _EMPTY_PROPERTY_ELEMENTS,
    _FORBIDDEN_XML_RE,
    _MATH_ELEMENTS,
    _MAX_OMML_DEPTH,
    _MAX_OMML_LENGTH,
    _ON_OFF_ELEMENTS,
    _PROPERTY_ORDER,
    _VALUE_ELEMENTS,
    merge_run_styles,
    re,
)
from .formula_omml_emit import (
    _append_control_style,
    _append_function,
    _append_limit,
    _append_matrix,
    _append_nary,
    _append_node,
    _append_prescript,
    _append_run,
    _append_script,
    _append_sequence,
    _effective_style,
    _math_attrs,
    _math_element,
    _math_tag,
)
from .formula_omml_validate import (
    _child_names,
    _math_value,
    _parse_omml_with_resource_limits,
    _qualified_name,
    _require_children,
    _require_repeated_children,
    _serialize_omml_with_resource_limits,
    _validate_drawing_element,
    _validate_math_structure,
    _validate_math_value,
    _validate_matrix_dimensions,
    _validate_property_children,
    _validate_xml_depth,
    _validate_xml_tree,
    validate_omml_fragment,
    validate_omml_resource_limits,
)
from .formula_omml_document import (
    emit_omml,
)

__all__ = [
    "DRAWING_NS",
    "MATH_NS",
    "emit_omml",
    "validate_omml_fragment",
    "validate_omml_resource_limits",
]
