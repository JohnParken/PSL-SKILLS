"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

def _template_spec_paths(directory: Path) -> list[Path]:
    """Return every template Design Spec directly inside one directory.

    A library workspace keeps the exact ``design_spec.md`` because its parent
    directory already names the kind and id. A project workspace root shares one
    ``templates/`` across kinds, so it keeps ``design_spec.<kind>.<id>.md`` and
    may hold one spec per kind side by side.
    """
    from .checker import (
        _TEMPLATE_SPEC_NAME_RE,
    )
    if not directory.is_dir():
        return []
    exact = directory / 'design_spec.md'
    qualified = sorted(
        path
        for path in directory.glob('design_spec.*.md')
        if _TEMPLATE_SPEC_NAME_RE.fullmatch(path.name)
    )
    if exact.is_file():
        # Mixing both shapes hides one of them from every reader that stops at
        # the first match, so it is reported rather than silently resolved.
        return [exact] + qualified
    return qualified


def _spec_declared_kind(spec_path: Path) -> str | None:
    """Return one spec's kind, from its filename when it carries one."""
    from .checker import (
        _TEMPLATE_SPEC_NAME_RE,
        _design_spec_kind,
    )
    match = _TEMPLATE_SPEC_NAME_RE.fullmatch(spec_path.name)
    if match is not None:
        return match.group('kind')
    return _design_spec_kind(spec_path)


def _roster_spec_paths(directory: Path) -> list[Path]:
    """Return every spec in one directory that owns an SVG roster."""
    from .checker import (
        _TEMPLATE_SPEC_NAME_RE,
        _design_spec_kind,
        _template_spec_paths,
    )
    roster = []
    for spec in _template_spec_paths(directory):
        match = _TEMPLATE_SPEC_NAME_RE.fullmatch(spec.name)
        if match is not None:
            if match.group('kind') in {'layout', 'deck'}:
                roster.append(spec)
        elif _design_spec_kind(spec) not in {'brand', 'style'}:
            roster.append(spec)
    return roster


def _roster_spec_path(directory: Path) -> Path | None:
    """Return the effective spec that owns this directory's SVG roster.

    A project root may carry both Layout and Deck. Layout owns reusable
    structure when present; Deck owns it only when no Layout is installed.
    """
    from .checker import (
        _roster_spec_paths,
        _spec_declared_kind,
    )
    roster = _roster_spec_paths(directory)
    for spec in roster:
        if spec.name == 'design_spec.md':
            return spec
    for kind in ('layout', 'deck'):
        for spec in roster:
            if _spec_declared_kind(spec) == kind:
                return spec
    return None


def _design_spec_kind(spec_path: Path) -> str | None:
    """Return ``kind`` declared in Design Spec frontmatter.

    Lightweight detector that does not require PyYAML — scans only the
    frontmatter block (``---`` delimited).
    """
    from .checker import (
        re,
    )
    try:
        text = spec_path.read_text(encoding='utf-8')
    except OSError:
        return None
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end == -1:
        return None
    fm_block = text[4:end]
    for line in fm_block.splitlines():
        stripped = line.strip()
        match = re.fullmatch(
            r'''kind\s*:\s*(?:(['"])(brand|style|layout|deck)\1|'''
            r'''(brand|style|layout|deck))'''
            r'''(?:\s+#.*)?\s*''',
            stripped,
        )
        if match:
            return match.group(2) or match.group(3)
    return None


def _declared_template_structure_mode(target_path: Path) -> str | None:
    """Return a template directory's explicit native structure mode."""
    from .checker import (
        _roster_spec_path,
        re,
    )
    directory = target_path.parent if target_path.is_file() else target_path
    spec_path = _roster_spec_path(directory)
    if spec_path is None:
        return None
    try:
        text = spec_path.read_text(encoding='utf-8')
    except OSError:
        return None
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end == -1:
        return None
    match = re.search(
        r'^native_structure_mode:\s*([A-Za-z0-9_-]+)\s*$',
        text[4:end],
        re.MULTILINE,
    )
    return match.group(1).lower() if match else None


def _declared_template_canvas_viewbox(target_path: Path) -> str | None:
    """Return a template design spec's locked root-canvas value."""
    from .checker import (
        _roster_spec_path,
        re,
    )
    directory = target_path.parent if target_path.is_file() else target_path
    spec_path = _roster_spec_path(directory)
    if spec_path is None:
        return None
    try:
        text = spec_path.read_text(encoding='utf-8')
    except OSError:
        return None
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end == -1:
        return None
    match = re.search(
        r'^canvas_viewbox:\s*["\']?([^"\'\r\n]+?)["\']?\s*$',
        text[4:end],
        re.MULTILINE,
    )
    return match.group(1).strip() if match else None


def _template_structure_checks_enabled(target_path: Path) -> bool:
    """Return whether positive structure checks apply to this template."""
    from .checker import (
        _declared_template_structure_mode,
    )
    return _declared_template_structure_mode(target_path) == 'structured'


def _direct_defs_index(
    root: ET.Element,
) -> tuple[Dict[str, ET.Element], set[str]]:
    """Return direct ``<defs>`` children by id plus duplicate ids."""
    from .checker import (
        _local_name,
    )
    definitions: Dict[str, ET.Element] = {}
    duplicates: set[str] = set()
    for defs_elem in root.iter():
        if _local_name(defs_elem) != 'defs':
            continue
        for child in defs_elem:
            definition_id = (child.get('id') or '').strip()
            if not definition_id:
                continue
            if definition_id in definitions:
                duplicates.add(definition_id)
            definitions[definition_id] = child
    return definitions, duplicates


def _effective_presentation_value(
    elem: ET.Element,
    name: str,
    parent_by_id: Dict[int, ET.Element],
) -> str | None:
    """Resolve one inherited presentation property for validation."""
    from .checker import (
        _parse_inline_style,
    )
    current: ET.Element | None = elem
    while current is not None:
        style_values = (
            _parse_inline_style(current.get('style'))
            if _parse_inline_style is not None else {}
        )
        if name in style_values:
            return style_values[name]
        direct = current.get(name)
        if direct is not None:
            return direct
        current = parent_by_id.get(id(current))
    return None


def _parse_viewbox_values(viewbox: str) -> Tuple[float, float, float, float] | None:
    """Parse a root viewBox into four numeric values."""
    from .checker import (
        CanvasContractError,
        parse_project_viewbox,
    )
    try:
        parsed = parse_project_viewbox(viewbox)
    except CanvasContractError:
        return None
    return 0.0, 0.0, float(parsed.width), float(parsed.height)


def _parse_placeholders_fallback(block: str) -> Dict[str, Tuple[str, ...]]:
    """Tiny YAML-free reader for the documented ``placeholders:`` shape.

    Used only when PyYAML is unavailable. Recognized lines (indentation-aware,
    two-space indent assumed):

    .. code-block:: yaml

        placeholders:
          01_cover: ["{{TITLE}}", "{{LOGO}}"]
          03_content: []
          03a_content_two_col:
            - "{{LEFT_TITLE}}"
            - "{{RIGHT_TITLE}}"

    Anything outside this minimal grammar is silently skipped — designers who
    rely on advanced YAML should install pyyaml.
    """
    from .checker import (
        re,
    )
    out: Dict[str, Tuple[str, ...]] = {}
    inline_re = re.compile(
        r"^\s{2}([A-Za-z0-9_]+)\s*:\s*\[(.*)\]\s*$"
    )
    empty_re = re.compile(r"^\s{2}([A-Za-z0-9_]+)\s*:\s*\[\s*\]\s*$")
    block_header_re = re.compile(r"^\s{2}([A-Za-z0-9_]+)\s*:\s*$")
    item_re = re.compile(r'^\s{4}-\s*"?([^"]+)"?\s*$')

    in_section = False
    current_block_key: str | None = None
    current_items: List[str] = []

    def _flush_block() -> None:
        nonlocal current_block_key, current_items
        if current_block_key is not None:
            out[current_block_key] = tuple(current_items)
            current_block_key = None
            current_items = []

    for line in block.splitlines():
        if line.startswith("placeholders:"):
            in_section = True
            continue
        if not in_section:
            continue

        # End of section: dedent to a non-key line.
        if line and not line.startswith(" "):
            _flush_block()
            in_section = False
            continue

        if current_block_key is not None:
            m = item_re.match(line)
            if m:
                value = m.group(1).strip().strip('"').strip("'")
                if value:
                    current_items.append(value)
                continue
            # Block ended.
            _flush_block()

        if empty_re.match(line):
            key = empty_re.match(line).group(1)
            out[key] = ()
            continue

        m = inline_re.match(line)
        if m:
            key, raw = m.group(1), m.group(2)
            items = [p.strip().strip('"').strip("'") for p in raw.split(",")]
            out[key] = tuple(item for item in items if item)
            continue

        m = block_header_re.match(line)
        if m:
            current_block_key = m.group(1)
            current_items = []
            continue

    _flush_block()
    return out


def _source_import_warning_count(summary: Dict[str, object]) -> int:
    """Return only a schema-compatible non-negative warning count."""
    value = summary.get('warning_count')
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _quality_source_fingerprint(results: List[Dict]) -> Dict[str, object]:
    """Bind a quality report to the exact SVG bytes that were checked."""
    from .checker import (
        Path,
        hashlib,
    )
    files: List[Dict[str, object]] = []
    aggregate = hashlib.sha256()
    candidates = sorted(
        (
            result
            for result in results
            if result.get('exists') and result.get('path')
        ),
        key=lambda result: Path(str(result['path'])).name,
    )
    for result in candidates:
        path = Path(str(result['path']))
        file_sha256 = result.get('source_sha256')
        if not isinstance(file_sha256, str):
            files.append({
                'file': path.name,
                'sha256': None,
                'error': 'source bytes were not available during validation',
            })
            file_sha256 = 'unreadable'
        else:
            files.append({'file': path.name, 'sha256': file_sha256})
        aggregate.update(path.name.encode('utf-8'))
        aggregate.update(b'\0')
        aggregate.update(file_sha256.encode('ascii'))
        aggregate.update(b'\n')
    return {
        'algorithm': 'sha256',
        'digest': aggregate.hexdigest(),
        'file_count': len(files),
        'files': files,
    }
