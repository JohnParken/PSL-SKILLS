#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tokenizer, AST dataclasses, and recursive-descent parser for Excel formulas."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Optional, Union


Scalar = Union[Decimal, bool, str, None]

MAX_FORMULA_LENGTH = 8192
MAX_PARSE_DEPTH = 96
MAX_EXCEL_ROW = 1_048_576
MAX_EXCEL_COLUMN = 16_384  # XFD


class FormulaError(Exception):
    """Formula failure with a stable machine-readable category."""

    def __init__(self, code: str, message: str):
        if code not in {"unsupported", "calculation_error", "cycle"}:
            raise ValueError("FormulaError code must be unsupported, calculation_error, or cycle")
        self.code = code
        self.message = message
        super().__init__(message)


def _error(code: str, message: str) -> FormulaError:
    return FormulaError(code, message)


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    position: int


@dataclass(frozen=True)
class _Cell:
    column: int
    row: int
    address: str


@dataclass(frozen=True)
class _Range:
    sheet: str
    first_column: int
    first_row: int
    last_column: int
    last_row: int


@dataclass(frozen=True)
class _PreparedRange:
    area: _Range
    values: tuple[Scalar, ...]


@dataclass(frozen=True)
class _Criterion:
    operator: str
    kind: str
    value: Union[Decimal, str]
    pattern: tuple[tuple[str, str], ...]


_CELL_RE = re.compile(r"\$?([A-Za-z]+)\$?([0-9]+)\Z")
_CELL_PREFIX_RE = re.compile(r"\$?[A-Za-z]+\$?[0-9]+")
_NUMBER_RE = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char in "_.·"


def _tokenize(formula: str) -> list[_Token]:
    """Tokenize an Excel formula string into a list of lexical tokens."""
    if not isinstance(formula, str):
        raise _error("calculation_error", "Formula must be a string")
    if len(formula) > MAX_FORMULA_LENGTH:
        raise _error("unsupported", "Formula exceeds the 8192-character limit")

    source = formula.lstrip()
    offset = len(formula) - len(source)
    if source.startswith("="):
        source = source[1:]
        offset += 1

    tokens: list[_Token] = []
    i = 0
    while i < len(source):
        char = source[i]
        if char.isspace():
            i += 1
            continue

        if char == '"':
            start = i
            i += 1
            pieces: list[str] = []
            while i < len(source):
                if source[i] == '"':
                    if i + 1 < len(source) and source[i + 1] == '"':
                        pieces.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                pieces.append(source[i])
                i += 1
            else:
                raise _error("calculation_error", "Unterminated string literal")
            tokens.append(_Token("STRING", "".join(pieces), offset + start))
            continue

        if char == "'":
            start = i
            i += 1
            pieces = []
            while i < len(source):
                if source[i] == "'":
                    if i + 1 < len(source) and source[i + 1] == "'":
                        pieces.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                pieces.append(source[i])
                i += 1
            else:
                raise _error("calculation_error", "Unterminated quoted sheet name")
            tokens.append(_Token("QUOTED_SHEET", "".join(pieces), offset + start))
            continue

        if ("0" <= char <= "9" or
                (char == "." and i + 1 < len(source) and "0" <= source[i + 1] <= "9")):
            match = _NUMBER_RE.match(source, i)
            if match is None:
                raise _error("calculation_error", "Invalid numeric literal")
            end = match.end()
            if end < len(source) and source[end] in "eE":
                after_e = end + 1
                if after_e < len(source) and source[after_e] in "+-":
                    after_e += 1
                if after_e < len(source) and "0" <= source[after_e] <= "9":
                    raise _error("unsupported", "Exponent notation is unsupported")
            tokens.append(_Token("NUMBER", match.group(0), offset + i))
            i = end
            continue

        # A cell-shaped atom is kept as one token. The parser decides whether
        # it is a cell or a sheet name by looking for a following exclamation.
        if char == "$" or char.isalpha() or char == "_":
            match = _CELL_PREFIX_RE.match(source, i)
            if match is not None:
                end = match.end()
                if end == len(source) or not _is_word_char(source[end]):
                    tokens.append(_Token("ATOM", match.group(0), offset + i))
                    i = end
                    continue

            if char.isalpha() or char == "_":
                end = i + 1
                while end < len(source) and _is_word_char(source[end]):
                    end += 1
                tokens.append(_Token("ATOM", source[i:end], offset + i))
                i = end
                continue

        if source.startswith("<=", i) or source.startswith(">=", i) or source.startswith("<>", i):
            tokens.append(_Token("OP", source[i:i + 2], offset + i))
            i += 2
            continue

        if char in "+-*/=<>():,!$":
            kind = "OP" if char in "+-*/=<>" else char
            tokens.append(_Token(kind, char, offset + i))
            i += 1
            continue

        if char in "^&%[]{};":
            raise _error("unsupported", "Formula uses an unsupported operator or reference form")
        raise _error("calculation_error", "Formula contains an invalid character")

    tokens.append(_Token("EOF", "", offset + len(source)))
    return tokens


class _Parser:
    """Recursive-descent parser for a bounded subset of Excel formula ASTs."""

    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.index = 0

    def _current(self) -> _Token:
        return self.tokens[self.index]

    def _take(self) -> _Token:
        token = self._current()
        self.index += 1
        return token

    def _accept(self, kind: str, value: Optional[str] = None) -> Optional[_Token]:
        token = self._current()
        if token.kind == kind and (value is None or token.value == value):
            self.index += 1
            return token
        return None

    def _expect(self, kind: str, value: Optional[str] = None) -> _Token:
        token = self._accept(kind, value)
        if token is None:
            raise _error("calculation_error", "Malformed formula")
        return token

    def parse(self):
        if self._current().kind == "EOF":
            raise _error("calculation_error", "Formula is empty")
        node = self._comparison(0)
        if self._current().kind != "EOF":
            if self._current().kind == "ATOM":
                raise _error("unsupported", "Named references and implicit operators are unsupported")
            raise _error("calculation_error", "Unexpected token after formula")
        return node

    def _comparison(self, depth: int):
        node = self._additive(depth)
        while self._current().kind == "OP" and self._current().value in {"=", "<>", "<", "<=", ">", ">="}:
            op = self._take().value
            right = self._additive(depth)
            node = ("binary", op, node, right)
        return node

    def _additive(self, depth: int):
        node = self._multiplicative(depth)
        while self._current().kind == "OP" and self._current().value in {"+", "-"}:
            op = self._take().value
            right = self._multiplicative(depth)
            node = ("binary", op, node, right)
        return node

    def _multiplicative(self, depth: int):
        node = self._unary(depth)
        while self._current().kind == "OP" and self._current().value in {"*", "/"}:
            op = self._take().value
            right = self._unary(depth)
            node = ("binary", op, node, right)
        return node

    def _unary(self, depth: int):
        token = self._current()
        if token.kind == "OP" and token.value in {"+", "-"}:
            if depth >= MAX_PARSE_DEPTH:
                raise _error("calculation_error", "Formula nesting exceeds the supported depth")
            op = self._take().value
            return ("unary", op, self._unary(depth + 1))
        return self._primary(depth)

    def _primary(self, depth: int):
        token = self._current()
        if token.kind == "NUMBER":
            self._take()
            try:
                return ("literal", Decimal(token.value))
            except DecimalException:
                raise _error("calculation_error", "Invalid numeric literal")
        if token.kind == "STRING":
            self._take()
            return ("literal", token.value)
        if self._accept("(") is not None:
            if depth >= MAX_PARSE_DEPTH:
                raise _error("calculation_error", "Formula nesting exceeds the supported depth")
            node = self._comparison(depth + 1)
            self._expect(")")
            return node
        if token.kind == "QUOTED_SHEET":
            return self._reference_or_range(depth)
        if token.kind == "ATOM":
            if self.index + 1 < len(self.tokens) and self.tokens[self.index + 1].kind == "(":
                return self._function(depth)
            if self.index + 1 < len(self.tokens) and self.tokens[self.index + 1].kind == "!":
                return self._reference_or_range(depth)
            self._take()
            upper = token.value.upper()
            if upper == "TRUE":
                return ("literal", True)
            if upper == "FALSE":
                return ("literal", False)
            if _CELL_RE.fullmatch(token.value):
                first = _cell_from_text(token.value)
                return self._range_after_ref(None, first)
            raise _error("unsupported", "Named references are unsupported")
        if token.kind == "EOF":
            raise _error("calculation_error", "Formula ended unexpectedly")
        if token.kind in {",", ")", ":", "!"}:
            raise _error("calculation_error", "Malformed formula")
        raise _error("unsupported", "Formula contains unsupported syntax")

    def _function(self, depth: int):
        name = self._expect("ATOM").value.upper()
        self._expect("(")
        if depth >= MAX_PARSE_DEPTH:
            raise _error("calculation_error", "Formula nesting exceeds the supported depth")
        args = []
        if self._accept(")") is None:
            while True:
                args.append(self._comparison(depth + 1))
                if self._accept(",") is not None:
                    continue
                self._expect(")")
                break
        return ("call", name, args)

    def _reference_or_range(self, depth: int):
        first_sheet, first_cell = self._reference_component()
        return self._range_after_ref(first_sheet, first_cell)

    def _reference_component(self):
        sheet = None
        token = self._current()
        if token.kind == "QUOTED_SHEET":
            sheet = self._take().value
            self._expect("!")
            ref_token = self._current()
            if ref_token.kind != "ATOM" or not _CELL_RE.fullmatch(ref_token.value):
                raise _error("calculation_error", "Sheet reference must be followed by a cell reference")
            self._take()
            return sheet, _cell_from_text(ref_token.value)

        if token.kind != "ATOM":
            raise _error("calculation_error", "Expected a cell reference")
        if self.index + 1 < len(self.tokens) and self.tokens[self.index + 1].kind == "!":
            sheet = self._take().value
            self._take()  # !
            ref_token = self._current()
            if ref_token.kind != "ATOM" or not _CELL_RE.fullmatch(ref_token.value):
                raise _error("calculation_error", "Sheet reference must be followed by a cell reference")
            self._take()
            return sheet, _cell_from_text(ref_token.value)

        self._take()
        if not _CELL_RE.fullmatch(token.value):
            raise _error("unsupported", "Named references are unsupported")
        return None, _cell_from_text(token.value)

    def _range_after_ref(self, first_sheet: Optional[str], first_cell: _Cell):
        if self._accept(":") is None:
            return ("ref", first_sheet, first_cell)
        second_sheet, second_cell = self._reference_component()
        if first_sheet is None and second_sheet is not None:
            raise _error("unsupported", "A range cannot change sheets between its endpoints")
        if (first_sheet is not None and second_sheet is not None
                and first_sheet.casefold() != second_sheet.casefold()):
            raise _error("unsupported", "Cross-sheet endpoint ranges are unsupported")
        return ("range", first_sheet or second_sheet, first_cell, second_cell)


def _cell_from_text(text: str) -> _Cell:
    match = _CELL_RE.fullmatch(text)
    if match is None:
        raise _error("calculation_error", "Invalid cell reference")
    column_text, row_text = match.groups()
    column = 0
    for char in column_text.upper():
        column = column * 26 + ord(char) - ord("A") + 1
    try:
        row = int(row_text)
    except ValueError:
        raise _error("calculation_error", "Invalid row number in cell reference")
    if column < 1 or column > MAX_EXCEL_COLUMN or row < 1 or row > MAX_EXCEL_ROW:
        raise _error("calculation_error", "Cell reference is outside Excel worksheet bounds")
    letters = _column_letters(column)
    address = f"{letters}{row}"
    return _Cell(column, row, address)


def _column_letters(column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


__all__ = [
    "FormulaError",
    "MAX_EXCEL_COLUMN",
    "MAX_EXCEL_ROW",
    "MAX_FORMULA_LENGTH",
    "MAX_PARSE_DEPTH",
    "Scalar",
    "_CELL_PREFIX_RE",
    "_CELL_RE",
    "_Cell",
    "_Criterion",
    "_NUMBER_RE",
    "_Parser",
    "_PreparedRange",
    "_Range",
    "_Token",
    "_cell_from_text",
    "_column_letters",
    "_error",
    "_is_word_char",
    "_tokenize",
]
