# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Formula omml document implementation."""
from __future__ import annotations

from .formula_omml_shared import (
    DRAWING_NS,
    ET,
    MATH_NS,
    Sequence,
)

def emit_omml(expression: Sequence, *, display: bool) -> str:
    """Emit one block or inline Office Math root from the parsed AST."""
    from .formula_omml_emit import _append_sequence, _math_element, _math_tag
    from .formula_omml_validate import validate_omml_fragment
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
