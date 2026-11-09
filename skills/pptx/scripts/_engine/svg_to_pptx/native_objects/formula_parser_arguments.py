# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
from __future__ import annotations

from .formula_parser_source import (
    BARE_DELIMITER_COMMAND_PAIRS,
    FormulaCompileError,
    Function,
    GroupChar,
    Limit,
    NO_OUTPUT_COMMANDS,
    Nary,
    Node,
    OPERATOR_BOUNDARY_COMMANDS,
    Prescript,
    SPACING_COMMANDS,
    Script,
    Sequence,
    _MAX_PARSE_DEPTH,
    _is_command_letter,
    _read_control_word,
    _read_raw_group,
    append_child,
    is_empty,
)

class _ArgumentParserMixin:
    """Arguments parsing shared by the LaTeX parser."""

    def _parse_group(self) -> Sequence:
        self.position += 1
        saved_style = self.current_style
        try:
            return self._parse_nested_sequence(terminator="}")
        finally:
            self.current_style = saved_style

    def _parse_required_argument(
        self,
        description: str,
        *,
        allow_empty: bool = False,
        text_mode: bool = False,
    ) -> Sequence:
        self._skip_whitespace()
        saved_text_depth = self.text_mode_depth
        if text_mode:
            self.text_mode_depth += 1
        try:
            if self.position >= len(self.source):
                self._fail(f"{description.capitalize()} is missing")
            if self.source[self.position] == "{":
                result = self._parse_group()
            else:
                result = Sequence((self._parse_nested_bare_atom(),))
        finally:
            self.text_mode_depth = saved_text_depth
        if not allow_empty and is_empty(result):
            self._fail(f"{description.capitalize()} cannot be empty")
        return result

    def _parse_optional_argument(
        self,
        description: str,
        *,
        allow_empty: bool = False,
    ) -> Sequence | None:
        self._skip_whitespace()
        if self.position >= len(self.source) or self.source[self.position] != "[":
            return None
        self.position += 1
        result = self._parse_nested_sequence(terminator="]")
        if not allow_empty and is_empty(result):
            self._fail(f"{description.capitalize()} cannot be empty")
        return result

    def _parse_raw_required_group(self, description: str) -> str:
        try:
            raw, self.position = _read_raw_group(self.source, self.position)
        except FormulaCompileError as exc:
            self._fail(f"{description.capitalize()} must use a braced group")
            raise AssertionError from exc
        return raw

    def _parse_raw_optional_group(self, description: str) -> str | None:
        self._skip_whitespace()
        if self.position >= len(self.source) or self.source[self.position] != "[":
            return None
        closing = self.source.find("]", self.position + 1)
        if closing < 0:
            self._fail(f"Unclosed {description}")
        raw = self.source[self.position + 1:closing]
        self.position = closing + 1
        self._skip_whitespace()
        return raw

    def _parse_nested_sequence(self, **kwargs: object) -> Sequence:
        self.depth += 1
        if self.depth > _MAX_PARSE_DEPTH:
            self._fail(f"Formula nesting exceeds {_MAX_PARSE_DEPTH} levels")
        try:
            return self._parse_sequence(**kwargs)
        finally:
            self.depth -= 1

    def _parse_nested_atom(self) -> Node:
        self.depth += 1
        if self.depth > _MAX_PARSE_DEPTH:
            self._fail(f"Formula nesting exceeds {_MAX_PARSE_DEPTH} levels")
        try:
            return self._parse_complete_atom()
        finally:
            self.depth -= 1

    def _parse_nested_bare_atom(self) -> Node:
        self.depth += 1
        if self.depth > _MAX_PARSE_DEPTH:
            self._fail(f"Formula nesting exceeds {_MAX_PARSE_DEPTH} levels")
        try:
            return self._parse_atom()
        finally:
            self.depth -= 1

    def _parse_complete_atom(self) -> Node:
        atom = self._parse_atom()
        if isinstance(atom, (Nary, Function)):
            atom = self._parse_limit_modifier(atom)
        atom = self._parse_scripts(atom)
        if isinstance(atom, (Nary, Function)):
            atom = self._parse_limit_modifier(atom)
            atom = self._parse_owned_body(atom)
        return atom

    def _parse_scripts(self, base: Node) -> Node:
        subscript: Sequence | None = None
        superscript: Sequence | None = None
        while True:
            saved_position = self.position
            self._skip_whitespace()
            if self.position >= len(self.source) or self.source[self.position] not in "^_":
                self.position = saved_position
                break
            marker = self.source[self.position]
            self.position += 1
            argument = self._parse_required_argument(f"script marker {marker!r}")
            if marker == "_":
                if subscript is not None:
                    self._fail("Duplicate subscript for one base")
                subscript = argument
            else:
                if superscript is not None:
                    self._fail("Duplicate superscript for one base")
                superscript = argument
        if subscript is None and superscript is None:
            return base
        if isinstance(base, Nary):
            return Nary(
                base.symbol,
                base.category,
                subscript,
                superscript,
                base.body,
                base.limit_modifier,
            )
        if isinstance(base, Function):
            return Function(
                base.name,
                base.body,
                subscript,
                superscript,
                base.limit_style,
                base.limit_modifier,
            )
        if isinstance(base, GroupChar):
            return Limit(base, lower=subscript, upper=superscript)
        if isinstance(base, Limit):
            return Limit(
                base.base,
                lower=subscript or base.lower,
                upper=superscript or base.upper,
            )
        return Script(base, subscript=subscript, superscript=superscript)

    def _parse_prescript(self) -> Prescript:
        subscript: Sequence | None = None
        superscript: Sequence | None = None
        while self.position < len(self.source) and self.source[self.position] in "^_":
            marker = self.source[self.position]
            self.position += 1
            argument = self._parse_required_argument(f"pre-script marker {marker!r}")
            if marker == "_":
                if subscript is not None:
                    self._fail("Duplicate pre-subscript")
                subscript = argument
            else:
                if superscript is not None:
                    self._fail("Duplicate pre-superscript")
                superscript = argument
            self._skip_whitespace()
        if self.position >= len(self.source):
            self._fail("Pre-script has no base")
        base = self._parse_nested_atom()
        return Prescript(base, subscript=subscript, superscript=superscript)

    def _parse_limit_modifier(self, node: Nary | Function) -> Nary | Function:
        saved = self.position
        self._skip_whitespace()
        command: str | None = None
        if self._at_control_word("limits"):
            command = "limits"
        elif self._at_control_word("nolimits"):
            command = "nolimits"
        if command is None:
            self.position = saved
            return node
        if node.limit_modifier is not None:
            self._fail("Operator has more than one limit modifier")
        self._consume_control_word(command)
        if isinstance(node, Nary):
            return Nary(
                node.symbol,
                node.category,
                node.subscript,
                node.superscript,
                node.body,
                command,
            )
        return Function(
            node.name,
            node.body,
            node.subscript,
            node.superscript,
            node.limit_style,
            command,
        )

    def _parse_owned_body(self, node: Nary | Function) -> Nary | Function:
        if node.body is not None:
            return node
        saved = self.position
        self._skip_whitespace()
        leading_spacing: list[Node] = []
        while self._transparent_prefix_follows():
            append_child(leading_spacing, self._parse_nested_atom())
            self._skip_whitespace()
        if self.position < len(self.source) and self.source[self.position] in "+-":
            append_child(leading_spacing, self._parse_nested_atom())
            self._skip_whitespace()
            while self._transparent_prefix_follows():
                append_child(leading_spacing, self._parse_nested_atom())
                self._skip_whitespace()
        if not self._owned_body_follows():
            self.position = saved
            return node
        if self.source[self.position] == "{":
            body = self._parse_group()
        else:
            body = Sequence((self._parse_nested_atom(),))
        if leading_spacing:
            body = Sequence((*leading_spacing, *body.children))
        if isinstance(node, Nary):
            children = list(body.children)
            while True:
                saved_tail = self.position
                self._skip_whitespace()
                tail_prefix: list[Node] = []
                while self._transparent_prefix_follows():
                    append_child(tail_prefix, self._parse_nested_atom())
                    self._skip_whitespace()
                if not self._owned_body_follows():
                    self.position = saved_tail
                    break
                for prefix_node in tail_prefix:
                    append_child(children, prefix_node)
                append_child(children, self._parse_nested_atom())
            body = Sequence(tuple(children))
        elif isinstance(node, Function):
            children = list(body.children)
            while self._call_delimiter_follows():
                append_child(children, self._parse_nested_atom())
            body = Sequence(tuple(children))
        if isinstance(node, Nary):
            return Nary(
                node.symbol,
                node.category,
                node.subscript,
                node.superscript,
                body,
                node.limit_modifier,
            )
        return Function(
            node.name,
            body,
            node.subscript,
            node.superscript,
            node.limit_style,
            node.limit_modifier,
        )

    def _transparent_prefix_follows(self) -> bool:
        if self.position >= len(self.source):
            return False
        if self.source[self.position] == "~":
            return True
        if self.source[self.position] != "\\" or self.position + 1 >= len(self.source):
            return False
        following = self.source[self.position + 1]
        if not _is_command_letter(following):
            return following in SPACING_COMMANDS
        command, _ = _read_control_word(self.source, self.position)
        return (
            command in SPACING_COMMANDS
            or command in NO_OUTPUT_COMMANDS
            or command in {"mkern", "mskip", "hspace"}
        )

    def _call_delimiter_follows(self) -> bool:
        self._skip_whitespace()
        if self.position >= len(self.source):
            return False
        if self.source[self.position] in "([|":
            return True
        if self.source.startswith((r"\|", r"\{"), self.position):
            return True
        if self.source[self.position] != "\\":
            return False
        if self.position + 1 >= len(self.source) or not _is_command_letter(
            self.source[self.position + 1]
        ):
            return False
        command, _ = _read_control_word(self.source, self.position)
        return command in BARE_DELIMITER_COMMAND_PAIRS

    def _owned_body_follows(self) -> bool:
        if self.position >= len(self.source) or self._at_environment_boundary():
            return False
        if any(
            self._at_control_word(command)
            for command in {"right", "middle", "end"}
        ):
            return False
        char = self.source[self.position]
        if char in "}])&^_+-=<>/,;:!?":
            return False
        if char != "\\":
            return True
        if (
            self.position + 1 < len(self.source)
            and not _is_command_letter(self.source[self.position + 1])
        ):
            return self.source[self.position + 1] not in {"\\", ",", ";", ":", "!", " "}
        command, _ = _read_control_word(self.source, self.position)
        return command not in OPERATOR_BOUNDARY_COMMANDS
