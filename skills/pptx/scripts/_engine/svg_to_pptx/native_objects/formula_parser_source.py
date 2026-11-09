# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
#!/usr/bin/env python3
r"""
PPT Master - Microsoft 365 LaTeX Formula Parser

Parse the documented Microsoft 365 LaTeX import profile into the internal
native-formula AST. Unknown commands remain fail-closed.

See references/native-formula.md for the owning authoring contract.

Usage:
    Import parse_latex_formula() from the native formula compiler facade.

Examples:
    parse_latex_formula(r"\binom{n}{k}", display=True)

Dependencies:
    None (only uses standard library and local PPT Master modules)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    append_child,
    is_empty,
    merge_run_styles,
)
from .formula_profile import (
    ACCENT_COMMANDS,
    BARE_DELIMITER_COMMAND_PAIRS,
    COLOR_NAMES,
    DELIMITER_COMMANDS,
    DISCARDED_ARGUMENT_COMMANDS,
    EQUATION_ARRAY_ENVIRONMENTS,
    GREEK_SYMBOLS,
    LIMIT_FUNCTIONS,
    MATRIX_ENVIRONMENTS,
    NARY_COMMANDS,
    NEGATED_SYMBOLS,
    NO_OUTPUT_COMMANDS,
    OPERATOR_BOUNDARY_COMMANDS,
    SPACING_COMMANDS,
    STANDARD_FUNCTIONS,
    SYMBOL_COMMANDS,
    UNSUPPORTED_COMMANDS,
    VARIANT_UPPERCASE_GREEK,
)


_MAX_PARSE_DEPTH = 128
_MAX_MACRO_EXPANSIONS = 500
_MAX_EXPANDED_LENGTH = 1_048_576
_ENVIRONMENT_NAME_RE = re.compile(r"[A-Za-z]+\*?")
_COLOR_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_LENGTH_RE = re.compile(
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mu|em|ex|pt)",
    re.IGNORECASE,
)
_BIG_DELIMITER_RE = re.compile(
    r"\\(?:big|Big|bigg|Bigg)([lrm]?)(?![A-Za-z])\s*"
    r"(\\[A-Za-z]+|\\[{}|]|[()\[\]{}|.])"
)
_BIG_LEFT_COMMAND = "pptmasterbigleft"
_BIG_RIGHT_COMMAND = "pptmasterbigright"
_BIG_MIDDLE_COMMAND = "pptmasterbigmiddle"
_BIG_SYMMETRIC_COMMAND = "pptmasterbigsymmetric"
_MIDDLE_DELIMITER_COMMANDS = ("middle", _BIG_MIDDLE_COMMAND)
_RIGHT_DELIMITER_COMMANDS = ("right", _BIG_RIGHT_COMMAND)
_WHITE_OPEN_RE = re.compile(
    r"(?:(?:\\left)?(?:\[|\\lbrack))\\!"
    r"(?:(?:\\left)?(?:\[|\\lbrack))"
)
_WHITE_CLOSE_RE = re.compile(
    r"(?:(?:\\right)?(?:\]|\\rbrack))\\!"
    r"(?:(?:\\right)?(?:\]|\\rbrack))"
)

_ROMAN_STYLE = RunStyle(style="p", is_default=True)
_TEXT_STYLE = RunStyle(normal=True)
_MATH_MODE_RESET_STYLE = RunStyle(
    normal=False,
    bold=False,
    italic=False,
    typeface="",
)
_SPACING_STYLE = RunStyle()
_PLAIN_OPERATOR_CHARS = frozenset("+−*/=<>±∓×÷⋅,;:!?")
_INFIX_FRACTIONS = frozenset({"over", "atop", "choose", "brace", "brack"})

_STYLE_WRAPPERS = {
    "mathrm": RunStyle(style="p"),
    "mathbf": RunStyle(style="b"),
    "mathit": RunStyle(style="i"),
    "mathsf": RunStyle(style="p", script="sans-serif"),
    "mathtt": RunStyle(style="p", script="monospace"),
    "mathbb": RunStyle(style="p", script="double-struck"),
    "Bbb": RunStyle(style="p", script="double-struck"),
    "mathcal": RunStyle(style="p", script="script"),
    "mathscr": RunStyle(style="p", script="script"),
    "mathfrak": RunStyle(style="p", script="fraktur"),
    "boldsymbol": RunStyle(style="bi"),
    "bm": RunStyle(style="bi"),
    "text": _TEXT_STYLE,
    "textrm": _TEXT_STYLE,
    "textnormal": _TEXT_STYLE,
    "textbf": RunStyle(normal=True, bold=True),
    "textit": RunStyle(normal=True, italic=True),
    "emph": RunStyle(normal=True, italic=True),
    "textsf": RunStyle(normal=True, typeface="Arial"),
    "texttt": RunStyle(normal=True, typeface="Courier New"),
    "mbox": _TEXT_STYLE,
    "hbox": _TEXT_STYLE,
}

_DECLARATION_STYLES = {
    "rm": RunStyle(style="p"),
    "bf": RunStyle(style="b"),
    "it": RunStyle(style="i"),
    "cal": RunStyle(style="p", script="script"),
    "frak": RunStyle(style="p", script="fraktur"),
    "sf": RunStyle(style="p", script="sans-serif"),
    "tt": RunStyle(style="p", script="monospace"),
}

_OVER_UNDER_COMMANDS = {
    "overbrace": ("⏞", "top", "bot"),
    "underbrace": ("⏟", "bot", "top"),
    "underrightarrow": ("→", "bot", "top"),
    "underleftarrow": ("←", "bot", "top"),
    "underleftrightarrow": ("↔", "bot", "top"),
}

_OVER_ARROW_COMMANDS = {
    "overrightarrow": "→",
    "overleftarrow": "←",
    "overleftrightarrow": "↔",
}


class FormulaCompileError(ValueError):
    """Raised when formula source violates the Microsoft 365 profile."""


@dataclass(frozen=True)
class _Macro:
    parameter_count: int
    body: str


def _is_command_letter(char: str) -> bool:
    return char.isascii() and char.isalpha()


def _xml_character_allowed(char: str) -> bool:
    codepoint = ord(char)
    return (
        codepoint in {0x09, 0x0A, 0x0D}
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _read_control_word(source: str, position: int) -> tuple[str, int]:
    if position >= len(source) or source[position] != "\\":
        raise FormulaCompileError("Expected a LaTeX control word")
    position += 1
    start = position
    while position < len(source) and _is_command_letter(source[position]):
        position += 1
    if position == start:
        raise FormulaCompileError("Expected a LaTeX control word")
    return source[start:position], position


def _skip_whitespace(source: str, position: int) -> int:
    while position < len(source) and source[position].isspace():
        position += 1
    return position


def _read_raw_group(source: str, position: int) -> tuple[str, int]:
    position = _skip_whitespace(source, position)
    if position >= len(source) or source[position] != "{":
        raise FormulaCompileError("Expected a braced LaTeX group")
    depth = 1
    cursor = position + 1
    start = cursor
    while cursor < len(source):
        char = source[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:cursor], cursor + 1
        cursor += 1
    raise FormulaCompileError("Unclosed braced LaTeX group")


def _read_optional_integer(source: str, position: int) -> tuple[int | None, int]:
    position = _skip_whitespace(source, position)
    if position >= len(source) or source[position] != "[":
        return None, position
    closing = source.find("]", position + 1)
    if closing < 0:
        raise FormulaCompileError("Unclosed optional macro parameter count")
    raw = source[position + 1:closing].strip()
    if not raw.isdigit() or not 0 <= int(raw) <= 9:
        raise FormulaCompileError("Macro parameter count must be between 0 and 9")
    return int(raw), closing + 1


def _collect_macros(source: str) -> tuple[str, dict[str, _Macro]]:
    macros: dict[str, _Macro] = {}
    output: list[str] = []
    position = 0
    while position < len(source):
        if source[position] != "\\":
            output.append(source[position])
            position += 1
            continue
        try:
            command, command_end = _read_control_word(source, position)
        except FormulaCompileError:
            output.append(source[position])
            position += 1
            continue
        if command not in {"newcommand", "renewcommand", "def"}:
            output.append(source[position:command_end])
            position = command_end
            continue

        cursor = _skip_whitespace(source, command_end)
        if command in {"newcommand", "renewcommand"}:
            name_group, cursor = _read_raw_group(source, cursor)
            name_group = name_group.strip()
            if not re.fullmatch(r"\\[A-Za-z]+", name_group):
                raise FormulaCompileError(
                    f"\\{command} requires one control-word macro name"
                )
            parameter_count, cursor = _read_optional_integer(source, cursor)
            body, cursor = _read_raw_group(source, cursor)
            count = parameter_count or 0
            if re.search(r"#(?:0|[1-9][0-9])", body):
                raise FormulaCompileError("Macro body uses an invalid parameter number")
            macros[name_group[1:]] = _Macro(count, body)
        else:
            if cursor >= len(source) or source[cursor] != "\\":
                raise FormulaCompileError("\\def requires one control-word macro name")
            name, cursor = _read_control_word(source, cursor)
            parameters: list[int] = []
            cursor = _skip_whitespace(source, cursor)
            while cursor < len(source) and source[cursor] == "#":
                if cursor + 1 >= len(source) or source[cursor + 1] not in "123456789":
                    raise FormulaCompileError("\\def parameters must use #1 through #9")
                parameters.append(int(source[cursor + 1]))
                cursor = _skip_whitespace(source, cursor + 2)
            expected = list(range(1, len(parameters) + 1))
            if parameters != expected:
                raise FormulaCompileError("\\def parameters must be consecutive from #1")
            body, cursor = _read_raw_group(source, cursor)
            macros[name] = _Macro(len(parameters), body)
        position = cursor
    return "".join(output), macros


def _read_macro_argument(source: str, position: int) -> tuple[str, int]:
    position = _skip_whitespace(source, position)
    if position >= len(source):
        raise FormulaCompileError("Macro invocation is missing an argument")
    if source[position] == "{":
        return _read_raw_group(source, position)
    if source[position] == "\\":
        if position + 1 >= len(source):
            raise FormulaCompileError("Macro invocation ends with a backslash")
        if _is_command_letter(source[position + 1]):
            _, end = _read_control_word(source, position)
            return source[position:end], end
        return source[position:position + 2], position + 2
    return source[position], position + 1


def _expand_macros(source: str, macros: dict[str, _Macro]) -> str:
    if not macros:
        return source
    expansion_count = 0
    while True:
        output: list[str] = []
        position = 0
        replaced = False
        while position < len(source):
            if source[position] != "\\" or position + 1 >= len(source):
                output.append(source[position])
                position += 1
                continue
            if not _is_command_letter(source[position + 1]):
                output.append(source[position:position + 2])
                position += 2
                continue
            command, cursor = _read_control_word(source, position)
            macro = macros.get(command)
            if macro is None:
                output.append(source[position:cursor])
                position = cursor
                continue
            arguments: list[str] = []
            for _ in range(macro.parameter_count):
                argument, cursor = _read_macro_argument(source, cursor)
                arguments.append(argument)
            body = macro.body
            for index, argument in enumerate(arguments, start=1):
                body = body.replace(f"#{index}", argument)
            output.append(body)
            position = cursor
            expansion_count += 1
            if expansion_count > _MAX_MACRO_EXPANSIONS:
                raise FormulaCompileError(
                    f"Formula exceeds the {_MAX_MACRO_EXPANSIONS}-macro expansion limit"
                )
            replaced = True
        source = "".join(output)
        if len(source) > _MAX_EXPANDED_LENGTH:
            raise FormulaCompileError(
                f"Expanded formula exceeds the {_MAX_EXPANDED_LENGTH}-character limit"
            )
        if not replaced:
            return source


def _strip_outer_delimiters(source: str) -> str:
    pairs = (("$$", "$$"), ("$", "$"), ("\\(", "\\)"), ("\\[", "\\]"))
    for opener, closer in pairs:
        if source.startswith(opener):
            if not source.endswith(closer) or len(source) <= len(opener) + len(closer):
                raise FormulaCompileError(f"Unclosed outer LaTeX delimiter {opener!r}")
            return source[len(opener):-len(closer)].strip()
    return source


def _normalize_big_delimiters(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        direction = match.group(1)
        delimiter = match.group(2)
        if direction:
            command = {
                "l": _BIG_LEFT_COMMAND,
                "r": _BIG_RIGHT_COMMAND,
                "m": _BIG_MIDDLE_COMMAND,
            }[direction]
            return f"\\{command}{delimiter}"
        openers = {
            "(", "[", "{", ".", r"\{", r"\lbrace", r"\langle", r"\lfloor",
            r"\lceil", r"\lvert", r"\lVert", r"\lbrack",
        }
        closers = {
            ")", "]", "}", r"\}", r"\rbrace", r"\rangle", r"\rfloor",
            r"\rceil", r"\rvert", r"\rVert", r"\rbrack",
        }
        symmetric = {"|", r"\|", r"\vert", r"\Vert"}
        if delimiter in openers:
            return f"\\{_BIG_LEFT_COMMAND}{delimiter}"
        if delimiter in closers:
            return f"\\{_BIG_RIGHT_COMMAND}{delimiter}"
        if delimiter in symmetric:
            return f"\\{_BIG_SYMMETRIC_COMMAND}{delimiter}"
        return delimiter

    return _BIG_DELIMITER_RE.sub(replace, source)


def _normalize_white_brackets(source: str) -> str:
    source = _WHITE_OPEN_RE.sub(r"\\left⟦", source)
    return _WHITE_CLOSE_RE.sub(r"\\right⟧", source)


def _prepare_source(source: str) -> str:
    source = _strip_outer_delimiters(source.strip())
    if "%" in source:
        position = 0
        while position < len(source):
            if source[position] == "%" and (position == 0 or source[position - 1] != "\\"):
                raise FormulaCompileError("Unescaped % comments are outside the Office profile")
            position += 1
    without_definitions, macros = _collect_macros(source)
    expanded = _expand_macros(without_definitions, macros)
    return _normalize_white_brackets(_normalize_big_delimiters(expanded)).strip()


def _space_from_length(raw: str) -> str:
    match = _LENGTH_RE.fullmatch(raw.strip())
    if match is None:
        raise FormulaCompileError(f"Unsupported math spacing length: {raw!r}")
    amount = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = {"mu": 1.0, "em": 18.0, "ex": 9.0, "pt": 1.8}[unit]
    mu = min(2160.0, amount * multiplier)
    if mu <= 0:
        return "\u200b"
    whole_em = int(mu // 18)
    remainder = mu - whole_em * 18
    output = "\u2003" * whole_em
    if remainder >= 14:
        output += "\u2003"
    elif remainder >= 9:
        output += "\u2002"
    elif remainder >= 4.5:
        output += "\u2004"
    elif remainder >= 3.5:
        output += "\u205f"
    elif remainder >= 2:
        output += "\u2009"
    elif remainder > 0:
        output += "\u200b"
    return output or "\u200b"


_STYLE_COLOR_COMMANDS = frozenset({"color", "textcolor"})
