"""Resolve the DOCX output directory from router configuration."""

from dataclasses import dataclass
import json
import os
import sys
from pathlib import Path
import re


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


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError("nonstandard JSON constant")


def _parse_json(value: str) -> object:
    return json.loads(
        value,
        object_pairs_hook=_reject_duplicate_object_keys,
        parse_constant=_reject_nonstandard_constant,
    )


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


def read_router_config_from_environment() -> str | None:
    """Read configuration, logging its raw value by default unless debug is disabled."""
    value = os.environ.get("ROUTER_CONFIG_VARIABLES")
    if os.environ.get("DOCX_DEBUG_ROUTER_CONFIG", "1") == "1":
        try:
            print("router-config-debug: ROUTER_CONFIG_VARIABLES="
                  + json.dumps(value, ensure_ascii=True), file=sys.stderr)
        except (OSError, UnicodeError):
            pass  # Debug output must not change configuration or primary success.
    return value


def resolve_output_location(router_config_str: str | None) -> OutputLocation:
    """Return a relative output path, falling back safely on bad configuration."""
    if router_config_str is None:
        return _fallback("missing_router_config")
    if not isinstance(router_config_str, str):
        return _fallback("invalid_router_config")
    if router_config_str == "":
        return _fallback("missing_router_config")

    try:
        router_config = _parse_json(router_config_str)
    except (ValueError, RecursionError):
        return _fallback("invalid_router_config_json")

    if isinstance(router_config, list):
        common_values: list[object] = []
        for entry in router_config:
            if not isinstance(entry, dict):
                return _fallback("invalid_router_config_entry")
            name = entry.get("name")
            if not isinstance(name, str) or not name or "value" not in entry:
                return _fallback("invalid_router_config_entry")
            if name == "commonParam":
                common_values.append(entry["value"])
        if not common_values:
            return _fallback("missing_common_param")
        if len(common_values) != 1:
            return _fallback("duplicate_common_param")
        common_param = common_values[0]
    elif isinstance(router_config, dict):
        # Preserve the previously documented direct-object configuration.
        if "commonParam" not in router_config:
            return _fallback("missing_common_param")
        common_param = router_config["commonParam"]
    else:
        return _fallback("invalid_router_config_shape")

    if isinstance(common_param, str):
        try:
            common_param = _parse_json(common_param)
        except (ValueError, RecursionError):
            return _fallback("invalid_common_param_json")
    if not isinstance(common_param, list):
        return _fallback("invalid_common_param_shape")

    question_id_values: list[object] = []
    for entry in common_param:
        if not isinstance(entry, dict):
            return _fallback("invalid_common_param_entry")
        name = entry.get("name")
        if not isinstance(name, str) or not name or "value" not in entry:
            return _fallback("invalid_common_param_entry")
        if name == "questionId":
            question_id_values.append(entry["value"])

    if not question_id_values:
        return _fallback("missing_question_id")
    if len(question_id_values) != 1:
        return _fallback("duplicate_question_id")

    question_id = _normalize_question_id(question_id_values[0])
    if question_id is None:
        return _fallback("invalid_question_id")

    return OutputLocation(Path("output") / question_id, False, None)
