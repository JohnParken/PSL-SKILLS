"""Controlled Boolean SVG document adapter; never rewrites source geometry.

Uses the pinned upstream merge-shapes algorithms. Publication is the caller's
responsibility. Temporary snapshots live only under the confirmed task cwd.
"""
from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from tl_workspace import private_workspace
from xml.etree import ElementTree as ET

from tl_guard import ContractError, bare_name, read_local, validate_svg
from tl_svg_resources import materialize_svg_resources

FONT_SUFFIXES = {'.ttf', '.otf', '.ttc', '.otc'}
OPERATIONS = {'union', 'combine', 'fragment', 'intersect', 'subtract'}
SVG_NS = 'http://www.w3.org/2000/svg'


def _configuration(manifest):
    if not isinstance(manifest, dict) or set(manifest) - {'operation', 'source_ids', 'output_id', 'style', 'fonts'}:
        raise ContractError('Unknown Boolean manifest field')
    operation = manifest.get('operation')
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ContractError('Unknown Boolean operation')
    sources = manifest.get('source_ids')
    if (not isinstance(sources, list) or not 2 <= len(sources) <= 32
            or any(not isinstance(value, str) or not value or len(value) > 256 for value in sources)
            or len(set(sources)) != len(sources)):
        raise ContractError('Boolean requires two to thirty-two unique bounded source ids')
    if operation == 'fragment' and len(sources) > 8:
        raise ContractError('Fragment execution budget permits at most eight operands')
    output_id = manifest.get('output_id')
    if not isinstance(output_id, str) or not 1 <= len(output_id) <= 256:
        raise ContractError('Boolean output_id must be bounded text')
    style = manifest.get('style', {})
    if (not isinstance(style, dict) or len(style) > 16
            or any(not isinstance(key, str) or not isinstance(value, str) or len(value) > 256
                   for key, value in style.items())):
        raise ContractError('Boolean style must contain bounded text attributes')
    # Validate overrides as SVG before the upstream serializer handles them.
    probe = ET.Element('svg', {'xmlns': SVG_NS})
    ET.SubElement(probe, 'path', style)
    validate_svg(ET.tostring(probe))
    fonts = manifest.get('fonts', [])
    if not isinstance(fonts, list) or len(fonts) > 32 or any(not isinstance(name, str) for name in fonts):
        raise ContractError('Fonts must be a bounded list of explicit bare filenames')
    if len(set(fonts)) != len(fonts):
        raise ContractError('Duplicate font filename')
    return operation, sources, output_id, style, fonts


def _font_snapshots(names):
    snapshots = {}
    for name in names:
        suffix = Path(name).suffix.lower()
        if suffix not in FONT_SUFFIXES:
            raise ContractError('Only OpenType font snapshots are supported')
        bare_name(name, suffix)
        data = read_local(name, suffix, 16 * 1024 * 1024)
        if data[:4] not in {b'\x00\x01\x00\x00', b'OTTO', b'ttcf', b'true'}:
            raise ContractError('Invalid OpenType font signature')
        snapshots[name] = data
        if sum(map(len, snapshots.values())) > 64 * 1024 * 1024:
            raise ContractError('Combined font snapshots exceed sixty-four MiB')
    return snapshots


DEFINITION_TAGS = {'linearGradient', 'radialGradient', 'pattern', 'clipPath', 'mask', 'filter', 'marker', 'symbol'}
LOCAL_URL = re.compile(r'url\s*\(([^)]*)\)', re.IGNORECASE)


def _references(element):
    """Collect only validated local resource references, including xlink href."""
    for node in element.iter():
        for name, value in node.attrib.items():
            if name.rsplit('}', 1)[-1] == 'href' and value.startswith('#'):
                yield value[1:]
            for reference in LOCAL_URL.findall(value):
                normalized = reference.strip().strip("'\"")
                if not normalized.startswith('#'):
                    raise ContractError('Definition reference must be local')
                yield normalized[1:]


def _copy_definitions(source, fragments, result, budget):
    """Copy the exact referenced definition closure, never source drawing peers."""
    index = {}
    for node in source.iter():
        identifier = node.get('id')
        if identifier:
            if identifier in index:
                raise ContractError('Ambiguous duplicate SVG definition id')
            index[identifier] = node
    selected = {}
    visiting = set()

    def visit(identifier, depth):
        if depth > 80:
            raise ContractError('Definition reference depth budget exceeded')
        if identifier in visiting:
            raise ContractError('Cyclic SVG paint definition reference')
        if identifier in selected:
            return
        node = index.get(identifier)
        if node is None:
            raise ContractError('Unknown SVG paint definition id')
        if node.tag.rsplit('}', 1)[-1] not in DEFINITION_TAGS:
            raise ContractError('Paint dependency must identify a definition, not a source drawing')
        visiting.add(identifier)
        for dependency in _references(node):
            visit(dependency, depth + 1)
        visiting.remove(identifier)
        selected[identifier] = node

    for identifier in _references(fragments):
        visit(identifier, 0)
    if not selected:
        return
    # Nested selected definitions are copied once with their enclosing resource.
    descendants = {id(child) for node in selected.values() for child in node.iter() if child is not node}
    roots = [node for node in selected.values() if id(node) not in descendants]
    allocation_bytes = sum(len(ET.tostring(node, encoding='utf-8')) for node in roots)
    allocation_nodes = sum(sum(1 for _ in node.iter()) for node in roots) + 1
    budget['bytes'] = budget.get('bytes', 0) + allocation_bytes
    budget['nodes'] = budget.get('nodes', 0) + allocation_nodes
    if budget['bytes'] > 32 * 1024 * 1024 or budget['nodes'] > 20000:
        raise ContractError('SVG definition allocation budget exceeded')
    definitions = ET.SubElement(result, 'defs')
    for node in roots:
        cloned = copy.deepcopy(node)
        cloned.tail = None
        definitions.append(cloned)


def boolean_document(svg_name, manifest):
    """Return a standalone result SVG and evidence, without publishing files.

    The manifest contains operation, source_ids, output_id, optional style and
    optional fonts (bare task-root filenames). Fragment produces separately
    named sibling paths, not a union. No z-order placement is guessed.
    """
    from svg_to_pptx.shape_boolean import render_boolean_svg_fragments

    operation, sources, output_id, style, font_names = _configuration(manifest)
    bare_name(svg_name, '.svg')
    original = read_local(svg_name, '.svg')
    budget = {}
    svg = materialize_svg_resources(svg_name, {svg_name: original}, budget)
    validate_svg(svg)
    root = ET.fromstring(svg)
    text_characters = sum(len(node.text or '') + len(node.tail or '') for node in root.iter())
    if text_characters > 5000:
        raise ContractError('Boolean text execution budget exceeds five thousand characters')
    fonts = _font_snapshots(font_names)
    with private_workspace(prefix='tl-geometry-') as directory:
        workspace = Path(directory)
        source = workspace / 'source.svg'
        source.write_bytes(svg)
        font_dirs = []
        if 'fonts' in manifest:
            font_root = workspace / 'fonts'
            font_root.mkdir()
            for name, data in fonts.items():
                (font_root / name).write_bytes(data)
            # Even an explicitly empty roster disables implicit fallback.
            font_dirs = [font_root]
        fragment = render_boolean_svg_fragments(
            source, operation=operation, source_ids=sources, output_id=output_id,
            style=style, font_dirs=font_dirs,
        )
    # Only viewport attributes belong to the result: old transforms/opacity
    # would be applied twice and unrelated ids/resources must not be copied.
    result = ET.Element('svg', {'xmlns': SVG_NS})
    for name in ('viewBox', 'width', 'height', 'preserveAspectRatio'):
        if name in root.attrib:
            result.set(name, root.attrib[name])
    if len(fragment.encode('utf-8')) > 4 * 1024 * 1024:
        raise ContractError('Boolean result exceeds SVG byte budget')
    wrapped = ('<svg xmlns="' + SVG_NS + '">' + fragment + '</svg>').encode('utf-8')
    validate_svg(wrapped)
    fragments = ET.fromstring(wrapped)
    _copy_definitions(root, fragments, result, budget)
    result.extend(fragments)
    document = ET.tostring(result, encoding='utf-8', xml_declaration=True)
    validate_svg(document)
    evidence = {
        'operation': operation, 'source_ids': sources, 'source_sha256': hashlib.sha256(original).hexdigest(),
        'source_unchanged': True, 'result_ids': [node.get('id') for node in fragments],
        'coordinate_space': 'SVG-root', 'viewBox': root.get('viewBox'),
        'placement': 'Place at original operands z-order under final semantic parent; never under old transformed ancestor.',
        'fragment_order': 'top-to-bottom, left-to-right sibling paths' if operation == 'fragment' else None,
        'font_policy': 'explicit snapshots only' if 'fonts' in manifest else 'platform system font roots only; no home fonts',
        'fonts': [{'file': name, 'sha256': hashlib.sha256(data).hexdigest()} for name, data in fonts.items()],
        'resource_budget': budget,
        'execution_limits': {'text_characters': 5000, 'font_files': 32, 'font_bytes': 67108864,
                             'operands': 8 if operation == 'fragment' else 32},
    }
    return document, evidence
