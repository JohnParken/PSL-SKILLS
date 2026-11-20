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

from formula_parser import (
    FormulaError,
    MAX_EXCEL_COLUMN,
    MAX_EXCEL_ROW,
    MAX_FORMULA_LENGTH,
    MAX_PARSE_DEPTH,
    Scalar,
    _CELL_PREFIX_RE,
    _CELL_RE,
    _Cell,
    _Criterion,
    _NUMBER_RE,
    _Parser,
    _PreparedRange,
    _Range,
    _Token,
    _cell_from_text,
    _column_letters,
    _error,
    _is_word_char,
    _tokenize,
)
from formula_wildcards import _wildcard_tokens, _wildcard_matches


Resolver = Callable[[str, str], object]

MAX_RANGE_CELLS = 100_000
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


_CRITERIA_NUMBER_RE = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)


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
