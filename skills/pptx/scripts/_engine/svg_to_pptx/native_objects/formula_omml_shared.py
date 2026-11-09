# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
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


MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"

_MAX_OMML_LENGTH = 1_048_576
_MAX_OMML_DEPTH = 256
_FORBIDDEN_XML_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

_MATH_ELEMENTS = frozenset({
    "acc", "accPr", "bar", "barPr", "baseJc", "begChr", "box", "boxPr",
    "borderBox", "borderBoxPr", "chr", "count", "ctrlPr", "d", "deg", "degHide",
    "den", "dPr", "e", "endChr", "eqArr", "eqArrPr", "f", "fName",
    "fPr", "func", "funcPr", "groupChr", "groupChrPr", "grow", "hideBot",
    "hideLeft", "hideRight", "hideTop", "jc", "lim", "limLoc", "limLow",
    "limLowPr", "limUpp", "limUppPr", "lit", "m", "mc", "mcPr", "mcs",
    "mPr", "mr", "nary", "naryPr", "nor", "num", "oMath", "oMathPara",
    "oMathParaPr", "phant", "phantPr", "plcHide", "pos", "r", "rad",
    "radPr", "rPr", "scr", "sepChr", "show", "sPre", "sPrePr", "opEmu",
    "sSub", "sSubSup", "sSup", "strikeBLTR", "strikeTLBR",
    "sty", "sub", "subHide", "sup", "supHide", "t",
    "type", "vertJc", "zeroAsc", "zeroDesc", "zeroWid",
})
_DRAWING_ELEMENTS = frozenset({
    "cs", "ea", "latin", "rPr", "solidFill", "srgbClr",
})
_DRAWING_ATTRIBUTES = frozenset({
    "b", "dirty", "i", "lang", "sz", "typeface", "val",
})
_ARGUMENT_ELEMENTS = frozenset({
    "acc", "bar", "borderBox", "box", "d", "eqArr", "f", "func", "groupChr",
    "limLow", "limUpp", "m", "nary", "phant", "r", "rad", "sPre",
    "sSub", "sSubSup", "sSup",
})
_ARGUMENT_CONTAINERS = frozenset({
    "deg", "den", "e", "fName", "lim", "num", "oMath", "sub", "sup",
})
_ON_OFF_ELEMENTS = frozenset({
    "degHide", "grow", "hideBot", "hideLeft", "hideRight", "hideTop", "nor",
    "lit", "opEmu", "plcHide", "show", "strikeBLTR", "strikeTLBR", "subHide", "supHide",
    "zeroAsc", "zeroDesc", "zeroWid",
})
_VALUE_ELEMENTS = frozenset({
    "baseJc", "begChr", "chr", "count", "degHide", "endChr", "grow",
    "hideBot", "hideLeft", "hideRight", "hideTop", "jc", "limLoc",
    "lit", "nor", "opEmu", "plcHide", "pos", "scr", "sepChr", "show", "strikeBLTR",
    "strikeTLBR", "sty", "subHide", "supHide", "type", "vertJc", "zeroAsc",
    "zeroDesc", "zeroWid",
})
_EMPTY_PROPERTY_ELEMENTS = frozenset({
    "funcPr", "limLowPr", "limUppPr", "sPrePr",
})
_PROPERTY_ORDER = {
    "accPr": ("chr", "ctrlPr"),
    "barPr": ("pos", "ctrlPr"),
    "boxPr": ("opEmu", "ctrlPr"),
    "borderBoxPr": (
        "hideTop", "hideBot", "hideLeft", "hideRight", "strikeBLTR",
        "strikeTLBR", "ctrlPr",
    ),
    "dPr": ("begChr", "sepChr", "endChr", "grow", "ctrlPr"),
    "eqArrPr": ("baseJc",),
    "fPr": ("type", "ctrlPr"),
    "groupChrPr": ("chr", "pos", "vertJc", "ctrlPr"),
    "mPr": ("baseJc", "plcHide", "mcs"),
    "mcPr": ("count",),
    "naryPr": (
        "chr", "limLoc", "grow", "subHide", "supHide", "ctrlPr",
    ),
    "oMathParaPr": ("jc",),
    "phantPr": ("show", "zeroWid", "zeroAsc", "zeroDesc"),
    "radPr": ("degHide", "ctrlPr"),
    "rPr": ("lit", "nor", "scr", "sty"),
}
