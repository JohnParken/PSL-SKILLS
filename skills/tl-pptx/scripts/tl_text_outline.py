"""Explicit, limited single-line outlines using a caller-authorized local font.

outline_text(manifest) returns SVG bytes; the caller owns output publication.
This is unkerned horizontal glyph placement, NOT a complex-script shaper or a
page renderer. Ordinary presentation text never enters this opt-in adapter.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from tl_guard import ContractError, bare_name, read_local, validate_svg

SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_NS = 'http://www.w3.org/1999/xlink'
MAX_FONT_BYTES = 16 * 1024 * 1024
MAX_TEXT_LENGTH = 512
# convert interprets unitless SVG coordinates as 96 pixels per inch.
MIN_CANVAS_SIZE = 96
MAX_CANVAS_SIZE = 5376  # PowerPoint slide limit: 56 inches.


def _spec(manifest):
    allowed = {'text', 'font', 'font_authorized', 'font_size', 'color'}
    if not isinstance(manifest, dict) or set(manifest) - allowed:
        raise ContractError('Unknown text-outline manifest field')
    if manifest.get('font_authorized') is not True:
        raise ContractError('Explicit authorization to use the supplied font for outlines is required')
    text = manifest.get('text')
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT_LENGTH or not text.strip():
        raise ContractError('Text must contain 1 to 512 characters and visible content')
    if any(not (0x20 <= ord(char) <= 0x7e or 0x4e00 <= ord(char) <= 0x9fff) for char in text):
        raise ContractError('Only single-line printable ASCII and basic CJK ideographs are supported; no shaping or combining marks')
    name = manifest.get('font')
    if not isinstance(name, str):
        raise ContractError('A local font filename is required')
    suffix = Path(name).suffix.lower()
    if suffix not in {'.ttf', '.otf'}:
        raise ContractError('Supply one static TTF or OTF font, not a collection or system font name')
    bare_name(name, suffix)
    size = manifest.get('font_size', 32)
    if isinstance(size, bool) or not isinstance(size, (float, int)) or not math.isfinite(size) or not 1 <= size <= 256:
        raise ContractError('font_size must be finite, between 1 and 256 points')
    color = manifest.get('color', '#000000')
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ContractError('color must be #RRGGBB')
    return text, name, suffix, float(size), color


def _font_data(name, suffix):
    data = read_local(name, suffix, MAX_FONT_BYTES)
    if data[:4] not in {b'\x00\x01\x00\x00', b'OTTO'} or len(data) < 12:
        raise ContractError('Invalid static OpenType font signature')
    count = int.from_bytes(data[4:6], 'big')
    if not 1 <= count <= 256 or len(data) < 12 + count * 16:
        raise ContractError('Invalid OpenType table directory')
    tags = set()
    for offset in range(12, 12 + count * 16, 16):
        tag = data[offset:offset + 4]
        start = int.from_bytes(data[offset + 8:offset + 12], 'big')
        length = int.from_bytes(data[offset + 12:offset + 16], 'big')
        if tag in tags or start + length > len(data):
            raise ContractError('Invalid or duplicate OpenType table')
        tags.add(tag)
    if tags & {b'fvar', b'COLR', b'CPAL', b'SVG ', b'CBDT', b'CBLC', b'sbix'}:
        raise ContractError('Variable, color and bitmap fonts are outside the supported outline subset')
    if not tags & {b'glyf', b'CFF '}:
        raise ContractError('Font has no supported static outline table')
    return data


def _expand_svg(data, text, color):
    validate_svg(data)
    root = ET.fromstring(data)
    definitions = root.find(f'{{{SVG_NS}}}defs')
    if definitions is None:
        raise ContractError('Font did not produce path definitions')
    paths = {}
    for node in definitions:
        if node.tag != f'{{{SVG_NS}}}path' or set(node.attrib) != {'id', 'd'} or node.get('id') in paths:
            raise ContractError('Unexpected or duplicate font path definition')
        paths[node.get('id')] = node
    uses = [node for node in root if node is not definitions]
    if ''.join(node.get('data-text', '') for node in uses) != text or len(uses) != len(text):
        raise ContractError('Font export substituted, omitted or reordered characters')
    output = ET.Element(f'{{{SVG_NS}}}svg', dict(root.attrib))
    for node in uses:
        if node.tag != f'{{{SVG_NS}}}use' or set(node.attrib) - {'data-text', f'{{{XLINK_NS}}}href', 'transform'}:
            raise ContractError('Unexpected font export drawing primitive')
        reference = node.get(f'{{{XLINK_NS}}}href', '')
        if not reference.startswith('#') or reference[1:] not in paths or not node.get('transform'):
            raise ContractError('Font export contains an unresolved glyph')
        path = copy.deepcopy(paths[reference[1:]])
        del path.attrib['id']
        path.set('transform', node.get('transform'))
        path.set('fill', color)
        path.set('fill-rule', 'nonzero')
        output.append(path)
    result = ET.tostring(output, encoding='utf-8', xml_declaration=True)
    validate_svg(result)
    return result


def outline_text(manifest: dict) -> bytes:
    """Return paths for explicit local-font text; raise ContractError on refusal.

    Fields: text, font (bare .ttf/.otf), font_authorized=True; optional
    font_size=32 points and color='#000000'. Authorization is the caller's
    declaration, not an automated font-license determination. No temp files,
    rendering, fallback fonts, filesystem discovery or network access are used.
    """
    text, name, suffix, size, color = _spec(manifest)
    data = _font_data(name, suffix)
    import fitz
    try:
        font = fitz.Font(fontbuffer=data)
        if any(not font.has_glyph(ord(char), fallback=False) for char in text):
            raise ContractError('Supplied font lacks a requested glyph; substitution is forbidden')
        box = font.bbox
        advance = sum(font.glyph_advance(ord(char)) for char in text) * size
        left = 4 + max(0, -box.x0 * size)
        baseline = 4 + max(0, box.y1 * size)
        width = left + advance + max(0, box.x1 * size) + 4
        height = baseline + max(0, -box.y0 * size) + 4
        if not all(math.isfinite(value) and 1 <= value <= MAX_CANVAS_SIZE for value in (width, height)):
            raise ContractError('Text outline canvas exceeds the PowerPoint 56-inch limit; use shorter text or a smaller font_size')
        # Pad only the trailing canvas. Never scale or shift glyphs to fit.
        width = max(MIN_CANVAS_SIZE, width)
        height = max(MIN_CANVAS_SIZE, height)
        with fitz.open() as document:
            page = document.new_page(width=width, height=height)
            page.insert_font(fontname='explicit', fontbuffer=data)
            page.insert_text((left, baseline), text, fontsize=size, fontname='explicit')
            svg = page.get_svg_image(text_as_path=True).encode('utf-8')
        return _expand_svg(svg, text, color)
    except ContractError:
        raise
    except Exception as exc:
        raise ContractError('Supplied font could not be converted to verified simple outlines') from exc
