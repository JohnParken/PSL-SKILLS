"""Bounded native import and roundtrip workspaces, never arbitrary directory CLI.

Every consumed source is an immutable no-follow snapshot. Workspace archives are
validated before any write; original PPTX is reimported into a private task-root
baseline rather than trusting uploaded manifests or backing paths.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import unicodedata
import stat
from tl_workspace import private_workspace
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from tl_build import _constant_error, _unique_object
from tl_guard import ContractError, MAX_INPUT_BYTES, MAX_SLIDES, REFERENCE_ATTRIBUTES, bare_name, read_local, validate_svg
from tl_package_guard import (MAX_ARCHIVE_BYTES, MAX_EXPANDED_BYTES, MAX_MEMBER_BYTES,
                              MAX_MEMBERS, PackageBudget, _safe_xml, inspect_package, is_xml_part)

BUNDLE_MANIFEST = 'tl-workspace.json'
BUNDLE_SCHEMA = 'tl-pptx.workspace.v1'


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _json(payload):
    if len(payload) > MAX_MEMBER_BYTES:
        raise ContractError('Workspace JSON byte budget exceeded')
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_constant_error)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise ContractError('Invalid workspace JSON') from exc
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 200000 or depth > 60:
            raise ContractError('Workspace JSON complexity budget exceeded')
        if isinstance(item, float) and not math.isfinite(item):
            raise ContractError('Non-finite workspace JSON number')
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def _member_path(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or '%' in name or name.startswith('/') or len(name.encode()) > 1024
            or any(ord(char) < 32 for char in name)
            or any(part in {'', '.', '..'} for part in name.split('/'))):
        raise ContractError('Unsafe workspace member name')
    return PurePosixPath(name)


def inspect_workspace_zip(payload):
    """Return bounded regular-file entries and checked manifest; never extract."""
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ContractError('Workspace archive byte budget exceeded')
    entries = {}
    equivalents = set()
    file_keys = set()
    directory_keys = set()
    budget = PackageBudget()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                name = info.filename[:-1] if info.is_dir() else info.filename
                _member_path(name)
                if info.orig_filename != info.filename:
                    raise ContractError('Truncated workspace member name')
                mode = info.external_attr >> 16
                if (info.flag_bits & 1 or stat.S_ISLNK(mode)
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or (info.is_dir() and (info.file_size or stat.S_IFMT(mode) == stat.S_IFREG))
                        or (not info.is_dir() and stat.S_IFMT(mode) == stat.S_IFDIR)):
                    raise ContractError('Workspace must contain only unencrypted regular files')
                key = unicodedata.normalize('NFC', name).casefold()
                if key in equivalents:
                    raise ContractError('Duplicate or case-equivalent workspace member')
                equivalents.add(key)
                ancestors = {'/'.join(key.split('/')[:index]) for index in range(1, len(key.split('/')))}
                if ancestors & file_keys or (not info.is_dir() and key in directory_keys):
                    raise ContractError('Workspace file/directory collision')
                directory_keys.update(ancestors)
                (directory_keys if info.is_dir() else file_keys).add(key)
                budget.members += 1
                budget.expanded += info.file_size
                if (budget.members > MAX_MEMBERS or info.file_size > MAX_MEMBER_BYTES
                        or budget.expanded > MAX_EXPANDED_BYTES):
                    raise ContractError('Workspace expanded byte/member budget exceeded')
                if info.is_dir():
                    continue
                with archive.open(info) as stream:
                    content = stream.read(MAX_MEMBER_BYTES + 1)
                if len(content) != info.file_size:
                    raise ContractError('Workspace member length mismatch')
                if is_xml_part(info.filename, content):
                    _safe_xml(content, budget)
                if info.filename.lower().endswith('.json'):
                    _json(content)
                if content.startswith(b'PK\x03\x04'):
                    inspect_package(content, budget=budget, require_presentation=info.filename.endswith('.pptx'))
                entries[info.filename] = content
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise ContractError('Invalid workspace ZIP') from exc
    if any(name.lower().endswith('.gz') for name in entries):
        from tl_templates import inspect_payload_stores
        stores = inspect_payload_stores(entries)
        if budget.expanded + stores['expanded_store_bytes'] > MAX_EXPANDED_BYTES:
            raise ContractError('Combined workspace compressed-store budget exceeded')
    if BUNDLE_MANIFEST not in entries:
        raise ContractError('Workspace identity manifest missing')
    metadata = _json(entries[BUNDLE_MANIFEST])
    if not isinstance(metadata, dict) or metadata.get('schema') != BUNDLE_SCHEMA:
        raise ContractError('Unsupported workspace identity manifest')
    hashes = metadata.get('files')
    if not isinstance(hashes, dict) or set(hashes) != set(entries) - {BUNDLE_MANIFEST}:
        raise ContractError('Workspace file roster mismatch')
    for name, expected in hashes.items():
        if not isinstance(expected, str) or expected != _digest(entries[name]):
            raise ContractError('Workspace file hash mismatch')
    return {'entries': entries, 'metadata': metadata, 'part_count': len(entries),
            'expanded_bytes': budget.expanded, 'structural_validation': 'passed'}


def extract_workspace(entries, root):
    """Write verified snapshots only into a new, empty private workspace."""
    root = Path(root)
    if root.is_symlink() or (root.exists() and any(root.iterdir())):
        raise ContractError('Extraction root must be new and empty')
    root.mkdir(parents=True, exist_ok=True)
    for name, data in entries.items():
        relative = _member_path(name)
        path = root.joinpath(*relative.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)


def pack_workspace(root, *, source_sha256='', kind='native-roundtrip'):
    """Serialize an owned workspace, never links; return validated ZIP bytes."""
    root = Path(root)
    entries = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ContractError('Workspace symlinks are forbidden')
        if path.is_file():
            name = path.relative_to(root).as_posix()
            _member_path(name)
            if name == BUNDLE_MANIFEST:
                continue
            if path.stat().st_size > MAX_MEMBER_BYTES:
                raise ContractError('Workspace file too large')
            entries[name] = path.read_bytes()
            if len(entries) > MAX_MEMBERS or sum(map(len, entries.values())) > MAX_EXPANDED_BYTES:
                raise ContractError('Workspace aggregate byte/member budget exceeded')
    metadata = {'schema': BUNDLE_SCHEMA, 'kind': kind, 'source_sha256': source_sha256,
                'files': {name: _digest(data) for name, data in entries.items()}}
    entries[BUNDLE_MANIFEST] = json.dumps(metadata, sort_keys=True).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    payload = output.getvalue()
    inspect_workspace_zip(payload)
    return payload


def _source_snapshot(name):
    payload = read_local(name, '.pptx', MAX_ARCHIVE_BYTES)
    evidence = inspect_package(payload)
    _check_slide_count(evidence)
    evidence.pop('entries')
    evidence['source_sha256'] = _digest(payload)
    return payload, evidence


def _check_slide_count(evidence):
    """Apply declared presentation size before any semantic conversion."""
    count = evidence.get('slide_count')
    if not isinstance(count, int) or not 1 <= count <= MAX_SLIDES:
        raise ContractError('Presentation must contain one to fifty slides')


def _import_private(payload, root):
    _check_slide_count(inspect_package(payload))
    from pptx_to_svg.converter import ConvertOptions, convert_pptx_to_svg
    source = root / 'input.pptx'
    source.write_bytes(payload)
    workspace = root / 'workspace'
    result = convert_pptx_to_svg(source, workspace, ConvertOptions(roundtrip=True))
    if not 1 <= len(result.slides) <= MAX_SLIDES:
        raise ContractError('Presentation must contain one to fifty slides')
    return workspace, result


def analyze_pptx(name):
    """Return read-only JSON bytes and evidence for one task-local PPTX."""
    from dataclasses import asdict
    from pptx_to_svg.converter import ConvertOptions, convert_pptx_to_svg
    payload, evidence = _source_snapshot(name)
    with private_workspace(prefix='tl-pptx-analysis-') as directory:
        source = Path(directory).resolve() / 'source.pptx'
        source.write_bytes(payload)
        result = convert_pptx_to_svg(source, options=ConvertOptions())
        if not 1 <= len(result.slides) <= MAX_SLIDES:
            raise ContractError('Presentation must contain one to fifty slides')
        from pptx_ooxml.analyzer import analyze_pptx as analyze_native_content
        content = analyze_native_content(source)
        report = {'content': content, 'source_sha256': evidence['source_sha256'], 'slide_count': len(result.slides),
                  'canvas_px': result.canvas_px, 'theme_colors': result.theme_colors,
                  'theme_fonts': result.theme_fonts,
                  'notes': [asdict(note) for note in result.speaker_notes],
                  'diagnostics': [asdict(item) for item in result.diagnostics]}
        encoded = json.dumps(report, ensure_ascii=False, default=str).encode()
        if len(encoded) > MAX_MEMBER_BYTES:
            raise ContractError('Analysis output byte budget exceeded')
    return encoded, evidence


def import_workspace(name):
    """Return portable editable workspace ZIP; publication belongs to caller."""
    payload, evidence = _source_snapshot(name)
    with private_workspace(prefix='tl-pptx-import-') as directory:
        workspace, result = _import_private(payload, Path(directory).resolve())
        output = pack_workspace(workspace, source_sha256=evidence['source_sha256'])
        evidence['slide_count'] = len(result.slides)
        evidence['authoring_files'] = sorted(path.name for path in (workspace / 'authoring-svg-flat').glob('*.svg'))
    return output, evidence


def _validate_edit(data, destination, workspace, budget=None):
    """Validate editable SVG, granting only regenerated local resource access.

    Local resources are checked against the private regenerated baseline. Native
    reference attributes are allowed only when already present in that baseline;
    the validation copy removes their indirection, not the authored document.
    """
    from lxml import etree
    if len(data) > MAX_INPUT_BYTES:
        raise ContractError('Edited SVG byte budget exceeded')
    budget = {} if budget is None else budget
    tree = _safe_xml(data, PackageBudget())
    from tl_roundtrip_refs import preflight_native_references
    preflight_native_references(tree, workspace, budget)
    baseline = _safe_xml(destination.read_bytes(), PackageBudget())
    allowed_refs = {(key, value) for element in baseline.iter()
                    for key, value in element.attrib.items() if etree.QName(key).localname in REFERENCE_ATTRIBUTES}
    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue
        tag = etree.QName(element).localname
        for key, value in list(element.attrib.items()):
            attribute = etree.QName(key).localname
            if attribute in REFERENCE_ATTRIBUTES:
                if (key, value) not in allowed_refs:
                    raise ContractError('Unregistered native reference in edited SVG')
                del element.attrib[key]
            elif attribute in {'href', 'src', 'poster'} and not value.startswith(('#', 'data:')) and tag != 'a':
                parsed = urlsplit(value)
                if parsed.scheme or parsed.netloc or parsed.query or '\\' in value or '%' in value:
                    raise ContractError('External or ambiguous edited resource path')
                path = (destination.parent / unquote(parsed.path)).resolve()
                try:
                    path.relative_to(workspace.resolve())
                except ValueError as exc:
                    raise ContractError('Edited resource escapes private workspace') from exc
                if not path.is_file() or path.is_symlink():
                    raise ContractError('Edited resource is absent from regenerated workspace')
                element.set(key, '#validated-local-resource')
    validate_svg(etree.tostring(tree), budget=budget.setdefault('decoded', [0]))


def rebuild_workspace(zip_name, *, edits=None, page_plan=None, notes=None, animation_config=None):
    """Rebuild from verified source plus explicit task-local edit snapshots.

    Uploaded backing/configuration is not executed. All trusted backing and
    manifests are regenerated from the validated original PPTX, then only
    declared editable SVGs and bounded page-plan/notes/motion inputs are applied.
    """
    bundle = inspect_workspace_zip(read_local(zip_name, '.zip', MAX_ARCHIVE_BYTES))
    metadata, entries = bundle['metadata'], bundle['entries']
    if metadata.get('kind') != 'native-roundtrip':
        raise ContractError('Expected native-roundtrip workspace bundle')
    source = entries.get('sources/source.pptx')
    if source is None or _digest(source) != metadata.get('source_sha256'):
        raise ContractError('Workspace original source identity mismatch')
    inspect_package(source)
    edits = {} if edits is None else edits
    if not isinstance(edits, dict) or len(edits) > MAX_SLIDES:
        raise ContractError('Edits must be a bounded authoring-name to local-filename mapping')
    frozen_edits = {}
    for authoring_name, local_name in edits.items():
        bare_name(authoring_name, '.svg')
        frozen_edits[authoring_name] = read_local(local_name, '.svg')
    plan = _json(read_local(page_plan, '.json', 2 * 1024 * 1024)) if page_plan else None
    if plan is not None:
        pages = plan.get('pages') if isinstance(plan, dict) else None
        if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_SLIDES:
            raise ContractError('Page plan requires one to fifty pages')
    # Re-encode to freeze caller-owned structures and reject non-finite values.
    overlays = _json(json.dumps({'notes': notes, 'animation_config': animation_config}, allow_nan=False).encode())
    with private_workspace(prefix='tl-pptx-roundtrip-') as directory:
        workspace, _result = _import_private(source, Path(directory).resolve())
        authoring = workspace / 'authoring-svg-flat'
        if plan is not None:
            manifest = _json((workspace / 'analysis/roundtrip_manifest.json').read_bytes())
            by_index = {row['index']: Path(row['layeredSvg']).name for row in manifest['slides']}
            used = set()
            for index, page in enumerate(plan['pages'], 1):
                if not isinstance(page, dict) or set(page) - {'source_slide', 'svg'}:
                    raise ContractError('Invalid page plan row')
                source_index = page.get('source_slide')
                if not isinstance(source_index, int) or isinstance(source_index, bool) or source_index not in by_index:
                    raise ContractError('Invalid source slide in page plan')
                original = by_index[source_index]
                name = page.get('svg', original)
                if name.casefold() in used and 'svg' not in page:
                    name = f'copy_{index:03d}_{original}'
                    page['svg'] = name
                bare_name(name, '.svg')
                if name.casefold() in used:
                    raise ContractError('Duplicate page plan output name')
                used.add(name.casefold())
                if not (authoring / name).exists():
                    (authoring / name).write_bytes((authoring / original).read_bytes())
        edit_budget = {}
        for name, data in frozen_edits.items():
            destination = authoring / name
            if not destination.is_file():
                raise ContractError('Edited SVG is not registered by the original source')
            _validate_edit(data, destination, workspace, edit_budget)
            destination.write_bytes(data)
        if plan is not None:
            (workspace / 'page_plan.json').write_text(json.dumps(plan), encoding='utf-8')
        from authoring_roundtrip import materialize_flat_authoring_roundtrip
        materialized = materialize_flat_authoring_roundtrip(workspace, authoring, workspace / 'materialized')
        from tl_roundtrip_build import build_roundtrip
        payload, evidence = build_roundtrip(workspace, materialized, **overlays)
        checked = inspect_package(payload)
        checked.pop('entries')
        evidence.update(checked)
        evidence.update({'source_sha256': _digest(source), 'sha256': _digest(payload)})
    return payload, evidence
