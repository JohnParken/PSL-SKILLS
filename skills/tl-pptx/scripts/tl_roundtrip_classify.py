"""Upstream MIT roundtrip classification helpers; CLI functions preserved verbatim."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from authoring_roundtrip import RoundtripPage
from pptx_workspace import load_roundtrip_manifest, slide_animation_config_sha256, source_pptx_path
from svg_to_pptx.pptx_package.builder import RoundtripSlidePatch
from svg_to_pptx.animation_config import resolve_morph_pairs, resolve_slide_animation_config

def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _roundtrip_animation_config_for_pages(
    config: dict[str, object],
    pages: tuple[RoundtripPage, ...],
) -> dict[str, object]:
    """Key per-slide motion by output SVG stem, inheriting source rows for copies."""
    expanded = copy.deepcopy(config)
    raw_slides = expanded.get("slides")
    if not isinstance(raw_slides, dict):
        raise RuntimeError("Round-trip animation sidecar slides must be an object")
    output_slides: dict[str, object] = {}
    for page_index, page in enumerate(pages):
        output_stem = page.svg_stem
        source_stem = Path(page.source_svg_name).stem
        if output_stem in raw_slides:
            selected = copy.deepcopy(raw_slides[output_stem])
            row_origin = "declares"
        elif source_stem in raw_slides:
            selected = copy.deepcopy(raw_slides[source_stem])
            row_origin = "inherits"
        else:
            continue
        if isinstance(selected, dict):
            morph = selected.get("morph")
            if isinstance(morph, dict):
                original_from = morph.get("from")
                previous_page = pages[page_index - 1] if page_index else None
                previous_source_stem = (
                    Path(previous_page.source_svg_name).stem
                    if previous_page is not None
                    else None
                )
                if (
                    isinstance(original_from, str)
                    and original_from.strip()
                    and previous_page is not None
                    and original_from == previous_page.svg_stem
                ):
                    pass
                elif (
                    isinstance(original_from, str)
                    and original_from.strip()
                    and previous_page is not None
                    and original_from == previous_source_stem
                ):
                    morph["from"] = previous_page.svg_stem
                elif isinstance(original_from, str) and original_from.strip():
                    previous_label = (
                        f'"{previous_page.svg_stem}" from '
                        f'"{previous_source_stem}"'
                        if previous_page is not None
                        else "no previous output page"
                    )
                    raise RuntimeError(
                        f'Round-trip output page "{output_stem}" {row_origin} '
                        f'Morph from "{original_from}", but it follows '
                        f'{previous_label}; add an explicit animations.json '
                        f'row for "{output_stem}" that overrides morph.from '
                        'for this output adjacency, or remove that Morph row'
                    )
        output_slides[output_stem] = selected
    expanded["slides"] = output_slides
    return expanded


def _roundtrip_note_changed(
    project_path: Path,
    row: dict[str, object],
    page: RoundtripPage,
) -> bool:
    """Return whether one output page changes its inherited source notes."""
    relative = Path("notes") / f"{page.svg_stem}.md"
    output_note = project_path / relative
    if page.svg_name != page.source_svg_name:
        return output_note.is_file()

    note = row.get("notes")
    if note is None:
        return output_note.is_file()
    if not isinstance(note, dict):
        raise RuntimeError(
            f"Round-trip manifest slide {page.source_slide} notes metadata "
            "must be an object"
        )
    note_file = note.get("file")
    if not isinstance(note_file, str) or not note_file:
        raise RuntimeError(
            f"Round-trip manifest slide {page.source_slide} notes metadata "
            "must declare notes.file"
        )
    expected_file = relative.as_posix()
    if note_file != expected_file:
        raise RuntimeError(
            f"Round-trip manifest slide {page.source_slide} notes.file must be "
            f"{expected_file!r} for {page.svg_name}; got {note_file!r}"
        )
    expected_sha = note.get("sha256")
    if not isinstance(expected_sha, str):
        raise RuntimeError(
            f"Round-trip manifest slide {page.source_slide} notes metadata "
            "must declare notes.sha256"
        )
    return not output_note.is_file() or _path_sha256(output_note) != expected_sha


def _roundtrip_passthrough_candidates(
    project_path: Path,
    native_files: list[Path],
    pages: tuple[RoundtripPage, ...],
    *,
    source_dir: str,
    authoring_report: dict[str, object] | None,
    changed_resource_pages: frozenset[int],
) -> set[int]:
    """Return source slides whose visual and editable sidecars are unchanged."""
    manifest = load_roundtrip_manifest(project_path)
    if manifest is None or manifest.get('schema') != 'ppt-master.roundtrip-workspace.v1':
        raise RuntimeError(
            "Round-trip export requires analysis/roundtrip_manifest.json "
            "with schema ppt-master.roundtrip-workspace.v1"
        )
    raw_slides = manifest.get('slides')
    if not isinstance(raw_slides, list):
        return set()
    slide_rows = {
        int(row['index']): row
        for row in raw_slides
        if isinstance(row, dict) and isinstance(row.get('index'), int)
    }
    if len(pages) != len(native_files):
        raise RuntimeError(
            "Round-trip output page roster differs from materialized SVG files"
        )

    candidates: set[int] = set()
    if authoring_report is not None:
        raw_documents = authoring_report.get('documents')
        if not isinstance(raw_documents, list):
            return set()
        reports = {
            str(row.get('file')): row
            for row in raw_documents
            if isinstance(row, dict) and isinstance(row.get('file'), str)
        }
        for page in pages:
            if page.source_slide not in slide_rows:
                raise RuntimeError(
                    f"Round-trip manifest has no source slide {page.source_slide}"
                )
            report = reports.get(page.svg_name)
            if not isinstance(report, dict):
                continue
            if (
                report.get('document_unchanged') is True
                and
                isinstance(report.get('source_ref_count'), int)
                and report.get('source_ref_count') == report.get('unchanged_refs')
                and int(report.get('edited_refs') or 0) == 0
                and int(report.get('deleted_refs') or 0) == 0
                and int(report.get('authored_unreferenced') or 0) == 0
                and report.get('defs_changed') is False
            ):
                candidates.add(page.output_index)
    elif source_dir == 'svg':
        files_by_name = {path.name: path for path in native_files}
        for page in pages:
            row = slide_rows[page.source_slide]
            relative = row.get('layeredSvg')
            expected = row.get('layeredSvgSha256')
            if not isinstance(relative, str) or not isinstance(expected, str):
                continue
            path = files_by_name.get(Path(relative).name)
            if path is not None and _path_sha256(path) == expected:
                candidates.add(page.output_index)
    else:
        return set()

    sidecars = manifest.get('sidecars')
    animation = sidecars.get('animations') if isinstance(sidecars, dict) else None
    if not isinstance(animation, dict):
        return set()
    animation_file = animation.get('file')
    if not isinstance(animation_file, str):
        raise RuntimeError(
            "Round-trip manifest must declare sidecars.animations.file"
        )
    current_animation = project_path / animation_file
    if not current_animation.is_file():
        return set()
    try:
        current_animation_config = json.loads(
            current_animation.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot read round-trip animation sidecar {current_animation}: {exc}"
        ) from exc
    if not isinstance(current_animation_config, dict):
        raise RuntimeError(
            f"Round-trip animation sidecar must be a JSON object: {current_animation}"
        )
    expanded_animation_config = _roundtrip_animation_config_for_pages(
        current_animation_config,
        pages,
    )
    # Explicit identity edits also touch the source endpoint, whose own motion
    # row may still match the import baseline. Classify both ends as patches.
    morph_slides = {
        stem
        for pair in resolve_morph_pairs(
            [page.svg_stem for page in pages], expanded_animation_config,
        )
        for stem in (pair.source_slide, pair.destination_slide)
    }
    candidates.difference_update(
        page.output_index for page in pages if page.svg_stem in morph_slides
    )
    pages_by_output = {page.output_index: page for page in pages}
    for index in tuple(candidates):
        page = pages_by_output[index]
        expected = slide_rows[page.source_slide].get("animationSha256")
        if not isinstance(expected, str):
            raise RuntimeError(
                f"Round-trip manifest slide {page.source_slide} lacks "
                "animationSha256"
            )
        actual = slide_animation_config_sha256(
            expanded_animation_config,
            page.svg_stem,
        )
        if actual != expected:
            candidates.discard(index)

    for index in tuple(candidates):
        page = pages_by_output[index]
        row = slide_rows[page.source_slide]
        if _roundtrip_note_changed(project_path, row, page):
            candidates.discard(index)
        derived = row.get("derivedResources")
        if not isinstance(derived, list):
            raise RuntimeError(
                f"Round-trip manifest slide {page.source_slide} lacks "
                "derivedResources"
            )
        for resource_index, raw in enumerate(derived):
            if not isinstance(raw, dict):
                raise RuntimeError(
                    f"Round-trip slide {index} derivedResources[{resource_index}] "
                    "must be an object"
                )
            relative = raw.get("file")
            expected_sha = raw.get("sha256")
            if not isinstance(relative, str) or not isinstance(expected_sha, str):
                raise RuntimeError(
                    f"Round-trip slide {index} derived resource is incomplete"
                )
            path = (project_path / relative).resolve()
            try:
                path.relative_to(project_path.resolve())
            except ValueError as exc:
                raise RuntimeError(
                    f"Round-trip derived resource escapes the project: {relative}"
                ) from exc
            if not path.is_file() or _path_sha256(path) != expected_sha:
                candidates.discard(index)
                break
    candidates.difference_update(changed_resource_pages)
    return candidates


def _roundtrip_slide_patches(
    project_path: Path,
    authoring_report: dict[str, object] | None,
    passthrough_slides: set[int],
    pages: tuple[RoundtripPage, ...],
    *,
    changed_resource_pages: frozenset[int],
    force_visual_changed: bool,
    force_motion_changed: bool,
    force_transition_changed: bool,
    force_transition_replaced: bool,
    force_animation_changed: bool,
    force_notes_changed: bool,
    force_advance_changed: bool = False,
) -> dict[int, RoundtripSlidePatch]:
    """Build strict source-overlay metadata for edited authoring slides."""
    if authoring_report is None:
        return {}
    manifest = load_roundtrip_manifest(project_path)
    if manifest is None:
        raise RuntimeError("Round-trip manifest is missing")
    raw_slides = manifest.get("slides")
    if not isinstance(raw_slides, list):
        raise RuntimeError("Round-trip manifest slides must be an array")
    slide_rows = {
        int(row["index"]): row
        for row in raw_slides
        if isinstance(row, dict) and isinstance(row.get("index"), int)
    }
    sidecars = manifest.get("sidecars")
    animation = sidecars.get("animations") if isinstance(sidecars, dict) else None
    animation_file = animation.get("file") if isinstance(animation, dict) else None
    if not isinstance(animation_file, str):
        raise RuntimeError(
            "Round-trip manifest must declare sidecars.animations.file"
        )
    try:
        animation_config = json.loads(
            (project_path / animation_file).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot read round-trip animation sidecar: {exc}"
        ) from exc
    if not isinstance(animation_config, dict):
        raise RuntimeError("Round-trip animation sidecar must be a JSON object")
    animation_config = _roundtrip_animation_config_for_pages(
        animation_config,
        pages,
    )
    baseline = animation.get("baseline")
    if not isinstance(baseline, dict):
        # Older workspaces stored only a combined hash. Recover their import
        # projection in memory from the immutable source, without publishing it.
        from pptx_to_svg.converter import ConvertOptions, convert_pptx_to_svg

        baseline = convert_pptx_to_svg(
            source_pptx_path(project_path),
            options=ConvertOptions(inheritance_mode="both", roundtrip=True),
        ).animation_config
    animation_defaults = _as_dict(_as_dict(animation_config.get("defaults")).get("animation"))
    baseline_defaults = _as_dict(_as_dict(baseline.get("defaults")).get("animation"))
    defaults_transition = _as_dict(_as_dict(animation_config.get("defaults")).get("transition"))
    baseline_defaults_transition = _as_dict(_as_dict(baseline.get("defaults")).get("transition"))

    documents = authoring_report.get("documents")
    if not isinstance(documents, list):
        raise RuntimeError("Authoring round-trip report documents must be an array")
    pages_by_file = {page.svg_name: page for page in pages}
    if len(documents) != len(pages):
        raise RuntimeError(
            "Authoring round-trip report differs from the output page plan"
        )
    patches: dict[int, RoundtripSlidePatch] = {}
    for raw in documents:
        if not isinstance(raw, dict) or not isinstance(raw.get("file"), str):
            raise RuntimeError("Authoring round-trip document report is incomplete")
        page = pages_by_file.get(str(raw["file"]))
        if page is None:
            raise RuntimeError(
                f"Unexpected authoring round-trip slide name: {raw['file']}"
            )
        if raw.get("source_slide") != page.source_slide:
            raise RuntimeError(
                f"Authoring round-trip source mapping changed for {page.svg_name}"
            )
        index = page.output_index
        if index in passthrough_slides:
            continue
        row = slide_rows.get(page.source_slide)
        if row is None:
            raise RuntimeError(
                f"Round-trip manifest has no source slide {page.source_slide} "
                "for authoring overlay"
            )
        slide_config = _as_dict(
            _as_dict(animation_config.get("slides")).get(page.svg_stem)
        )
        baseline_slide = _as_dict(
            _as_dict(baseline.get("slides")).get(Path(page.source_svg_name).stem)
        )
        slide_transition_config = _as_dict(slide_config.get("transition"))
        baseline_transition = _as_dict(baseline_slide.get("transition"))
        def _transition_key_changed(key: str) -> bool:
            # A slide row wins; otherwise a user-edited default (different from
            # the import baseline default) is a deck-wide request.
            if key in slide_transition_config:
                return slide_transition_config[key] != baseline_transition.get(key)
            return (
                key in defaults_transition
                and defaults_transition[key] != baseline_defaults_transition.get(key)
            )

        transition_replaced = force_transition_replaced or any(
            _transition_key_changed(key)
            for key in ("effect", "effect_options", "duration", "sound")
        )
        advance_changed = force_advance_changed or _transition_key_changed("auto_advance")
        transition_changed = force_transition_changed or transition_replaced or advance_changed
        animation_changed = force_animation_changed or (
            (
                ("animation" in slide_config or animation_defaults != baseline_defaults)
                and resolve_slide_animation_config(animation_defaults, _as_dict(slide_config.get("animation")))
                != resolve_slide_animation_config(baseline_defaults, _as_dict(baseline_slide.get("animation")))
            )
            or (
                "groups" in slide_config
                and _as_dict(slide_config.get("groups")) != _as_dict(baseline_slide.get("groups"))
            )
        )
        motion_changed = (
            force_motion_changed
            or transition_changed
            or animation_changed
        )

        def _ref_set(field: str) -> frozenset[str]:
            value = raw.get(field)
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                raise RuntimeError(
                    f"Authoring round-trip slide {index} has invalid {field}"
                )
            return frozenset(value)

        authoring_visual_changed = (
            force_visual_changed
            or int(raw.get("edited_refs") or 0) > 0
            or int(raw.get("deleted_refs") or 0) > 0
            or int(raw.get("authored_unreferenced") or 0) > 0
            or raw.get("defs_changed") is True
        )
        patches[index] = RoundtripSlidePatch(
            warnings=raw.setdefault("warnings", []),
            source_ref_ids=_ref_set("source_ref_ids"),
            edited_ref_ids=_ref_set("edited_ref_ids"),
            deleted_ref_ids=_ref_set("deleted_ref_ids"),
            visual_changed=(
                authoring_visual_changed
                or index in changed_resource_pages
            ),
            authoring_visual_changed=authoring_visual_changed,
            motion_changed=motion_changed,
            transition_changed=transition_changed,
            transition_replaced=transition_replaced,
            animation_changed=animation_changed,
            advance_changed=advance_changed,
            notes_changed=(
                force_notes_changed
                or _roundtrip_note_changed(project_path, row, page)
            ),
        )
    return patches


