# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
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


# Responsibility: utils_constants; adapted from the original module.

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
