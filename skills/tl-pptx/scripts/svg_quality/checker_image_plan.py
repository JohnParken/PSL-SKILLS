"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class ImagePlanQualityMixin:
    """Upstream image plan checker methods."""

    @staticmethod
    def _split_md_table_row(line: str) -> List[str]:
        """Split a simple Markdown table row into stripped cells."""
        return [cell.strip().strip('`') for cell in line.strip().strip('|').split('|')]

    @classmethod
    def _extract_image_resource_rows(cls, spec_text: str) -> List[Dict[str, str]]:
        """Extract rows from design_spec.md §VIII Image Resource List."""
        from .checker import (
            re,
        )
        section_match = re.search(
            r"^##\s+VIII\.\s+Image Resource List\b.*?(?=^##\s+|\Z)",
            spec_text,
            re.MULTILINE | re.DOTALL,
        )
        if not section_match:
            return []

        lines = section_match.group(0).splitlines()
        header = None
        rows: List[Dict[str, str]] = []
        in_resource_table = False
        for line in lines:
            if not line.strip().startswith('|'):
                if in_resource_table and rows:
                    break
                continue

            cells = cls._split_md_table_row(line)
            if not cells:
                continue
            if header is None:
                if any(cell.lower() == 'filename' for cell in cells):
                    header = cells
                    in_resource_table = True
                continue
            if set(cell.replace('-', '').strip() for cell in cells) == {''}:
                continue
            if not in_resource_table:
                continue
            row = {header[i]: cells[i] if i < len(cells) else '' for i in range(len(header))}
            filename = row.get('Filename', '').strip()
            if (
                filename.lower() != 'filename'
                and any(value.strip() for value in row.values())
            ):
                rows.append(row)

        return rows

    @staticmethod
    def _row_filename(row: Dict[str, str]) -> str:
        from .checker import (
            Path,
        )
        return Path(row.get('Filename', '').strip()).name

    @staticmethod
    def _row_raw_filename(row: Dict[str, str]) -> str:
        return row.get('Filename', '').strip()

    @staticmethod
    def _row_type(row: Dict[str, str]) -> str:
        return row.get('Type', '').strip()

    @staticmethod
    def _row_acquire(row: Dict[str, str]) -> str:
        return row.get('Acquire Via', '').strip().lower()

    @staticmethod
    def _row_status(row: Dict[str, str]) -> str:
        return row.get('Status', '').strip().lower()

    @staticmethod
    def _row_layout(row: Dict[str, str]) -> str:
        # ``Image pattern`` is the current §VIII column; ``Layout pattern`` is
        # the pre-rename spelling still present in older Design Specs.
        value = row.get('Image pattern', '').strip()
        if value:
            return value
        return row.get('Layout pattern', '').strip()

    @staticmethod
    def _row_crop(row: Dict[str, str]) -> str:
        return row.get('Crop Policy', '').strip().lower()

    @staticmethod
    def _layout_projection_matches(left: str, right: str) -> bool:
        """Compare one Strategist recommendation without locking its wording."""
        from .checker import (
            re,
        )
        left_ids = re.findall(r'#([0-9]+)(?![0-9])', left)
        right_ids = re.findall(r'#([0-9]+)(?![0-9])', right)
        if left_ids or right_ids:
            return left_ids == right_ids

        def normalize(value: str) -> str:
            return re.sub(r'\s+', ' ', value.replace('`', '')).strip()

        return normalize(left) == normalize(right)

    def _load_project_lock_image_entries(
        self,
        project_path: Path,
    ) -> Tuple[Dict[str, List[Dict[str, str]]], str | None]:
        """Return parsed image-lock rows keyed by basename."""
        from .checker import (
            Path,
            _parse_spec_lock,
            _parse_spec_lock_image_value,
            defaultdict,
        )
        lock_path = project_path / 'spec_lock.md'
        if not lock_path.exists():
            return {}, f"{lock_path} does not exist"
        if _parse_spec_lock is None:
            return {}, "spec_lock parser is unavailable"
        if _parse_spec_lock_image_value is None:
            return {}, "spec_lock image parser is unavailable"
        try:
            lock = _parse_spec_lock(lock_path)
        except Exception as exc:
            return {}, f"cannot parse {lock_path}: {exc}"

        entries: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        legacy_metadata_keys = {
            'image_rendering',
            'image_rendering_references',
            'image_rendering_behavior',
        }
        errors: List[str] = []
        for key, value in lock.get('images', {}).items():
            if str(key).strip().lower() in legacy_metadata_keys:
                continue
            try:
                parsed = _parse_spec_lock_image_value(str(key), str(value))
            except ValueError as exc:
                errors.append(f"images row {key!r} {exc}")
                continue
            path_part = parsed['path']
            filename = Path(path_part).name
            if not filename:
                continue
            entries[filename].append({
                'key': str(key),
                'path': path_part,
                'source': parsed['source'],
                'pattern': parsed['pattern'],
                'crop': parsed['crop'],
                'legacy': parsed['legacy'],
            })
        error = (
            f"{lock_path}: " + "; ".join(errors)
            if errors
            else None
        )
        return dict(entries), error

    def _load_project_lock_images(self, project_path: Path) -> set[str]:
        """Return filenames listed under spec_lock.md images."""
        entries, _error = self._load_project_lock_image_entries(project_path)
        return set(entries)

    @classmethod
    def _load_project_svg_image_references(
        cls,
        project_path: Path,
    ) -> Tuple[
        Dict[Path, Dict[str, set[Path]]],
        Dict[Path, int],
        Dict[str, List[Tuple[Path, str, Tuple[str, ...]]]],
    ]:
        """Parse rendered image instances, paths, and crop mechanisms."""
        from .checker import (
            ET,
            XLINK_NS,
            _resolve_external_image_reference,
            defaultdict,
            discover_slide_svgs,
        )
        svg_dir = project_path / 'svg_output'
        if not svg_dir.exists():
            return {}, {}, {}
        out: Dict[Path, Dict[str, set[Path]]] = {}
        inline_counts: Dict[Path, int] = {}
        placements: Dict[
            str,
            List[Tuple[
                Path,
                str,
                Tuple[str, ...],
                Tuple[float, float, float, float] | None,
            ]],
        ] = defaultdict(list)
        for svg_path in discover_slide_svgs(svg_dir):
            try:
                root = ET.parse(svg_path).getroot()
            except (OSError, ET.ParseError):
                continue
            working_root, parent_by_id, images = cls._visible_image_elements(root)
            references: Dict[str, set[Path]] = defaultdict(set)
            inline_count = 0
            for element in images:
                href = (
                    element.get('href')
                    or element.get(f'{{{XLINK_NS}}}href')
                    or ''
                )
                if href.lstrip().lower().startswith('data:'):
                    inline_count += 1
                    continue
                filename = cls._external_image_reference_basename(href)
                if not filename:
                    continue
                references.setdefault(filename, set())
                placements[filename].append((
                    svg_path,
                    element.get('preserveAspectRatio') or '',
                    cls._image_crop_mechanisms(
                        element,
                        working_root,
                        parent_by_id,
                    ),
                    cls._image_frame_geometry(
                        element,
                        working_root,
                        parent_by_id,
                    ),
                ))
                if _resolve_external_image_reference is not None:
                    resolved = _resolve_external_image_reference(
                        svg_path.parent,
                        href,
                    )
                    if resolved is not None:
                        references[filename].add(resolved.resolve())
            # A text picture fill keeps its <image> inside <defs><pattern
            # data-pptx-text-image-fill>; the glyphs that reference the
            # pattern render that file, so it counts as a placement.
            for element in cls._text_image_fill_images(working_root):
                href = (
                    element.get('href')
                    or element.get(f'{{{XLINK_NS}}}href')
                    or ''
                )
                if href.lstrip().lower().startswith('data:'):
                    inline_count += 1
                    continue
                filename = cls._external_image_reference_basename(href)
                if not filename:
                    continue
                references.setdefault(filename, set())
                placements[filename].append((
                    svg_path,
                    element.get('preserveAspectRatio') or '',
                    ('text picture fill',),
                    None,
                ))
                if _resolve_external_image_reference is not None:
                    resolved = _resolve_external_image_reference(
                        svg_path.parent,
                        href,
                    )
                    if resolved is not None:
                        references[filename].add(resolved.resolve())
            out[svg_path] = dict(references)
            if inline_count:
                inline_counts[svg_path] = inline_count
        return out, inline_counts, dict(placements)

    @classmethod
    def _text_image_fill_images(cls, root: ET.Element) -> List[ET.Element]:
        """Return <image> children of text picture-fill patterns in use."""
        from .checker import (
            SVG_NS,
            _local_name,
            _parse_inline_style,
            re,
        )
        patterns = {
            pattern.get('id'): pattern
            for pattern in root.iter(f'{{{SVG_NS}}}pattern')
            if pattern.get('data-pptx-text-image-fill') and pattern.get('id')
        }
        if not patterns:
            return []
        used: set[str] = set()
        for element in root.iter():
            if _local_name(element) not in {'text', 'tspan'}:
                continue
            style_values = (
                _parse_inline_style(element.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            fill = style_values.get('fill') or element.get('fill') or ''
            match = re.match(r'\s*url\(\s*[\'"]?#([^)\'"\s]+)', fill)
            if match and match.group(1) in patterns:
                used.add(match.group(1))
        return [
            image
            for pattern_id in used
            for image in patterns[pattern_id].iter(f'{{{SVG_NS}}}image')
        ]

    @staticmethod
    def _image_frame_geometry(
        image: ET.Element,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[float, float, float, float] | None:
        """Return (frame width, frame height, source fraction w, h) of one instance.

        Inside the nested-``<svg>`` crop transport the frame is the wrapper and
        the ``viewBox`` selects a unit-coordinate fraction of the source; a
        plain ``<image>`` shows the whole source in its own box.
        """
        from .checker import (
            _local_name,
            math,
            re,
        )
        def _number(raw: str | None) -> float | None:
            if raw is None:
                return None
            try:
                value = float(raw.strip().removesuffix('px'))
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) and value > 0 else None

        parent = parent_by_id.get(id(image))
        if (
            parent is not None
            and parent is not root
            and _local_name(parent) == 'svg'
            and parent.get('viewBox')
        ):
            parts = [
                part for part in re.split(r'[\s,]+', parent.get('viewBox', '').strip())
                if part
            ]
            if len(parts) != 4:
                return None
            width = _number(parent.get('width'))
            height = _number(parent.get('height'))
            fraction_width = _number(parts[2])
            fraction_height = _number(parts[3])
            if None in (width, height, fraction_width, fraction_height):
                return None
            return (width, height, fraction_width, fraction_height)
        width = _number(image.get('width'))
        height = _number(image.get('height'))
        if width is None or height is None:
            return None
        return (width, height, 1.0, 1.0)

    def _measure_image_pixels(
        self,
        paths: set[Path] | None,
    ) -> Tuple[int, int] | None:
        """Return the EXIF-oriented pixel size of the first readable file."""
        for path in sorted(paths or ()):
            cached = self._image_pixel_sizes.get(path)
            if cached is not None:
                return cached
            try:
                from PIL import Image, ImageOps  # type: ignore
                with Image.open(path) as image:
                    oriented = ImageOps.exif_transpose(image)
                    size = (int(oriented.width), int(oriented.height))
            except Exception:  # noqa: BLE001 - unreadable files are reported elsewhere
                continue
            if size[0] > 0 and size[1] > 0:
                self._image_pixel_sizes[path] = size
                return size
        return None

    @staticmethod
    def _stretch_deviation(
        geometry: Tuple[float, float, float, float] | None,
        source_size: Tuple[int, int] | None,
    ) -> float | None:
        """Return how far a ``none`` placement departs from the source aspect."""
        if geometry is None or source_size is None:
            return None
        frame_width, frame_height, fraction_width, fraction_height = geometry
        expected = (fraction_width * source_size[0]) / (fraction_height * source_size[1])
        if expected <= 0:
            return None
        return abs((frame_width / frame_height) / expected - 1.0)

    @staticmethod
    def _image_crop_mechanisms(
        image: ET.Element,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[str, ...]:
        """Return objective clipping mechanisms affecting one image instance."""
        from .checker import (
            _local_name,
            _parse_inline_style,
        )
        mechanisms: List[str] = []
        current: ET.Element | None = image
        while current is not None:
            tag = _local_name(current)
            style_values = (
                _parse_inline_style(current.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            for property_name in ('clip-path', 'mask'):
                value = style_values.get(property_name)
                if value is None:
                    value = current.get(property_name)
                if value and value.strip().lower() != 'none':
                    mechanisms.append(f"<{tag}> {property_name}")
            overflow = style_values.get('overflow')
            if overflow is None:
                overflow = current.get('overflow')
            if overflow and overflow.strip().lower() in {'hidden', 'clip'}:
                mechanisms.append(f"<{tag}> overflow={overflow.strip()!r}")
            if current is not root and tag == 'svg':
                mechanisms.append('nested <svg> viewport')
            current = parent_by_id.get(id(current))
        return tuple(dict.fromkeys(mechanisms))

