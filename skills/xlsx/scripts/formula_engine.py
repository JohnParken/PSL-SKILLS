#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A small, safe evaluator for a deliberately limited Excel formula subset.

The evaluator accepts numeric and string literals, booleans, cell references,
rectangular ranges, basic arithmetic and comparisons, and a deliberately small
set of aggregate and conditional functions. It does not use Python ``eval``.
References are resolved by the caller so workbook loading and dependency/cycle
handling stay outside this module.

Supported semantics are intentionally conservative: blank cells are zero in
arithmetic; booleans are zero or one in arithmetic; text is not coerced during
arithmetic; and aggregate functions ignore text, booleans, and blanks found in
ranges. Scalar text and boolean aggregate arguments are unsupported. Comparisons
between two strings are case-insensitive. ROUND uses Decimal ROUND_HALF_UP.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Underflow,
    localcontext,
)
from typing import Callable, Optional, Union

from formula_wildcards import _wildcard_tokens, _wildcard_matches


Scalar = Union[Decimal, bool, str, None]
Resolver = Callable[[str, str], object]

MAX_FORMULA_LENGTH = 8192
MAX_RANGE_CELLS = 100_000
MAX_PARSE_DEPTH = 96
MAX_EXCEL_ROW = 1_048_576
MAX_EXCEL_COLUMN = 16_384  # XFD
FORMULA_PRECISION = 28
AGGREGATE_PRECISION = 1000
DECIMAL_EMIN = -999_999
DECIMAL_EMAX = 999_999
MAX_ROUND_DIGITS = 1000
MAX_CONDITIONAL_RANGE_CELLS = 200_000
MAX_CONDITION_PAIRS = 127
MAX_SUMPRODUCT_RANGES = 255
MAX_CRITERION_LENGTH = 255
MAX_CRITERIA_MATCH_WORK = 10_000_000

CONDITIONAL_FUNCTIONS = frozenset({"SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS"})
_RANGE_FUNCTIONS = CONDITIONAL_FUNCTIONS | {"SUMPRODUCT"}


def _new_decimal_context(precision: int = FORMULA_PRECISION, extra_traps=()) -> Context:
    traps = [InvalidOperation, DivisionByZero, Overflow, Underflow]
    traps.extend(extra_traps)
    return Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=DECIMAL_EMIN,
        Emax=DECIMAL_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=traps,
    )


def _exact_sum(values: list[Decimal]) -> Decimal:
    try:
        with localcontext(_new_decimal_context(AGGREGATE_PRECISION, (Inexact,))):
            return sum(values, Decimal(0))
    except Inexact:
        raise _error(
            "calculation_error",
            "Aggregate exceeds the 1000-digit exact accumulation limit",
        )


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
_CRITERIA_NUMBER_RE = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)


def _tokenize(formula: str) -> list[_Token]:
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


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char in "_.·"


class _Parser:
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


def _normalize_resolved(value: object) -> Scalar:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _error("calculation_error", "Cell contains a non-finite number")
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error("calculation_error", "Cell contains a non-finite number")
        return Decimal(str(value))
    raise _error("unsupported", "Cell resolver returned an unsupported value type")


def _number(value: Scalar) -> Decimal:
    if value is None:
        return Decimal(0)
    if isinstance(value, bool):
        return Decimal(1 if value else 0)
    if isinstance(value, Decimal):
        return value
    raise _error("unsupported", "Text cannot be converted to a number")


def _compare_values(left: Scalar, right: Scalar, op: str) -> bool:
    if isinstance(left, bool) != isinstance(right, bool):
        raise _error("unsupported", "Mixed boolean comparisons are unsupported")
    if isinstance(left, str) and isinstance(right, str):
        a, b = left.casefold(), right.casefold()
    elif isinstance(left, str) or isinstance(right, str):
        raise _error("unsupported", "Text and numeric values cannot be compared")
    else:
        a, b = _number(left), _number(right)
    if op == "=":
        return a == b
    if op == "<>":
        return a != b
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    return a >= b


def _make_criterion(value: object) -> _Criterion:
    if isinstance(value, bool):
        raise _error("unsupported", "Boolean criteria are unsupported")
    if isinstance(value, Decimal):
        return _Criterion("=", "number", value, ())
    if not isinstance(value, str):
        raise _error("unsupported", "Blank and non-scalar criteria are unsupported")
    if len(value) > MAX_CRITERION_LENGTH:
        raise _error("unsupported", "Criteria exceed the 255-character limit")

    operator = "="
    operand = value
    for candidate in ("<=", ">=", "<>", "=", "<", ">"):
        if value.startswith(candidate):
            operator = candidate
            operand = value[len(candidate):]
            break

    if _CRITERIA_NUMBER_RE.fullmatch(operand):
        try:
            number = Decimal(operand)
        except DecimalException:
            raise _error("calculation_error", "Numeric criterion is outside supported Decimal limits")
        if not number.is_finite():
            raise _error("calculation_error", "Numeric criterion is non-finite")
        return _Criterion(operator, "number", number, ())

    if operator in {"<", "<=", ">", ">="}:
        raise _error("unsupported", "Text ordering criteria are unsupported")
    if not operand:
        return _Criterion(operator, "empty", "", ())
    return _Criterion(operator, "text", operand, _wildcard_tokens(operand))


def _validate_criteria_values(values: tuple[Scalar, ...], criterion: _Criterion) -> None:
    for value in values:
        if isinstance(value, bool):
            raise _error("unsupported", "Boolean values in criteria ranges are unsupported")
        if criterion.kind == "number" and not isinstance(value, Decimal):
            raise _error("unsupported", "Numeric criteria require numeric criteria-range values")
        if (criterion.kind == "text" and value is not None
                and not isinstance(value, str)):
            raise _error("unsupported", "Text criteria cannot compare numeric criteria-range values")


def _check_criteria_match_work(
    conditions: list[tuple[_PreparedRange, _Criterion]],
) -> None:
    work = 0
    for criteria_range, criterion in conditions:
        if criterion.kind != "text":
            continue
        pattern_length = max(1, len(criterion.pattern))
        for value in criteria_range.values:
            text_length = len(value) if isinstance(value, str) else 0
            work += max(1, text_length) * pattern_length
            if work > MAX_CRITERIA_MATCH_WORK:
                raise _error("unsupported", "Criteria matching exceeds the 10000000-step limit")


def _validate_sum_values(values: tuple[Scalar, ...]) -> None:
    for value in values:
        if isinstance(value, bool):
            raise _error("unsupported", "Boolean values in conditional sum ranges are unsupported")


def _criterion_matches(value: Scalar, criterion: _Criterion) -> bool:
    if criterion.kind == "number":
        number = criterion.value
        if not isinstance(number, Decimal) or not isinstance(value, Decimal):
            raise _error("unsupported", "Numeric criteria require numeric criteria-range values")
        if criterion.operator == "=":
            return value == number
        if criterion.operator == "<>":
            return value != number
        if criterion.operator == "<":
            return value < number
        if criterion.operator == "<=":
            return value <= number
        if criterion.operator == ">":
            return value > number
        return value >= number

    if isinstance(value, bool):
        raise _error("unsupported", "Boolean values in criteria ranges are unsupported")
    if criterion.kind == "empty":
        is_empty = value is None or value == ""
        return is_empty if criterion.operator == "=" else not is_empty

    if value is None:
        return criterion.operator == "<>"
    if isinstance(value, Decimal):
        raise _error("unsupported", "Text criteria cannot compare numeric criteria-range values")
    if not isinstance(value, str):
        raise _error("unsupported", "Criteria ranges contain an unsupported value")
    matches = _wildcard_matches(value, criterion.pattern)
    return matches if criterion.operator == "=" else not matches


def _sumproduct(arrays: list[_PreparedRange]) -> Decimal:
    try:
        with localcontext(_new_decimal_context(AGGREGATE_PRECISION, (Inexact,))):
            total = Decimal(0)
            for index in range(len(arrays[0].values)):
                factors = [array.values[index] for array in arrays]
                if any(not isinstance(value, Decimal) for value in factors):
                    product = Decimal(0)
                elif any(value == 0 for value in factors):
                    product = Decimal(0)
                else:
                    product = Decimal(1)
                    for value in factors:
                        product *= value
                total += product
            return total
    except Inexact:
        raise _error(
            "calculation_error",
            "SUMPRODUCT exceeds the 1000-digit exact multiplication or accumulation limit",
        )


class _Evaluator:
    def __init__(self, sheet: str, resolve: Resolver):
        self.sheet = sheet
        self.resolve = resolve

    def evaluate(self, root):
        # An explicit work stack keeps long flat expressions from consuming
        # Python's recursion limit. Nested syntax is bounded by the parser.
        work = [("eval", root)]
        values: list[object] = []
        while work:
            action, item = work.pop()
            if action == "eval":
                kind = item[0]
                if kind == "literal":
                    values.append(item[1])
                elif kind == "ref":
                    values.append(self._resolve(item[1], item[2].address))
                elif kind == "range":
                    values.append(self._make_range(item[1], item[2], item[3]))
                elif kind == "unary":
                    work.append(("unary", item[1]))
                    work.append(("eval", item[2]))
                elif kind == "binary":
                    work.append(("binary", item[1]))
                    work.append(("eval", item[3]))
                    work.append(("eval", item[2]))
                elif kind == "call":
                    name, args = item[1], item[2]
                    if name == "IF":
                        self._check_arity(name, args, 3)
                        work.append(("if_choose", (args[1], args[2])))
                        work.append(("eval", args[0]))
                    elif name in _RANGE_FUNCTIONS:
                        prepared_ranges = self._prepare_conditional_ranges(name, args)
                        work.append(("call", (name, len(args))))
                        for index in range(len(args) - 1, -1, -1):
                            if index in prepared_ranges:
                                work.append(("push", prepared_ranges[index]))
                            else:
                                work.append(("eval", args[index]))
                    else:
                        if name not in {"SUM", "COUNT", "AVERAGE", "MIN", "MAX", "ROUND"}:
                            raise _error("unsupported", f"Function {name} is unsupported")
                        work.append(("call", (name, len(args))))
                        for arg in reversed(args):
                            work.append(("eval", arg))
                else:  # pragma: no cover - parser emits only known node shapes
                    raise _error("calculation_error", "Invalid formula expression")
            elif action == "unary":
                value = values.pop()
                number = _number(value)
                values.append(+number if item == "+" else -number)
            elif action == "binary":
                right = values.pop()
                left = values.pop()
                if item in {"=", "<>", "<", "<=", ">", ">="}:
                    values.append(_compare_values(left, right, item))
                else:
                    a, b = _number(left), _number(right)
                    if item == "+":
                        result = a + b
                    elif item == "-":
                        result = a - b
                    elif item == "*":
                        result = a * b
                    else:
                        if b == 0:
                            raise _error("calculation_error", "Division by zero")
                        result = a / b
                    if not result.is_finite():
                        raise _error("calculation_error", "Arithmetic produced a non-finite result")
                    values.append(result)
            elif action == "if_choose":
                condition = values.pop()
                if isinstance(condition, bool):
                    truth = condition
                elif condition is None:
                    truth = False
                elif isinstance(condition, Decimal):
                    truth = condition != 0
                else:
                    raise _error("unsupported", "IF condition must be numeric or boolean")
                branch = item[0] if truth else item[1]
                work.append(("eval", branch))
            elif action == "call":
                name, count = item
                args = values[-count:] if count else []
                if count:
                    del values[-count:]
                values.append(self._call(name, args))
            elif action == "push":
                values.append(item)

        if len(values) != 1:
            raise _error("calculation_error", "Formula did not produce one result")
        result = values[0]
        if isinstance(result, _Range):
            raise _error("unsupported", "A range cannot be used as a scalar result")
        return result

    def _resolve(self, sheet: Optional[str], address: str) -> Scalar:
        try:
            return _normalize_resolved(self.resolve(sheet if sheet is not None else self.sheet, address))
        except FormulaError:
            raise
        except Exception as exc:
            raise _error("calculation_error", f"Cell resolution failed: {exc}")

    def _make_range(self, sheet: Optional[str], first: _Cell, last: _Cell) -> _Range:
        target_sheet = sheet if sheet is not None else self.sheet
        min_column, max_column = sorted((first.column, last.column))
        min_row, max_row = sorted((first.row, last.row))
        cell_count = (max_column - min_column + 1) * (max_row - min_row + 1)
        if cell_count > MAX_RANGE_CELLS:
            raise _error("unsupported", "Range exceeds the 100000-cell expansion limit")
        return _Range(target_sheet, min_column, min_row, max_column, max_row)

    @staticmethod
    def _range_shape(area: _Range) -> tuple[int, int]:
        return (
            area.last_row - area.first_row + 1,
            area.last_column - area.first_column + 1,
        )

    def _prepare_conditional_ranges(self, name: str, args: list) -> dict[int, _PreparedRange]:
        if name == "SUMIF":
            if len(args) not in {2, 3}:
                raise _error("calculation_error", "SUMIF expects 2 or 3 arguments")
            positions = [0] + ([2] if len(args) == 3 else [])
        elif name == "SUMIFS":
            if len(args) < 3 or len(args) % 2 != 1:
                raise _error("calculation_error", "SUMIFS expects a sum range and condition pairs")
            if (len(args) - 1) // 2 > MAX_CONDITION_PAIRS:
                raise _error("unsupported", "SUMIFS exceeds the 127-condition-pair limit")
            positions = [0] + list(range(1, len(args), 2))
        elif name == "COUNTIF":
            self._check_arity(name, args, 2)
            positions = [0]
        elif name == "COUNTIFS":
            if len(args) < 2 or len(args) % 2 != 0:
                raise _error("calculation_error", "COUNTIFS expects condition pairs")
            if len(args) // 2 > MAX_CONDITION_PAIRS:
                raise _error("unsupported", "COUNTIFS exceeds the 127-condition-pair limit")
            positions = list(range(0, len(args), 2))
        else:
            if not 1 <= len(args) <= MAX_SUMPRODUCT_RANGES:
                raise _error("calculation_error", "SUMPRODUCT expects 1 to 255 ranges")
            positions = list(range(len(args)))

        areas = []
        for position in positions:
            node = args[position]
            if node[0] == "ref":
                area = self._make_range(node[1], node[2], node[2])
            elif node[0] == "range":
                area = self._make_range(node[1], node[2], node[3])
            else:
                raise _error("unsupported", f"{name} range arguments must be cell references")
            areas.append((position, area))

        total_cells = sum(
            (area.last_row - area.first_row + 1)
            * (area.last_column - area.first_column + 1)
            for _, area in areas
        )
        if total_cells > MAX_CONDITIONAL_RANGE_CELLS:
            raise _error("unsupported", "Function ranges exceed the 200000-cell total limit")

        if name in {"SUMIF", "SUMIFS", "COUNTIFS", "SUMPRODUCT"}:
            first_shape = self._range_shape(areas[0][1])
            if any(self._range_shape(area) != first_shape for _, area in areas[1:]):
                raise _error("unsupported", f"{name} range arguments must have identical dimensions")

        prepared = {}
        for position, area in areas:
            prepared[position] = _PreparedRange(area, tuple(self._range_values(area)))
        return prepared

    def _range_values(self, area: _Range):
        for row in range(area.first_row, area.last_row + 1):
            for column in range(area.first_column, area.last_column + 1):
                address = f"{_column_letters(column)}{row}"
                yield self._resolve(area.sheet, address)

    @staticmethod
    def _check_arity(name: str, args: list, expected: int) -> None:
        if len(args) != expected:
            raise _error("calculation_error", f"{name} expects {expected} arguments")

    def _call(self, name: str, args: list[object]):
        if name in CONDITIONAL_FUNCTIONS:
            return self._call_conditional(name, args)
        if name == "SUMPRODUCT":
            return _sumproduct(args)
        if name not in {"SUM", "COUNT", "AVERAGE", "MIN", "MAX", "ROUND"}:
            raise _error("unsupported", f"Function {name} is unsupported")
        if name == "ROUND":
            self._check_arity(name, args, 2)
            number = _number(args[0])
            digits_value = _number(args[1])
            if (digits_value < -MAX_ROUND_DIGITS
                    or digits_value > MAX_ROUND_DIGITS):
                raise _error("calculation_error", "ROUND digits exceed the supported limit")
            if digits_value != digits_value.to_integral_value():
                raise _error("calculation_error", "ROUND digits must be an integer")
            digits = int(digits_value)
            try:
                with localcontext(_new_decimal_context()):
                    quantum = Decimal(1).scaleb(-digits)
                    result = number.quantize(quantum, rounding=ROUND_HALF_UP)
                    if not result.is_finite():
                        raise _error("calculation_error", "ROUND produced a non-finite result")
                    return result
            except DecimalException:
                raise _error("calculation_error", "ROUND result is outside supported Decimal precision")

        numeric: list[Decimal] = []
        for arg in args:
            if isinstance(arg, _Range):
                for value in self._range_values(arg):
                    if isinstance(value, Decimal):
                        numeric.append(value)
                continue
            if arg is None:
                continue
            if isinstance(arg, str):
                raise _error("unsupported", "Text scalar arguments to aggregate functions are unsupported")
            if isinstance(arg, bool):
                raise _error("unsupported", "Boolean scalar arguments to aggregate functions are unsupported")
            if isinstance(arg, Decimal):
                numeric.append(arg)

        if name == "COUNT":
            return Decimal(len(numeric))
        if name == "SUM":
            return _exact_sum(numeric)
        if name == "AVERAGE":
            if not numeric:
                raise _error("calculation_error", "AVERAGE has no numeric values")
            return _exact_sum(numeric) / Decimal(len(numeric))
        if not numeric:
            return Decimal(0)
        return min(numeric) if name == "MIN" else max(numeric)

    def _call_conditional(self, name: str, args: list[object]) -> Decimal:
        if name == "SUMIF":
            criteria_range = args[0]
            criterion = _make_criterion(args[1])
            sum_range = args[2] if len(args) == 3 else criteria_range
            _validate_criteria_values(criteria_range.values, criterion)
            _validate_sum_values(sum_range.values)
            _check_criteria_match_work([(criteria_range, criterion)])
            matched = [
                value
                for condition_value, value in zip(criteria_range.values, sum_range.values)
                if _criterion_matches(condition_value, criterion)
                and isinstance(value, Decimal)
            ]
            return _exact_sum(matched)

        if name == "SUMIFS":
            sum_range = args[0]
            conditions = [
                (args[index], _make_criterion(args[index + 1]))
                for index in range(1, len(args), 2)
            ]
            for criteria_range, criterion in conditions:
                _validate_criteria_values(criteria_range.values, criterion)
            _validate_sum_values(sum_range.values)
            _check_criteria_match_work(conditions)
            matched = []
            for index, value in enumerate(sum_range.values):
                if all(
                    _criterion_matches(criteria_range.values[index], criterion)
                    for criteria_range, criterion in conditions
                ) and isinstance(value, Decimal):
                    matched.append(value)
            return _exact_sum(matched)

        if name == "COUNTIF":
            criteria_range = args[0]
            criterion = _make_criterion(args[1])
            _validate_criteria_values(criteria_range.values, criterion)
            _check_criteria_match_work([(criteria_range, criterion)])
            count = sum(
                1 for value in criteria_range.values
                if _criterion_matches(value, criterion)
            )
            return Decimal(count)

        conditions = [
            (args[index], _make_criterion(args[index + 1]))
            for index in range(0, len(args), 2)
        ]
        for criteria_range, criterion in conditions:
            _validate_criteria_values(criteria_range.values, criterion)
        _check_criteria_match_work(conditions)
        count = 0
        for index in range(len(conditions[0][0].values)):
            if all(
                _criterion_matches(criteria_range.values[index], criterion)
                for criteria_range, criterion in conditions
            ):
                count += 1
        return Decimal(count)


def evaluate(formula: str, sheet: str, resolve: Resolver) -> Scalar:
    """Evaluate a limited Excel formula using ``resolve(sheet, uppercase_A1)``.

    ``resolve`` returns a scalar (or a compatible Python int/float) and may
    raise :class:`FormulaError`; in particular, cycle errors pass through
    unchanged. This function returns Decimal, bool, str, or None.
    """
    if not callable(resolve):
        raise _error("calculation_error", "resolve must be callable")
    try:
        expression = _Parser(_tokenize(formula)).parse()
        with localcontext(_new_decimal_context()):
            result = _Evaluator(sheet, resolve).evaluate(expression)
        if isinstance(result, Decimal):
            if not result.is_finite():
                raise _error("calculation_error", "Formula result is non-finite")
            return result
        if isinstance(result, (bool, str)) or result is None:
            return result
        raise _error("unsupported", "Formula produced an unsupported value")
    except FormulaError:
        raise
    except (DecimalException, OverflowError, RecursionError) as exc:
        raise _error("calculation_error", f"Formula calculation failed: {exc}")


__all__ = ["FormulaError", "evaluate"]
