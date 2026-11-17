"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class AttributionQualityMixin:
    """Upstream attribution checker methods."""

    def _find_image_sources_manifest(self, svg_path: Path) -> Path | None:
        """Locate image_sources.json for a project SVG.

        Quality checks run primarily on <project>/svg_output/*.svg, but this
        also supports SVGs checked from project root or svg_final.
        """
        bases = (svg_path.parent, svg_path.parent.parent, svg_path.parent.parent.parent)
        for base in bases:
            candidate = base / 'images' / 'image_sources.json'
            if candidate.exists():
                return candidate
        return None

    def _load_image_sources_manifest(
        self,
        svg_path: Path,
    ) -> Tuple[Dict, str | None, Path | None]:
        manifest_path = self._find_image_sources_manifest(svg_path)
        if manifest_path is None:
            return {}, None, None
        payload, error = self._read_image_sources_manifest(manifest_path)
        return payload, error, manifest_path

    def _read_image_sources_manifest(
        self,
        manifest_path: Path,
    ) -> Tuple[Dict, str | None]:
        """Read one provenance manifest without accepting damaged state."""
        from .checker import (
            Path,
            json,
        )
        if manifest_path in self._source_manifest_cache:
            return self._source_manifest_cache[manifest_path]
        try:
            payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            payload = {}
            error = f"cannot read {manifest_path}: {exc}"
        else:
            if not isinstance(payload, dict):
                error = f"{manifest_path} must contain a JSON object"
                payload = {}
            elif not isinstance(payload.get('items'), list):
                error = f"{manifest_path} must contain an items array"
                payload = {}
            elif any(not isinstance(item, dict) for item in payload['items']):
                error = f"{manifest_path} items must contain JSON objects"
                payload = {}
            else:
                seen_filenames: set[str] = set()
                error = None
                for index, item in enumerate(payload['items']):
                    filename = item.get('filename')
                    if (
                        not isinstance(filename, str)
                        or not filename.strip()
                        or filename in {'.', '..'}
                        or '/' in filename
                        or '\\' in filename
                        or ':' in filename
                        or Path(filename).is_absolute()
                    ):
                        error = (
                            f"{manifest_path} items[{index}].filename must be "
                            "a non-empty bare filename"
                        )
                        break
                    if filename in seen_filenames:
                        error = (
                            f"{manifest_path} contains duplicate filename "
                            f"{filename!r}"
                        )
                        break
                    seen_filenames.add(filename)
                if error:
                    payload = {}
        self._source_manifest_cache[manifest_path] = (payload, error)
        return payload, error

    @staticmethod
    def _external_image_reference_basename(href: str) -> str | None:
        """Return a decoded basename for one local external image href."""
        from .checker import (
            Path,
            html,
            unquote,
            urlsplit,
        )
        if not href or href.startswith('data:'):
            return None
        decoded_href = html.unescape(href)
        parsed = urlsplit(decoded_href)
        if parsed.scheme and parsed.scheme != 'file':
            return None
        path_part = (
            parsed.path
            if parsed.scheme
            else decoded_href.split('?', 1)[0].split('#', 1)[0]
        )
        return Path(unquote(path_part)).name or None

    @classmethod
    def _referenced_image_basenames(cls, root: ET.Element) -> set[str]:
        """Return external image basenames rendered by one parsed SVG."""
        from .checker import (
            XLINK_NS,
        )
        filenames = set()
        _working_root, _parent_by_id, images = cls._visible_image_elements(root)
        for elem in images:
            href = elem.get('href') or elem.get(f'{{{XLINK_NS}}}href')
            filename = cls._external_image_reference_basename(href or '')
            if filename:
                filenames.add(filename)
        return filenames

    def _check_sourced_image_attribution(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ):
        """Require visible credit text for attribution-required web images.

        image_search.py records the legal tier in images/image_sources.json;
        Executor must render compact credit text into the SVG. This check
        binds each credit to the referenced image's author and license instead
        of accepting one generic deck-level CC token.
        """
        manifest, error, manifest_path = self._load_image_sources_manifest(svg_path)
        if error:
            if (
                manifest_path is not None
                and manifest_path not in self._source_manifest_errors_reported
            ):
                result['errors'].append(
                    f"Invalid image source manifest: {error}"
                )
                self._source_manifest_errors_reported.add(manifest_path)
            return

        items = manifest.get('items') or []
        if not items:
            return

        credit_blocks = self._visible_svg_text_blocks(root)
        referenced_filenames = self._referenced_image_basenames(root)

        for item in items:
            if not item.get('attribution_required') and item.get('license_tier') != 'attribution-required':
                continue

            filename = str(item.get('filename') or '')
            if not filename or filename not in referenced_filenames:
                continue

            license_name = str(item.get('license_name') or '').upper()
            # Only a Creative Commons licence has a token the credit must
            # repeat; a publisher's own source-credit terms bind the credit to
            # the author/source name alone.
            if 'CC' in license_name or 'CREATIVE COMMONS' in license_name:
                license_token = 'CC BY-SA' if 'BY-SA' in license_name else 'CC BY'
            else:
                license_token = None
            author = str(item.get('author') or '').strip()
            has_credit = bool(author) and any(
                author.casefold() in block.casefold()
                and (license_token is None or license_token in block.upper())
                for block in credit_blocks
            )
            if not has_credit:
                expected = f"{author or 'unknown author'}"
                if license_token:
                    expected += f"; {license_token}"
                result['errors'].append(
                    f"Missing image-specific inline attribution for sourced "
                    f"image {filename} ({expected}). Add compact author + "
                    f"license credit per references/image-searcher.md §7."
                )

    @classmethod
    def _visible_svg_text_blocks(cls, root: ET.Element) -> List[str]:
        """Return rendered text blocks, excluding hidden/non-visual content."""
        from .checker import (
            SVG_NS,
            _UseExpansionError,
            _expand_local_use_references,
            copy,
            re,
        )
        working_root = copy.deepcopy(root)
        if (
            _expand_local_use_references is not None
            and _UseExpansionError is not None
        ):
            try:
                _expand_local_use_references(working_root)
            except _UseExpansionError:
                working_root = copy.deepcopy(root)
        parent_by_id = {
            id(child): parent
            for parent in working_root.iter()
            for child in list(parent)
        }

        blocks: List[str] = []
        for element in working_root.iter(f'{{{SVG_NS}}}text'):
            if (
                cls._is_hidden_element(element, parent_by_id)
                or cls._has_non_visual_ancestor(
                    element,
                    working_root,
                    parent_by_id,
                )
                or cls._has_zero_opacity(element, parent_by_id)
            ):
                continue
            text = re.sub(r'\s+', ' ', ' '.join(element.itertext())).strip()
            if text:
                blocks.append(text)
        return blocks

    @staticmethod
    def _normalize_size(value: str) -> str:
        """Normalize a font-size value for drift comparison.

        Unit-bearing SVG values are reported as errors before drift checking.
        The legacy `px` strip remains to avoid a duplicate drift warning after
        the hard error has already identified the unit problem.
        """
        v = value.strip().lower()
        if v.endswith('px'):
            v = v[:-2].strip()
        return v

    @staticmethod
    def _normalize_font_stack(stack: str) -> str:
        """Normalize a font-family stack for comparison: split on commas, strip
        quotes / whitespace, lowercase, rejoin. Collapses cosmetic differences
        (comma spacing, single vs double quotes, case) so that
        `Consolas,'Courier New',monospace` matches `Consolas, "Courier New", monospace`."""
        parts = [p.strip().strip('"\'').lower() for p in stack.split(',')]
        return ','.join(p for p in parts if p)

    def _categorize_issue(self, error_msg: str) -> str:
        """Categorize issue type"""
        if 'Invalid XML' in error_msg:
            return 'XML well-formedness'
        elif 'viewBox' in error_msg:
            return 'viewBox issues'
        elif 'foreignObject' in error_msg:
            return 'foreignObject'
        elif 'paint' in error_msg.lower() or 'color value' in error_msg.lower():
            return 'Paint issues'
        elif 'font' in error_msg.lower():
            return 'Font issues'
        else:
            return 'Other'

    def _configure_prototype_context(
        self,
        target_path: Path,
        svg_files: List[Path],
    ) -> None:
        """Map generated pages to selected prototypes for inherited diagnostics."""
        from .checker import (
            _TemplateStructureError,
            _load_pptx_structure_lock,
            discover_slide_svgs,
            json,
        )
        self._prototype_by_output = {}
        self._active_prototype_path = None
        self._active_template_reuse_scope = None
        self._source_import_summary = {
            'warning_count': 0,
            'by_code': {},
        }
        if (
            self.template_mode
            or self.quick_generate
            or _load_pptx_structure_lock is None
        ):
            return
        project_path = self._resolve_project_path(target_path)
        try:
            structure_lock = _load_pptx_structure_lock(project_path)
        except (_TemplateStructureError, OSError):
            # The project-level structure gate reports the actionable parser
            # error. Inherited classification is optional and stays silent.
            return
        if structure_lock is None:
            return
        self._active_template_reuse_scope = getattr(
            structure_lock,
            'template_reuse_scope',
            None,
        )
        references = {
            reference.slide_num: reference.svg_path
            for reference in structure_lock.prototypes
        }
        if target_path.is_file():
            sibling_files = discover_slide_svgs(target_path.parent)
            resolved_target = target_path.resolve()
            slide_num = next(
                (
                    index
                    for index, sibling in enumerate(sibling_files, start=1)
                    if sibling.resolve() == resolved_target
                ),
                1,
            )
            prototype = references.get(slide_num)
            if prototype is not None:
                self._prototype_by_output[resolved_target] = prototype.resolve()
        else:
            for slide_num, svg_path in enumerate(svg_files, start=1):
                prototype = references.get(slide_num)
                if prototype is not None:
                    self._prototype_by_output[svg_path.resolve()] = prototype.resolve()

        if self._active_template_reuse_scope not in {'mirror', 'layout'}:
            return
        manifest_path = (
            project_path / 'templates' / 'template_execution_manifest.json'
        )
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if manifest.get('schema') != 'ppt-master.template-execution-manifest.v1':
            return
        source_import = manifest.get('source_import')
        if isinstance(source_import, dict):
            self._source_import_summary = source_import

