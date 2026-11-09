# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
from __future__ import annotations

from .formula_parser_source import (
    ACCENT_COMMANDS,
    Accent,
    AlignmentPoint,
    BARE_DELIMITER_COMMAND_PAIRS,
    Bar,
    BorderBox,
    COLOR_NAMES,
    DELIMITER_COMMANDS,
    DISCARDED_ARGUMENT_COMMANDS,
    Delimiter,
    EQUATION_ARRAY_ENVIRONMENTS,
    EquationArray,
    FormulaCompileError,
    Fraction,
    Function,
    GREEK_SYMBOLS,
    GroupChar,
    LIMIT_FUNCTIONS,
    Limit,
    MATRIX_ENVIRONMENTS,
    Matrix,
    NARY_COMMANDS,
    NEGATED_SYMBOLS,
    NO_OUTPUT_COMMANDS,
    Nary,
    Node,
    OPERATOR_BOUNDARY_COMMANDS,
    OperatorEmulator,
    Phantom,
    Prescript,
    Radical,
    RunStyle,
    SPACING_COMMANDS,
    STANDARD_FUNCTIONS,
    SYMBOL_COMMANDS,
    Script,
    Sequence,
    Styled,
    Text,
    UNSUPPORTED_COMMANDS,
    VARIANT_UPPERCASE_GREEK,
    _BIG_DELIMITER_RE,
    _BIG_LEFT_COMMAND,
    _BIG_MIDDLE_COMMAND,
    _BIG_RIGHT_COMMAND,
    _BIG_SYMMETRIC_COMMAND,
    _COLOR_HEX_RE,
    _DECLARATION_STYLES,
    _ENVIRONMENT_NAME_RE,
    _INFIX_FRACTIONS,
    _LENGTH_RE,
    _MATH_MODE_RESET_STYLE,
    _MAX_EXPANDED_LENGTH,
    _MAX_MACRO_EXPANSIONS,
    _MAX_PARSE_DEPTH,
    _MIDDLE_DELIMITER_COMMANDS,
    _Macro,
    _OVER_ARROW_COMMANDS,
    _OVER_UNDER_COMMANDS,
    _PLAIN_OPERATOR_CHARS,
    _RIGHT_DELIMITER_COMMANDS,
    _ROMAN_STYLE,
    _SPACING_STYLE,
    _STYLE_COLOR_COMMANDS,
    _STYLE_WRAPPERS,
    _TEXT_STYLE,
    _WHITE_CLOSE_RE,
    _WHITE_OPEN_RE,
    _collect_macros,
    _expand_macros,
    _is_command_letter,
    _normalize_big_delimiters,
    _normalize_white_brackets,
    _prepare_source,
    _read_control_word,
    _read_macro_argument,
    _read_optional_integer,
    _read_raw_group,
    _skip_whitespace,
    _space_from_length,
    _strip_outer_delimiters,
    _xml_character_allowed,
    append_child,
    dataclass,
    is_empty,
    merge_run_styles,
    re,
)

from .formula_parser_arguments import _ArgumentParserMixin
from .formula_parser_delimiters import _DelimiterParserMixin
from .formula_parser_environments import _EnvironmentParserMixin
from .formula_parser_chemistry import _ChemParser

class _LatexParser(_ArgumentParserMixin, _DelimiterParserMixin, _EnvironmentParserMixin):
    """Parse one prepared formula source under the Office import profile."""

    def __init__(
        self,
        source: str,
        *,
        display: bool,
        text_mode: bool = False,
        inherited_style: RunStyle | None = None,
    ) -> None:
        self.source = source
        self.position = 0
        self.depth = 0
        self.display = display
        self.text_mode_depth = 1 if text_mode else 0
        self.current_style = inherited_style
        self.environment_stack: list[str] = []

    def parse(self) -> Sequence:
        result = self._parse_sequence()
        if self.position != len(self.source):
            self._fail("Unexpected trailing formula source")
        if is_empty(result):
            self._fail("Formula is empty")
        return result

    def _parse_sequence(
        self,
        *,
        terminator: str | None = None,
        stop_commands: frozenset[str] = frozenset(),
        stop_at_environment_boundary: bool = False,
    ) -> Sequence:
        children: list[Node] = []
        while self.position < len(self.source):
            if terminator is not None and self.source[self.position] == terminator:
                self.position += 1
                return Sequence(tuple(children))
            if any(self._at_control_word(command) for command in stop_commands):
                return Sequence(tuple(children))
            if stop_at_environment_boundary and self._at_environment_boundary():
                return Sequence(tuple(children))

            char = self.source[self.position]
            if char == "}":
                self._fail("Unexpected closing group")
            if char == "&":
                self._fail("Alignment marker '&' is only valid inside an environment")
            if char in "^_":
                append_child(children, self._parse_prescript())
                continue
            infix = self._current_infix_command()
            if infix is not None:
                if not children:
                    self._fail(f"Infix fraction \\{infix} has no numerator")
                self._consume_control_word(infix)
                right = self._parse_nested_sequence(
                    terminator=terminator,
                    stop_commands=stop_commands,
                    stop_at_environment_boundary=stop_at_environment_boundary,
                )
                if is_empty(right):
                    self._fail(f"Infix fraction \\{infix} has no denominator")
                left = Sequence(tuple(children))
                fraction = Fraction(
                    numerator=left,
                    denominator=right,
                    kind="bar" if infix == "over" else "noBar",
                )
                if infix in {"choose", "brace", "brack"}:
                    delimiters = {
                        "choose": ("(", ")"),
                        "brace": ("{", "}"),
                        "brack": ("[", "]"),
                    }[infix]
                    return Sequence((Delimiter(*delimiters, (Sequence((fraction,)),)),))
                return Sequence((fraction,))

            atom = self._parse_complete_atom()
            append_child(children, atom)

        if terminator is not None:
            self._fail(f"Unclosed group; expected {terminator!r}")
        return Sequence(tuple(children))

    def _parse_atom(self) -> Node:
        char = self.source[self.position]
        if char.isspace():
            while self.position < len(self.source) and self.source[self.position].isspace():
                self.position += 1
            return self._styled_text(" ") if self.text_mode_depth else Sequence(())
        if char == "{":
            return self._parse_group()
        if char == "\\":
            return self._parse_command()
        if char in "([|":
            return self._parse_or_literal_character_delimiter(char)
        if char == "$":
            if not self.text_mode_depth:
                self._fail("Math delimiters are only allowed around the complete marker source")
            return self._parse_text_embedded_math("$", "$")
        if char in "#%":
            self._fail(f"Unsupported TeX syntax character {char!r}")
        if ord(char) < 32:
            self._fail("Unsupported control character")

        self.position += 1
        if self.text_mode_depth:
            return self._styled_text(char)
        if char == "-":
            char = "−"
        elif char == "~":
            return self._styled_text("\u00a0", _SPACING_STYLE)
        style = _ROMAN_STYLE if char in _PLAIN_OPERATOR_CHARS else None
        return self._styled_text(char, style)

    def _parse_command(self) -> Node:
        from .formula_parser_chemistry import _ChemParser
        command_start = self.position
        self.position += 1
        if self.position >= len(self.source):
            self._fail("Trailing backslash", position=command_start)

        char = self.source[self.position]
        if not _is_command_letter(char):
            self.position += 1
            if char == "\\":
                self._fail(
                    "Row separator '\\\\' is only valid inside an environment",
                    position=command_start,
                )
            if char == "|":
                paired = self._try_symmetric_command_delimiter(command_start)
                if paired is not None:
                    return paired
                return self._styled_text("‖", _ROMAN_STYLE)
            if char == "(" and self.text_mode_depth:
                return self._parse_text_embedded_math("\\(", "\\)", opened=True)
            if char == "{":
                paired = self._try_escaped_brace_delimiter()
                if paired is not None:
                    return paired
            escaped = {
                "{": "{",
                "}": "}",
                "_": "_",
                "%": "%",
                "#": "#",
                "$": "$",
                "&": "&",
            }
            if char in escaped:
                return self._styled_text(
                    escaped[char],
                    literal=char == "&",
                )
            if char in SPACING_COMMANDS:
                return self._styled_text(SPACING_COMMANDS[char], _SPACING_STYLE)
            self._fail(f"Unsupported control symbol \\{char}", position=command_start)

        command = self._read_control_word_body()
        self._skip_whitespace()
        if command in UNSUPPORTED_COMMANDS:
            self._fail(f"Unsupported LaTeX command \\{command}", position=command_start)
        if command in {"vert", "Vert"}:
            paired = self._try_symmetric_named_delimiter(command)
            if paired is not None:
                return paired
        if command in GREEK_SYMBOLS:
            style = None
            if command[:1].isupper() and command not in VARIANT_UPPERCASE_GREEK:
                style = _ROMAN_STYLE
            return self._styled_text(GREEK_SYMBOLS[command], style)
        if command in SYMBOL_COMMANDS:
            return self._styled_text(SYMBOL_COMMANDS[command], _ROMAN_STYLE)
        if command in DELIMITER_COMMANDS:
            paired = self._try_command_delimiter(command, command_start)
            if paired is not None:
                return paired
            return self._styled_text(DELIMITER_COMMANDS[command], _ROMAN_STYLE)
        if command in NARY_COMMANDS:
            symbol, category = NARY_COMMANDS[command]
            return Nary(symbol=symbol, category=category)
        if command in STANDARD_FUNCTIONS:
            return Function(name=self._roman_name(command))
        if command in LIMIT_FUNCTIONS:
            return Function(name=self._roman_name(command), limit_style=True)
        if command == "operatorname":
            name = self._parse_required_argument("operator name", text_mode=True)
            return Function(name=Sequence((Styled(name, _ROMAN_STYLE),)))
        if command in {"frac", "dfrac", "tfrac", "cfrac", "ifrac"}:
            if (
                command == "cfrac"
                and self.position < len(self.source)
                and self.source[self.position] == "["
            ):
                self._parse_raw_optional_group("continued-fraction alignment")
            numerator = self._parse_required_argument("fraction numerator")
            denominator = self._parse_required_argument("fraction denominator")
            return Fraction(
                numerator=numerator,
                denominator=denominator,
                kind="skw" if command == "ifrac" else "bar",
            )
        if command in {"binom", "dbinom", "tbinom"}:
            numerator = self._parse_required_argument("binomial numerator")
            denominator = self._parse_required_argument("binomial denominator")
            fraction = Fraction(numerator, denominator, kind="noBar")
            return Delimiter("(", ")", (Sequence((fraction,)),))
        if command == "genfrac":
            left = self._delimiter_from_raw(self._parse_raw_required_group("left delimiter"))
            right = self._delimiter_from_raw(self._parse_raw_required_group("right delimiter"))
            thickness = self._parse_raw_required_group("fraction thickness").strip()
            self._parse_raw_required_group("fraction style")
            numerator = self._parse_required_argument("fraction numerator")
            denominator = self._parse_required_argument("fraction denominator")
            if not thickness:
                kind = "bar"
            elif thickness == "0":
                kind = "noBar"
            else:
                thickness_match = _LENGTH_RE.fullmatch(thickness)
                if thickness_match is None:
                    self._fail(
                        f"Invalid generalized-fraction thickness {thickness!r}"
                    )
                kind = (
                    "noBar"
                    if float(thickness_match.group(1)) == 0.0
                    else "bar"
                )
            fraction = Fraction(numerator, denominator, kind=kind)
            if left or right:
                return Delimiter(left, right, (Sequence((fraction,)),))
            return fraction
        if command == "sqrt":
            degree = self._parse_optional_argument("radical degree")
            body = self._parse_required_argument("radical body")
            return Radical(body=body, degree=degree)
        if command == "root":
            degree = self._parse_nested_sequence(stop_commands=frozenset({"of"}))
            if not self._at_control_word("of") or is_empty(degree):
                self._fail("\\root requires a degree followed by \\of")
            self._consume_control_word("of")
            body = self._parse_required_argument("radical body")
            return Radical(body=body, degree=degree)
        if command in {"left", _BIG_LEFT_COMMAND}:
            return self._parse_delimited_expression(command)
        if command == _BIG_RIGHT_COMMAND:
            right = self._parse_delimiter_token("explicit big right delimiter")
            return Delimiter("", right, (Sequence(()),))
        if command == _BIG_MIDDLE_COMMAND:
            separator = self._parse_delimiter_token("explicit big middle delimiter")
            return Delimiter("", "", (Sequence(()), Sequence(())), separator)
        if command == _BIG_SYMMETRIC_COMMAND:
            return self._parse_big_symmetric_delimiter()
        if command in {"right", "middle"}:
            self._fail(f"Unexpected \\{command} without matching \\left", position=command_start)
        if command == "begin":
            return self._parse_environment()
        if command == "end":
            self._fail("Unexpected \\end without matching \\begin", position=command_start)
        if command in _STYLE_WRAPPERS:
            text_mode = command in {
                "text", "textrm", "textnormal", "textbf", "textit", "emph",
                "textsf", "texttt", "mbox", "hbox",
            }
            body = self._parse_required_argument(f"\\{command} body", text_mode=text_mode)
            return Styled(body=body, style=_STYLE_WRAPPERS[command])
        if command in _DECLARATION_STYLES:
            self.current_style = merge_run_styles(
                self.current_style,
                _DECLARATION_STYLES[command],
            )
            return Sequence(())
        if command in ACCENT_COMMANDS:
            body = self._parse_required_argument(f"\\{command} body")
            return Accent(character=ACCENT_COMMANDS[command], body=body)
        if command == "overline":
            return Bar("top", self._parse_required_argument("overline body"))
        if command == "underline":
            return Bar("bot", self._parse_required_argument("underline body"))
        if command in _OVER_UNDER_COMMANDS:
            character, position, vertical = _OVER_UNDER_COMMANDS[command]
            body = self._parse_required_argument(f"\\{command} body")
            return GroupChar(character, position, vertical, body)
        if command in _OVER_ARROW_COMMANDS:
            body = self._parse_required_argument(f"\\{command} body")
            return GroupChar(_OVER_ARROW_COMMANDS[command], "top", "bot", body)
        if command in {"xrightarrow", "xleftarrow"}:
            lower = self._parse_optional_argument("arrow lower label", allow_empty=True)
            upper = self._parse_required_argument("arrow upper label", allow_empty=True)
            arrow = GroupChar(
                "→" if command == "xrightarrow" else "←",
                "bot",
                "top",
                upper,
            )
            return Limit(arrow, lower=lower) if lower is not None else arrow
        if command in {"overset", "stackrel"}:
            upper = self._parse_required_argument("upper limit")
            base = self._single_node(self._parse_required_argument("limit base"))
            return Limit(base=base, upper=upper)
        if command == "underset":
            lower = self._parse_required_argument("lower limit")
            base = self._single_node(self._parse_required_argument("limit base"))
            return Limit(base=base, lower=lower)
        if command == "buildrel":
            upper = self._parse_nested_sequence(stop_commands=frozenset({"over"}))
            if not self._at_control_word("over"):
                self._fail("\\buildrel requires \\over")
            if is_empty(upper):
                self._fail("\\buildrel requires an upper value")
            self._consume_control_word("over")
            base = self._single_node(self._parse_required_argument("buildrel base"))
            return Limit(base=base, upper=upper)
        if command == "substack":
            raw = self._parse_raw_required_group("substack body")
            return self._parse_synthetic_environment("aligned", raw)
        if command == "bmod":
            return Function(name=self._roman_name("mod"))
        if command in {"pmod", "mod"}:
            body = self._parse_required_argument(f"\\{command} body")
            content = Sequence((
                Text("mod", _ROMAN_STYLE),
                Text(" ", _SPACING_STYLE),
                *body.children,
            ))
            if command == "pmod":
                return Delimiter("(", ")", (content,))
            return content
        if command in _STYLE_COLOR_COMMANDS:
            color = self._parse_color()
            body = self._parse_required_argument(f"\\{command} body")
            return Styled(body, RunStyle(color=color))
        if command in {"boxed", "fbox", "framebox", "cancel", "bcancel", "xcancel"}:
            body = self._parse_required_argument(
                f"\\{command} body",
                text_mode=command in {"fbox", "framebox"},
            )
            return BorderBox(body=body, kind=command)
        if command in {"phantom", "hphantom", "vphantom"}:
            return Phantom(
                body=self._parse_required_argument(f"\\{command} body"),
                kind=command,
            )
        if command == "not":
            target = self._parse_required_argument("negated relation")
            node = self._single_node(target)
            if isinstance(node, Text) and len(node.value) == 1:
                negated = NEGATED_SYMBOLS.get(node.value)
                if negated is not None:
                    return self._styled_text(negated, _ROMAN_STYLE)
            return Accent("̸", target)
        if command == "eqalign":
            raw = self._parse_raw_required_group("eqalign body")
            return self._parse_synthetic_environment("aligned", raw.replace("\\cr", "\\\\"))
        if command == "ce":
            raw = self._parse_raw_required_group("chemical expression")
            return _ChemParser(raw, display=self.display).parse()
        if command == "bra":
            return Delimiter("⟨", "|", (self._parse_required_argument("bra body"),))
        if command == "ket":
            return Delimiter("|", "⟩", (self._parse_required_argument("ket body"),))
        if command in SPACING_COMMANDS:
            return self._styled_text(SPACING_COMMANDS[command], _SPACING_STYLE)
        if command in {"mkern", "mskip"}:
            return self._styled_text(self._parse_spacing_length(command), _SPACING_STYLE)
        if command == "hspace":
            return self._styled_text(
                _space_from_length(self._parse_raw_required_group("horizontal space")),
                _SPACING_STYLE,
            )
        if command in NO_OUTPUT_COMMANDS:
            return Sequence(())
        if command in DISCARDED_ARGUMENT_COMMANDS:
            self._parse_required_argument(f"\\{command} body", allow_empty=True)
            return Sequence(())
        if command == "mathrel":
            return OperatorEmulator(
                self._parse_required_argument("math relation body")
            )
        if command == "mathop":
            return Function(
                name=self._parse_required_argument("math operator body"),
                limit_style=True,
            )

        self._fail(f"Unsupported LaTeX command \\{command}", position=command_start)

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
