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

# Upstream MIT parser methods, separated by grammar responsibility.
class ParserDelimitersMixin:
    """Office formula grammar methods for delimiters."""

    def _parse_delimited_expression(self, command: str = "left") -> Delimiter:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            Delimiter,
            _MIDDLE_DELIMITER_COMMANDS,
            _RIGHT_DELIMITER_COMMANDS,
        )
        left = self._parse_delimiter_token(f"\\{command}")
        segments: list[Sequence] = []
        separator = ""
        stop_commands = frozenset({
            *_MIDDLE_DELIMITER_COMMANDS,
            *_RIGHT_DELIMITER_COMMANDS,
        })
        while True:
            segments.append(
                self._parse_nested_sequence(stop_commands=stop_commands)
            )
            middle_command = next(
                (
                    candidate
                    for candidate in _MIDDLE_DELIMITER_COMMANDS
                    if self._at_control_word(candidate)
                ),
                None,
            )
            if middle_command is not None:
                self._consume_control_word(middle_command)
                current = self._parse_delimiter_token(f"\\{middle_command}")
                if separator and separator != current:
                    self._fail("One delimiter expression cannot mix middle characters")
                separator = current
                continue
            right = ""
            right_command = next(
                (
                    candidate
                    for candidate in _RIGHT_DELIMITER_COMMANDS
                    if self._at_control_word(candidate)
                ),
                None,
            )
            if right_command is not None:
                self._consume_control_word(right_command)
                right = self._parse_delimiter_token(f"\\{right_command}")
            return Delimiter(left, right, tuple(segments), separator)


    def _parse_big_symmetric_delimiter(self) -> Delimiter:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            Delimiter,
            Sequence,
        )
        delimiter = self._parse_delimiter_token("explicit big symmetric delimiter")
        body_start = self.position
        closing = self._find_matching_big_symmetric(delimiter, body_start)
        if closing is None:
            return Delimiter("", "", (Sequence(()), Sequence(())), delimiter)
        closing_start, after_closing = closing
        body = self._parse_fragment(self.source[body_start:closing_start])
        self.position = after_closing
        return Delimiter(delimiter, delimiter, (body,))


    def _find_matching_big_symmetric(
        self,
        delimiter: str,
        start: int,
    ) -> tuple[int, int] | None:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            _BIG_SYMMETRIC_COMMAND,
            _is_command_letter,
            _read_control_word,
            _skip_whitespace,
        )
        group_depth = 0
        cursor = start
        while cursor < len(self.source):
            char = self.source[cursor]
            if char == "{":
                group_depth += 1
                cursor += 1
                continue
            if char == "}":
                group_depth = max(0, group_depth - 1)
                cursor += 1
                continue
            if char != "\\" or cursor + 1 >= len(self.source):
                cursor += 1
                continue
            if not _is_command_letter(self.source[cursor + 1]):
                cursor += 2
                continue
            command, after_command = _read_control_word(self.source, cursor)
            if group_depth != 0 or command != _BIG_SYMMETRIC_COMMAND:
                cursor = after_command
                continue
            saved_position = self.position
            try:
                self.position = _skip_whitespace(self.source, after_command)
                candidate = self._parse_delimiter_token(
                    "explicit big symmetric delimiter"
                )
                after_delimiter = self.position
            finally:
                self.position = saved_position
            if candidate == delimiter:
                return cursor, after_delimiter
            cursor = after_delimiter
        return None


    def _parse_delimiter_token(self, owner: str) -> str:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            DELIMITER_COMMANDS,
            _is_command_letter,
        )
        self._skip_whitespace()
        if self.position >= len(self.source):
            self._fail(f"{owner} requires a delimiter")
        char = self.source[self.position]
        if char != "\\":
            self.position += 1
            if char in "()[]|.⟦⟧":
                return "" if char == "." else char
            self._fail(f"Unsupported delimiter {char!r} after {owner}")
        command_start = self.position
        self.position += 1
        if self.position >= len(self.source):
            self._fail(f"{owner} requires a delimiter", position=command_start)
        if not _is_command_letter(self.source[self.position]):
            delimiter = self.source[self.position]
            self.position += 1
            if delimiter in "{}|":
                return "‖" if delimiter == "|" else delimiter
            self._fail(f"Unsupported delimiter command \\{delimiter}", position=command_start)
        command = self._read_control_word_body()
        self._skip_whitespace()
        if command not in DELIMITER_COMMANDS:
            self._fail(f"Unsupported delimiter command \\{command}", position=command_start)
        return DELIMITER_COMMANDS[command]


    def _parse_or_literal_character_delimiter(self, opener: str) -> Node:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            Delimiter,
            _ROMAN_STYLE,
        )
        closer = {"(": ")", "[": "]", "|": "|"}[opener]
        start = self.position
        if opener == closer:
            closing = self._find_symmetric_character(start, opener)
        else:
            closing = self._find_matching_character(start, opener, closer)
        if closing is None:
            self.position += 1
            return self._styled_text(opener, _ROMAN_STYLE)
        body_source = self.source[start + 1:closing]
        body = self._parse_fragment(body_source)
        self.position = closing + 1
        return Delimiter(opener, closer, (body,))


    def _find_symmetric_character(self, start: int, delimiter: str) -> int | None:
        group_depth = 0
        cursor = start + 1
        while cursor < len(self.source):
            char = self.source[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == "{":
                group_depth += 1
            elif char == "}" and group_depth:
                group_depth -= 1
            elif char == delimiter and group_depth == 0:
                return cursor
            cursor += 1
        return None


    def _try_escaped_brace_delimiter(self) -> Node | None:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            Delimiter,
        )
        closing = self._find_matching_control_symbol("{", "}", self.position)
        if closing is None:
            return None
        body = self._parse_fragment(self.source[self.position:closing])
        self.position = closing + 2
        return Delimiter("{", "}", (body,))


    def _try_command_delimiter(self, command: str, command_start: int) -> Node | None:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            BARE_DELIMITER_COMMAND_PAIRS,
            Delimiter,
            _read_control_word,
            _skip_whitespace,
        )
        pair = BARE_DELIMITER_COMMAND_PAIRS.get(command)
        if pair is None:
            return None
        closing_command, left, right = pair
        closing = self._find_matching_control_word(command, closing_command, self.position)
        if closing is None:
            return None
        body = self._parse_fragment(self.source[self.position:closing])
        _, after = _read_control_word(self.source, closing)
        self.position = _skip_whitespace(self.source, after)
        return Delimiter(left, right, (body,))


    def _try_symmetric_command_delimiter(self, command_start: int) -> Node | None:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            Delimiter,
        )
        closing = self.source.find("\\|", self.position)
        if closing < 0:
            return None
        body = self._parse_fragment(self.source[self.position:closing])
        self.position = closing + 2
        return Delimiter("‖", "‖", (body,))


    def _try_symmetric_named_delimiter(self, command: str) -> Node | None:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            Delimiter,
            _read_control_word,
            _skip_whitespace,
        )
        closing = self._find_symmetric_control_word(command, self.position)
        if closing is None:
            return None
        body = self._parse_fragment(self.source[self.position:closing])
        _, after = _read_control_word(self.source, closing)
        self.position = _skip_whitespace(self.source, after)
        delimiter = "‖" if command == "Vert" else "|"
        return Delimiter(delimiter, delimiter, (body,))


    def _find_matching_character(self, start: int, opener: str, closer: str) -> int | None:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            _BIG_LEFT_COMMAND,
            _BIG_MIDDLE_COMMAND,
            _BIG_RIGHT_COMMAND,
            _BIG_SYMMETRIC_COMMAND,
            _is_command_letter,
            _read_control_word,
        )
        expected_closers = [closer]
        group_depth = 0
        cursor = start + 1
        explicit_delimiter_commands = {
            "left",
            "right",
            "middle",
            _BIG_LEFT_COMMAND,
            _BIG_RIGHT_COMMAND,
            _BIG_MIDDLE_COMMAND,
            _BIG_SYMMETRIC_COMMAND,
        }
        while cursor < len(self.source):
            char = self.source[cursor]
            if char == "\\":
                if cursor + 1 >= len(self.source):
                    return None
                if not _is_command_letter(self.source[cursor + 1]):
                    cursor += 2
                    continue
                command, cursor = _read_control_word(self.source, cursor)
                if group_depth == 0 and command in explicit_delimiter_commands:
                    cursor = self._skip_scanned_delimiter_token(cursor)
                continue
            if char == "{":
                group_depth += 1
                cursor += 1
                continue
            if char == "}":
                if group_depth == 0:
                    return None
                group_depth -= 1
                cursor += 1
                continue
            if group_depth == 0:
                if char in "([":
                    expected_closers.append(")" if char == "(" else "]")
                elif char in ")]":
                    if char != expected_closers[-1]:
                        return None
                    expected_closers.pop()
                    if not expected_closers:
                        return cursor
            cursor += 1
        return None


    def _skip_scanned_delimiter_token(self, position: int) -> int:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            _is_command_letter,
            _read_control_word,
            _skip_whitespace,
        )
        position = _skip_whitespace(self.source, position)
        if position >= len(self.source):
            return position
        if self.source[position] != "\\":
            return position + 1
        if position + 1 >= len(self.source):
            return len(self.source)
        if not _is_command_letter(self.source[position + 1]):
            return position + 2
        _, position = _read_control_word(self.source, position)
        return position


    def _find_matching_control_symbol(
        self,
        opener: str,
        closer: str,
        start: int,
    ) -> int | None:
        depth = 1
        cursor = start
        while cursor + 1 < len(self.source):
            if self.source[cursor] != "\\":
                cursor += 1
                continue
            symbol = self.source[cursor + 1]
            if symbol == opener:
                depth += 1
            elif symbol == closer:
                depth -= 1
                if depth == 0:
                    return cursor
            cursor += 2
        return None


    def _find_matching_control_word(
        self,
        opener: str,
        closer: str,
        start: int,
    ) -> int | None:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            _is_command_letter,
            _read_control_word,
        )
        depth = 1
        cursor = start
        while cursor < len(self.source):
            if self.source[cursor] != "\\" or cursor + 1 >= len(self.source):
                cursor += 1
                continue
            if not _is_command_letter(self.source[cursor + 1]):
                cursor += 2
                continue
            command, end = _read_control_word(self.source, cursor)
            if command == opener:
                depth += 1
            elif command == closer:
                depth -= 1
                if depth == 0:
                    return cursor
            cursor = end
        return None


    def _find_symmetric_control_word(
        self,
        command: str,
        start: int,
    ) -> int | None:
        # Resolve the shared parser facade at call time.
        from .formula_parser import (
            _is_command_letter,
            _read_control_word,
        )
        group_depth = 0
        cursor = start
        while cursor < len(self.source):
            char = self.source[cursor]
            if char == "{":
                group_depth += 1
                cursor += 1
                continue
            if char == "}":
                group_depth = max(0, group_depth - 1)
                cursor += 1
                continue
            if char != "\\" or cursor + 1 >= len(self.source):
                cursor += 1
                continue
            if not _is_command_letter(self.source[cursor + 1]):
                cursor += 2
                continue
            current, end = _read_control_word(self.source, cursor)
            if group_depth == 0 and current == command:
                return cursor
            cursor = end
        return None


