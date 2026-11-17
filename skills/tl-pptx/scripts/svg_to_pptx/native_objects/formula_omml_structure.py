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
def _qualified_name(name: str) -> tuple[str | None, str]:
    if name.startswith("{") and "}" in name:
        namespace, local_name = name[1:].split("}", 1)
        return namespace, local_name
    return None, name


def _child_names(element: ET.Element) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _qualified_name,
    )
    return [_qualified_name(child.tag)[1] for child in element]


def _require_children(
    element: ET.Element,
    expected: tuple[str, ...],
) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _child_names,
        _qualified_name,
    )
    element_name = _qualified_name(element.tag)[1]
    actual = tuple(_child_names(element))
    if actual != expected:
        raise FormulaCompileError(
            f"m:{element_name} children must be {expected!r}, found {actual!r}"
        )


def _require_repeated_children(
    element: ET.Element,
    child_name: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _child_names,
        _qualified_name,
    )
    element_name = _qualified_name(element.tag)[1]
    actual = _child_names(element)
    if (
        not minimum <= len(actual) <= maximum
        or any(name != child_name for name in actual)
    ):
        raise FormulaCompileError(
            f"m:{element_name} must contain {minimum}..{maximum} "
            f"m:{child_name} children"
        )


def _validate_property_children(element: ET.Element, element_name: str) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _PROPERTY_ORDER,
        _child_names,
    )
    expected_order = _PROPERTY_ORDER[element_name]
    actual = _child_names(element)
    if len(actual) != len(set(actual)):
        raise FormulaCompileError(f"m:{element_name} contains duplicate properties")
    unknown = [name for name in actual if name not in expected_order]
    if unknown:
        raise FormulaCompileError(
            f"m:{element_name} contains unsupported properties: {unknown!r}"
        )
    order = [expected_order.index(name) for name in actual]
    if order != sorted(order):
        raise FormulaCompileError(f"m:{element_name} properties are out of order")
    if element_name == "rPr" and "nor" in actual and (
        "scr" in actual or "sty" in actual
    ):
        raise FormulaCompileError(
            "m:rPr cannot combine m:nor with m:scr or m:sty"
        )


def _math_value(element: ET.Element) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _math_tag,
        _qualified_name,
    )
    value = element.get(_math_tag("val"))
    if value is None:
        element_name = _qualified_name(element.tag)[1]
        raise FormulaCompileError(f"m:{element_name} requires m:val")
    return value


def _validate_math_value(element: ET.Element, element_name: str) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _ON_OFF_ELEMENTS,
        _math_value,
    )
    value = _math_value(element)
    allowed_values = {
        "baseJc": {"top", "center", "bot"},
        "jc": {"left", "right", "center", "centerGroup"},
        "limLoc": {"subSup", "undOvr"},
        "pos": {"top", "bot"},
        "scr": {
            "roman", "sans-serif", "monospace", "double-struck",
            "script", "fraktur",
        },
        "sty": {"p", "b", "i", "bi"},
        "type": {"bar", "lin", "noBar", "skw"},
        "vertJc": {"top", "bot"},
    }
    if element_name in _ON_OFF_ELEMENTS:
        if value not in {"on", "off", "true", "false", "1", "0"}:
            raise FormulaCompileError(
                f"m:{element_name} has invalid on/off value: {value!r}"
            )
        return
    if element_name == "count":
        if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 64:
            raise FormulaCompileError("m:count must be an integer from 1 to 64")
        return
    if element_name in {"begChr", "chr", "endChr", "sepChr"}:
        if len(value) > 1:
            raise FormulaCompileError(
                f"m:{element_name} must contain at most one character"
            )
        return
    allowed = allowed_values.get(element_name)
    if allowed is not None and value not in allowed:
        raise FormulaCompileError(
            f"m:{element_name} has invalid value: {value!r}"
        )


def _validate_drawing_element(element: ET.Element, element_name: str) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _child_names,
        _qualified_name,
    )
    attributes = {
        _qualified_name(name)[1]: value for name, value in element.attrib.items()
    }
    children = _child_names(element)
    if element_name == "rPr":
        unknown = set(attributes) - {"b", "dirty", "i", "lang", "sz"}
        if unknown:
            raise FormulaCompileError(
                f"a:rPr contains unsupported attributes: {sorted(unknown)!r}"
            )
        for name in ("b", "dirty", "i"):
            if name in attributes and attributes[name] not in {
                "on", "off", "true", "false", "1", "0",
            }:
                raise FormulaCompileError(
                    f"a:rPr@{name} has invalid on/off value"
                )
        if "sz" in attributes and (
            not attributes["sz"].isascii()
            or not attributes["sz"].isdigit()
            or not 100 <= int(attributes["sz"]) <= 400_000
        ):
            raise FormulaCompileError("a:rPr@sz must be from 100 to 400000")
        expected_order = ("solidFill", "latin", "ea", "cs")
        if len(children) != len(set(children)):
            raise FormulaCompileError("a:rPr contains duplicate child properties")
        if any(name not in expected_order for name in children):
            raise FormulaCompileError("a:rPr contains unsupported child properties")
        order = [expected_order.index(name) for name in children]
        if order != sorted(order):
            raise FormulaCompileError("a:rPr child properties are out of order")
        return
    if element_name == "solidFill":
        if attributes or children != ["srgbClr"]:
            raise FormulaCompileError("a:solidFill must contain one a:srgbClr")
        return
    if element_name == "srgbClr":
        if children or set(attributes) != {"val"} or not re.fullmatch(
            r"[0-9A-Fa-f]{6}", attributes["val"]
        ):
            raise FormulaCompileError("a:srgbClr@val must be six hexadecimal digits")
        return
    if element_name in {"latin", "ea", "cs"}:
        if children or set(attributes) != {"typeface"} or not attributes["typeface"]:
            raise FormulaCompileError(
                f"a:{element_name} must contain one non-empty typeface attribute"
            )


def _validate_math_structure(element: ET.Element, element_name: str) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        DRAWING_NS,
        MATH_NS,
        _ARGUMENT_CONTAINERS,
        _ARGUMENT_ELEMENTS,
        _EMPTY_PROPERTY_ELEMENTS,
        _PROPERTY_ORDER,
        _VALUE_ELEMENTS,
        _child_names,
        _qualified_name,
        _require_children,
        _require_repeated_children,
        _validate_math_value,
        _validate_property_children,
    )
    if element_name in _ARGUMENT_CONTAINERS:
        children = list(element)
        if element_name == "oMath" and not children:
            raise FormulaCompileError("m:oMath must contain a math expression")
        for child in children:
            namespace, child_name = _qualified_name(child.tag)
            if namespace != MATH_NS or child_name not in _ARGUMENT_ELEMENTS:
                raise FormulaCompileError(
                    f"m:{element_name} contains an invalid math argument child"
                )
        return
    fixed_children = {
        "acc": ("accPr", "e"),
        "bar": ("barPr", "e"),
        "borderBox": ("borderBoxPr", "e"),
        "box": ("boxPr", "e"),
        "f": ("fPr", "num", "den"),
        "func": ("funcPr", "fName", "e"),
        "groupChr": ("groupChrPr", "e"),
        "limLow": ("limLowPr", "e", "lim"),
        "limUpp": ("limUppPr", "e", "lim"),
        "mc": ("mcPr",),
        "nary": ("naryPr", "sub", "sup", "e"),
        "phant": ("phantPr", "e"),
        "rad": ("radPr", "deg", "e"),
        "sPre": ("sPrePr", "sub", "sup", "e"),
        "sSub": ("e", "sub"),
        "sSubSup": ("e", "sub", "sup"),
        "sSup": ("e", "sup"),
    }
    if element_name in fixed_children:
        _require_children(element, fixed_children[element_name])
        return
    if element_name == "oMathPara":
        children = tuple(_child_names(element))
        if children not in {("oMath",), ("oMathParaPr", "oMath")}:
            raise FormulaCompileError(
                "m:oMathPara must contain optional m:oMathParaPr then one m:oMath"
            )
        return
    if element_name == "d":
        children = _child_names(element)
        rows = children[1:] if children and children[0] == "dPr" else []
        if not 1 <= len(rows) <= 64 or any(name != "e" for name in rows):
            raise FormulaCompileError("m:d must contain m:dPr then 1..64 m:e children")
        return
    if element_name == "eqArr":
        children = _child_names(element)
        offset = 1 if children and children[0] == "eqArrPr" else 0
        rows = children[offset:]
        if not 1 <= len(rows) <= 64 or any(name != "e" for name in rows):
            raise FormulaCompileError("m:eqArr must contain 1..64 m:e rows")
        return
    if element_name == "m":
        children = _child_names(element)
        rows = children[1:] if children and children[0] == "mPr" else []
        if not 1 <= len(rows) <= 256 or any(name != "mr" for name in rows):
            raise FormulaCompileError("m:m must contain m:mPr then 1..256 m:mr rows")
        return
    if element_name == "mr":
        _require_repeated_children(element, "e", minimum=1, maximum=64)
        return
    if element_name == "mcs":
        _require_repeated_children(element, "mc", minimum=1, maximum=64)
        return
    if element_name == "r":
        children = [(_qualified_name(child.tag), child) for child in element]
        index = 0
        if index < len(children) and children[index][0] == (MATH_NS, "rPr"):
            index += 1
        if index < len(children) and children[index][0] == (DRAWING_NS, "rPr"):
            index += 1
        if index != len(children) - 1 or children[index][0] != (MATH_NS, "t"):
            raise FormulaCompileError(
                "m:r must contain optional m:rPr, optional a:rPr, then one m:t"
            )
        return
    if element_name == "ctrlPr":
        children = [(_qualified_name(child.tag), child) for child in element]
        if len(children) > 1 or (
            children and children[0][0] != (DRAWING_NS, "rPr")
        ):
            raise FormulaCompileError(
                "m:ctrlPr may contain only one optional a:rPr"
            )
        return
    if element_name in _PROPERTY_ORDER:
        _validate_property_children(element, element_name)
        return
    if element_name in _EMPTY_PROPERTY_ELEMENTS:
        if list(element):
            raise FormulaCompileError(f"m:{element_name} must be empty")
        return
    if element_name in _VALUE_ELEMENTS:
        if list(element):
            raise FormulaCompileError(f"m:{element_name} must not contain children")
        _validate_math_value(element, element_name)
        return
    if element_name == "t" and list(element):
        raise FormulaCompileError("m:t must not contain children")


def _validate_matrix_dimensions(root: ET.Element) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .formula_omml import (
        _math_tag,
        _math_value,
    )
    for matrix in root.iter(_math_tag("m")):
        properties = matrix.find(_math_tag("mPr"))
        columns = properties.find(_math_tag("mcs")) if properties is not None else None
        if columns is None:
            raise FormulaCompileError("m:mPr must contain m:mcs")
        column_count = 0
        for column in columns:
            column_properties = column.find(_math_tag("mcPr"))
            count = (
                column_properties.find(_math_tag("count"))
                if column_properties is not None
                else None
            )
            if count is None:
                raise FormulaCompileError("m:mcPr must contain m:count")
            column_count += int(_math_value(count))
        if not 1 <= column_count <= 64:
            raise FormulaCompileError("matrix column count must be from 1 to 64")
        for row in matrix.findall(_math_tag("mr")):
            if len(row.findall(_math_tag("e"))) != column_count:
                raise FormulaCompileError(
                    "every matrix row must match the declared column count"
                )


