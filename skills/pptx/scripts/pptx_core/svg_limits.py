"""Bound SVG reuse before a renderer clones referenced subtrees.

The estimate deliberately includes definitions and unused reference subtrees.
It is an upper bound on expanded XML size, not a measurement of peak RAM.
Only the standard library is used; checking never mutates the SVG tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET


MAX_EXPANSION_DEPTH = 128
XLINK_HREF = '{http://www.w3.org/1999/xlink}href'
LOCAL_HREF_RE = re.compile(r'#[A-Za-z_][A-Za-z0-9_.:-]*\Z')
URL_REFERENCE_RE = re.compile(r'''url\(\s*['"]?#[A-Za-z_][A-Za-z0-9_.:-]*''', re.IGNORECASE)


@dataclass(frozen=True)
class _ExpansionCost:
    nodes: int
    bytes: int
    identifier_sites: int
    height: int


def _escaped_size(value: str) -> int:
    """Count UTF-8 bytes with a conservative bound for XML escaping."""
    if not isinstance(value, str):
        raise ValueError('SVG tags, attributes and text must be strings')
    return (
        len(value.encode('utf-8'))
        + 4 * value.count('&')
        + 3 * (value.count('<') + value.count('>'))
        + 5 * (value.count('"') + value.count("'") + value.count('\t') + value.count('\r') + value.count('\n'))
    )


def _own_cost(element: ET.Element) -> tuple[int, int]:
    # Expanded namespace names exceed their serialized short prefixes. The
    # additional allowance covers punctuation and namespace declarations.
    byte_count = 128 + 2 * _escaped_size(element.tag)
    identifier_sites = 0
    for name, value in element.attrib.items():
        byte_count += 16 + _escaped_size(name) + _escaped_size(value)
        if name == 'id' and value:
            identifier_sites += 1
        if name in {'href', XLINK_HREF} and value.startswith('#'):
            identifier_sites += 1
        identifier_sites += len(URL_REFERENCE_RE.findall(value))
    if element.text:
        byte_count += _escaped_size(element.text)
    if element.tail:
        byte_count += _escaped_size(element.tail)
    return byte_count, identifier_sites


def check_expansion_budget(
    root: ET.Element,
    *,
    max_nodes: int = 20000,
    max_bytes: int,
) -> tuple[int, int]:
    """Return conservative expanded node/byte totals or raise ``ValueError``.

    Every local ``use`` adds its target subtree, including nested references.
    A memoized DAG cost makes exponential reuse fail without constructing the
    exponential tree. Cycles, missing/ambiguous IDs, and excessive depth fail
    before expansion. Definitions are intentionally counted conservatively.
    """
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
        raise ValueError('SVG node budget must be a positive integer')
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError('SVG byte budget must be a positive integer')
    if not isinstance(root, ET.Element):
        raise ValueError('SVG budget input must be an XML element')
    targets: dict[str, ET.Element] = {}
    own_costs: dict[ET.Element, tuple[int, int]] = {}
    reference_targets: dict[ET.Element, ET.Element] = {}
    references: list[tuple[ET.Element, str]] = []
    stack = [(root, 1)]
    raw_bytes = 0
    while stack:
        element, depth = stack.pop()
        if depth > MAX_EXPANSION_DEPTH:
            raise ValueError('SVG source depth exceeds 128 levels')
        if element in own_costs:
            raise ValueError('SVG source must be a tree without shared or cyclic element objects')
        own_costs[element] = _own_cost(element)
        raw_bytes += own_costs[element][0]
        if len(own_costs) > max_nodes or raw_bytes > max_bytes:
            raise ValueError('SVG source exceeds its node or byte budget')
        identifier = element.get('id')
        if identifier:
            if identifier in targets:
                raise ValueError('SVG contains duplicate IDs; reuse would be ambiguous')
            targets[identifier] = element
        if element.tag.rsplit('}', 1)[-1] == 'use':
            href = element.get('href')
            xlink_href = element.get(XLINK_HREF)
            if href is not None and xlink_href is not None and href != xlink_href:
                raise ValueError('SVG use href and xlink:href disagree')
            target = href if href is not None else xlink_href
            if target is None or not LOCAL_HREF_RE.fullmatch(target):
                raise ValueError('SVG use requires a same-document fragment reference')
            references.append((element, target[1:]))
        stack.extend((child, depth + 1) for child in reversed(list(element)))
    for element, identifier in references:
        if identifier not in targets:
            raise ValueError('SVG use references a missing ID')
        reference_targets[element] = targets[identifier]

    # The native expander uses a short numeric per-instance ID prefix. Count
    # the prefix once at every clone layer and every possible reference site.
    # This also bounds prefixes after collisions with IDs in the source tree.
    prefix_allowance = 32 + len(str(max_nodes))
    cache: dict[ET.Element, _ExpansionCost] = {}
    active: set[ET.Element] = set()

    def estimate(element: ET.Element) -> _ExpansionCost:
        if element in active:
            raise ValueError('SVG use references form a cycle')
        if element in cache:
            return cache[element]
        if len(active) >= MAX_EXPANSION_DEPTH:
            raise ValueError('SVG expanded depth exceeds 128 levels')
        active.add(element)
        byte_count, identifier_sites = own_costs[element]
        node_count = 1
        height = 1
        for child in element:
            cost = estimate(child)
            node_count += cost.nodes
            byte_count += cost.bytes
            identifier_sites += cost.identifier_sites
            height = max(height, cost.height + 1)
            if node_count > max_nodes or byte_count > max_bytes:
                raise ValueError('SVG reuse expansion exceeds its node or byte budget')
        target = reference_targets.get(element)
        if target is not None:
            cost = estimate(target)
            node_count += cost.nodes
            byte_count += cost.bytes + prefix_allowance * cost.identifier_sites + 256
            identifier_sites += cost.identifier_sites
            height = max(height, cost.height + 1)
        if node_count > max_nodes or byte_count > max_bytes:
            raise ValueError('SVG reuse expansion exceeds its node or byte budget')
        if height > MAX_EXPANSION_DEPTH:
            raise ValueError('SVG expanded depth exceeds 128 levels')
        active.remove(element)
        result = _ExpansionCost(node_count, byte_count, identifier_sites, height)
        cache[element] = result
        return result

    total = estimate(root)
    return total.nodes, total.bytes
