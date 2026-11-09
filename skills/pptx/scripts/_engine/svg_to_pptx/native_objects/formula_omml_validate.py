# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Formula omml validate implementation."""
from __future__ import annotations

from .formula_omml_shared import (
    DRAWING_NS,
    ET,
    FormulaCompileError,
    MATH_NS,
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
    re,
)

def _qualified_name(name: str) -> tuple[str | None, str]:
    if name.startswith("{") and "}" in name:
        namespace, local_name = name[1:].split("}", 1)
        return namespace, local_name
    return None, name


def _child_names(element: ET.Element) -> list[str]:
    return [_qualified_name(child.tag)[1] for child in element]


def _require_children(
    element: ET.Element,
    expected: tuple[str, ...],
) -> None:
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
    from .formula_omml_emit import _math_tag
    value = element.get(_math_tag("val"))
    if value is None:
        element_name = _qualified_name(element.tag)[1]
        raise FormulaCompileError(f"m:{element_name} requires m:val")
    return value


def _validate_math_value(element: ET.Element, element_name: str) -> None:
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
    from .formula_omml_emit import _math_tag
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


def _validate_xml_tree(root: ET.Element) -> None:
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
    pending: list[tuple[ET.Element, int]] = [(root, 1)]
    while pending:
        element, depth = pending.pop()
        if depth > _MAX_OMML_DEPTH:
            raise FormulaCompileError(
                f"OMML nesting exceeds {_MAX_OMML_DEPTH} levels"
            )
        pending.extend((child, depth + 1) for child in reversed(element))


def _parse_omml_with_resource_limits(xml: str) -> ET.Element:
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
    root = _parse_omml_with_resource_limits(xml)
    return _serialize_omml_with_resource_limits(root)


def validate_omml_fragment(xml: str) -> str:
    """Validate emitted Office Math XML and canonicalize namespace prefixes."""
    root = _parse_omml_with_resource_limits(xml)
    _validate_xml_tree(root)
    return _serialize_omml_with_resource_limits(root)
