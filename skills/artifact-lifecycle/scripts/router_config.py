"""Router environment access and strict parameter lookup; no filesystem policy."""
import json
import os
import sys

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

def read_environment_parameter(name: str, *, environ=None) -> str | None:
    """Read one variable without logging, mutation or filesystem access.

    An explicit mapping permits isolated callers and deterministic tests.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("environment name must be a nonempty string")
    return (os.environ if environ is None else environ).get(name)


def read_router_config_from_environment() -> str | None:
    """Read configuration, logging its raw value by default unless debug is disabled."""
    value = read_environment_parameter("ROUTER_CONFIG_VARIABLES")
    if os.environ.get("DOCX_DEBUG_ROUTER_CONFIG", "1") == "1":
        try:
            print("router-config-debug: ROUTER_CONFIG_VARIABLES="
                  + json.dumps(value, ensure_ascii=True), file=sys.stderr)
        except (OSError, UnicodeError):
            pass  # Debug output must not change configuration or primary success.
    return value

class RouterConfigError(ValueError):
    """Stable reason code; caller decides its own fallback policy."""


def get_common_parameter(router_config_str: str | None, parameter_name: str) -> object:
    """Return exactly one named common parameter, preserving its JSON type.

    Existing question-id reason codes remain stable for output_location callers.
    No path validation, environment mutation or filesystem access occurs here.
    """
    if not isinstance(parameter_name, str) or not parameter_name:
        raise ValueError("parameter name must be a nonempty string")
    if router_config_str is None:
        raise RouterConfigError("missing_router_config")
    if not isinstance(router_config_str, str):
        raise RouterConfigError("invalid_router_config")
    if router_config_str == "":
        raise RouterConfigError("missing_router_config")

    try:
        router_config = _parse_json(router_config_str)
    except (ValueError, RecursionError):
        raise RouterConfigError("invalid_router_config_json")

    if isinstance(router_config, list):
        common_values: list[object] = []
        for entry in router_config:
            if not isinstance(entry, dict):
                raise RouterConfigError("invalid_router_config_entry")
            name = entry.get("name")
            if not isinstance(name, str) or not name or "value" not in entry:
                raise RouterConfigError("invalid_router_config_entry")
            if name == "commonParam":
                common_values.append(entry["value"])
        if not common_values:
            raise RouterConfigError("missing_common_param")
        if len(common_values) != 1:
            raise RouterConfigError("duplicate_common_param")
        common_param = common_values[0]
    elif isinstance(router_config, dict):
        # Preserve the previously documented direct-object configuration.
        if "commonParam" not in router_config:
            raise RouterConfigError("missing_common_param")
        common_param = router_config["commonParam"]
    else:
        raise RouterConfigError("invalid_router_config_shape")

    if isinstance(common_param, str):
        try:
            common_param = _parse_json(common_param)
        except (ValueError, RecursionError):
            raise RouterConfigError("invalid_common_param_json")
    if not isinstance(common_param, list):
        raise RouterConfigError("invalid_common_param_shape")

    parameter_values: list[object] = []
    for entry in common_param:
        if not isinstance(entry, dict):
            raise RouterConfigError("invalid_common_param_entry")
        name = entry.get("name")
        if not isinstance(name, str) or not name or "value" not in entry:
            raise RouterConfigError("invalid_common_param_entry")
        if name == parameter_name:
            parameter_values.append(entry["value"])

    if not parameter_values:
        raise RouterConfigError("missing_question_id" if parameter_name == "questionId" else "missing_parameter")
    if len(parameter_values) != 1:
        raise RouterConfigError("duplicate_question_id" if parameter_name == "questionId" else "duplicate_parameter")

    return parameter_values[0]
