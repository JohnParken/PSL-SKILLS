#!/usr/bin/env python3
"""
PPT Master - Native Formula OMML Emitter

Emit the Microsoft 365 LaTeX profile AST as editable Office Math XML and
validate the narrow XML vocabulary produced by this module.

See references/native-formula.md for the owning compatibility contract.

Usage:
    Imported by formula_compiler.py.

Examples:
    emit_omml(expression, display=True)

Dependencies:
    None (only uses standard library and local PPT Master modules)
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from .formula_ast import (
    Accent,
    AlignmentPoint,
    Bar,
    BorderBox,
    Delimiter,
    EquationArray,
    Fraction,
    Function,
    GroupChar,
    Limit,
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
    merge_run_styles,
)
from .formula_parser import FormulaCompileError



# Adapted from formula_omml.py; upstream MIT, Hugo He, 2025-2026.
def _validate_xml_tree(root: ET.Element) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        DRAWING_NS,
        MATH_NS,
        XML_NS,
        _DRAWING_ATTRIBUTES,
        _DRAWING_ELEMENTS,
        _MATH_ELEMENTS,
        _VALUE_ELEMENTS,
        _qualified_name,
        _validate_drawing_element,
        _validate_math_structure,
        _validate_matrix_dimensions,
    )
    namespace, local_name = _qualified_name(root.tag)
    if namespace != MATH_NS or local_name not in {"oMathPara", "oMath"}:
        raise FormulaCompileError("OMML root must be m:oMathPara or m:oMath")

    math_roots = 0
    pending = [root]
    while pending:
        element = pending.pop()
        pending.extend(reversed(element))
        element_namespace, element_name = _qualified_name(element.tag)
        if element_namespace == MATH_NS and element_name not in _MATH_ELEMENTS:
            raise FormulaCompileError(
                f"OMML contains unsupported math element: {element_name!r}"
            )
        if element_namespace == DRAWING_NS and element_name not in _DRAWING_ELEMENTS:
            raise FormulaCompileError(
                f"OMML contains unsupported DrawingML element: {element_name!r}"
            )
        if element_namespace not in {MATH_NS, DRAWING_NS}:
            raise FormulaCompileError(
                f"OMML contains unsupported element namespace: {element_namespace!r}"
            )
        if element_namespace == MATH_NS and element_name in {"oMathPara", "oMath"}:
            math_roots += 1
        for attribute, value in element.attrib.items():
            attribute_namespace, attribute_name = _qualified_name(attribute)
            if (
                attribute_namespace == MATH_NS
                and attribute_name == "val"
                and element_namespace == MATH_NS
                and element_name in _VALUE_ELEMENTS
            ):
                continue
            if (
                attribute_namespace in {None, DRAWING_NS}
                and element_namespace == DRAWING_NS
                and attribute_name in _DRAWING_ATTRIBUTES
            ):
                continue
            if (
                attribute_namespace == XML_NS
                and element_namespace == MATH_NS
                and element_name == "t"
                and attribute_name == "space"
                and value == "preserve"
            ):
                continue
            raise FormulaCompileError(
                f"OMML contains unsupported attribute {attribute_name!r}"
            )
        if element_namespace == MATH_NS:
            _validate_math_structure(element, element_name)
        else:
            _validate_drawing_element(element, element_name)
        if element.text:
            if not (
                element_namespace == MATH_NS and element_name == "t"
            ) and element.text.strip():
                raise FormulaCompileError(
                    f"OMML text is only allowed inside m:t, found in {element_name!r}"
                )
        if element.tail and element.tail.strip():
            raise FormulaCompileError("OMML contains unexpected trailing text")

    expected_roots = 2 if local_name == "oMathPara" else 1
    if math_roots != expected_roots:
        raise FormulaCompileError("OMML must contain exactly one math expression")
    _validate_matrix_dimensions(root)
    if local_name == "oMathPara":
        direct_expressions = [
            child
            for child in root
            if _qualified_name(child.tag) == (MATH_NS, "oMath")
        ]
        if len(direct_expressions) != 1:
            raise FormulaCompileError(
                "m:oMathPara must contain exactly one direct m:oMath expression"
            )


def _validate_xml_depth(root: ET.Element) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _MAX_OMML_DEPTH,
    )
    pending: list[tuple[ET.Element, int]] = [(root, 1)]
    while pending:
        element, depth = pending.pop()
        if depth > _MAX_OMML_DEPTH:
            raise FormulaCompileError(
                f"OMML nesting exceeds {_MAX_OMML_DEPTH} levels"
            )
        pending.extend((child, depth + 1) for child in reversed(element))


def _parse_omml_with_resource_limits(xml: str) -> ET.Element:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _FORBIDDEN_XML_RE,
        _MAX_OMML_LENGTH,
        _validate_xml_depth,
    )
    if not isinstance(xml, str):
        raise FormulaCompileError("OMML fragment must be a string")
    if not xml.strip():
        raise FormulaCompileError("OMML fragment is empty")
    if len(xml) > _MAX_OMML_LENGTH:
        raise FormulaCompileError(
            f"OMML fragment exceeds the {_MAX_OMML_LENGTH}-character limit"
        )
    if _FORBIDDEN_XML_RE.search(xml):
        raise FormulaCompileError("DOCTYPE and ENTITY declarations are forbidden in OMML")
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, RecursionError) as exc:
        raise FormulaCompileError(f"Invalid OMML XML: {exc}") from exc
    _validate_xml_depth(root)
    return root


def _serialize_omml_with_resource_limits(root: ET.Element) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        DRAWING_NS,
        MATH_NS,
        _MAX_OMML_LENGTH,
    )
    ET.register_namespace("m", MATH_NS)
    ET.register_namespace("a", DRAWING_NS)
    try:
        canonical = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    except RecursionError as exc:
        raise FormulaCompileError("OMML nesting exceeds the XML serializer limit") from exc
    if len(canonical) > _MAX_OMML_LENGTH:
        raise FormulaCompileError(
            f"Canonical OMML exceeds the {_MAX_OMML_LENGTH}-character limit"
        )
    return canonical


def validate_omml_resource_limits(xml: str) -> str:
    """Apply only the shared serialized-size and XML-depth resource limits."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _parse_omml_with_resource_limits,
        _serialize_omml_with_resource_limits,
    )
    root = _parse_omml_with_resource_limits(xml)
    return _serialize_omml_with_resource_limits(root)


def validate_omml_fragment(xml: str) -> str:
    """Validate emitted Office Math XML and canonicalize namespace prefixes."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _parse_omml_with_resource_limits,
        _serialize_omml_with_resource_limits,
        _validate_xml_tree,
    )
    root = _parse_omml_with_resource_limits(xml)
    _validate_xml_tree(root)
    return _serialize_omml_with_resource_limits(root)


def emit_omml(expression: Sequence, *, display: bool) -> str:
    """Emit one block or inline Office Math root from the parsed AST."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        DRAWING_NS,
        MATH_NS,
        _append_sequence,
        _math_element,
        _math_tag,
        validate_omml_fragment,
    )
    if display:
        root = ET.Element(_math_tag("oMathPara"))
        properties = _math_element(root, "oMathParaPr")
        _math_element(properties, "jc", val="center")
        math = _math_element(root, "oMath")
    else:
        root = ET.Element(_math_tag("oMath"))
        math = root
    _append_sequence(math, expression, None, display=display)
    ET.register_namespace("m", MATH_NS)
    ET.register_namespace("a", DRAWING_NS)
    xml = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return validate_omml_fragment(xml)


