# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
from __future__ import annotations

from .formula_parser_source import (
    AlignmentPoint,
    COLOR_NAMES,
    DELIMITER_COMMANDS,
    Delimiter,
    EQUATION_ARRAY_ENVIRONMENTS,
    EquationArray,
    FormulaCompileError,
    MATRIX_ENVIRONMENTS,
    Matrix,
    Node,
    RunStyle,
    Sequence,
    Styled,
    Text,
    _COLOR_HEX_RE,
    _ENVIRONMENT_NAME_RE,
    _INFIX_FRACTIONS,
    _LENGTH_RE,
    _MATH_MODE_RESET_STYLE,
    _ROMAN_STYLE,
    _is_command_letter,
    _skip_whitespace,
    _space_from_length,
    merge_run_styles,
    re,
)

class _EnvironmentParserMixin:
    """Environments parsing shared by the LaTeX parser."""

    def _parse_environment(self) -> Node:
        environment = self._parse_environment_name("\\begin")
        if (
            environment not in MATRIX_ENVIRONMENTS
            and environment not in EQUATION_ARRAY_ENVIRONMENTS
        ):
            self._fail(f"Unsupported formula environment {environment!r}")

        if environment == "CD":
            return self._parse_cd_environment()

        column_alignments: tuple[str, ...] = ()
        if environment in {"array", "subarray"}:
            raw = self._parse_raw_required_group(f"{environment} column specification")
            filtered = tuple(char for char in raw if char in "lcr")
            if not filtered:
                self._fail(f"Environment {environment!r} requires l/c/r columns")
            column_alignments = tuple("center" for _ in filtered)
        elif environment in {"alignat", "alignat*", "alignedat"}:
            raw = self._parse_raw_required_group(f"{environment} column count").strip()
            if not raw.isdigit() or int(raw) <= 0:
                self._fail(f"Environment {environment!r} requires a positive column count")

        self.environment_stack.append(environment)
        try:
            rows = self._parse_environment_rows(environment)
        finally:
            self.environment_stack.pop()

        if environment in MATRIX_ENVIRONMENTS:
            if not column_alignments and rows:
                column_alignments = tuple("center" for _ in rows[0])
            matrix = Matrix(environment, rows, column_alignments)
            delimiters = MATRIX_ENVIRONMENTS[environment]
            if delimiters is not None:
                return Delimiter(delimiters[0], delimiters[1], (Sequence((matrix,)),))
            return matrix

        eq_rows: list[Sequence] = []
        for row in rows:
            children: list[Node] = []
            for index, cell in enumerate(row):
                if index:
                    children.append(AlignmentPoint())
                children.extend(cell.children)
            eq_rows.append(Sequence(tuple(children)))
        equation_array = EquationArray(tuple(eq_rows))
        if environment == "cases":
            return Delimiter("{", "", (Sequence((equation_array,)),))
        if environment == "rcases":
            return Delimiter("", "}", (Sequence((equation_array,)),))
        return equation_array

    def _parse_cd_environment(self) -> Matrix:
        ending = r"\end{CD}"
        closing = self.source.find(ending, self.position)
        if closing < 0:
            self._fail("Unclosed formula environment 'CD'")
        raw = self.source[self.position:closing]
        self.position = closing + len(ending)
        raw_rows = re.split(r"\\\\|\\cr(?![A-Za-z])", raw)
        if raw_rows and not raw_rows[-1].strip():
            raw_rows.pop()
        rows: list[tuple[Sequence, ...]] = []
        for raw_row in raw_rows:
            row = raw_row.strip()
            if not row:
                self._fail("Environment 'CD' cannot contain an empty row")
            horizontal_parts = re.split(r"@(>>>|<<<)", row)
            if len(horizontal_parts) > 1:
                cells: list[Sequence] = []
                for index, part in enumerate(horizontal_parts):
                    if index % 2:
                        cells.append(Sequence((Text(
                            "→" if part == ">>>" else "←",
                            _ROMAN_STYLE,
                        ),)))
                    else:
                        if "@" in part:
                            self._fail(
                                "Environment 'CD' supports only @>>>, @<<<, @VVV, and @AAA"
                            )
                        cells.append(self._parse_fragment(part.strip()))
                rows.append(tuple(cells))
                continue
            vertical_tokens = re.findall(r"@(VVV|AAA)", row)
            vertical_remainder = re.sub(r"@(VVV|AAA)|[&\s]", "", row)
            if vertical_tokens and not vertical_remainder:
                cells: list[Sequence] = []
                for index, token in enumerate(vertical_tokens):
                    if index:
                        cells.append(Sequence(()))
                    cells.append(Sequence((Text(
                        "↓" if token == "VVV" else "↑",
                        _ROMAN_STYLE,
                    ),)))
                rows.append(tuple(cells))
                continue
            if "@" in row:
                self._fail("Environment 'CD' supports only @>>>, @<<<, @VVV, and @AAA")
            rows.append(tuple(self._parse_fragment(cell.strip()) for cell in row.split("&")))
        if not rows:
            self._fail("Environment 'CD' cannot be empty")
        column_count = max(len(row) for row in rows)
        padded_rows = tuple(
            (*row, *(Sequence(()) for _ in range(column_count - len(row))))
            for row in rows
        )
        return Matrix(
            "CD",
            padded_rows,
            tuple("center" for _ in range(column_count)),
        )

    def _parse_environment_rows(
        self,
        environment: str,
    ) -> tuple[tuple[Sequence, ...], ...]:
        rows: list[tuple[Sequence, ...]] = []
        row: list[Sequence] = []
        while True:
            cell = self._parse_nested_sequence(stop_at_environment_boundary=True)
            row.append(cell)
            if self.position < len(self.source) and self.source[self.position] == "&":
                self.position += 1
                continue
            if self.source.startswith("\\\\", self.position):
                self.position += 2
                rows.append(tuple(row))
                row = []
                self._skip_whitespace()
                if self._at_control_word("end"):
                    self._consume_environment_end(environment)
                    break
                continue
            if self._at_control_word("cr"):
                self._consume_control_word("cr")
                rows.append(tuple(row))
                row = []
                if self._at_control_word("end"):
                    self._consume_environment_end(environment)
                    break
                continue
            if self._at_control_word("end"):
                rows.append(tuple(row))
                self._consume_environment_end(environment)
                break
            self._fail(f"Malformed environment {environment!r}")
        if not rows or not rows[0]:
            self._fail(f"Environment {environment!r} cannot be empty")
        if environment in MATRIX_ENVIRONMENTS:
            column_count = len(rows[0])
            if any(len(current) != column_count for current in rows):
                self._fail(f"Environment {environment!r} has inconsistent column counts")
        return tuple(rows)

    def _parse_synthetic_environment(self, environment: str, raw: str) -> Node:
        from .formula_parser import _LatexParser
        parser = _LatexParser(
            f"\\begin{{{environment}}}{raw}\\end{{{environment}}}",
            display=self.display,
            inherited_style=self.current_style,
        )
        result = parser.parse()
        return self._single_node(result)

    def _consume_environment_end(self, expected: str) -> None:
        self._consume_control_word("end")
        actual = self._parse_environment_name("\\end")
        if actual != expected:
            self._fail(
                f"Mismatched environment ending: expected {expected!r}, got {actual!r}"
            )

    def _parse_environment_name(self, owner: str) -> str:
        raw = self._parse_raw_required_group(f"{owner} environment name")
        if _ENVIRONMENT_NAME_RE.fullmatch(raw) is None:
            self._fail(f"Invalid environment name {raw!r}")
        return raw

    def _at_environment_boundary(self) -> bool:
        return (
            (self.position < len(self.source) and self.source[self.position] == "&")
            or self.source.startswith("\\\\", self.position)
            or self._at_control_word("cr")
            or self._at_control_word("end")
        )

    def _parse_text_embedded_math(
        self,
        opener: str,
        closer: str,
        *,
        opened: bool = False,
    ) -> Sequence:
        from .formula_parser import _LatexParser
        start = self.position if opened else self.position + len(opener)
        if not opened:
            self.position += len(opener)
        closing = self.source.find(closer, self.position)
        if closing < 0:
            self._fail(f"Unclosed embedded math delimiter {opener!r}", position=start)
        raw = self.source[self.position:closing]
        self.position = closing + len(closer)
        embedded = _LatexParser(
            raw,
            display=False,
            inherited_style=self.current_style,
        ).parse()
        return Sequence((Styled(embedded, _MATH_MODE_RESET_STYLE),))

    def _parse_fragment(self, raw: str) -> Sequence:
        from .formula_parser import _LatexParser
        if not raw:
            return Sequence(())
        return _LatexParser(
            raw,
            display=self.display,
            text_mode=bool(self.text_mode_depth),
            inherited_style=self.current_style,
        ).parse()

    def _parse_spacing_length(self, command: str) -> str:
        self._skip_whitespace()
        match = _LENGTH_RE.match(self.source, self.position)
        if match is None:
            self._fail(f"\\{command} requires a numeric length with mu/em/ex/pt")
        self.position = match.end()
        self._skip_whitespace()
        return _space_from_length(match.group(0))

    def _parse_color(self) -> str:
        raw = self._parse_raw_required_group("formula color").strip()
        if _COLOR_HEX_RE.fullmatch(raw):
            return raw[1:].upper()
        color = COLOR_NAMES.get(raw.lower())
        if color is None:
            self._fail(f"Unsupported formula color {raw!r}")
        return color

    def _delimiter_from_raw(self, raw: str) -> str:
        raw = raw.strip()
        if raw in {"", "."}:
            return ""
        if raw in "()[]|{}⟨⟩⌊⌋⌈⌉⟦⟧":
            return raw
        control_symbols = {r"\{": "{", r"\}": "}", r"\|": "‖"}
        if raw in control_symbols:
            return control_symbols[raw]
        if raw.startswith("\\"):
            command = raw[1:]
            if command in DELIMITER_COMMANDS:
                return DELIMITER_COMMANDS[command]
        self._fail(f"Unsupported generalized-fraction delimiter {raw!r}")

    def _roman_name(self, name: str) -> Sequence:
        return Sequence((Text(name, _ROMAN_STYLE),))

    def _single_node(self, sequence: Sequence) -> Node:
        if len(sequence.children) == 1:
            return sequence.children[0]
        return sequence

    def _styled_text(
        self,
        value: str,
        style: RunStyle | None = None,
        *,
        literal: bool = False,
    ) -> Text:
        return Text(
            value,
            merge_run_styles(self.current_style, style),
            literal=literal,
        )

    def _current_infix_command(self) -> str | None:
        for command in _INFIX_FRACTIONS:
            if self._at_control_word(command):
                return command
        return None

    def _at_control_word(self, expected: str) -> bool:
        prefix = f"\\{expected}"
        if not self.source.startswith(prefix, self.position):
            return False
        following = self.position + len(prefix)
        return following >= len(self.source) or not _is_command_letter(self.source[following])

    def _consume_control_word(self, expected: str) -> None:
        if not self._at_control_word(expected):
            self._fail(f"Expected \\{expected}")
        self.position += len(expected) + 1
        self._skip_whitespace()

    def _read_control_word_body(self) -> str:
        start = self.position
        while self.position < len(self.source) and _is_command_letter(self.source[self.position]):
            self.position += 1
        return self.source[start:self.position]

    def _skip_whitespace(self) -> None:
        self.position = _skip_whitespace(self.source, self.position)

    def _fail(self, message: str, *, position: int | None = None) -> None:
        offset = self.position if position is None else position
        start = max(0, offset - 16)
        end = min(len(self.source), offset + 16)
        context = self.source[start:end].replace("\n", " ")
        raise FormulaCompileError(f"{message} at offset {offset}: {context!r}")
