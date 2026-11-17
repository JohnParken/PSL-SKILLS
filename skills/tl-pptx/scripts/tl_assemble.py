"""Memory-only native PPTX assembly for the vendored PPT Master converter.

Adapted package assembly sequence from hugohe3/ppt-master, MIT, Copyright
(c) 2025-2026 Hugo He, commit 7e0df6705de0cd24345210d31f07fcb5a32b6900,
svg_to_pptx/pptx_package/builder.py. This is a new-deck assembler, not the
upstream template/roundtrip, motion, narration or master-promotion builder.
The outer runner owns input validation, private SVG staging and publication.
No output or temporary files are created here. Never reload and save the
assembled package through python-pptx: doing so can lose native OOXML parts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import math
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from pptx import Presentation

CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
OFFICE_REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PML_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main'
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_PART_BYTES = 32 * 1024 * 1024
MEDIA_TYPES = {
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'webp': 'image/webp', 'svg': 'image/svg+xml',
    'bmp': 'image/bmp', 'emf': 'image/x-emf', 'wmf': 'image/x-wmf',
    'tif': 'image/tiff', 'tiff': 'image/tiff',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}


def _xml(payload):
    """Read generated XML without accepting entity declarations."""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    if len(payload) > MAX_PART_BYTES or re.search(br'<!\s*(DOCTYPE|ENTITY)\b', payload, re.I):
        raise ValueError('Unsafe or oversized generated XML')
    return ET.fromstring(payload)


def _xml_bytes(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _part_name(name):
    """Require a canonical internal ZIP member, never a filesystem path."""
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))
            or any(ord(char) < 32 for char in name) or '%' in name or '#' in name or '?' in name):
        raise ValueError('Invalid generated package part name')
    return name


def _source_for_rels(name):
    if name == '_rels/.rels':
        return ''
    parent, basename = posixpath.split(name)
    if posixpath.basename(parent) != '_rels' or not basename.endswith('.rels'):
        raise ValueError('Invalid relationships part location')
    return posixpath.join(posixpath.dirname(parent), basename[:-5])


def _resolve_target(source, target):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or '\\' in target:
        raise ValueError('Invalid internal relationship target')
    decoded = unquote(parsed.path)
    if not decoded or '%' in decoded or '\\' in decoded:
        raise ValueError('Invalid internal relationship target')
    result = posixpath.normpath(decoded.lstrip('/') if decoded.startswith('/')
                               else posixpath.join(posixpath.dirname(source), decoded))
    return _part_name(result)


def _content_types(entries):
    root = _xml(entries['[Content_Types].xml'])
    if root.tag != f'{{{CT_NS}}}Types':
        raise ValueError('Invalid content-types root')
    defaults, overrides = {}, {}
    for element in root:
        if element.tag == f'{{{CT_NS}}}Default':
            key = element.get('Extension')
            destination = defaults
        elif element.tag == f'{{{CT_NS}}}Override':
            key = element.get('PartName', '').removeprefix('/')
            destination = overrides
        else:
            raise ValueError('Unexpected content-type element')
        value = element.get('ContentType')
        if not key or not value or key in destination:
            raise ValueError('Invalid or duplicate content-type declaration')
        destination[key] = value
    return root, defaults, overrides


def _add_content_types(entries, additions, extensions):
    root, defaults, overrides = _content_types(entries)
    for extension in sorted(extensions):
        content_type = MEDIA_TYPES.get(extension)
        if not content_type:
            raise ValueError(f'Undeclared media type: {extension}')
        if extension in defaults and defaults[extension] != content_type:
            raise ValueError('Conflicting default content type')
        if extension not in defaults:
            ET.SubElement(root, f'{{{CT_NS}}}Default', Extension=extension, ContentType=content_type)
    for name, content_type in sorted(additions.items()):
        name = _part_name(name.removeprefix('/'))
        if name in overrides and overrides[name] != content_type:
            raise ValueError('Conflicting content-type override')
        if name not in overrides:
            ET.SubElement(root, f'{{{CT_NS}}}Override', PartName='/' + name, ContentType=content_type)
    entries['[Content_Types].xml'] = _xml_bytes(root)


def validate_entries(entries, slide_count):
    """Verify package graph and XML references, not visual Office fidelity."""
    if len(entries) > 10000 or sum(map(len, entries.values())) > MAX_PACKAGE_BYTES:
        raise ValueError('Generated package resource limit exceeded')
    _, defaults, overrides = _content_types(entries)
    relationship_count = 0
    for name, payload in entries.items():
        _part_name(name)
        if len(payload) > MAX_PART_BYTES:
            raise ValueError('Generated part resource limit exceeded')
        if name != '[Content_Types].xml' and name not in overrides and name.rsplit('.', 1)[-1] not in defaults:
            raise ValueError('Part has no content type')
        if not name.endswith(('.xml', '.rels')):
            continue
        root = _xml(payload)
        if not name.endswith('.rels'):
            continue
        source = _source_for_rels(name)
        if source and source not in entries:
            raise ValueError('Relationships source is missing')
        if root.tag != f'{{{REL_NS}}}Relationships':
            raise ValueError('Invalid relationships root')
        ids = set()
        for relation in root:
            rel_id = relation.get('Id')
            target = relation.get('Target')
            mode = relation.get('TargetMode', 'Internal')
            if (relation.tag != f'{{{REL_NS}}}Relationship' or not rel_id or rel_id in ids
                    or not target or not relation.get('Type') or mode not in ('Internal', 'External')):
                raise ValueError('Invalid or duplicate relationship')
            ids.add(rel_id)
            relationship_count += 1
            if mode == 'External':
                from hyperlink_contract import parse_hyperlink_target
                if (relation.get('Type') != OFFICE_REL_NS + '/hyperlink'
                        or parse_hyperlink_target(target).kind != 'external'):
                    raise ValueError('Only passive upstream-supported hyperlink relationships are allowed')
            elif _resolve_target(source, target) not in entries:
                raise ValueError('Dangling internal relationship')
        if source and source.endswith('.xml'):
            source_root = _xml(entries[source])
            for element in source_root.iter():
                for key, value in element.attrib.items():
                    if key.startswith('{' + OFFICE_REL_NS + '}') and value not in ids:
                        raise ValueError('XML references a missing relationship ID')
    for name in overrides:
        if name not in entries:
            raise ValueError('Content type names a missing part')
    actual = len(_xml(entries['ppt/presentation.xml']).findall(f'{{{PML_NS}}}sldIdLst/{{{PML_NS}}}sldId'))
    if actual != slide_count:
        raise ValueError('Presentation slide roster mismatch')
    return {'status': 'verified', 'slide_count': actual, 'part_count': len(entries),
            'relationship_count': relationship_count, 'visual_validation': 'not_run'}


def _base_package(slide_count, size, notes):
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = size
    for index in range(slide_count):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        if notes and notes[index]:
            slide.notes_slide.notes_text_frame.text = notes[index]
    presentation.core_properties.author = ''
    presentation.core_properties.last_modified_by = ''
    presentation.core_properties.title = ''
    presentation.core_properties.created = datetime.now(timezone.utc)
    presentation.core_properties.modified = presentation.core_properties.created
    buffer = io.BytesIO()
    presentation.save(buffer)
    with zipfile.ZipFile(buffer) as package:
        entries = {name: package.read(name) for name in package.namelist()}
    root = _xml(entries['ppt/presentation.xml'])
    size_element = root.find(f'{{{PML_NS}}}sldSz')
    size_type = 'custom'
    for width, height, label in ((4, 3, 'screen4x3'), (16, 9, 'screen16x9'), (16, 10, 'screen16x10')):
        if size[0] * height == size[1] * width:
            size_type = label
            break
    size_element.set('type', size_type)
    entries['ppt/presentation.xml'] = _xml_bytes(root)
    return entries


def _merge_slide(entries, index, conversion, additions, extensions):
    slide_xml, media, relationships, _animation_targets, package_parts, content_types = conversion
    slide_name = f'ppt/slides/slide{index}.xml'
    rels_name = f'ppt/slides/_rels/slide{index}.xml.rels'
    old_rels = _xml(entries[rels_name])
    layout = [item for item in old_rels if item.get('Type') == OFFICE_REL_NS + '/slideLayout']
    if len(layout) != 1:
        raise ValueError('Base slide must have exactly one layout')
    rels = ET.Element(f'{{{REL_NS}}}Relationships')
    layout[0].set('Id', 'rId1')
    rels.append(layout[0])
    media_names = {}
    for name, payload in media.items():
        _part_name(name)
        if '/' in name or not isinstance(payload, bytes):
            raise ValueError('Invalid generated media')
        extension = name.rsplit('.', 1)[-1].lower()
        if extension not in MEDIA_TYPES or extension == 'xlsx':
            raise ValueError('Unsupported generated media type')
        member = f'ppt/media/image_{hashlib.sha256(payload).hexdigest()}.{extension}'
        if member in entries and entries[member] != payload:
            raise ValueError('Media hash collision')
        entries[member] = payload
        media_names[name] = '../media/' + posixpath.basename(member)
        extensions.add(extension)
    for relation in relationships:
        attributes = {'Id': relation['id'], 'Type': relation['type'], 'Target': relation['target']}
        if attributes['Target'].startswith('../media/'):
            original = attributes['Target'][len('../media/'):]
            if original not in media_names:
                raise ValueError('Missing generated media payload')
            attributes['Target'] = media_names[original]
        if relation.get('target_mode') is not None:
            attributes['TargetMode'] = relation['target_mode']
        ET.SubElement(rels, f'{{{REL_NS}}}Relationship', attributes)
    ids = {element.get('Id') for element in rels}
    for old in old_rels:
        if old is layout[0]:
            continue
        if old.get('Type') != OFFICE_REL_NS + '/notesSlide':
            raise ValueError('Unexpected base slide relationship')
        counter = 2
        while f'rId{counter}' in ids:
            counter += 1
        old.set('Id', f'rId{counter}')
        ids.add(f'rId{counter}')
        rels.append(old)
    for name, payload in package_parts.items():
        _part_name(name)
        if not isinstance(payload, bytes):
            raise ValueError('Invalid generated package payload')
        if name in entries and entries[name] != payload:
            raise ValueError('Generated package part collision')
        entries[name] = payload
        if name.endswith('.xlsx'):
            extensions.add('xlsx')
    for name, value in content_types.items():
        key = name.removeprefix('/')
        if key in additions and additions[key] != value:
            raise ValueError('Conflicting generated content type')
        additions[key] = value
    entries[slide_name] = slide_xml.encode('utf-8')
    entries[rels_name] = _xml_bytes(rels)


def assemble_pptx(svg_files, *, resource_root, notes=None, native_objects=True,
                  text_flow='preserve', converter=None, registry_factory=None):
    """Return (PPTX bytes, structural report) from caller-validated private SVGs.

    All SVGs must share a zero-origin viewBox. Notes are plain text in slide
    order. Full native converter behavior is retained; animation targets are
    deliberately not emitted as motion. No template import/edit is performed.
    Dependency injection is for deterministic adapter testing, not a CLI hook.
    """
    if not svg_files or len(svg_files) > 100:
        raise ValueError('Expected 1 to 100 SVG pages')
    if notes is not None and (len(notes) != len(svg_files) or any(not isinstance(n, str) for n in notes)):
        raise ValueError('Notes must be a plain-text string per slide')
    canvases = []
    for path in svg_files:
        root = _xml(Path(path).read_bytes())
        values = root.get('viewBox', '').split()
        if len(values) != 4:
            raise ValueError('SVG needs a four-value viewBox')
        canvas = tuple(float(value) for value in values)
        if (root.tag != '{http://www.w3.org/2000/svg}svg' or canvas[:2] != (0, 0)
                or any(not math.isfinite(value) for value in canvas) or not all(1 <= value <= 10000 for value in canvas[2:])):
            raise ValueError('Invalid SVG canvas')
        canvases.append(canvas)
    if any(canvas != canvases[0] for canvas in canvases):
        raise ValueError('SVG canvas mismatch')
    size = tuple(round(value * 9525) for value in canvases[0][2:])
    entries = _base_package(len(svg_files), size, notes)
    if converter is None:
        from svg_to_pptx.drawingml.converter import convert_svg_to_slide_shapes
        converter = convert_svg_to_slide_shapes
    if registry_factory is None:
        from pptx_ooxml.package import PackagePartRegistry
        registry_factory = PackagePartRegistry
    _, _, base_overrides = _content_types(entries)
    registry = registry_factory(content_types=base_overrides)
    registry.reserve_source(entries, 'new presentation base')
    additions, extensions, trace = {}, set(), []
    for index, path in enumerate(svg_files, 1):
        converted = converter(Path(path), slide_num=index, slide_count=len(svg_files),
                              resource_root=Path(resource_root), verbose=False, native_objects=native_objects,
                              package_parts=registry, text_flow=text_flow, trace_out=trace,
                              image_optimize=False, dangerous_nonconforming_export=False)
        _merge_slide(entries, index, converted, additions, extensions)
    _add_content_types(entries, additions, extensions)
    report = validate_entries(entries, len(svg_files))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as package:
        for name, payload in sorted(entries.items()):
            package.writestr(name, payload)
    report['conversion_trace'] = trace
    report['bytes'] = buffer.tell()
    return buffer.getvalue(), report
