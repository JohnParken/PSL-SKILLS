"""Coordinate, transform, color, and font helpers for DrawingML conversion.

See references/shared-standards-core.md §2.1 for project geometry and
references/svg-effects.md §§6.2–6.8 for paint, image-fit, line-presentation,
and transform authoring contracts.
"""

from __future__ import annotations

import colorsys
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET

from pptx_gradients import (
    NATIVE_GRADIENT_ATTR,
    NATIVE_GRADIENT_PREVIEW_SHA256_ATTR,
    NATIVE_GRADIENT_SHA256_ATTR,
    preserved_native_gradient_xml,
)

from pptx_shapes import (
    OOXML_COORDINATE_MAX,
    resolve_preset_preview_hash,
    svg_preset_preview_fingerprint,
    validate_ooxml_xfrm,
)
from language_tags import language_base, language_uses_rtl, normalize_language_tag

from .context import AffineMatrix, ConvertContext, IDENTITY_MATRIX

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_NS = 'http://www.w3.org/1999/xlink'

EMU_PER_PX = 9525  # 1 SVG px = 9525 EMU (96 DPI)
FONT_PX_TO_HUNDREDTHS_PT = 75  # 1px = 0.75pt -> 75 hundredths-of-a-point
DRAWINGML_TEXT_FONT_SIZE_MIN = 100
DRAWINGML_TEXT_FONT_SIZE_MAX = 400_000
ANGLE_UNIT = 60000  # DrawingML angle: 60000ths of a degree

# SVG attributes inheritable from parent <g>
INHERITABLE_ATTRS = [
    'fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linecap',
    'stroke-linejoin', 'fill-opacity', 'stroke-opacity',
    'font-family', 'font-size', 'font-weight', 'font-style',
    'text-anchor', 'letter-spacing', 'text-decoration',
]

# Known East Asian fonts
EA_FONTS = {
    'PingFang SC', 'PingFang TC', 'PingFang HK',
    'Microsoft YaHei', 'Microsoft JhengHei',
    'SimSun', 'SimHei', 'FangSong', 'KaiTi', 'STKaiti',
    'STHeiti', 'STSong', 'STFangsong', 'STXihei', 'STZhongsong',
    'Hiragino Sans', 'Hiragino Sans GB', 'Hiragino Mincho ProN',
    'Hiragino Kaku Gothic ProN', 'Hiragino Kaku Gothic Pro',
    'Hiragino Mincho Pro',
    'Noto Sans SC', 'Noto Sans TC', 'Noto Serif SC', 'Noto Serif TC',
    'Noto Sans CJK SC', 'Noto Serif CJK SC',
    'Noto Sans JP', 'Noto Serif JP', 'Noto Sans CJK JP',
    'Source Han Sans SC', 'Source Han Sans TC',
    'Source Han Serif SC', 'Source Han Serif TC',
    'Source Han Sans JP', 'Source Han Serif JP',
    'WenQuanYi Micro Hei', 'WenQuanYi Zen Hei',
    'YouYuan', 'LiSu', 'HuaWenKaiTi',
    'Heiti TC', 'Kaiti TC', 'Songti SC', 'Songti TC',
    # Windows 10/11 + Office default / common Simplified Chinese
    'DengXian', 'DengXian Light', 'DengXian Bold', 'Microsoft YaHei UI',
    # Office display Chinese (华文 / 方正) — usually title-only, not on every client
    'STXingkai', 'STLiti', 'STXinwei', 'STHupo', 'STCaiyun',
    'FZShuTi', 'FZYaoti',
    # Common Traditional Chinese (Office)
    'DFKai-SB', 'MingLiU', 'PMingLiU', 'MingLiU_HKSCS',
    'MingLiU-ExtB', 'PMingLiU-ExtB',
    'Microsoft JhengHei UI',
    # Japanese fonts (Windows-available)
    'Yu Gothic', 'Yu Gothic UI', 'Yu Mincho',
    'Meiryo', 'Meiryo UI', 'メイリオ',
    'MS Gothic', 'MS Mincho', 'MS PGothic', 'MS PMincho', 'MS UI Gothic',
    # Korean
    'Malgun Gothic', 'Gulim', 'Dotum', 'Batang',
    'Noto Sans KR', 'Noto Serif KR',
}
SYSTEM_FONTS = {'system-ui', '-apple-system', 'BlinkMacSystemFont'}

# macOS/Linux-only fonts -> Windows equivalents
FONT_FALLBACK_WIN = {
    '微软雅黑': 'Microsoft YaHei',
    'PingFang SC': 'Microsoft YaHei',
    'PingFang TC': 'Microsoft JhengHei',
    'PingFang HK': 'Microsoft JhengHei',
    'Heiti TC': 'Microsoft JhengHei',
    'Kaiti TC': 'DFKai-SB',
    'Hiragino Sans': 'Microsoft YaHei',
    'Hiragino Sans GB': 'Microsoft YaHei',
    'Hiragino Mincho ProN': 'SimSun',
    'STHeiti': 'SimHei',
    'STSong': 'SimSun',
    'STKaiti': 'KaiTi',
    'STFangsong': 'FangSong',
    'STXihei': 'Microsoft YaHei',
    'STZhongsong': 'SimSun',
    'Songti SC': 'SimSun',
    'Songti TC': 'PMingLiU',
    'Noto Sans SC': 'Microsoft YaHei',
    'Noto Sans CJK SC': 'Microsoft YaHei',
    'Noto Sans TC': 'Microsoft JhengHei',
    'Noto Serif SC': 'SimSun',
    'Noto Serif CJK SC': 'SimSun',
    'Noto Serif TC': 'PMingLiU',
    # Japanese: keep as-is if user specified (PowerPoint will fallback if uninstalled)
    # 'Noto Sans JP': → keep as 'Noto Sans JP' (do not map)
    # 'メイリオ': → keep as 'メイリオ' (Meiryo alias)
    'メイリオ': 'Meiryo',
    'Source Han Sans SC': 'Microsoft YaHei',
    'Source Han Sans TC': 'Microsoft JhengHei',
    'Source Han Serif SC': 'SimSun',
    'Source Han Serif TC': 'PMingLiU',
    'Source Han Sans JP': 'Noto Sans JP',
    'Source Han Serif JP': 'Noto Serif JP',
    'WenQuanYi Micro Hei': 'Microsoft YaHei',
    'WenQuanYi Zen Hei': 'Microsoft YaHei',
    # Latin fonts (macOS / Linux / Web -> Windows)
    'SF Pro': 'Segoe UI',
    'SF Pro Display': 'Segoe UI',
    'SF Pro Text': 'Segoe UI',
    'SF Mono': 'Consolas',
    'Menlo': 'Consolas',
    'Monaco': 'Consolas',
    'Helvetica Neue': 'Arial',
    'Helvetica': 'Arial',
    'Roboto': 'Segoe UI',
    'Ubuntu': 'Segoe UI',
    'Liberation Sans': 'Arial',
    'Liberation Serif': 'Times New Roman',
    'Liberation Mono': 'Consolas',
    'DejaVu Sans': 'Segoe UI',
    'DejaVu Serif': 'Times New Roman',
    'DejaVu Sans Mono': 'Consolas',
}

GENERIC_FONT_MAP = {
    'monospace': 'Consolas',
    'sans-serif': 'Segoe UI',
    'serif': 'Times New Roman',
}

_FONT_CANONICAL_NAMES = {
    name.casefold(): name
    for name in EA_FONTS | SYSTEM_FONTS | FONT_FALLBACK_WIN.keys() | GENERIC_FONT_MAP.keys()
}

# When the latin font is serif and no EA font is specified,
# prefer SimSun (serif CJK) over Microsoft YaHei (sans-serif CJK).
_SERIF_LATIN = {
    'Times New Roman', 'Georgia', 'Garamond', 'Palatino', 'Palatino Linotype',
    'Book Antiqua', 'Cambria', 'SimSun', 'Liberation Serif', 'DejaVu Serif',
}

# Common Office/OS faces accepted without a custom-font warning on their
# corresponding target locale. Actual playback availability remains
# target-specific; keep these examples aligned with strategist.md §g.
PPT_SAFE_FONTS = frozenset({
    'microsoft yahei', 'simhei', 'simsun', 'kaiti', 'fangsong',
    'dengxian',
    'microsoft jhenghei', 'microsoft jhenghei ui', 'pmingliu', 'mingliu',
    'mingliu_hkscs', 'dfkai-sb',
    'pingfang sc', 'heiti sc', 'songti sc', 'stsong',
    'pingfang tc', 'pingfang hk', 'heiti tc', 'songti tc', 'kaiti tc',
    'yu gothic', 'yu gothic ui', 'yu mincho',
    'meiryo', 'meiryo ui',
    'ms gothic', 'ms mincho', 'ms pgothic', 'ms pmincho', 'ms ui gothic',
    'malgun gothic', 'gulim', 'dotum', 'batang',
    'nirmala ui', 'mangal', 'kokila', 'aparajita', 'utsaah',
    'leelawadee ui', 'leelawadee', 'cordia new', 'angsana new', 'browallia new',
    'david', 'miriam', 'frank ruehl', 'gisha', 'levenim mt', 'narkisim', 'aharoni',
    'arial', 'arial black', 'calibri', 'segoe ui', 'verdana',
    'helvetica', 'helvetica neue', 'tahoma', 'trebuchet ms',
    'times new roman', 'times', 'georgia', 'cambria', 'cambria math', 'palatino',
    'garamond', 'book antiqua',
    'consolas', 'courier new', 'menlo', 'monaco',
    'impact',
})

# Parsed SVG stroke-dasharray values -> DrawingML prstDash
DASH_PRESETS = {
    (4.0, 4.0): 'dash',
    (6.0, 3.0): 'dash',
    (2.0, 2.0): 'sysDot',
    (8.0, 4.0): 'lgDash',
    (8.0, 4.0, 2.0, 4.0): 'lgDashDot',
}
PROJECT_STROKE_ENUM_VALUES = {
    'stroke-linecap': frozenset({'butt', 'round', 'square'}),
    'stroke-linejoin': frozenset({'bevel', 'miter', 'round'}),
    'vector-effect': frozenset({'none', 'non-scaling-stroke'}),
}
PROJECT_IMAGE_ASPECT_RATIO_ANCHORS = {
    'xMinYMin': (0.0, 0.0),
    'xMidYMin': (0.5, 0.0),
    'xMaxYMin': (1.0, 0.0),
    'xMinYMid': (0.0, 0.5),
    'xMidYMid': (0.5, 0.5),
    'xMaxYMid': (1.0, 0.5),
    'xMinYMax': (0.0, 1.0),
    'xMidYMax': (0.5, 1.0),
    'xMaxYMax': (1.0, 1.0),
}
PROJECT_IMAGE_ASPECT_RATIO_MODES = frozenset({'meet', 'slice'})
PROJECT_OPACITY_PROPERTIES = (
    'opacity',
    'fill-opacity',
    'stroke-opacity',
    'stop-opacity',
    'flood-opacity',
)
PROJECT_PERCENTAGE_OPACITY_PROPERTIES = frozenset({
    'stop-opacity',
    'flood-opacity',
})
PROJECT_PAINT_PROPERTIES = (
    'fill',
    'stroke',
    'stop-color',
    'flood-color',
    'data-pptx-fg',
    'data-pptx-bg',
)
PROJECT_REFERENCE_PAINT_PROPERTIES = frozenset({'fill', 'stroke'})
PROJECT_DEFINITION_TAGS = frozenset({
    'clipPath',
    'filter',
    'linearGradient',
    'marker',
    'pattern',
    'radialGradient',
})
PROJECT_GRADIENT_TAGS = frozenset({'linearGradient', 'radialGradient'})
PROJECT_TEXT_IMAGE_FILL_ATTR = 'data-pptx-text-image-fill'
PROJECT_TEXT_IMAGE_FILL_MODES = frozenset({'stretch', 'tile'})
# PPTX angle projection can overshoot a unit box by at most ~0.1036.
PROJECT_LINEAR_GRADIENT_COORDINATE_MIN = -0.105
PROJECT_LINEAR_GRADIENT_COORDINATE_MAX = 1.105
PROJECT_RADIAL_FOCUS_TOLERANCE = 0.00001
PROJECT_FILTER_PRIMITIVES = frozenset({
    'feDropShadow',
    'feGaussianBlur',
    'feOffset',
    'feFlood',
    'feComposite',
    'feMerge',
    'feMergeNode',
    'feComponentTransfer',
    'feFuncA',
})
PROJECT_FILTER_EFFECT_PRIMITIVES = frozenset({
    'feDropShadow',
    'feGaussianBlur',
})
PROJECT_FILTER_PUBLIC_TARGETS = frozenset({
    'rect',
    'circle',
    'image',
    'path',
    'polygon',
    'text',
})
_PROJECT_MARKER_NUMBER_TOKEN = (
    r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
)
_PROJECT_MARKER_POINT_TOKEN = (
    rf'{_PROJECT_MARKER_NUMBER_TOKEN}'
    rf'(?:\s*,\s*|\s+){_PROJECT_MARKER_NUMBER_TOKEN}'
)
_PROJECT_MARKER_TRIANGLE_PATH_RE = re.compile(
    rf'^\s*M\s*{_PROJECT_MARKER_POINT_TOKEN}'
    rf'(?:\s*L\s*{_PROJECT_MARKER_POINT_TOKEN}){{2}}\s*Z\s*$',
    re.IGNORECASE,
)
_PROJECT_MARKER_DIAMOND_PATH_RE = re.compile(
    rf'^\s*M\s*{_PROJECT_MARKER_POINT_TOKEN}'
    rf'(?:\s*L\s*{_PROJECT_MARKER_POINT_TOKEN}){{3}}\s*Z\s*$',
    re.IGNORECASE,
)
_PROJECT_MARKER_ARROW_PATH_RE = re.compile(
    rf'^\s*M\s*{_PROJECT_MARKER_POINT_TOKEN}'
    rf'(?:\s*L\s*{_PROJECT_MARKER_POINT_TOKEN}){{2}}\s*$',
    re.IGNORECASE,
)
_PROJECT_MARKER_COMMAND_POINT_RE = re.compile(
    rf'[ML]\s*({_PROJECT_MARKER_NUMBER_TOKEN})'
    rf'(?:\s*,\s*|\s+)({_PROJECT_MARKER_NUMBER_TOKEN})',
    re.IGNORECASE,
)
PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS = frozenset({
    'defs',
    'desc',
    'metadata',
    'style',
    'title',
})
THICK_CIRCLE_COVERAGE_TOLERANCE = 1.0


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

_LENGTH_RE = re.compile(r'^\s*([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)\s*([A-Za-z%]*)\s*$')
_CANONICAL_PROJECT_GEOMETRY_LENGTH_RE = re.compile(
    r'^-?(?:\d+(?:\.\d+)?|\.\d+)$'
)
_PROJECT_STROKE_DASH_NUMBER_PATTERN = (
    r'[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?'
)
_PROJECT_STROKE_DASH_NUMBER_RE = re.compile(
    _PROJECT_STROKE_DASH_NUMBER_PATTERN
)
_PROJECT_STROKE_DASHARRAY_RE = re.compile(
    rf'\s*{_PROJECT_STROKE_DASH_NUMBER_PATTERN}'
    rf'(?:(?:\s+|\s*,\s*){_PROJECT_STROKE_DASH_NUMBER_PATTERN})+\s*'
)
PROJECT_GEOMETRY_LENGTH_ATTRIBUTES = {
    'svg': frozenset({'x', 'y', 'width', 'height'}),
    'rect': frozenset({'x', 'y', 'width', 'height', 'rx', 'ry'}),
    'circle': frozenset({'cx', 'cy', 'r'}),
    'ellipse': frozenset({'cx', 'cy', 'rx', 'ry'}),
    'line': frozenset({'x1', 'y1', 'x2', 'y2'}),
    'text': frozenset({'x', 'y'}),
    'tspan': frozenset({'x', 'y', 'dx', 'dy'}),
    'image': frozenset({'x', 'y', 'width', 'height'}),
    'use': frozenset({'x', 'y', 'width', 'height'}),
}
PROJECT_NON_NEGATIVE_LENGTH_ATTRIBUTES = frozenset({
    'width', 'height', 'r', 'rx', 'ry', 'stroke-width',
})


_TRANSFORM_NUMBER_PATTERN = (
    r'[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?'
)
_TRANSFORM_NUMBER_RE = re.compile(_TRANSFORM_NUMBER_PATTERN)
_CANONICAL_TRANSFORM_NUMBER_RE = re.compile(
    r'-?(?:\d+(?:\.\d+)?|\.\d+)$'
)
_TRANSFORM_OPERATION_RE = re.compile(r'([A-Za-z]+)\(([^()]*)\)')
_TRANSFORM_WHITESPACE_RE = re.compile(r'[ \t\r\n]*')
_TRANSFORM_SEPARATOR_PATTERN = (
    r'(?:[ \t\r\n]+|[ \t\r\n]*,[ \t\r\n]*)'
)
_TRANSFORM_SEPARATOR_RE = re.compile(_TRANSFORM_SEPARATOR_PATTERN)
_TRANSFORM_ARGUMENTS_RE = re.compile(
    rf'[ \t\r\n]*{_TRANSFORM_NUMBER_PATTERN}'
    rf'(?:{_TRANSFORM_SEPARATOR_PATTERN}{_TRANSFORM_NUMBER_PATTERN})*'
    r'[ \t\r\n]*'
)
_TRANSFORM_ARITIES = {
    'matrix': frozenset({6}),
    'translate': frozenset({1, 2}),
    'scale': frozenset({1, 2}),
    'rotate': frozenset({1, 3}),
}
_FULL_TRANSFORM_TAGS = frozenset({
    'rect', 'circle', 'ellipse', 'line', 'path', 'polygon', 'polyline',
    'image',
})
_TRANSFORM_CONTAINER_TAGS = frozenset({'g', 'use'})
_TRANSFORM_DEFINITION_BOUNDARIES = frozenset({'clipPath', 'marker', 'pattern'})
_NON_VISUAL_TRANSFORM_CHILD_TAGS = frozenset({
    'defs', 'title', 'desc', 'metadata', 'style',
})


_TRANSFORM_ARC_STYLE_ATTRS = (
    'fill',
    'stroke',
    'stroke-width',
    'stroke-dasharray',
)


_PROJECT_STROKE_STYLE_ATTRIBUTES = (
    'stroke-dasharray',
    'stroke-dashoffset',
    'stroke-linecap',
    'stroke-linejoin',
    'vector-effect',
)


_CSS_NAMED_COLORS = {
    'black': '000000',
    'silver': 'C0C0C0',
    'gray': '808080',
    'grey': '808080',
    'white': 'FFFFFF',
    'maroon': '800000',
    'red': 'FF0000',
    'purple': '800080',
    'fuchsia': 'FF00FF',
    'magenta': 'FF00FF',
    'green': '008000',
    'lime': '00FF00',
    'olive': '808000',
    'yellow': 'FFFF00',
    'navy': '000080',
    'blue': '0000FF',
    'teal': '008080',
    'aqua': '00FFFF',
    'cyan': '00FFFF',
    'orange': 'FFA500',
    'brown': 'A52A2A',
    'pink': 'FFC0CB',
    'gold': 'FFD700',
    'transparent': None,
    'lightgray': 'D3D3D3',
    'lightgrey': 'D3D3D3',
    'darkgray': 'A9A9A9',
    'darkgrey': 'A9A9A9',
}


_EA_DEFAULTS_BY_LANGUAGE = (
    (('ja',), ('Yu Gothic', 'Yu Mincho')),
    (('ko',), ('Malgun Gothic', 'Batang')),
    (('zh-hant', 'zh-tw', 'zh-hk', 'zh-mo'), ('Microsoft JhengHei', 'PMingLiU')),
)
# macOS Japanese faces map to Japanese Windows faces, not to Chinese ones.
_JA_FONT_FALLBACK_WIN = {
    'Hiragino Sans': 'Yu Gothic',
    'Hiragino Kaku Gothic ProN': 'Yu Gothic',
    'Hiragino Kaku Gothic Pro': 'Yu Gothic',
    'Hiragino Mincho ProN': 'Yu Mincho',
    'Hiragino Mincho Pro': 'Yu Mincho',
}


_NON_LATIN_SCRIPT_BASES = frozenset({
    'zh', 'ja', 'ko',
    'ar', 'fa', 'ps', 'sd', 'ug', 'ur',
    'he', 'yi',
    'hi', 'mr', 'ne', 'sa',
    'th',
    'be', 'bg', 'kk', 'ky', 'mk', 'mn', 'ru', 'sr', 'uk',
    'el',
})


_FIXED_SPACE_EMS = {
    ' ': 0.5,    # en space
    ' ': 1.0,    # em space
    ' ': 1 / 3,  # three-per-em space
    ' ': 0.25,   # four-per-em space
    ' ': 1 / 6,  # six-per-em space
    ' ': 0.55,   # figure space
    ' ': 0.3,    # punctuation space
    ' ': 0.2,    # thin space
    ' ': 0.1,    # hair space
    ' ': 0.2,    # narrow no-break space
}


_UNDECOMPOSED_BASE_LETTERS = str.maketrans('đĐłŁøØħĦ', 'dDlLoOhH')


_FONT_ADVANCES_CACHE = None


def get_font_advances(
    font_family: str | None,
    font_weight: str = '400',
    font_style: str = 'normal',
) -> dict[str, float] | None:
    """Return bundled glyph advances for the primary family and style."""
    family = primary_font_family(font_family)
    if not family:
        return None

    global _FONT_ADVANCES_CACHE
    if _FONT_ADVANCES_CACHE is None:
        with Path(__file__).with_name('font_advances.json').open(encoding='utf-8') as handle:
            _FONT_ADVANCES_CACHE = json.load(handle)['families']

    bold = font_weight in ('bold', '600', '700', '800', '900')
    italic = font_style in ('italic', 'oblique')
    style = 'bold' if bold else 'regular'
    if italic:
        style = 'bold-italic' if bold else 'italic'
    entry = _FONT_ADVANCES_CACHE.get(family, {}).get(style)
    return entry['advances'] if entry is not None else None



# Stable public facade; implementation is split by conversion responsibility.
from .utils_units import (
    px_to_emu,
    font_px_to_hpt,
    _f,
    _parse_svg_length_parts,
    parse_svg_length,
    parse_project_geometry_length,
    is_canonical_project_geometry_length,
    format_project_geometry_length,
    parse_project_opacity,
    is_project_opacity_default_form,
    format_project_opacity,
    parse_project_image_aspect_ratio,
    format_project_image_aspect_ratio,
    _parse_project_stroke_dasharray,
    parse_project_stroke_dasharray,
    noncanonical_stroke_dash_numbers,
    parse_project_stroke_enum,
    is_thick_circle_shorthand,
    svg_length_x,
    svg_length_y,
    svg_length_size,
)
from .utils_transforms import (
    matrix_multiply,
    _translate_matrix,
    _scale_matrix,
    _rotate_matrix,
    _parse_transform_operations,
    parse_transform_operations,
    noncanonical_transform_numbers,
    _transform_operations_matrix,
    parse_transform_matrix,
    transform_point,
    validate_dml_shape_matrix,
    _svg_element_tag,
    _transform_element_label,
    _visual_transform_children,
    _iter_visual_transform_tree,
    _transform_arc_styles,
    _is_project_thick_circle,
    supports_full_project_transform,
    iter_project_transforms,
    _has_positive_rounding,
    _contains_rounded_rect,
    _contains_native_marker,
    _project_thick_circle_ids,
)
from .utils_stroke import (
    iter_project_stroke_styles,
    project_stroke_style_errors,
    _contains_thick_circle,
)
from .utils_transform_validation import (
    _is_unit_axis_reflection,
    _transform_semantic_error,
    project_transform_errors,
    rect_to_dml_xfrm,
    _extract_inheritable_styles,
    _get_attr,
    ctx_x,
    ctx_y,
    ctx_w,
    ctx_h,
)
from .utils_paint import (
    parse_inline_style,
    svg_hidden_reason,
    is_stroked_zero_axis_shape,
    iter_project_geometry_lengths,
    project_geometry_length_errors,
    iter_project_image_aspect_ratios,
    project_image_aspect_ratio_errors,
    iter_project_opacities,
    project_opacity_errors,
    _finite_float,
    _parse_color_channel,
    _parse_alpha_channel,
    parse_opacity,
    quantize_ooxml_unit_ratio,
    quantize_ooxml_alpha,
    _functional_color_parts,
    _parse_hue_degrees,
    _parse_percentage,
    parse_svg_color,
    parse_project_paint,
    is_project_paint_default_form,
    iter_project_paints,
    project_paint_errors,
    project_definition_index,
    project_definition_errors,
    parse_hex_color,
    combine_opacity,
    parse_stop_style,
    resolve_url_id,
    get_effective_filter_id,
)
from .utils_markers import (
    _project_marker_polygon_points,
    _project_marker_path_points,
    _project_marker_cross,
    _project_marker_segments_cross,
    _project_marker_quadrilateral_type,
    classify_project_marker_shape,
    _project_effective_presentation_value,
    project_marker_errors,
    resolve_project_text_image_fill,
    project_paint_reference_errors,
)
from .utils_gradients import (
    project_mask_errors,
    parse_project_gradient_ratio,
    parse_project_linear_gradient_coordinate,
    is_project_radial_focus_point,
    project_gradient_errors,
)
from .utils_filters import (
    parse_project_filter_params,
    project_filter_drawingml_coordinates,
    project_filter_errors,
    _is_compact_authored_preset_filter_target,
    _is_imported_preset_preview_filter_target,
    is_picture_effect_carrier,
)
from .utils_fonts import (
    _language_is,
    _ea_default,
    parse_font_family,
    unsafe_exported_font_faces,
    _is_han_char,
    _is_hiragana_char,
    _is_katakana_char,
    _is_hangul_char,
    is_cjk_char,
    _contains_codepoint_range,
    _explicit_language_script,
    _default_language_for_script,
    text_has_rtl_characters,
    text_uses_rtl,
    detect_text_lang,
    _is_grapheme_extend,
    _is_regional_indicator,
    _is_virama,
    _is_emoji_base,
    _unicode_script_key,
    _virama_script_key,
    split_project_text_clusters,
    resolve_text_run_fonts,
)
from .utils_text_width import (
    _base_letter,
    _estimate_character_width,
    _estimate_grapheme_width,
    primary_font_family,
    estimate_text_cluster_widths,
    estimate_text_width,
    _xml_escape,
)

