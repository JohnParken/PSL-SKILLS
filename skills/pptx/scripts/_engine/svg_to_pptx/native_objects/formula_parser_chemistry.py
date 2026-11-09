# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
from __future__ import annotations

from .formula_parser_source import (
    FormulaCompileError,
    Fraction,
    Limit,
    Node,
    Prescript,
    Script,
    Sequence,
    Text,
    _ROMAN_STYLE,
    _SPACING_STYLE,
    _TEXT_STYLE,
    _read_raw_group,
    append_child,
    re,
)

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
        from .formula_parser import _LatexParser
        from .formula_parser_chemistry import _ChemParser
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
        from .formula_parser_chemistry import _ChemParser
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
        from .formula_parser import _LatexParser
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
        from .formula_parser_chemistry import _ChemParser
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
