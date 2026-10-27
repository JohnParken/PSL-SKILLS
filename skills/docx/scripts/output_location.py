"""Resolve the DOCX output directory from router configuration."""

from dataclasses import dataclass
import json
from pathlib import Path
import re


_ID_RECORD_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
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


def _normalize_id_record(value: object) -> str | None:
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
    if _ID_RECORD_PATTERN.fullmatch(value) is None:
        return None
    if value.upper() in _WINDOWS_DEVICE_NAMES:
        return None
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

    if not isinstance(router_config, dict):
        return _fallback("invalid_router_config_shape")
    if "commonParam" not in router_config:
        return _fallback("missing_common_param")

    common_param = router_config["commonParam"]
    if isinstance(common_param, str):
        try:
            common_param = _parse_json(common_param)
        except (ValueError, RecursionError):
            return _fallback("invalid_common_param_json")
    if not isinstance(common_param, list):
        return _fallback("invalid_common_param_shape")

    id_record_values: list[object] = []
    for entry in common_param:
        if not isinstance(entry, dict):
            return _fallback("invalid_common_param_entry")
        name = entry.get("name")
        if not isinstance(name, str) or not name or "value" not in entry:
            return _fallback("invalid_common_param_entry")
        if name == "idRecord":
            id_record_values.append(entry["value"])

    if not id_record_values:
        return _fallback("missing_id_record")
    if len(id_record_values) != 1:
        return _fallback("duplicate_id_record")

    id_record = _normalize_id_record(id_record_values[0])
    if id_record is None:
        return _fallback("invalid_id_record")

    return OutputLocation(Path(id_record) / "output", False, None)
