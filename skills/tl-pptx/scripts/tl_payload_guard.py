"""Validate opaque inline DrawingML before the upstream converter parses it.

PPT Master decodes these seven transport channels independently of the outer
SVG parser. Validate each decoded document, not merely its base64 wrapper.
This module does not interpret chart/table/formula JSON or package ZIP payloads.
"""
from __future__ import annotations

import base64
import binascii
import io

from lxml import etree

SVG_NS = 'http://www.w3.org/2000/svg'
DRAWING_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PRESENTATION_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main'
RELATIONSHIP_NAMESPACES = frozenset({
    'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'http://purl.oclc.org/ooxml/officeDocument/relationships',
})
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_XML_NODES = 20000
MAX_XML_DEPTH = 80
ATTRIBUTE_ROOTS = {
    'data-pptx-shape-style': frozenset({f'{{{PRESENTATION_NS}}}style'}),
    'data-pptx-custgeom': frozenset({f'{{{DRAWING_NS}}}custGeom'}),
    'data-pptx-gradient-ooxml': frozenset({f'{{{DRAWING_NS}}}gradFill'}),
    'data-pptx-effect-ooxml': frozenset({
        f'{{{DRAWING_NS}}}effectLst', f'{{{DRAWING_NS}}}effectDag',
    }),
}
METADATA_ROOTS = {
    'txbody': frozenset({f'{{{PRESENTATION_NS}}}txBody'}),
    'placeholder-sppr': frozenset({f'{{{PRESENTATION_NS}}}spPr'}),
    'roundtrip-graphic-frame': frozenset({f'{{{PRESENTATION_NS}}}graphicFrame'}),
}
ACTIVE_ELEMENTS = frozenset({
    'oleObj', 'control', 'activeX', 'altChunk', 'script',
    'hlinkClick', 'hlinkMouseOver',
})
EXTERNAL_ATTRIBUTES = frozenset({'href', 'src', 'poster', 'Target'})


class NativePayloadError(ValueError):
    """An opaque native payload failed pre-conversion validation."""


def _validate_xml(encoded: str, expected_roots: frozenset[str], budget: list[int]) -> int:
    """Validate one relationship-free XML fragment and charge its real size."""
    # The input SVG already has a cap, but keep this helper independently bounded.
    if len(encoded) > ((MAX_DOCUMENT_BYTES + 2) // 3) * 4 + 2:
        raise NativePayloadError('Native XML payload exceeds its byte budget')
    try:
        raw = base64.b64decode(encoded, validate=True)
        decoded = raw.decode('utf-8')
        if '\x00' in decoded:
            raise ValueError('NUL bytes cannot occur in UTF-8 XML')
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise NativePayloadError('Native XML payload must be valid base64 UTF-8') from exc
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise NativePayloadError('Native XML payload is empty or exceeds its byte budget')
    budget[0] += len(raw)
    if budget[0] > MAX_TOTAL_BYTES:
        raise NativePayloadError('Native resources exceed the cumulative byte budget')
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                             huge_tree=False, remove_comments=False)
    try:
        tree = etree.parse(io.BytesIO(raw), parser)
    except etree.XMLSyntaxError as exc:
        raise NativePayloadError('Native XML payload is malformed or unsafe') from exc
    if tree.docinfo.encoding.upper().replace('-', '') not in {'UTF8', 'ASCII', 'USASCII'}:
        raise NativePayloadError('Native XML must declare UTF-8 or ASCII encoding')
    if tree.docinfo.doctype:
        raise NativePayloadError('DTD and entities are forbidden in native XML')
    if tree.xpath('//processing-instruction()'):
        raise NativePayloadError('Processing instructions are forbidden in native XML')
    root = tree.getroot()
    if root.tag not in expected_roots:
        raise NativePayloadError('Native XML root does not match its transport channel')
    stack = [(root, 1)]
    local_nodes = 0
    while stack:
        element, depth = stack.pop()
        local_nodes += 1
        budget[1] += 1
        if budget[1] > MAX_XML_NODES or depth > MAX_XML_DEPTH:
            raise NativePayloadError('Native XML exceeds its node or depth budget')
        if isinstance(element, etree._Entity):
            raise NativePayloadError('Entities are forbidden in native XML')
        if not isinstance(element.tag, str):
            continue
        if etree.QName(element).localname in ACTIVE_ELEMENTS:
            raise NativePayloadError('Active objects and hyperlink actions are forbidden in native XML')
        for key, value in element.attrib.items():
            attribute = etree.QName(key)
            if attribute.namespace in RELATIONSHIP_NAMESPACES:
                raise NativePayloadError('Part-local relationships are forbidden in inline native XML')
            if attribute.localname in EXTERNAL_ATTRIBUTES and value:
                raise NativePayloadError('External resource references are forbidden in native XML')
            if attribute.localname == 'TargetMode' and value.lower() == 'external':
                raise NativePayloadError('External relationships are forbidden in native XML')
        stack.extend((child, depth + 1) for child in element)
    return local_nodes


def validate_native_payloads(root, budget: list[int] | None = None) -> dict:
    """Preflight every inline DrawingML transport in an already-parsed SVG.

    ``budget[0]`` is the caller's cumulative resource-byte count. ``budget[1]``
    accumulates decoded native-XML nodes across SVGs and nested SVG images.
    Pass the same mutable budget through recursive validation. The source SVG
    bytes should already be charged by the caller. No file or network access.
    """
    if budget is None:
        budget = [0, 0]
    if len(budget) == 1:
        budget.append(0)
    if len(budget) < 2 or any(not isinstance(value, int) or value < 0 for value in budget[:2]):
        raise NativePayloadError('Invalid native resource budget')
    payloads = 0
    before_bytes, before_nodes = budget[:2]
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        for attribute, expected_roots in ATTRIBUTE_ROOTS.items():
            encoded = element.get(attribute)
            if encoded is not None:
                _validate_xml(encoded, expected_roots, budget)
                payloads += 1
        if etree.QName(element).localname != 'metadata':
            continue
        expected_roots = METADATA_ROOTS.get(element.get('data-pptx-part'))
        if expected_roots is None:
            continue
        if element.get('data-pptx-encoding') != 'base64' or len(element):
            raise NativePayloadError('Native XML metadata requires one base64 text payload')
        _validate_xml((element.text or '').strip(), expected_roots, budget)
        payloads += 1
    return {'payloads': payloads, 'decoded_bytes': budget[0] - before_bytes,
            'nodes': budget[1] - before_nodes}
