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



# Responsibility: elements_image_sources; adapted from the original module.

def _resolve_external_image(
    svg_dir: Path,
    href: str,
    resource_root: Path | None = None,
) -> Path:
    """Resolve a non-data-URI image href to a file on disk.

    The href is interpreted exactly relative to the owning SVG and must remain
    inside the project. No root, ``images/``, or template-path guessing occurs.
    """
    candidate = resolve_external_image_reference(
        svg_dir,
        href,
        project_root=resource_root,
    )
    if candidate is not None:
        return candidate
    raise FileNotFoundError(f'External image not found: {href}')


_PROJECT_IMAGE_FORMATS = {
    'jpeg': 'jpg',
    'jpg': 'jpg',
    'png': 'png',
}

def _normalize_project_image_format(raw: str) -> str | None:
    return _PROJECT_IMAGE_FORMATS.get(raw.strip().lower().lstrip('.'))


def _valid_project_image_payload(img_format: str, img_data: bytes) -> bool:
    """Return whether bytes satisfy the bounded PNG/JPEG embedding contract."""
    try:
        inspect_image(img_data, img_format)
    except ImageValidationError:
        return False
    return True

def _decode_data_image_uri(href: str) -> tuple[str, bytes] | None:
    """Decode and validate one closed-project image data URI."""
    if not href.startswith('data:') or ',' not in href:
        return None

    header, payload = href.split(',', 1)
    match = re.fullmatch(
        r'data:image/([A-Za-z0-9.+-]+)(?:;[^;,]*)*?(?:;base64)?',
        header,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    img_format = _normalize_project_image_format(match.group(1))
    if img_format is None:
        return None

    is_base64 = any(
        part.strip().lower() == 'base64'
        for part in header.split(';')[1:]
    )
    if len(payload) > MAX_IMAGE_BYTES * 3:
        raise ImageValidationError('image data URI exceeds its encoded size limit')
    if is_base64 and len(payload) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ImageValidationError('image data URI exceeds the 32 MiB decoded limit')
    try:
        if is_base64:
            img_data = base64.b64decode(payload, validate=True)
        else:
            img_data = unquote_to_bytes(payload)
    except (ValueError, binascii.Error):
        return None
    inspect_image(img_data, img_format)
    return img_format, img_data


@dataclass(frozen=True)
class ProjectImageSource:
    """Validated bytes and package extension for one SVG image reference."""

    img_format: str
    img_data: bytes


def _project_image_href(elem: ET.Element) -> str:
    href_keys = tuple(
        key for key in ('href', f'{{{XLINK_NS}}}href')
        if key in elem.attrib
    )
    if len(href_keys) != 1:
        raise ValueError('requires exactly one href or xlink:href')
    href = elem.get(href_keys[0])
    if href is None or not href.strip():
        raise ValueError('href cannot be empty')
    return href


def load_project_image_source(
    elem: ET.Element,
    svg_dir: Path | None,
    resource_root: Path | None = None,
) -> ProjectImageSource:
    """Load one exact SVG image source or raise a contract error."""
    if elem.tag != f'{{{SVG_NS}}}image':
        raise ValueError('expected an SVG-namespace <image> element')
    href = _project_image_href(elem)
    if href.startswith('data:'):
        decoded = _decode_data_image_uri(href)
        if decoded is None:
            raise ValueError(
                'data URI must contain a supported, non-empty image with '
                'valid encoding and bytes'
            )
        img_format, img_data = decoded
        return ProjectImageSource(img_format, img_data)

    if svg_dir is None:
        raise ValueError('external image requires an SVG directory context')
    try:
        img_path = _resolve_external_image(svg_dir, href, resource_root)
    except FileNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    img_format = _normalize_project_image_format(img_path.suffix)
    if img_format is None:
        raise ValueError(
            f'external image has unsupported file extension {img_path.suffix!r}'
        )
    try:
        with img_path.open('rb') as image_file:
            img_data = image_file.read(MAX_IMAGE_BYTES + 1)
    except OSError as exc:
        raise ValueError(f'cannot read external image {href!r}: {exc}') from exc
    try:
        inspect_image(img_data, img_format)
    except ImageValidationError as exc:
        raise ValueError(f'external image {href!r}: {exc}') from exc
    return ProjectImageSource(img_format, img_data)


def project_image_errors(
    root: ET.Element,
    svg_dir: Path | None,
    *,
    allow_template_placeholders: bool = False,
    resource_root: Path | None = None,
) -> list[str]:
    """Return source and frame errors for exact SVG image elements."""
    from .elements_clip_geometry import _element_contract_label
    errors: list[str] = []
    for elem in root.iter():
        if elem.tag.rsplit('}', 1)[-1] != 'image':
            continue
        label = _element_contract_label(elem)
        if elem.tag != f'{{{SVG_NS}}}image':
            errors.append(
                f'{label} must use the SVG namespace '
                f'{SVG_NS!r}'
            )
            continue
        style_values = parse_inline_style(elem.get('style'))
        for attribute in ('width', 'height'):
            raw = style_values.get(attribute)
            if raw is None:
                raw = elem.get(attribute)
            if raw is None:
                errors.append(
                    f'{label} requires explicit positive {attribute}'
                )
                continue
            try:
                value = parse_project_geometry_length(raw, attribute)
            except ValueError:
                # The shared geometry-length contract owns syntax diagnostics.
                continue
            if value <= 0:
                errors.append(
                    f'{label} {attribute} must be positive; got {raw!r}'
                )
        try:
            raw_href = _project_image_href(elem)
        except ValueError as exc:
            errors.append(f'{label} invalid image source: {exc}')
            continue
        if (
            allow_template_placeholders
            and '{{' in raw_href
            and '}}' in raw_href
        ):
            continue
        try:
            load_project_image_source(elem, svg_dir, resource_root)
        except ValueError as exc:
            errors.append(f'{label} invalid image source: {exc}')
    return sorted(errors)
