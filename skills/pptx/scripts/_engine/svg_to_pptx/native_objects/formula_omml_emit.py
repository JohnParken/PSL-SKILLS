# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Formula omml emit implementation."""
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
    merge_run_styles,
)

def _math_tag(local_name: str) -> str:
    return f"{{{MATH_NS}}}{local_name}"


def _math_attrs(**values: str) -> dict[str, str]:
    return {_math_tag(name): value for name, value in values.items()}


def _math_element(
    parent: ET.Element,
    local_name: str,
    **attributes: str,
) -> ET.Element:
    return ET.SubElement(parent, _math_tag(local_name), _math_attrs(**attributes))


def _effective_style(
    inherited: RunStyle | None,
    local: RunStyle | None,
) -> RunStyle | None:
    return merge_run_styles(inherited, local)


def _append_control_style(
    properties: ET.Element,
    style: RunStyle | None,
) -> None:
    """Persist local control-glyph style that cannot live on a math run."""
    if style is None:
        return
    drawing_attributes: dict[str, str] = {}
    if style.style == "bi":
        drawing_attributes.update({"b": "1", "i": "1"})
    if not style.color and not drawing_attributes:
        return
    control_properties = _math_element(properties, "ctrlPr")
    drawing_properties = ET.SubElement(
        control_properties,
        f"{{{DRAWING_NS}}}rPr",
        drawing_attributes,
    )
    if style.color:
        solid_fill = ET.SubElement(
            drawing_properties,
            f"{{{DRAWING_NS}}}solidFill",
        )
        ET.SubElement(
            solid_fill,
            f"{{{DRAWING_NS}}}srgbClr",
            {"val": style.color},
        )


def _append_run(
    parent: ET.Element,
    value: str,
    style: RunStyle | None,
    *,
    literal: bool = False,
) -> None:
    if not value:
        return
    if style is not None and style.script == "script" and any(
        not character.isupper() for character in value
    ):
        start = 0
        uppercase = value[0].isupper()
        for index in range(1, len(value) + 1):
            current = value[index].isupper() if index < len(value) else not uppercase
            if current == uppercase:
                continue
            segment_style = style if uppercase else RunStyle(
                normal=style.normal,
                color=style.color,
                bold=style.bold,
                italic=style.italic,
                typeface=style.typeface,
            )
            _append_run(
                parent,
                value[start:index],
                segment_style,
                literal=literal,
            )
            start = index
            uppercase = current
        return
    run = _math_element(parent, "r")
    if literal or (
        style is not None and (style.style or style.normal or style.script)
    ):
        properties = _math_element(run, "rPr")
        if literal:
            _math_element(properties, "lit", val="on")
        if style is not None and style.normal is True:
            _math_element(properties, "nor", val="on")
        elif style is not None:
            if style.script:
                _math_element(properties, "scr", val=style.script)
            if style.style:
                _math_element(properties, "sty", val=style.style)
    if style is not None and any(
        value is not None
        for value in (style.color, style.bold, style.italic, style.typeface)
    ):
        drawing_attributes: dict[str, str] = {}
        if style.bold is not None:
            drawing_attributes["b"] = "1" if style.bold else "0"
        if style.italic is not None:
            drawing_attributes["i"] = "1" if style.italic else "0"
        drawing_properties = ET.SubElement(
            run,
            f"{{{DRAWING_NS}}}rPr",
            drawing_attributes,
        )
        if style.color:
            solid_fill = ET.SubElement(
                drawing_properties,
                f"{{{DRAWING_NS}}}solidFill",
            )
            ET.SubElement(
                solid_fill,
                f"{{{DRAWING_NS}}}srgbClr",
                {"val": style.color},
            )
        if style.typeface:
            for local_name in ("latin", "ea", "cs"):
                ET.SubElement(
                    drawing_properties,
                    f"{{{DRAWING_NS}}}{local_name}",
                    {"typeface": style.typeface},
                )
    text = _math_element(run, "t")
    if value != value.strip() or "  " in value:
        text.set(f"{{{XML_NS}}}space", "preserve")
    text.text = value


def _append_sequence(
    parent: ET.Element,
    sequence: Sequence,
    inherited_style: RunStyle | None,
    *,
    display: bool,
) -> None:
    for child in sequence.children:
        _append_node(parent, child, inherited_style, display=display)


def _append_node(
    parent: ET.Element,
    node: Node,
    inherited_style: RunStyle | None = None,
    *,
    display: bool,
) -> None:
    if isinstance(node, Text):
        _append_run(
            parent,
            node.value,
            _effective_style(inherited_style, node.style),
            literal=node.literal,
        )
        return
    if isinstance(node, Sequence):
        _append_sequence(parent, node, inherited_style, display=display)
        return
    if isinstance(node, Styled):
        _append_sequence(
            parent,
            node.body,
            _effective_style(inherited_style, node.style),
            display=display,
        )
        return
    if isinstance(node, Fraction):
        fraction = _math_element(parent, "f")
        properties = _math_element(fraction, "fPr")
        _math_element(properties, "type", val=node.kind)
        _append_control_style(properties, inherited_style)
        numerator = _math_element(fraction, "num")
        _append_sequence(numerator, node.numerator, inherited_style, display=display)
        denominator = _math_element(fraction, "den")
        _append_sequence(denominator, node.denominator, inherited_style, display=display)
        return
    if isinstance(node, Radical):
        radical = _math_element(parent, "rad")
        properties = _math_element(radical, "radPr")
        _math_element(properties, "degHide", val="off" if node.degree else "on")
        _append_control_style(properties, inherited_style)
        degree = _math_element(radical, "deg")
        if node.degree is not None:
            _append_sequence(degree, node.degree, inherited_style, display=display)
        body = _math_element(radical, "e")
        _append_sequence(body, node.body, inherited_style, display=display)
        return
    if isinstance(node, Script):
        _append_script(parent, node, inherited_style, display=display)
        return
    if isinstance(node, Prescript):
        _append_prescript(parent, node, inherited_style, display=display)
        return
    if isinstance(node, Nary):
        _append_nary(parent, node, inherited_style, display=display)
        return
    if isinstance(node, Delimiter):
        delimiter = _math_element(parent, "d")
        properties = _math_element(delimiter, "dPr")
        _math_element(properties, "begChr", val=node.left)
        _math_element(properties, "sepChr", val=node.separator)
        _math_element(properties, "endChr", val=node.right)
        _math_element(properties, "grow", val="on")
        _append_control_style(properties, inherited_style)
        for segment in node.segments:
            body = _math_element(delimiter, "e")
            _append_sequence(body, segment, inherited_style, display=display)
        return
    if isinstance(node, Matrix):
        _append_matrix(parent, node, inherited_style, display=display)
        return
    if isinstance(node, AlignmentPoint):
        _append_run(parent, "&", inherited_style)
        return
    if isinstance(node, EquationArray):
        equation_array = _math_element(parent, "eqArr")
        properties = _math_element(equation_array, "eqArrPr")
        _math_element(properties, "baseJc", val="center")
        for row in node.rows:
            body = _math_element(equation_array, "e")
            _append_sequence(body, row, inherited_style, display=display)
        return
    if isinstance(node, Accent):
        accent = _math_element(parent, "acc")
        properties = _math_element(accent, "accPr")
        _math_element(properties, "chr", val=node.character)
        _append_control_style(properties, inherited_style)
        body = _math_element(accent, "e")
        _append_sequence(body, node.body, inherited_style, display=display)
        return
    if isinstance(node, Bar):
        bar = _math_element(parent, "bar")
        properties = _math_element(bar, "barPr")
        _math_element(properties, "pos", val=node.position)
        _append_control_style(properties, inherited_style)
        body = _math_element(bar, "e")
        _append_sequence(body, node.body, inherited_style, display=display)
        return
    if isinstance(node, GroupChar):
        group = _math_element(parent, "groupChr")
        properties = _math_element(group, "groupChrPr")
        _math_element(properties, "chr", val=node.character)
        _math_element(properties, "pos", val=node.position)
        _math_element(properties, "vertJc", val=node.vertical_justification)
        _append_control_style(properties, inherited_style)
        body = _math_element(group, "e")
        _append_sequence(body, node.body, inherited_style, display=display)
        return
    if isinstance(node, Limit):
        _append_limit(parent, node, inherited_style, display=display)
        return
    if isinstance(node, Function):
        _append_function(parent, node, inherited_style, display=display)
        return
    if isinstance(node, OperatorEmulator):
        box = _math_element(parent, "box")
        properties = _math_element(box, "boxPr")
        _math_element(properties, "opEmu", val="on")
        _append_control_style(properties, inherited_style)
        body = _math_element(box, "e")
        _append_sequence(body, node.body, inherited_style, display=display)
        return
    if isinstance(node, Phantom):
        phantom = _math_element(parent, "phant")
        properties = _math_element(phantom, "phantPr")
        if node.kind == "phantom":
            _math_element(properties, "show", val="off")
        elif node.kind == "hphantom":
            _math_element(properties, "show", val="off")
            _math_element(properties, "zeroAsc", val="on")
            _math_element(properties, "zeroDesc", val="on")
        elif node.kind == "vphantom":
            _math_element(properties, "show", val="off")
            _math_element(properties, "zeroWid", val="on")
        else:
            raise FormulaCompileError(f"Unsupported phantom kind: {node.kind!r}")
        body = _math_element(phantom, "e")
        _append_sequence(body, node.body, inherited_style, display=display)
        return
    if isinstance(node, BorderBox):
        border = _math_element(parent, "borderBox")
        properties = _math_element(border, "borderBoxPr")
        if node.kind in {"cancel", "bcancel", "xcancel"}:
            for side in ("hideTop", "hideBot", "hideLeft", "hideRight"):
                _math_element(properties, side, val="on")
        if node.kind == "cancel":
            _math_element(properties, "strikeBLTR", val="on")
        elif node.kind == "bcancel":
            _math_element(properties, "strikeTLBR", val="on")
        elif node.kind == "xcancel":
            _math_element(properties, "strikeBLTR", val="on")
            _math_element(properties, "strikeTLBR", val="on")
        elif node.kind not in {"boxed", "fbox", "framebox"}:
            raise FormulaCompileError(f"Unsupported border-box kind: {node.kind!r}")
        _append_control_style(properties, inherited_style)
        body = _math_element(border, "e")
        _append_sequence(body, node.body, inherited_style, display=display)
        return
    raise FormulaCompileError(f"Unsupported internal formula node: {type(node).__name__}")


def _append_script(
    parent: ET.Element,
    node: Script,
    inherited_style: RunStyle | None,
    *,
    display: bool,
) -> None:
    if node.subscript is not None and node.superscript is not None:
        script = _math_element(parent, "sSubSup")
    elif node.subscript is not None:
        script = _math_element(parent, "sSub")
    else:
        script = _math_element(parent, "sSup")
    base = _math_element(script, "e")
    _append_node(base, node.base, inherited_style, display=display)
    if node.subscript is not None:
        subscript = _math_element(script, "sub")
        _append_sequence(subscript, node.subscript, inherited_style, display=display)
    if node.superscript is not None:
        superscript = _math_element(script, "sup")
        _append_sequence(superscript, node.superscript, inherited_style, display=display)


def _append_prescript(
    parent: ET.Element,
    node: Prescript,
    inherited_style: RunStyle | None,
    *,
    display: bool,
) -> None:
    script = _math_element(parent, "sPre")
    _math_element(script, "sPrePr")
    subscript = _math_element(script, "sub")
    if node.subscript is not None:
        _append_sequence(subscript, node.subscript, inherited_style, display=display)
    superscript = _math_element(script, "sup")
    if node.superscript is not None:
        _append_sequence(superscript, node.superscript, inherited_style, display=display)
    base = _math_element(script, "e")
    _append_node(base, node.base, inherited_style, display=display)


def _append_nary(
    parent: ET.Element,
    node: Nary,
    inherited_style: RunStyle | None,
    *,
    display: bool,
) -> None:
    nary = _math_element(parent, "nary")
    properties = _math_element(nary, "naryPr")
    _math_element(properties, "chr", val=node.symbol)
    if node.limit_modifier == "limits":
        limit_location = "undOvr"
    elif node.limit_modifier == "nolimits":
        limit_location = "subSup"
    elif node.category == "integral":
        limit_location = "subSup"
    else:
        limit_location = "undOvr" if display else "subSup"
    _math_element(properties, "limLoc", val=limit_location)
    _math_element(properties, "grow", val="off")
    _math_element(properties, "subHide", val="off" if node.subscript else "on")
    _math_element(properties, "supHide", val="off" if node.superscript else "on")
    _append_control_style(properties, inherited_style)
    subscript = _math_element(nary, "sub")
    if node.subscript is not None:
        _append_sequence(subscript, node.subscript, inherited_style, display=display)
    superscript = _math_element(nary, "sup")
    if node.superscript is not None:
        _append_sequence(superscript, node.superscript, inherited_style, display=display)
    body = _math_element(nary, "e")
    if node.body is not None:
        _append_sequence(body, node.body, inherited_style, display=display)


def _append_matrix(
    parent: ET.Element,
    node: Matrix,
    inherited_style: RunStyle | None,
    *,
    display: bool,
) -> None:
    matrix = _math_element(parent, "m")
    properties = _math_element(matrix, "mPr")
    _math_element(properties, "baseJc", val="center")
    _math_element(properties, "plcHide", val="on")
    columns = _math_element(properties, "mcs")
    column_count = len(node.rows[0])
    for _ in range(column_count):
        column = _math_element(columns, "mc")
        column_properties = _math_element(column, "mcPr")
        _math_element(column_properties, "count", val="1")
    for row_data in node.rows:
        row = _math_element(matrix, "mr")
        for cell_data in row_data:
            cell = _math_element(row, "e")
            _append_sequence(cell, cell_data, inherited_style, display=display)


def _append_limit(
    parent: ET.Element,
    node: Limit,
    inherited_style: RunStyle | None,
    *,
    display: bool,
) -> None:
    base: Node = node.base
    if node.lower is not None:
        lower = _math_element(parent, "limLow")
        _math_element(lower, "limLowPr")
        expression = _math_element(lower, "e")
        _append_node(expression, base, inherited_style, display=display)
        limit = _math_element(lower, "lim")
        _append_sequence(limit, node.lower, inherited_style, display=display)
        base = Sequence(())
        if node.upper is not None:
            wrapper = ET.Element(_math_tag("limUpp"))
            _math_element(wrapper, "limUppPr")
            expression = _math_element(wrapper, "e")
            expression.append(lower)
            limit = _math_element(wrapper, "lim")
            _append_sequence(limit, node.upper, inherited_style, display=display)
            parent.remove(lower)
            parent.append(wrapper)
        return
    if node.upper is not None:
        upper = _math_element(parent, "limUpp")
        _math_element(upper, "limUppPr")
        expression = _math_element(upper, "e")
        _append_node(expression, base, inherited_style, display=display)
        limit = _math_element(upper, "lim")
        _append_sequence(limit, node.upper, inherited_style, display=display)
        return
    _append_node(parent, base, inherited_style, display=display)


def _append_function(
    parent: ET.Element,
    node: Function,
    inherited_style: RunStyle | None,
    *,
    display: bool,
) -> None:
    function = _math_element(parent, "func")
    _math_element(function, "funcPr")
    name_parent = _math_element(function, "fName")
    name: Node = node.name
    if node.subscript is not None or node.superscript is not None:
        use_limits = node.limit_modifier == "limits" or (
            node.limit_modifier != "nolimits" and node.limit_style and display
        )
        if use_limits:
            name = Limit(name, lower=node.subscript, upper=node.superscript)
        else:
            name = Script(name, node.subscript, node.superscript)
    _append_node(name_parent, name, inherited_style, display=display)
    body = _math_element(function, "e")
    if node.body is not None:
        _append_sequence(body, node.body, inherited_style, display=display)
