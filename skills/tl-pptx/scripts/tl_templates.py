"""Task-local template bundles using preserved PPT Master template algorithms.

The caller supplies bare filenames, never directories. This module returns
bytes and evidence; only the public TL runner may publish/archive those bytes.
Registration, application and preview require the independent TL release gate.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import gzip
import io
import json
from pathlib import Path
from tl_workspace import private_workspace

from tl_guard import ContractError, MAX_SLIDES, read_local
from tl_package_guard import MAX_ARCHIVE_BYTES, MAX_MEMBER_BYTES, MAX_EXPANDED_BYTES, PackageBudget, _safe_xml, inspect_package
from tl_roundtrip import BUNDLE_MANIFEST, _json, _member_path, extract_workspace, inspect_workspace_zip, pack_workspace
from tl_assetpacks import asset_attributions, preserve_asset_attributions

KINDS = frozenset({'brand', 'style', 'layout', 'deck'})


def _bundle(name):
    """Freeze a single explicit task-root bundle before inspecting content."""
    payload = read_local(name, '.zip', MAX_ARCHIVE_BYTES)
    bundle = inspect_workspace_zip(payload)
    inspect_payload_stores(bundle['entries'])
    return bundle


def inspect_payload_stores(entries):
    """Bound gzip expansion and decoded native stores before any hydration.

    This is a container preflight, not authorization to follow payload paths.
    The caller must still validate every reference against its exact roster.
    """
    expanded = 0
    count = 0
    xml_budget = PackageBudget()
    for name, content in entries.items():
        if not name.endswith('.gz'):
            continue
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
                raw = stream.read(MAX_MEMBER_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise ContractError('Invalid gzip workspace resource') from exc
        expanded += len(raw)
        count += 1
        if len(raw) > MAX_MEMBER_BYTES or expanded > MAX_EXPANDED_BYTES:
            raise ContractError('Native store decompression budget exceeded')
        if not name.endswith('.json.gz'):
            continue
        document = _json(raw)
        if not isinstance(document, dict):
            raise ContractError('Native store must be a JSON object')
        payloads = document.get('payloads', {})
        if not isinstance(payloads, dict):
            raise ContractError('Native store payloads must be an object')
        for encoded in payloads.values():
            if not isinstance(encoded, str):
                raise ContractError('Native store payload must be base64 text')
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise ContractError('Invalid native store payload encoding') from exc
            expanded += len(decoded)
            if len(decoded) > MAX_MEMBER_BYTES or expanded > MAX_EXPANDED_BYTES:
                raise ContractError('Decoded native store budget exceeded')
            if decoded.lstrip().startswith(b'<') or decoded.startswith((b'\xff\xfe', b'\xfe\xff')):
                _safe_xml(decoded, xml_budget)
            else:
                _json(decoded)
    return {'gzip_resources': count, 'expanded_store_bytes': expanded}


def _template_views(bundle):
    """Resolve explicit selected template roots without executing their metadata."""
    selection = bundle['metadata'].get('asset_selection')
    if selection is None:
        return [bundle]
    if not isinstance(selection, dict) or selection.get('schema') != 'tl-pptx.asset-selection.v1':
        raise ContractError('Invalid asset selection template metadata')
    roots = selection.get('template_roots')
    if (not isinstance(roots, list) or not 1 <= len(roots) <= 4
            or not all(isinstance(value, str) for value in roots) or len(set(roots)) != len(roots)):
        raise ContractError('A template selection must identify one to four distinct roots')
    views = []
    for root in roots:
        parts = _member_path(root).parts
        if len(parts) != 3 or parts[0] != 'template-workspaces' or parts[1] not in {'brands', 'styles', 'layouts', 'decks'}:
            raise ContractError('Selected template root is outside the fixed template namespace')
        prefix = root + '/'
        entries = {name[len(prefix):]: content for name, content in bundle['entries'].items() if name.startswith(prefix)}
        if 'templates/design_spec.md' not in entries:
            raise ContractError('Selected template root lacks its required specification')
        if parts[1] != 'styles':
            for name, content in bundle['entries'].items():
                if name.startswith(('icons/', 'sounds/')):
                    if name in entries and entries[name] != content:
                        raise ContractError('Selected shared asset conflicts with a template-owned asset')
                    entries[name] = content
        metadata = dict(bundle['metadata'])
        metadata['kind'] = 'template-workspace'
        views.append({**bundle, 'entries': entries, 'metadata': metadata})
    return views


def _single_template(bundle):
    views = _template_views(bundle)
    if len(views) != 1:
        raise ContractError('Creation/registration/preview requires exactly one selected template root')
    return views[0]


def _report(payload, operation, **extra):
    return {'operation': operation, 'sha256': hashlib.sha256(payload).hexdigest(),
            'byte_count': len(payload), 'structural_validation': 'passed',
            'visual_validation': 'not_run', 'publication': 'not_started', **extra}


def _integrity_gate():
    """Apply the independent release gate before protected template actions."""
    from attribution_guard import require_skill_integrity
    try:
        require_skill_integrity()
    except SystemExit as exc:
        raise ContractError(f'Template integrity gate did not pass (exit {exc.code})') from exc


def _quality_gate(root):
    """Invoke the same full template CLI gate, including integrity validation."""
    from apply_template import _load_root, _validate_root
    record = _load_root(str(root), root.parent / 'project-placeholder')
    _validate_root(record)
    return {'template_quality': 'passed'}


def _project_authoring(workspace, source_name, output_name, projection_kind, id_prefix, reuse=None):
    """Preserved template-import projection/extraction sequence on private data."""
    from extract_svg_assets import extract_directory
    from svg_authoring_view import project_svg_batch
    source = workspace / source_name
    output = workspace / output_name
    sources = sorted(source.rglob('*.svg'))
    if not sources:
        raise ContractError('Template conversion produced no SVG pages')
    mapping = [(path, output / path.relative_to(source)) for path in sources]
    reports = project_svg_batch(
        mapping, source, output, force=False, projection_kind=projection_kind,
        source_proxy_dir=workspace / 'images/source-object-previews',
    )
    extract_directory(
        output, workspace / 'icons', 'imported', min_decoration_bytes=3000,
        inplace=True, id_prefix=id_prefix,
        inventory_path=workspace / f'{output_name}_vector_asset_inventory.json',
        reuse_inventory_path=reuse,
    )
    return len(reports)


def import_template(name, *, inheritance_mode='both', strict=False):
    """PPTX -> portable source/structure/layered/flat authoring bundle.

    This prepares reference facts and authoring material, not a fabricated
    Brand/Style/Layout/Deck specification. Creation remains an explicit later
    authoring-and-validation operation.
    """
    if inheritance_mode not in {'both', 'layered', 'flat'} or not isinstance(strict, bool):
        raise ContractError('Invalid template import option')
    source = read_local(name, '.pptx', MAX_ARCHIVE_BYTES)
    inspection = inspect_package(source)
    slide_count = inspection.get('slide_count')
    if not isinstance(slide_count, int) or not 1 <= slide_count <= MAX_SLIDES:
        raise ContractError('Template source must contain one to fifty slides before conversion')
    from pptx_to_svg import convert_pptx_to_svg
    from pptx_to_svg.converter import ConvertOptions
    from template_import.manifest import build_manifest
    from template_import.native_structure import write_native_structure_bundle
    from pptx_workspace import AUTHORING_SVG_DIR, AUTHORING_SVG_FLAT_DIR
    with private_workspace(prefix='tl-pptx-template-') as temporary:
        private = Path(temporary).resolve()
        original = private / 'source.pptx'
        original.write_bytes(source)
        workspace = private / 'workspace'
        workspace.mkdir()
        manifest = build_manifest(original, workspace, include_flat_svg=inheritance_mode == 'both')
        manifest['source']['pptx'] = 'sources/source.pptx'
        analysis = workspace / 'analysis'
        analysis.mkdir(exist_ok=True)
        (analysis / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        contract = write_native_structure_bundle(original, workspace, manifest)
        result = convert_pptx_to_svg(
            original, workspace,
            ConvertOptions(images_subdir='images', embed_images=False, keep_hidden=False,
                           inheritance_mode=inheritance_mode, strict=strict,
                           asset_name_map=manifest.get('images', {}).get('imageMap', {})),
        )
        layered_count = _project_authoring(
            workspace, 'svg', AUTHORING_SVG_DIR.name,
            'flat' if inheritance_mode == 'flat' else 'layered', 'layered',
        )
        flat_count = 0
        if inheritance_mode == 'both':
            flat_count = _project_authoring(
                workspace, 'svg-flat', AUTHORING_SVG_FLAT_DIR.name, 'flat', 'flat',
                workspace / f'{AUTHORING_SVG_DIR.name}_vector_asset_inventory.json',
            )
        payload = pack_workspace(workspace, source_sha256=hashlib.sha256(source).hexdigest(), kind='template-source')
        report = _report(payload, 'template-import', slide_count=len(result.slides),
                         master_count=len(manifest['masters']), layout_count=len(manifest['layouts']),
                         authoring_files=layered_count, flat_authoring_files=flat_count,
                         source_sha256=hashlib.sha256(source).hexdigest(),
                         preservation_eligible=contract['strategy']['preservationEligible'],
                         diagnostics_count=len(result.diagnostics),
                         source_part_count=inspection['part_count'],
                         template_created=False)
    return payload, report


def _guard_svg_resources(entries):
    """Return validated, materialized snapshots without changing uploaded bytes."""
    from tl_svg_resources import materialize_svg_resources
    snapshots = dict(entries)
    budget = {}
    for member in entries:
        if member.lower().endswith('.svg'):
            snapshots[member] = materialize_svg_resources(member, entries, budget)
    themes = [(name, payload) for name, payload in entries.items() if name.endswith('/source_themes.json')]
    for _name, payload in themes:
        document = json.loads(payload)
        for record in document.get('masters', {}).values():
            try:
                raw = base64.b64decode(record['payload'], validate=True)
            except (KeyError, TypeError, ValueError) as exc:
                raise ContractError('Invalid encoded template source theme') from exc
            _safe_xml(raw, PackageBudget())
    return snapshots


def register_template_bundle(name, *, kind, template_id):
    """Validate and register one authored template in a private output library.

    Four upstream kinds are preserved. No user's installed library is changed.
    The output ZIP contains the authored workspace and its registration record.
    """
    _integrity_gate()
    from register_template import _extract_entry, _write_index, is_safe_template_id
    if kind not in KINDS or not is_safe_template_id(template_id):
        raise ContractError('Expected a valid template kind and single-segment identifier')
    bundle = _single_template(_bundle(name))
    bundle['entries'] = _guard_svg_resources(bundle['entries'])
    with private_workspace(prefix='tl-pptx-register-') as temporary:
        root = Path(temporary).resolve() / 'workspace'
        extract_workspace({key: value for key, value in bundle['entries'].items() if key != BUNDLE_MANIFEST}, root)
        evidence = _quality_gate(root)
        entry = _extract_entry(kind, template_id, root)
        registration = root / 'tl-template-registration.json'
        index_path = root / 'tl-template-index.json'
        if registration.exists() or index_path.exists():
            raise ContractError('Bundle already contains a registration record')
        _write_index(index_path, OrderedDict([(template_id, entry['entry'])]), dry_run=False)
        registration.write_text(json.dumps({'schema': 'tl-pptx.template-registration.v1',
                                           'kind': kind, 'id': template_id, **entry}, ensure_ascii=False, indent=2), encoding='utf-8')
        payload = pack_workspace(root, source_sha256=bundle['metadata'].get('source_sha256', ''), kind='registered-template')
        payload = preserve_asset_attributions(payload, [bundle['metadata']])
    return payload, _report(payload, 'template-register', template_kind=kind, template_id=template_id, **evidence)


def create_template(name, *, kind, template_id):
    """Finish explicit authored template creation through the same full gate."""
    payload, report = register_template_bundle(name, kind=kind, template_id=template_id)
    report['operation'] = 'template-create'
    return payload, report


def apply_template_bundles(project_name, template_names):
    """Apply explicit template bundles to a private project copy, never in place."""
    _integrity_gate()
    from apply_template import apply_templates
    if not isinstance(template_names, (tuple, list)) or not 1 <= len(template_names) <= 4:
        raise ContractError('Apply one to four explicit template bundle filenames')
    if len(set(template_names)) != len(template_names):
        raise ContractError('Duplicate template bundle filename')
    project = _bundle(project_name)
    templates = []
    expanded = project['expanded_bytes']
    for name in template_names:
        bundle = _bundle(name)
        expanded += bundle['expanded_bytes']
        if expanded > MAX_EXPANDED_BYTES:
            raise ContractError('Combined template bundle expansion budget exceeded')
        templates.extend(_template_views(bundle))
        if len(templates) > 4:
            raise ContractError('At most four template workspaces may contribute to one application')
    for bundle in [project, *templates]:
        bundle['entries'] = _guard_svg_resources(bundle['entries'])
    with private_workspace(prefix='tl-pptx-apply-') as temporary:
        private = Path(temporary).resolve()
        project_root = private / 'project'
        extract_workspace(project['entries'], project_root)
        roots = []
        for index, bundle in enumerate(templates):
            root = private / f'template-{index}'
            extract_workspace(bundle['entries'], root)
            # The transport identity is not part of upstream Style content.
            (root / BUNDLE_MANIFEST).unlink(missing_ok=True)
            (root / 'tl-template-registration.json').unlink(missing_ok=True)
            (root / 'tl-template-index.json').unlink(missing_ok=True)
            roots.append(str(root))
        # Retain validation=True: integrity and the complete original quality CLI.
        plan = apply_templates(project_root, roots, validate=True)
        payload = pack_workspace(project_root, source_sha256=project['metadata'].get('source_sha256', ''), kind='project-workspace')
        payload = preserve_asset_attributions(payload, [item['metadata'] for item in [project, *templates]])
        report = _report(payload, 'template-apply', installed_specs=plan.installed_specs,
                         template_count=len(roots), template_quality='passed')
    return payload, report


def preview_template(name, *, native_objects=True):
    """Create and inspect a review PPTX without exposing directory/output paths."""
    _integrity_gate()
    from template_preview_pptx import main as preview_main
    if not isinstance(native_objects, bool):
        raise ContractError('native_objects must be a boolean')
    bundle = _single_template(_bundle(name))
    bundle['entries'] = _guard_svg_resources(bundle['entries'])
    with private_workspace(prefix='tl-pptx-preview-') as temporary:
        private = Path(temporary).resolve()
        root = private / 'workspace'
        extract_workspace({key: value for key, value in bundle['entries'].items()
                           if key not in {BUNDLE_MANIFEST, 'tl-template-registration.json', 'tl-template-index.json'}}, root)
        _quality_gate(root)
        output = private / 'review.pptx'
        arguments = [str(root), '--output', str(output)]
        if native_objects:
            arguments.append('--native-charts-and-tables')
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = preview_main(arguments)
        if code != 0 or not output.is_file() or output.is_symlink():
            raise ContractError('Template preview did not produce a verified PPTX')
        if output.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ContractError('Template preview byte budget exceeded')
        payload = output.read_bytes()
        evidence = inspect_package(payload)
        evidence.pop('entries')
    return payload, _report(payload, 'template-preview', template_quality='passed',
                            asset_attributions=asset_attributions([bundle['metadata']]), **evidence)
