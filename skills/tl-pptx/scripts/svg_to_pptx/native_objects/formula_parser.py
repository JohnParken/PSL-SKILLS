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



# Grammar mixins preserve the original parser method bodies.
from .formula_parser_core import ParserCoreMixin
from .formula_parser_arguments import ParserArgumentsMixin
from .formula_parser_delimiters import ParserDelimitersMixin
from .formula_parser_environments import ParserEnvironmentsMixin

class _LatexParser(ParserCoreMixin, ParserArgumentsMixin, ParserDelimitersMixin, ParserEnvironmentsMixin):
    """Parse one prepared formula source under the Office import profile."""



_STYLE_COLOR_COMMANDS = frozenset({"color", "textcolor"})


class _ChemParser:
    """Parse the documented Microsoft 365 mhchem subset into shared AST nodes."""

    _ARROWS = {
        "<-->": "⟷",
        "<=>": "⇌",
        "<->": "↔",
        "->": "→",
        "<-": "←",
    }
    _UNSUPPORTED_AT_LABEL_RE = re.compile(r"@[<>=-]+\{")

    def __init__(self, source: str, *, display: bool) -> None:
        self.source = source
        self.position = 0
        self.display = display

    def parse(self) -> Sequence:
        children: list[Node] = []
        while self.position < len(self.source):
            if self._UNSUPPORTED_AT_LABEL_RE.match(self.source, self.position):
                raise FormulaCompileError(
                    "mhchem @{} arrow labels are unsupported; use [above][below]"
                )
            self._reject_unsupported_arrow()
            arrow = self._match_arrow()
            if arrow is not None:
                append_child(children, arrow)
                continue
            char = self.source[self.position]
            if char.isspace():
                while self.position < len(self.source) and self.source[self.position].isspace():
                    self.position += 1
                append_child(children, Text(" ", _TEXT_STYLE))
                continue
            if char == "\\":
                parser = _LatexParser(self.source[self.position:], display=self.display)
                node = parser._parse_complete_atom()
                self.position += parser.position
                append_child(children, node)
                continue
            if char == "{":
                raw, self.position = _read_raw_group(self.source, self.position)
                if raw in {"(", ")", "[", "]"}:
                    append_child(children, Text(raw, _ROMAN_STYLE))
                    continue
                body = _ChemParser(raw, display=self.display).parse()
                append_child(children, body)
                continue
            if char in "([":
                append_child(children, self._parse_grouped_formula(char))
                continue
            if char.isupper():
                append_child(children, self._parse_element())
                continue
            if char.isdigit():
                append_child(children, self._parse_number(children))
                continue
            if char == "^" and self._standalone_marker():
                self.position += 1
                append_child(children, Text("↑", _ROMAN_STYLE))
                continue
            if char in "^_":
                if self._has_right_script_base(children):
                    base = children.pop()
                    append_child(children, self._parse_explicit_scripts(base))
                else:
                    append_child(children, self._parse_isotope())
                continue
            if char in "+-":
                if self._is_bond_dash():
                    self.position += 1
                    append_child(children, Text("‒", _ROMAN_STYLE))
                elif (
                    children
                    and self.position > 0
                    and not self.source[self.position - 1].isspace()
                ):
                    self._attach_charge(children)
                else:
                    self.position += 1
                    append_child(children, Text("+" if char == "+" else "−", _ROMAN_STYLE))
                continue
            if char == "*":
                self.position += 1
                append_child(children, Text("·", _ROMAN_STYLE))
                continue
            if char == "=":
                self.position += 1
                append_child(children, Text("=", _ROMAN_STYLE))
                continue
            if char == "v" and self._standalone_marker():
                self.position += 1
                append_child(children, Text("↓", _ROMAN_STYLE))
                continue
            if char.islower():
                start = self.position
                while self.position < len(self.source) and self.source[self.position].islower():
                    self.position += 1
                word = self.source[start:self.position]
                following = self.position
                while following < len(self.source) and self.source[following].isspace():
                    following += 1
                is_variable = (
                    len(word) == 1
                    and following < len(self.source)
                    and self.source[following].isupper()
                )
                style = None if is_variable else _ROMAN_STYLE
                append_child(children, Text(word, style))
                continue
            self.position += 1
            append_child(children, Text("−" if char == "-" else char, _ROMAN_STYLE))
        return Sequence(tuple(children))

    def _parse_element(self) -> Node:
        start = self.position
        self.position += 1
        while self.position < len(self.source) and self.source[self.position].islower():
            self.position += 1
        base: Node = Text(self.source[start:self.position], _ROMAN_STYLE)
        if self.position < len(self.source) and self.source[self.position].isdigit():
            digits = self._read_digits()
            base = Script(base, subscript=Sequence((Text(digits, _ROMAN_STYLE),)))
        if self.position < len(self.source) and self.source[self.position] in "^_":
            base = self._parse_explicit_scripts(base)
        if (
            self.position < len(self.source)
            and self.source[self.position] in "+-"
            and not self._is_bond_dash()
        ):
            charge = self._read_charge()
            base = self._merge_script(
                base,
                superscript=Sequence((Text(charge, _ROMAN_STYLE),)),
            )
        return base

    def _parse_number(self, children: list[Node]) -> Node:
        start = self.position
        numerator = self._read_digits()
        number: Node = Text(numerator, _ROMAN_STYLE)
        if self.position < len(self.source) and self.source[self.position] == "/":
            saved = self.position
            self.position += 1
            if self.position < len(self.source) and self.source[self.position].isdigit():
                denominator = self._read_digits()
                number = Fraction(
                    Sequence((Text(numerator, _ROMAN_STYLE),)),
                    Sequence((Text(denominator, _ROMAN_STYLE),)),
                )
            else:
                self.position = saved
        if self._is_coefficient_position(start):
            following = self.position
            while following < len(self.source) and self.source[following].isspace():
                following += 1
            if following < len(self.source) and self.source[following].isupper():
                self.position = following
                return Sequence((number, Text("\u2009", _SPACING_STYLE)))
        return number

    def _parse_grouped_formula(self, opener: str) -> Node:
        closer = ")" if opener == "(" else "]"
        start = self.position + 1
        depth = 1
        cursor = start
        while cursor < len(self.source):
            if self.source[cursor] == opener:
                depth += 1
            elif self.source[cursor] == closer:
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if cursor >= len(self.source):
            raise FormulaCompileError(f"Unclosed chemical delimiter {opener!r}")
        body = _ChemParser(self.source[start:cursor], display=self.display).parse()
        self.position = cursor + 1
        base: Node = Sequence((
            Text(opener, _ROMAN_STYLE),
            *body.children,
            Text(closer, _ROMAN_STYLE),
        ))
        if self.position < len(self.source) and self.source[self.position].isdigit():
            digits = self._read_digits()
            if self.position < len(self.source) and self.source[self.position] in "+-":
                charge = digits + self._read_charge()
                base = Script(
                    base,
                    superscript=Sequence((Text(charge, _ROMAN_STYLE),)),
                )
            else:
                base = Script(base, subscript=Sequence((Text(digits, _ROMAN_STYLE),)))
        if self.position < len(self.source) and self.source[self.position] in "+-":
            charge = self._read_charge()
            base = self._merge_script(
                base,
                superscript=Sequence((Text(charge, _ROMAN_STYLE),)),
            )
        return base

    def _parse_isotope(self) -> Prescript:
        subscript: Sequence | None = None
        superscript: Sequence | None = None
        while self.position < len(self.source) and self.source[self.position] in "^_":
            marker = self.source[self.position]
            self.position += 1
            raw = self._read_chem_script()
            value = self._chem_script_sequence(raw)
            if marker == "_":
                subscript = value
            else:
                superscript = value
        if self.position >= len(self.source) or not self.source[self.position].isupper():
            raise FormulaCompileError("Chemical pre-script requires an element base")
        base = self._parse_element()
        return Prescript(base, subscript=subscript, superscript=superscript)

    def _parse_explicit_scripts(self, base: Node) -> Script:
        subscript: Sequence | None = None
        superscript: Sequence | None = None
        while self.position < len(self.source) and self.source[self.position] in "^_":
            marker = self.source[self.position]
            self.position += 1
            raw = self._read_chem_script()
            if (
                marker == "^"
                and self.position < len(self.source)
                and self.source[self.position] in "+-"
            ):
                raw += self._read_charge()
            value = self._chem_script_sequence(raw)
            if marker == "_":
                subscript = value
            else:
                superscript = value
        return self._merge_script(
            base,
            subscript=subscript,
            superscript=superscript,
        )

    def _read_chem_script(self) -> str:
        if self.position >= len(self.source):
            raise FormulaCompileError("Chemical script is missing an argument")
        if self.source[self.position] == "{":
            raw, self.position = _read_raw_group(self.source, self.position)
            return raw
        char = self.source[self.position]
        self.position += 1
        return char

    def _attach_charge(self, children: list[Node]) -> None:
        charge = self._read_charge()
        base = children.pop()
        children.append(self._merge_script(
            base,
            superscript=Sequence((Text(charge, _ROMAN_STYLE),)),
        ))

    def _merge_script(
        self,
        base: Node,
        *,
        subscript: Sequence | None = None,
        superscript: Sequence | None = None,
    ) -> Script:
        if isinstance(base, Script):
            return Script(
                base.base,
                subscript or base.subscript,
                superscript or base.superscript,
            )
        return Script(base, subscript=subscript, superscript=superscript)

    def _chem_script_sequence(self, raw: str) -> Sequence:
        if "\\" in raw:
            return _LatexParser(raw, display=self.display).parse()
        normalized = raw.replace("-", "−")
        style = None if len(normalized) == 1 and normalized.islower() else _ROMAN_STYLE
        return Sequence((Text(normalized, style),))

    def _has_right_script_base(self, children: list[Node]) -> bool:
        if not children or self.position == 0:
            return False
        previous = self.source[self.position - 1]
        if previous.isspace() or previous in "+-=<>*/([{,;:":
            return False
        return not isinstance(children[-1], Limit)

    def _is_coefficient_position(self, start: int) -> bool:
        cursor = start - 1
        while cursor >= 0 and self.source[cursor].isspace():
            cursor -= 1
        return cursor < 0 or self.source[cursor] in "+<>=-"

    def _is_bond_dash(self) -> bool:
        return (
            self.position < len(self.source)
            and self.source[self.position] == "-"
            and self.position + 1 < len(self.source)
            and self.source[self.position + 1].isupper()
        )

    def _read_charge(self) -> str:
        start = self.position
        while self.position < len(self.source) and self.source[self.position] in "+-":
            self.position += 1
        return self.source[start:self.position].replace("-", "−")

    def _read_digits(self) -> str:
        start = self.position
        while self.position < len(self.source) and self.source[self.position].isdigit():
            self.position += 1
        return self.source[start:self.position]

    def _match_arrow(self) -> Node | None:
        for token, symbol in self._ARROWS.items():
            if not self.source.startswith(token, self.position):
                continue
            self.position += len(token)
            upper = self._read_arrow_label()
            lower = self._read_arrow_label()
            return Limit(Text(symbol, _ROMAN_STYLE), lower=lower, upper=upper)
        return None

    def _reject_unsupported_arrow(self) -> None:
        for token in ("<<=>>", "<=>>", "<<=>", "->>"):
            if self.source.startswith(token, self.position):
                raise FormulaCompileError(
                    f"Unsupported chemical reaction arrow {token!r}"
                )

    def _read_arrow_label(self) -> Sequence | None:
        if self.position >= len(self.source) or self.source[self.position] != "[":
            return None
        start = self.position + 1
        depth = 1
        brace_depth = 0
        cursor = start
        while cursor < len(self.source):
            char = self.source[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == "{":
                brace_depth += 1
            elif char == "}" and brace_depth:
                brace_depth -= 1
            elif brace_depth == 0 and char == "[":
                depth += 1
            elif brace_depth == 0 and char == "]":
                depth -= 1
                if depth == 0:
                    raw = self.source[start:cursor]
                    self.position = cursor + 1
                    return _ChemParser(raw, display=self.display).parse()
            cursor += 1
        raise FormulaCompileError("Unclosed chemical arrow label")

    def _standalone_marker(self) -> bool:
        before = self.position == 0 or self.source[self.position - 1].isspace()
        after_index = self.position + 1
        after = after_index >= len(self.source) or self.source[after_index].isspace()
        return before and after


def parse_latex_formula(latex: str, *, display: bool) -> Sequence:
    """Validate, normalize, and parse one Microsoft 365 LaTeX expression."""
    if not isinstance(latex, str):
        raise FormulaCompileError("LaTeX formula must be a string")
    source = latex.strip()
    if not source:
        raise FormulaCompileError("LaTeX formula is empty")
    if any(not _xml_character_allowed(char) for char in source):
        raise FormulaCompileError("LaTeX formula contains an invalid XML character")
    prepared = _prepare_source(source)
    if not prepared:
        raise FormulaCompileError("LaTeX formula is empty after preprocessing")
    return _LatexParser(prepared, display=display).parse()


__all__ = ["FormulaCompileError", "parse_latex_formula"]
