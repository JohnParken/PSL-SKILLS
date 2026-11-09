# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes
from xml.etree import ElementTree as ET

from pptx_core.images import ImageValidationError, MAX_IMAGE_BYTES, inspect_image

from pptx_shapes import (
    CONNECTOR_PRESET_TYPES,
    OOXML_COORDINATE_MAX,
    OOXML_COORDINATE_MIN,
    get_preset_registry,
    has_relationship_attributes,
    load_shape_type_values,
    validate_ooxml_xfrm,
)
from pptx_effects import (
    EFFECT_REASON_ATTR,
    EFFECT_STATUS_ATTR,
    NATIVE_EFFECT_ATTR,
    NATIVE_EFFECT_SHA256_ATTR,
    preserved_native_effect_xml,
)
from hyperlink_contract import svg_hyperlink_href
from pptx_to_svg.preset_authoring import AUTHORING_ATTR, AUTHORING_VALUE
from resource_paths import (
    resolve_external_image_reference,
)

from .context import (
    TEXT_FLOW_PRESERVE,
    TEXT_FLOW_SPLIT,
    ConvertContext,
    ShapeResult,
)
from .hyperlinks import (
    HYPERLINK_ACTION_KEY,
    HYPERLINK_RID_KEY,
    hyperlink_click_xml,
    hyperlink_run_metadata,
)
from .theme_colors import color_node_xml
from .theme_fonts import theme_font_tokens
from .text_baseline import drawingml_text_baseline_offset
from .text_properties import (
    drawingml_letter_spacing,
    normalize_project_text_segments,
    parse_project_baseline_shift,
    parse_project_font_style,
    parse_project_font_weight,
    parse_project_letter_spacing,
    parse_project_text_anchor,
    parse_project_text_decoration,
    resolve_project_xml_space,
)
from .utils import (
    SVG_NS, XLINK_NS, ANGLE_UNIT, FONT_PX_TO_HUNDREDTHS_PT,
    PROJECT_IMAGE_ASPECT_RATIO_ANCHORS,
    px_to_emu, _f, _get_attr, parse_svg_length,
    svg_length_x, svg_length_y, svg_length_size,
    ctx_x, ctx_y, ctx_w, ctx_h,
    rect_to_dml_xfrm,
    combine_opacity, parse_hex_color, parse_svg_color,
    resolve_project_text_image_fill, resolve_url_id, get_effective_filter_id,
    parse_inline_style, parse_font_family, is_cjk_char,
    detect_text_lang, estimate_text_cluster_widths, font_px_to_hpt,
    get_font_advances, primary_font_family,
    resolve_text_run_fonts, split_project_text_clusters,
    text_has_rtl_characters, text_uses_rtl,
    is_thick_circle_shorthand, parse_project_geometry_length,
    is_canonical_project_geometry_length,
    parse_project_image_aspect_ratio,
    parse_project_opacity,
    parse_project_stroke_dasharray,
    quantize_ooxml_alpha,
    project_definition_index,
    svg_hidden_reason,
    is_stroked_zero_axis_shape,
    matrix_multiply, parse_transform_matrix, parse_transform_operations,
    transform_point, _xml_escape,
)
from .styles import (
    build_solid_fill, build_gradient_fill,
    build_fill_xml, build_stroke_xml, build_effect_xml, classify_filter_effect,
    get_element_opacity, get_fill_opacity, get_stroke_opacity,
)
from .paths import (
    PathCommand, parse_svg_path, parse_svg_points, svg_path_to_absolute,
    normalize_path_commands, path_commands_to_drawingml,
    transform_path_commands,
)



# Responsibility: elements_preset_geometry; adapted from the original module.

def _parse_preset_geometry_metadata(
    elem: ET.Element,
    ctx: ConvertContext | None = None,
) -> tuple[str | None, list[tuple[str, str]], tuple[float, float, float, float] | None]:
    """Parse and validate rendering-neutral preset geometry metadata."""
    from .elements_shape_xml import PPTX_PRESET_SHAPE_TYPES
    from .elements_shape_xml import _PPTX_AV_PREFIX
    from .elements_shape_xml import _PPTX_GUIDE_NAME_RE
    from .elements_shape_xml import _PPTX_VAL_FORMULA_RE
    status = (elem.get('data-pptx-geometry-status') or '').strip()
    authoring = elem.get(AUTHORING_ATTR)
    if authoring not in {None, AUTHORING_VALUE}:
        raise ValueError(f'Unsupported {AUTHORING_ATTR} value {authoring!r}')
    if authoring == AUTHORING_VALUE:
        object_kind = elem.get('data-pptx-object')
        if object_kind not in {'shape', 'connector'}:
            raise ValueError(
                'Authored preset metadata requires data-pptx-object='
                '"shape" or "connector"'
            )
        preset = elem.get('data-pptx-prst')
        if preset is None:
            raise ValueError('Authored preset metadata requires data-pptx-prst')
        if preset in CONNECTOR_PRESET_TYPES and object_kind != 'connector':
            raise ValueError(
                f'Connector preset {preset!r} requires '
                'data-pptx-object="connector"'
            )
        if object_kind == 'connector' and preset not in CONNECTOR_PRESET_TYPES:
            raise ValueError(
                f'Authored connector requires a connector preset, got {preset!r}'
            )
        if elem.get('data-pptx-frame') is None:
            raise ValueError('Authored preset metadata requires data-pptx-frame')
    if status not in {'', 'exact', 'unsupported'}:
        raise ValueError(
            f'Unsupported data-pptx-geometry-status {status!r}; '
            'expected exact or unsupported'
        )
    raw_reason = elem.get('data-pptx-geometry-reason')
    if raw_reason is not None and status != 'unsupported':
        raise ValueError(
            'data-pptx-geometry-reason requires '
            'data-pptx-geometry-status="unsupported"'
        )
    if status == 'unsupported':
        reason = (raw_reason or 'unspecified').strip()
        raise ValueError(f'Unsupported source PPTX geometry: {reason}')

    prst = elem.get('data-pptx-prst')
    allowed_guide_names: frozenset[str] = frozenset()
    if prst is not None:
        if prst != prst.strip() or prst not in PPTX_PRESET_SHAPE_TYPES:
            raise ValueError(f'Unknown or invalid data-pptx-prst {prst!r}')
        allowed_guide_names = frozenset(
            guide.name
            for guide in get_preset_registry().get(prst).adjustments
        )

    guide_formulas: dict[str, str] = {}
    for attr_name, raw_fmla in elem.attrib.items():
        if not attr_name.startswith(_PPTX_AV_PREFIX):
            continue
        if prst is None:
            raise ValueError(f'{attr_name} requires data-pptx-prst')
        guide_name = attr_name[len(_PPTX_AV_PREFIX):]
        if not _PPTX_GUIDE_NAME_RE.fullmatch(guide_name):
            raise ValueError(f'Invalid preset adjustment guide name {guide_name!r}')
        if guide_name not in allowed_guide_names:
            raise ValueError(
                f'Preset {prst!r} has no adjustment guide named {guide_name!r}'
            )
        formula = raw_fmla.strip()
        if not formula:
            raise ValueError(f'{attr_name} must not be empty')
        match = _PPTX_VAL_FORMULA_RE.fullmatch(formula)
        if match is not None:
            value = int(match.group(1))
            if not OOXML_COORDINATE_MIN <= value <= OOXML_COORDINATE_MAX:
                raise ValueError(
                    f'{attr_name} value {value} is outside OOXML coordinate range'
                )
        guide_formulas[guide_name] = formula

    # Compatibility for SVGs emitted before the generic ``data-pptx-av-*``
    # contract. New imports always use the canonical full-formula attributes.
    if prst == 'round2SameRect':
        guide_names = set(guide_formulas)
        for guide_name, default in (('adj1', 16667), ('adj2', 0)):
            legacy_name = f'data-pptx-{guide_name}'
            if guide_name in guide_names or elem.get(legacy_name) is None:
                continue
            raw_value = elem.get(legacy_name, str(default))
            try:
                value = int(float(raw_value))
            except ValueError as exc:
                raise ValueError(f'{legacy_name} must be numeric, got {raw_value!r}') from exc
            value = max(0, min(100000, value))
            guide_formulas[guide_name] = f'val {value}'

    guides: list[tuple[str, str]] = []
    if prst is not None and guide_formulas:
        registry = get_preset_registry()
        try:
            evaluated = registry.evaluate(
                prst,
                100000,
                100000,
                adjustments=guide_formulas,
            )
        except ValueError as exc:
            raise ValueError(
                f'Invalid adjustment formula for preset {prst!r}: {exc}'
            ) from exc
        for name, value in evaluated.adjustments.items():
            if (
                name in guide_formulas
                and not OOXML_COORDINATE_MIN
                <= value
                <= OOXML_COORDINATE_MAX
            ):
                raise ValueError(
                    f'data-pptx-av-{name} evaluates outside OOXML coordinate range'
                )
        guides = [
            (guide.name, guide_formulas[guide.name])
            for guide in registry.get(prst).adjustments
            if guide.name in guide_formulas
        ]

    frame = None
    raw_frame = elem.get('data-pptx-frame')
    if raw_frame is not None:
        parts = re.split(r'[\s,]+', raw_frame.strip())
        if len(parts) != 4:
            raise ValueError(
                'data-pptx-frame must contain exactly four numbers: x y width height'
            )
        try:
            frame = tuple(float(part) for part in parts)
        except ValueError as exc:
            raise ValueError(f'Invalid data-pptx-frame {raw_frame!r}') from exc
        if not all(math.isfinite(value) for value in frame):
            raise ValueError(f'data-pptx-frame must contain finite numbers, got {raw_frame!r}')
        is_connector = (
            elem.get('data-pptx-object') == 'connector'
            or prst in CONNECTOR_PRESET_TYPES
        )
        is_native_group = (
            elem.tag.rsplit('}', 1)[-1] == 'g'
            and elem.get('data-pptx-object') == 'group'
        )
        if is_connector or is_native_group or is_stroked_zero_axis_shape(elem, ctx):
            if frame[2] < 0 or frame[3] < 0 or (frame[2] == 0 and frame[3] == 0):
                kind = 'Connector' if is_connector else 'Group'
                raise ValueError(
                    f'{kind} data-pptx-frame dimensions must be non-negative '
                    f'and not both zero, got {raw_frame!r}'
                )
        elif frame[2] <= 0 or frame[3] <= 0:
            raise ValueError(
                f'data-pptx-frame width and height must be positive, got {raw_frame!r}'
            )
        validate_ooxml_xfrm(
            px_to_emu(frame[0]),
            px_to_emu(frame[1]),
            px_to_emu(frame[2]),
            px_to_emu(frame[3]),
        )

    return prst, guides, frame


def validate_preset_geometry_metadata(
    elem: ET.Element,
    ctx: ConvertContext | None = None,
) -> list[str]:
    """Return native shape metadata errors for authoring-time validation."""
    from .elements_shape_xml import _decode_shape_style
    errors: list[str] = []
    try:
        _parse_preset_geometry_metadata(elem, ctx)
    except ValueError as exc:
        errors.append(str(exc))
    if elem.get('data-pptx-custgeom') is not None:
        try:
            _build_preserved_custom_geom(elem)
        except ValueError as exc:
            errors.append(str(exc))
    if elem.get('data-pptx-shape-style') is not None:
        try:
            _decode_shape_style(elem)
        except ValueError as exc:
            errors.append(str(exc))
    raw_shape_id = elem.get('data-pptx-shape-id')
    if raw_shape_id is not None:
        try:
            shape_id = int(raw_shape_id)
        except ValueError:
            errors.append(f'Invalid data-pptx-shape-id {raw_shape_id!r}')
        else:
            if shape_id < 2 or shape_id > 0xFFFFFFFF:
                errors.append(
                    'data-pptx-shape-id must be between 2 and 4294967295'
                )
    scope = elem.get('data-pptx-shape-scope')
    if scope is not None and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', scope) is None:
        errors.append(f'Invalid data-pptx-shape-scope {scope!r}')
    for endpoint in ('start', 'end'):
        target = elem.get(f'data-pptx-{endpoint}-shape-id')
        site = elem.get(f'data-pptx-{endpoint}-site')
        if (target is None) != (site is None):
            errors.append(
                f'Connector {endpoint} endpoint requires both shape-id and site'
            )
        if target is not None:
            try:
                target_id = int(target)
                site_id = int(site or '')
            except ValueError:
                errors.append(f'Invalid connector {endpoint} endpoint metadata')
            else:
                if target_id < 2 or target_id > 0xFFFFFFFF:
                    errors.append(f'Connector {endpoint} shape-id is out of range')
                if site_id < 0 or site_id > 0xFFFFFFFF:
                    errors.append(f'Connector {endpoint} site is out of range')
    return errors


def complete_preset_adjustments(
    prst: str,
    guides: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Fill missing preset adjustments in the definition's native order."""
    # PowerPoint repairs a preset whose avLst lists only some of the
    # adjustments the preset defines, so emit the full set in the preset's own
    # order with authored values overriding the normative defaults.
    authored = dict(guides)
    return [
        (guide.name, authored.get(guide.name, guide.formula))
        for guide in get_preset_registry().get(prst).adjustments
    ]


def _build_preset_geom_from_meta(elem: ET.Element, ctx: ConvertContext) -> str | None:
    """Build validated native DrawingML preset geometry from SVG metadata."""
    prst, guides, _frame = _parse_preset_geometry_metadata(elem, ctx)
    if prst is None:
        return None
    if not guides:
        return f'<a:prstGeom prst="{prst}"><a:avLst/></a:prstGeom>'
    completed = complete_preset_adjustments(prst, guides)
    guide_xml = ''.join(
        f'<a:gd name="{_xml_escape(name)}" fmla="{_xml_escape(fmla)}"/>'
        for name, fmla in completed
    )
    return f'<a:prstGeom prst="{prst}"><a:avLst>{guide_xml}</a:avLst></a:prstGeom>'


def _build_preserved_custom_geom(elem: ET.Element) -> str | None:
    """Return unchanged native ``a:custGeom`` metadata, or mark it stale."""
    kind = elem.get('data-pptx-geometry-kind')
    if kind is None:
        return None
    if kind != 'custom':
        raise ValueError(f'Unsupported data-pptx-geometry-kind {kind!r}')
    encoded = elem.get('data-pptx-custgeom')
    expected_hash = elem.get('data-pptx-geometry-sha256')
    if not encoded or not expected_hash:
        raise ValueError(
            'Custom geometry metadata requires data-pptx-custgeom and '
            'data-pptx-geometry-sha256'
        )
    actual_hash = hashlib.sha256(
        (elem.get('d') or '').strip().encode('utf-8')
    ).hexdigest()
    if actual_hash != expected_hash:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
        custom = ET.fromstring(raw)
        decoded = raw.decode('utf-8')
    except (ValueError, binascii.Error, UnicodeDecodeError, ET.ParseError) as exc:
        raise ValueError(f'Invalid custom geometry metadata: {exc}') from exc
    if custom.tag != (
        '{http://schemas.openxmlformats.org/drawingml/2006/main}custGeom'
    ):
        raise ValueError('Custom geometry metadata payload must be a:custGeom')
    if has_relationship_attributes(custom):
        raise ValueError(
            'Custom geometry metadata must not contain relationship attributes'
        )
    return decoded


def _shape_xfrm_from_preset_frame(
    elem: ET.Element,
    ctx: ConvertContext,
    fallback_raw_rect: tuple[float, float, float, float],
    fallback_resolved_rect: tuple[float, float, float, float],
    transform: str | None,
) -> tuple[str, int, int, int, int, tuple[int, int, int, int]]:
    """Use the preserved logical frame for native preset size when present."""
    from .elements_shape_xml import _shape_xfrm_from_svg_rect
    prst, _guides, frame = _parse_preset_geometry_metadata(elem, ctx)
    if frame is None:
        raw_x, raw_y, raw_w, raw_h = fallback_raw_rect
        x, y, w, h = fallback_resolved_rect
    else:
        raw_x, raw_y, raw_w, raw_h = frame
        x = ctx_x(raw_x, ctx)
        y = ctx_y(raw_y, ctx)
        w = ctx_w(raw_w, ctx)
        h = ctx_h(raw_h, ctx)
    preserves_zero_axis = (
        elem.get('data-pptx-object') == 'connector'
        or prst in CONNECTOR_PRESET_TYPES
        or is_stroked_zero_axis_shape(elem, ctx)
    )
    xfrm_attr, off_x, off_y, ext_cx, ext_cy, bounds_emu = _shape_xfrm_from_svg_rect(
        ctx,
        raw_x,
        raw_y,
        raw_w,
        raw_h,
        x,
        y,
        w,
        h,
        transform,
        preserve_degenerate_axes=preserves_zero_axis,
    )
    if not preserves_zero_axis:
        ext_cx = max(ext_cx, 1)
        ext_cy = max(ext_cy, 1)
    bounds_emu = (
        bounds_emu[0],
        bounds_emu[1],
        max(bounds_emu[2], off_x + ext_cx),
        max(bounds_emu[3], off_y + ext_cy),
    )
    return xfrm_attr, off_x, off_y, ext_cx, ext_cy, bounds_emu


def _pathlike_preset_xfrm(
    elem: ET.Element,
    ctx: ConvertContext,
    transform: str | None,
    min_x: float,
    min_y: float,
    width: float,
    height: float,
) -> tuple[str, int, int, int, int, tuple[int, int, int, int]]:
    """Resolve a path-like preset xfrm from its logical frame or visual bounds."""
    from .elements_shape_xml import _uses_full_transform
    _prst, _guides, frame = _parse_preset_geometry_metadata(elem, ctx)
    if frame is None:
        if _uses_full_transform(ctx, transform):
            tag = elem.tag.rsplit('}', 1)[-1]
            raise ValueError(
                f'Transformed preset-bearing <{tag}> requires data-pptx-frame '
                'to preserve its logical size'
            )
        off_x = px_to_emu(min_x)
        off_y = px_to_emu(min_y)
        ext_cx = max(px_to_emu(width), 1)
        ext_cy = max(px_to_emu(height), 1)
        return (
            '',
            off_x,
            off_y,
            ext_cx,
            ext_cy,
            (off_x, off_y, off_x + ext_cx, off_y + ext_cy),
        )
    return _shape_xfrm_from_preset_frame(
        elem,
        ctx,
        (0.0, 0.0, 1.0, 1.0),
        (min_x, min_y, width, height),
        transform,
    )
