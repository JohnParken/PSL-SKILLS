# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Reserve native-object part names across slides without filesystem access.

This extracts the upstream PackagePartRegistry's in-memory behavior. Source
package discovery and disk reads are excluded from this standard-library skill.
The independent final packager validates all part names, types and relationships.
"""

from __future__ import annotations

from collections.abc import Iterable


class PackagePartRegistry:
    """Reserve package names and reject conflicting native-object writes."""

    def __init__(self, content_types: dict[str, str] | None = None) -> None:
        self.parts: dict[str, bytes] = {}
        self.content_types = dict(content_types or {})
        self.owners: dict[str, str] = {}
        self.source_names: set[str] = set()
        self.reserve_source(self.content_types, "template/source content types")

    def reserve_source(self, names: Iterable[str], owner: str) -> None:
        """Reserve immutable source names before visiting any new objects."""
        for name in names:
            if name not in self.owners:
                self.owners[name] = owner
                self.source_names.add(name)

    def next_index(self, patterns: tuple[str, ...], start: int, occupied: Iterable[str] = ()) -> int:
        """Keep the preferred number unless a member of its part family exists."""
        used = self.owners.keys() | set(occupied)
        while any(pattern.format(start) in used for pattern in patterns):
            start += 1
        return start

    def register(
        self, parts: dict[str, bytes], content_types: dict[str, str], owner: str, *, source: bool = False,
    ) -> None:
        """Register an entire object atomically; identical source parts may reuse names."""
        for name in parts.keys() | content_types.keys():
            previous_owner = self.owners.get(name)
            existing = self.parts.get(name)
            previous_type = self.content_types.get(name)
            content_type = content_types.get(name)
            collision = previous_owner is not None and (
                not source or name not in self.source_names
                or (existing is not None and existing != parts.get(name))
            )
            if collision or (previous_type and content_type and previous_type != content_type):
                raise RuntimeError(
                    f"OOXML part collision: {name!r} between {previous_owner!r} and {owner!r}; "
                    "use a distinct part name for each new object"
                )
        for name in parts.keys() | content_types.keys():
            if name not in self.parts:
                self.owners[name] = owner
            if source:
                self.source_names.add(name)
        self.parts.update(parts)
        self.content_types.update(content_types)
