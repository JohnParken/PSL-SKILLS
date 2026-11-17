"""Materialize only explicit bundle-roster SVG resources, entirely in memory.

The upstream icon geometry/color/identity algorithms are retained. This adapter
adds pre-allocation, per-reference budgets and never opens a filesystem path.
Callers publish the returned bytes in their private copy, not the original input.
"""
from __future__ import annotations

import base64
import binascii
import math
import posixpath
from urllib.parse import unquote_to_bytes, urlsplit
from xml.etree import ElementTree as ET

from lxml import etree

from tl_guard import ContractError, MAX_DEPTH, MAX_INPUT_BYTES, MAX_NODES, MAX_TOTAL_BYTES, validate_svg
from svg_finalize import embed_icons as icons
from svg_to_pptx.use_expander import _LocalUseExpander

SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_NS = 'http://www.w3.org/1999/xlink'
IMAGE_MEDIA = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.svg': 'image/svg+xml'}
MAX_RESOURCE_DEPTH = 8


def _charge(budget, *, size=0, nodes=0):
    """Reserve work before copying, parsing, encoding, or creating elements."""
    budget['bytes'] = budget.get('bytes', 0) + size
    budget['nodes'] = budget.get('nodes', 0) + nodes
    if budget['bytes'] > MAX_TOTAL_BYTES or budget['nodes'] > MAX_NODES:
        raise ContractError('SVG roster expansion allocation budget exceeded')


class _ResourceTreeBuilder(etree.TreeBuilder):
    """Count each XML node before allocation with a cross-document budget."""

    def __init__(self, budget):
        super().__init__()
        self.budget = budget
        self.depth = 0

    def start(self, tag, attrs, nsmap=None):
        """Reserve one node before constructing it."""
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ContractError('SVG roster XML depth budget exceeded')
        _charge(self.budget, nodes=1)
        namespaces = {key or None: value for key, value in nsmap.items()} if nsmap else None
        return super().start(tag, attrs, namespaces)

    def end(self, tag):
        """Close the current element and release nesting depth."""
        node = super().end(tag)
        self.depth -= 1
        return node

    def comment(self, text):
        """Count comments as allocated XML nodes too."""
        _charge(self.budget, nodes=1)
        return super().comment(text)

    def pi(self, target, text):
        """Reject processing instructions rather than executing them."""
        raise ContractError('SVG resource processing instructions are forbidden')

    def doctype(self, name, public_id, system_id):
        """Reject DTD/entity declarations before tree construction."""
        raise ContractError('SVG resource DTD and entity declarations are forbidden')


def _parse(data, budget):
    if not isinstance(data, bytes) or len(data) > MAX_INPUT_BYTES:
        raise ContractError('SVG roster member exceeds its source byte budget')
    _charge(budget, size=len(data))
    parser = etree.XMLParser(target=_ResourceTreeBuilder(budget), resolve_entities=False,
                             load_dtd=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(data, parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ContractError('Invalid SVG roster resource XML') from exc
    if root.tag != f'{{{SVG_NS}}}svg':
        raise ContractError('SVG roster resource requires a namespaced SVG root')
    return root


def _member(name):
    """Accept canonical archive names only, never percent or OS path syntax."""
    if (not isinstance(name, str) or not name or name.startswith('/')
            or any(char in name for char in ('\\', ':', '%', '\x00'))
            or any(ord(char) < 32 for char in name)
            or any(part in {'', '.', '..'} for part in name.split('/'))):
        raise ContractError('Invalid SVG roster member name')
    return name


def _relative(origin, reference, entries):
    parsed = urlsplit(reference)
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or any(char in reference for char in ('\\', '%', '\x00'))
            or parsed.path.startswith('/') or not parsed.path):
        raise ContractError('SVG resource must be an unambiguous relative roster path')
    name = posixpath.normpath(posixpath.join(posixpath.dirname(origin), parsed.path))
    _member(name)
    if name not in entries:
        raise ContractError('SVG resource is absent from the explicit bundle roster')
    return name


def _svg_text(root):
    """Normalize SVG prefixes for the upstream pure string icon helpers."""
    normalized = etree.Element(root.tag, dict(root.attrib), nsmap={None: SVG_NS, 'xlink': XLINK_NS})
    normalized.text = root.text
    for child in list(root):
        normalized.append(child)
    return etree.tostring(normalized, encoding='unicode')


def _image_value(value, origin, entries, budget, ancestry, depth):
    if value.startswith('data:'):
        header, separator, encoded = value.partition(',')
        media = header[5:].split(';', 1)[0].lower()
        if not separator or media != 'image/svg+xml':
            return value  # Common final validation verifies all raster data URIs.
        try:
            data = base64.b64decode(encoded, validate=True) if ';base64' in header.lower() else unquote_to_bytes(encoded)
        except (ValueError, binascii.Error) as exc:
            raise ContractError('Invalid inline SVG encoding') from exc
        root = _materialize(data, origin, entries, budget, ancestry, depth + 1)
        data = etree.tostring(root)
    else:
        name = _relative(origin, value, entries)
        suffix = posixpath.splitext(name)[1].lower()
        media = IMAGE_MEDIA.get(suffix)
        if media is None:
            raise ContractError('Only bundled PNG, JPEG and SVG image resources are supported')
        if name in ancestry:
            raise ContractError('Cyclic SVG roster resource reference')
        data = entries[name]
        if not isinstance(data, bytes):
            raise ContractError('SVG roster resource must be immutable bytes')
        if media == 'image/svg+xml':
            root = _materialize(data, name, entries, budget, (*ancestry, name), depth + 1)
            data = etree.tostring(root)
        else:
            _charge(budget, size=len(data))
    # Charge the full encoded output before base64 creates it.
    _charge(budget, size=4 * ((len(data) + 2) // 3) + 64)
    return f'data:{media};base64,' + base64.b64encode(data).decode('ascii')


def _icon_group(element, entries, budget, ancestry, depth, used_ids):
    identifier = element.get('data-icon', '')
    try:
        library, name = icons._split_icon_identifier(identifier)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    target = f'icons/{library}/{name}.svg'
    if target not in entries or target in ancestry:
        raise ContractError('Missing or cyclic project-local icon in bundle roster')
    attrs = icons.parse_use_element(etree.tostring(element, encoding='unicode'))
    root = _materialize(entries[target], target, entries, budget, (*ancestry, target), depth + 1)
    source_nodes = sum(1 for _ in root.iter())
    content = _svg_text(root)
    if icons._is_preserve_color_asset(content):
        style = 'preserve'
        geometry = icons._get_viewbox_geometry(content) or (0.0, 0.0, 24.0, 24.0)
        shapes = icons._extract_svg_body(content)
    else:
        style = icons._detect_icon_style(content)
        geometry = icons._get_viewbox_size(content) or 16
        shapes = icons._extract_shape_elements(content, '#000000')
    values = geometry if isinstance(geometry, tuple) else (0.0, 0.0, geometry, geometry)
    if not all(math.isfinite(value) for value in values) or values[2] <= 0 or values[3] <= 0:
        raise ContractError('Project-local icon viewBox must be finite with positive extent')
    if not shapes:
        raise ContractError('Project-local icon has no embeddable shapes')
    # Two upstream parse/serialization passes and ID-prefix growth are bounded
    # before generate_icon_group allocates its strings and clone elements.
    reserve = len(content.encode('utf-8')) * 2 + 128 * source_nodes
    reserve += 64 * sum(1 for _ in icons._LOCAL_URL_RE.finditer(content))
    reserve += sum(len(key.encode()) + len(str(value).encode()) for key, value in attrs.items()) + 1024
    _charge(budget, size=reserve, nodes=source_nodes * 2 + 3)
    group = icons.generate_icon_group(attrs, shapes, style, geometry, used_ids=used_ids)
    wrapper = etree.fromstring(f'<svg xmlns="{SVG_NS}" xmlns:xlink="{XLINK_NS}">{group}</svg>'.encode())
    replacements = [node for node in wrapper if isinstance(node.tag, str)]
    if len(replacements) != 1:
        raise ContractError('Icon expansion did not produce exactly one group')
    return replacements[0]


def _materialize(data, origin, entries, budget, ancestry, depth):
    if depth > MAX_RESOURCE_DEPTH:
        raise ContractError('SVG roster resource nesting budget exceeded')
    root = _parse(data, budget)
    used_ids = {element.get('id') for element in root.iter() if element.get('id')}
    for element in list(root.iter()):
        if not isinstance(element.tag, str):
            continue
        local = etree.QName(element).localname
        if local in {'script', 'foreignObject', 'style', 'animate', 'animateTransform', 'set'}:
            raise ContractError('Active or unsupported SVG resource element')
        if any(etree.QName(key).localname.lower().startswith('on') for key in element.attrib):
            raise ContractError('Active SVG resource attributes are forbidden')
        if element.get('data-icon') is not None:
            if any(etree.QName(key).localname in {'href', 'src', 'poster'} for key in element.attrib):
                raise ContractError('Icon placeholder cannot also declare another resource')
            if local != 'use' or len(element):
                raise ContractError('data-icon must be an empty SVG use placeholder')
            replacement = _icon_group(element, entries, budget, ancestry, depth, used_ids)
            replacement.tail = element.tail
            element.getparent().replace(element, replacement)
            continue
        for key, value in list(element.attrib.items()):
            attribute = etree.QName(key).localname
            if attribute not in {'href', 'src', 'poster'} or value.startswith('#'):
                continue
            if local == 'a':
                continue  # Passive hyperlinks are validated, never followed.
            if local != 'image':
                raise ContractError('External SVG use resources are not supported; use a local #id or project icon')
            element.set(key, _image_value(value, origin, entries, budget, ancestry, depth))
    # Validate after resolving resources, before generic use cloning. The
    # preserved expander then uses the same cumulative allocation counters.
    serialized = etree.tostring(root)
    validate_svg(serialized)
    native_root = ET.fromstring(serialized)
    expander = _LocalUseExpander(native_root)
    expander.nodes_allocated = budget.get('nodes', 0)
    expander.bytes_allocated = budget.get('bytes', 0)
    expander.expand()
    budget['nodes'] = expander.nodes_allocated
    budget['bytes'] = expander.bytes_allocated
    rendered = ET.tostring(native_root, encoding='utf-8')
    validate_svg(rendered)
    return etree.fromstring(rendered)


def materialize_svg_resources(member, entries, budget=None):
    """Return validated self-contained SVG bytes from one explicit roster item.

    Supply the same empty dict as ``budget`` across every SVG in a bundle. Limits
    account for each reference occurrence, including recursive resources, rather
    than deduplicating work. Input entries and source bytes are never modified.
    """
    _member(member)
    if not isinstance(entries, dict) or member not in entries:
        raise ContractError('SVG member is absent from the explicit bundle roster')
    if budget is None:
        budget = {}
    if any(type(value) is not int or value < 0 for value in budget.values()):
        raise ContractError('Invalid shared SVG expansion budget')
    root = _materialize(entries[member], member, entries, budget, (member,), 0)
    result = etree.tostring(root)
    validate_svg(result)
    return result
