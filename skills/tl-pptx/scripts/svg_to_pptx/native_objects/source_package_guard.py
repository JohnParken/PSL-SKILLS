"""Bounded validation before restoring inline original chart package parts.

This restores upstream source-package capability without accepting filesystem
references, unbounded compressed workbooks or DTD/entity-bearing XML.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import zipfile

from lxml import etree

from tl_package_guard import (
    PackageBudget, _relationships, _safe_xml, inspect_package, is_xml_part, safe_part_name,
)

MAX_BLOB = 20_000_000


def _decode(blob):
    if not isinstance(blob, dict) or blob.get('encoding') != 'base64':
        raise RuntimeError('Source chart blob must be base64 metadata')
    encoded = blob.get('payload')
    checksum = blob.get('sha256')
    if not isinstance(encoded, str) or not isinstance(checksum, str) or len(encoded) > 4 * ((MAX_BLOB + 2) // 3):
        raise RuntimeError('Source chart blob metadata exceeds its budget')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError('Invalid source chart base64') from exc
    if len(raw) > MAX_BLOB or hashlib.sha256(raw).hexdigest() != checksum.lower():
        raise RuntimeError('Source chart checksum or size mismatch')
    return raw


def _xml(raw, budget):
    root = _safe_xml(raw, budget)
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if etree.QName(element).localname in {'oleObj', 'control', 'activeX', 'altChunk', 'script'}:
            raise RuntimeError('Active source chart objects are forbidden')
        if element.get('action') and element.get('action') != 'ppaction://hlinksldjump':
            raise RuntimeError('Active source chart action is forbidden')
    return root


def validate_source_chart_package(source):
    """Check every decoded part and nested workbook before original decoder."""
    if not isinstance(source, dict) or not isinstance(source.get('parts'), list) or not 1 <= len(source['parts']) <= 32:
        raise RuntimeError('Invalid source chart inventory')
    budget = PackageBudget()
    frame = _decode(source.get('frame'))
    budget.expanded += len(frame)
    _xml(frame, budget)
    names, roots = set(), {}
    for item in source['parts']:
        if not isinstance(item, dict):
            raise RuntimeError('Invalid source chart part')
        name = item.get('name')
        if not isinstance(name, str):
            raise RuntimeError('Source chart member is outside supported part families')
        canonical = safe_part_name(name)
        if canonical in names or not canonical.startswith(('ppt/charts/', 'ppt/embeddings/', 'ppt/theme/')):
            raise RuntimeError('Invalid or duplicate source chart member')
        names.add(canonical)
        raw = _decode(item)
        budget.expanded += len(raw)
        if budget.expanded > MAX_BLOB:
            raise RuntimeError('Source chart decoded bytes exceed budget')
        if name == source.get('chart_part') or is_xml_part(name, raw, item.get('content_type')):
            roots[canonical] = _xml(raw, budget)
        if zipfile.is_zipfile(io.BytesIO(raw)):
            inspect_package(raw, budget=budget, require_presentation=False)
    for name, root in roots.items():
        if name.endswith('.rels'):
            _relationships(name, root, names)
