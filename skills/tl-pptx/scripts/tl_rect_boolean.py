"""Exact, dependency-free Boolean subset for plain SVG rectangles.

No curve flattening, painting approximation, CSS evaluation or application
commands. Grid boundaries use rational arithmetic and emit one compound path.
"""
from fractions import Fraction
import re
from xml.etree import ElementTree as ET
from tl_guard import ContractError

_NS = 'http://www.w3.org/2000/svg'
_NUMBER = re.compile(r'-?(?:\d{1,9}(?:\.\d{1,6})?|\.\d{1,6})\Z')
_COLOR = re.compile(r'#[0-9a-fA-F]{6}\Z')


def _number(value):
    if not _NUMBER.fullmatch(value):
        raise ContractError('Rectangle Boolean requires bounded unitless decimal coordinates')
    return Fraction(value)


def _format(value):
    # Input decimals, comparisons and subtraction only: at most six places.
    scaled = value * 1000000
    if scaled.denominator != 1:
        raise ContractError('Rectangle Boolean coordinate cannot be represented exactly')
    negative = scaled < 0
    integer = abs(int(scaled))
    whole, tail = divmod(integer, 1000000)
    return ('-' if negative else '') + str(whole) + (('.' + str(tail).zfill(6).rstrip('0')) if tail else '')


def rectangle_fragment(root, operation, source_ids, output_id, style, fonts):
    """Return exact geometry or reject inputs outside the intentionally small subset."""
    if operation not in {'union', 'intersect', 'subtract', 'combine'}:
        raise ContractError('Rectangle Boolean supports union/intersect/subtract/combine only; fragment is unsupported')
    if not 2 <= len(source_ids) <= 32 or len(set(source_ids)) != len(source_ids):
        raise ContractError('Rectangle Boolean requires two to thirty-two unique operands')
    if fonts:
        raise ContractError('Rectangle Boolean does not accept fonts')
    if root.tag != '{' + _NS + '}svg' or set(root.attrib) - {'viewBox', 'width', 'height', 'preserveAspectRatio', 'id'}:
        raise ContractError('Rectangle Boolean rejects inherited root styling or transforms')
    for dimension in ('width', 'height'):
        if dimension in root.attrib and _number(root.get(dimension)) <= 0:
            raise ContractError('Rectangle Boolean viewport dimensions must be positive')
    for node in root.iter():
        if node.tag == '{' + _NS + '}style':
            raise ContractError('Rectangle Boolean rejects CSS stylesheets')
    index = {}
    for node in root.iter():
        if node.get('id'):
            if node.get('id') in index:
                raise ContractError('Duplicate SVG id')
            index[node.get('id')] = node
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:-]{0,255}', output_id):
        raise ContractError('Rectangle Boolean output_id must be an SVG identifier')
    if set(style) - {'fill', 'stroke'} or style.get('stroke', 'none') != 'none':
        raise ContractError('Rectangle Boolean supports only solid fill and no stroke')
    rects, fills = [], []
    for identifier in source_ids:
        node = index.get(identifier)
        if node is None or node not in list(root) or node.tag != '{' + _NS + '}rect':
            raise ContractError('Rectangle Boolean operands must be direct-root rect elements')
        if len(node) or set(node.attrib) - {'id', 'x', 'y', 'width', 'height', 'fill', 'stroke'}:
            raise ContractError('Rectangle Boolean rejects transforms, rounded corners, units, styles, masks and effects')
        fill = node.get('fill', '#000000')
        if not _COLOR.fullmatch(fill) or node.get('stroke', 'none') != 'none':
            raise ContractError('Rectangle Boolean requires opaque #RRGGBB fill and no stroke')
        x, y = _number(node.get('x', '0')), _number(node.get('y', '0'))
        w, h = _number(node.get('width', '')), _number(node.get('height', ''))
        if w <= 0 or h <= 0:
            raise ContractError('Rectangle Boolean dimensions must be positive')
        rects.append((x, y, x+w, y+h))
        fills.append(fill.lower())
    fill = style.get('fill', fills[0])
    if not _COLOR.fullmatch(fill) or ('fill' not in style and len(set(fills)) != 1):
        raise ContractError('Rectangle Boolean requires one solid output fill; specify fill for differently colored operands')
    xs = sorted({r[i] for r in rects for i in (0, 2)})
    ys = sorted({r[i] for r in rects for i in (1, 3)})
    edges = set()
    for left, right in zip(xs, xs[1:]):
        for top, bottom in zip(ys, ys[1:]):
            inside = [a <= left and right <= c and b <= top and bottom <= d for a,b,c,d in rects]
            keep = {'union': any(inside), 'intersect': all(inside),
                    'subtract': inside[0] and not any(inside[1:]),
                    'combine': sum(inside) % 2 == 1}[operation]
            if not keep:
                continue
            points = [(left,top),(right,top),(right,bottom),(left,bottom)]
            for start, end in zip(points, points[1:] + points[:1]):
                if (end,start) in edges:
                    edges.remove((end,start))
                else:
                    edges.add((start,end))
    if not edges:
        raise ContractError('Rectangle Boolean result is empty; no shape emitted')
    following = {}
    for start, end in sorted(edges):
        if start in following:
            raise ContractError('Rectangle Boolean rejects point-touching result contours')
        following[start] = end
    if len(set(following.values())) != len(following):
        raise ContractError('Rectangle Boolean result boundary is ambiguous')
    contours = []
    while following:
        start = min(following)
        current = start
        contour = [start]
        while True:
            current = following.pop(current)
            if current == start:
                break
            contour.append(current)
        contours.append('M ' + ' L '.join(_format(x)+' '+_format(y) for x,y in contour) + ' Z')
    path = ET.Element('path', {'id': output_id, 'd': ' '.join(contours), 'fill': fill,
                               'stroke': 'none', 'fill-rule': 'nonzero'})
    return ET.tostring(path, encoding='unicode')
