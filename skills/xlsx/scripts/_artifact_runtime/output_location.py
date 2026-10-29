from __future__ import annotations
"""Resolve the DOCX output directory from router configuration."""

from dataclasses import dataclass
from pathlib import Path
import re

from .router_config import RouterConfigError, get_common_parameter, read_router_config_from_environment


_QUESTION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


@dataclass(frozen=True)
class OutputLocation:
    output_dir: Path
    used_fallback: bool
    fallback_reason: str | None


def _fallback(reason: str) -> OutputLocation:
    return OutputLocation(Path("."), True, reason)


def _normalize_question_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value < 0:
            return None
        try:
            value = str(value)
        except ValueError:
            return None
    if not isinstance(value, str):
        return None
    if _QUESTION_ID_PATTERN.fullmatch(value) is None:
        return None
    if value.upper() in _WINDOWS_DEVICE_NAMES:
        return None
    return value


def resolve_output_location(router_config_str: str | None) -> OutputLocation:
    """Return a relative output path, falling back safely on bad configuration."""
    try:
        value = get_common_parameter(router_config_str, "questionId")
    except RouterConfigError as exc:
        return _fallback(str(exc))

    question_id = _normalize_question_id(value)
    if question_id is None:
        return _fallback("invalid_question_id")

    return OutputLocation(Path("output") / question_id, False, None)
