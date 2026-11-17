"""Core SVG -> DrawingML dispatcher, group handling, and main entry point."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from native_payloads import NativePayloadError, hydrate_native_payload_refs
from hyperlink_contract import (
    project_hyperlink_errors,
)
from pptx_effects import (
    project_effect_status_errors,
)

from .context import (
    ConvertContext,
)
from .paths import (
    project_freeform_geometry_errors,
    project_gradient_geometry_errors,
)
from .text_properties import (
    project_text_property_errors,
)
from .utils import (
    SVG_NS,
    project_definition_errors,
    project_filter_errors,
    project_gradient_errors,
    project_image_aspect_ratio_errors,
    project_mask_errors,
    project_marker_errors,
    project_opacity_errors,
    project_paint_errors,
    project_paint_reference_errors,
    project_stroke_style_errors,
    project_transform_errors,
)
from .elements import (
    project_clip_path_errors,
    project_image_errors,
    project_nested_svg_crop_errors,
)
from ..native_objects import (
    INLINE_FORMULA_ATTR,
    NativeMarkerAttributeError,
    inline_formula_marker_errors,
    native_metadata_payload_matches,
    native_replacement_kind,
)
from ..native_objects.marker_status import native_marker_status_errors



# Adapted from converter.py; upstream MIT, Hugo He, 2025-2026.
def _hydrate_native_payloads(root: ET.Element, svg_path: Path) -> int:
    """Resolve compressed workspace payload references for native conversion."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    try:
        return hydrate_native_payload_refs(root, svg_path)
    except NativePayloadError as exc:
        raise SvgNativeConversionError(
            f"{svg_path.name}: invalid native payload reference: {exc}"
        ) from exc


def _require_native_marker_attributes(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject contradictory native marker aliases before conversion."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors: list[str] = []
    for elem in root.iter():
        if elem.tag.rsplit('}', 1)[-1] == 'metadata':
            continue
        marker_errors = native_marker_status_errors(elem)
        if marker_errors:
            marker_id = elem.get('id') or elem.get('data-name') or '<unnamed>'
            errors.extend(f'{marker_id}: {error}' for error in marker_errors)
            continue
        marker_id = elem.get('id') or elem.get('data-name') or '<unnamed>'
        kind = native_replacement_kind(elem)
        if not kind:
            continue
        for child in elem:
            if child.tag.rsplit('}', 1)[-1] != 'metadata':
                continue
            try:
                native_metadata_payload_matches(child, kind)
            except NativeMarkerAttributeError as exc:
                errors.append(f'{marker_id}: {exc}')
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid native replacement metadata: '
        f'{preview}{suffix}'
    )


def _require_inline_formula_markers(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject malformed inline formula runs before text lowering."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = inline_formula_marker_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid {INLINE_FORMULA_ATTR} marker(s): '
        f'{preview}{suffix}'
    )


def _require_project_hyperlinks(
    root: ET.Element,
    svg_path: Path | str,
    *,
    slide_count: int | None,
) -> None:
    """Reject hyperlinks that cannot be represented faithfully in PPTX."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_hyperlink_errors(root, slide_count=slide_count)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid SVG hyperlink(s): {preview}{suffix}'
    )


def _native_replacement_enabled(elem: ET.Element, ctx: ConvertContext) -> bool:
    """Return whether this marker is active under the current export policy."""
    kind = native_replacement_kind(elem)
    if kind == 'formula':
        return True
    return ctx.native_objects_enabled and kind in {'chart', 'table'}


def _contains_enabled_native_replacement(
    elem: ET.Element,
    ctx: ConvertContext,
) -> bool:
    """Return whether one subtree contains an active native replacement."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        _native_replacement_enabled,
    )
    return any(
        descendant.tag.replace(f'{{{SVG_NS}}}', '') != 'metadata'
        and _native_replacement_enabled(descendant, ctx)
        for descendant in elem.iter()
    )


def _require_project_freeform_geometry(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject malformed path and points values with one aggregated error."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_freeform_geometry_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project freeform geometry: '
        f'{preview}{suffix}'
    )


def _require_project_nested_svg_crops(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject nested SVG outside the imported picture-crop transport."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_nested_svg_crop_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid nested SVG crop wrapper(s): '
        f'{preview}{suffix}'
    )


def _require_project_clip_paths(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject clip references that cannot produce native picture geometry."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_clip_path_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project clip-path(s): '
        f'{preview}{suffix}'
    )


def _require_project_images(
    root: ET.Element,
    svg_path: Path | str,
    resource_root: Path | None = None,
) -> None:
    """Reject invalid picture frames and unresolved or corrupt sources."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    path = Path(svg_path)
    errors = project_image_errors(
        root,
        path.parent,
        resource_root=resource_root,
    )
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{path.name}: invalid project image(s): {preview}{suffix}'
    )


def _require_project_transforms(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject invalid project transform syntax and mappings before conversion."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_transform_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project transform(s): '
        f'{preview}{suffix}'
    )


def _require_project_stroke_styles(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject invalid project line-style syntax and mappings before conversion."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_stroke_style_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project line style(s): '
        f'{preview}{suffix}'
    )


def _require_project_image_aspect_ratios(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject ambiguous image fit/crop values before native conversion."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_image_aspect_ratio_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project image aspect ratio(s): '
        f'{preview}{suffix}'
    )


def _require_project_opacities(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject malformed opacity values before native conversion."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_opacity_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project opacity value(s): '
        f'{preview}{suffix}'
    )


def _require_project_paints(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject invalid paint values before native conversion."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_paint_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project paint value(s): '
        f'{preview}{suffix}'
    )


def _require_project_masks(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject SVG masks before native conversion can silently drop them."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_mask_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project mask(s): '
        f'{preview}{suffix}'
    )


def _require_project_definitions(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject definitions outside the direct, unique local-ref contract."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_definition_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project definition(s): '
        f'{preview}{suffix}'
    )


def _require_project_paint_references(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject unresolved or context-invalid local paint references."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_paint_reference_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project paint reference(s): '
        f'{preview}{suffix}'
    )


def _require_project_line_end_markers(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject markers outside the native line-end contract."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_marker_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project line-end marker(s): '
        f'{preview}{suffix}'
    )


def _require_project_gradients(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject gradients outside the normalized native interface."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_gradient_errors(root) + project_gradient_geometry_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project gradient(s): '
        f'{preview}{suffix}'
    )


def _require_project_filters(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject filters outside the native shadow/glow interface."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_filter_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project filter(s): '
        f'{preview}{suffix}'
    )


def _require_project_effect_status(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject source effects that the importer cannot map without distortion."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_effect_status_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: unsupported imported PPTX effect(s): '
        f'{preview}{suffix}'
    )


def _require_project_text_properties(
    root: ET.Element,
    svg_path: Path | str,
) -> None:
    """Reject text declarations outside the closed DrawingML mapping."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    errors = project_text_property_errors(root)
    if not errors:
        return
    preview = '; '.join(errors[:8])
    suffix = '' if len(errors) <= 8 else f'; +{len(errors) - 8} more'
    raise SvgNativeConversionError(
        f'{Path(svg_path).name}: invalid project text property(s): '
        f'{preview}{suffix}'
    )


# ---------------------------------------------------------------------------
# Transform & layout helpers
# ---------------------------------------------------------------------------
