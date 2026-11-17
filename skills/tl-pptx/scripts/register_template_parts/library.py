"""Upstream template registration: library responsibilities."""
from __future__ import annotations

from .common import (
    KIND_CONFIG,
    Path,
    re,
)

def is_safe_template_id(template_id: object) -> bool:
    """Return whether an index key is one safe directory-segment id."""
    return (
        isinstance(template_id, str)
        and re.fullmatch(r"\w[\w.-]*", template_id) is not None
        and template_id not in {".", ".."}
        and not template_id.endswith(".")
    )


def get_library_paths(
    kind: str,
    scope: str = "builtin",
    *,
    templates_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Resolve a kind's workspace directory and index in one library layer."""
    if scope not in {"builtin", "local"}:
        raise ValueError(f"Unknown template library scope: {scope!r}")
    config = KIND_CONFIG[kind]
    builtin_dir = config["dir"]
    if templates_dir is None and scope == "builtin":
        return builtin_dir, config["index"]
    base = Path(templates_dir) if templates_dir is not None else builtin_dir.parent
    if scope == "local":
        base = base / "local"
    directory = base / builtin_dir.name
    return directory, directory / config["index"].name


def get_library_scope(root: Path, kind: str) -> str | None:
    """Identify a workspace directly inside either library layer by its path."""
    parent = root.resolve().parent
    for scope in ("builtin", "local"):
        directory, _index = get_library_paths(kind, scope)
        if parent == directory.resolve():
            return scope
    return None

