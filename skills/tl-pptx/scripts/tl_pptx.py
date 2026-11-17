"""Standalone, bounded SVG-to-native-PPTX entry point."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from xml.parsers import expat

SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))

from tl_guard import (ContractError, MAX_SLIDES, MAX_TOTAL_BYTES, bare_name,
                      publish_exclusive, read_local, validate_svg)

API_VERSION = '1.0'


class ArgumentError(ValueError):
    """A machine-readable command argument error."""


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep usage errors in the single-JSON output contract."""

    def error(self, message):
        raise ArgumentError(message)


def environment_check(write=False, require_deployment=True):
    """Check only actual dependencies and optionally task-root write access."""
    from tl_workspace import check_task_root
    check_task_root()
    if sys.version_info < (3, 10):
        raise ContractError('Python >=3.10 is required')
    expected = SCRIPT_ROOT / '.venv' / 'bin' / 'python'
    if require_deployment and (not os.access(expected, os.X_OK)
                               or Path(sys.executable).parent.resolve() != expected.parent.resolve()):
        raise ContractError('Use the deployment-provided scripts/.venv/bin/python; no fallback is permitted')
    if expat.version_info < (2, 6, 0):
        raise ContractError('Bundled Expat >=2.6.0 is required')
    versions = {name: importlib.metadata.version(name)
                for name in ('python-pptx', 'lxml', 'Pillow', 'XlsxWriter', 'typing-extensions')}
    for name, minimum, maximum in (('lxml', (6, 0), (7, 0)), ('Pillow', (12, 0), (14, 0)),
                                   ('XlsxWriter', (3, 0), (4, 0)), ('typing-extensions', (4, 9), None)):
        match = re.match(r'^(\d+)\.(\d+)', versions[name])
        release = tuple(map(int, match.groups())) if match else (0, 0)
        if release < minimum or (maximum is not None and release >= maximum):
            raise ContractError(f'{name} version does not satisfy requirements.txt')
    if versions['python-pptx'] != '1.0.2':
        raise ContractError('python-pptx==1.0.2 is required')
    if not hasattr(os, 'O_NOFOLLOW'):
        raise ContractError('No-follow file operations are required')
    from tl_capabilities import capability_status
    result = {'status': 'environment-ok', 'api_version': API_VERSION,
              'optional_capabilities': capability_status(),
              'dependencies': versions, 'task_write_check': 'not_requested',
              'artifact_validation': 'not_run'}
    if write:
        name = f'tl-pptx-probe-{uuid.uuid4().hex}.tmp'
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        identity = os.fstat(descriptor)
        try:
            os.write(descriptor, b'tl-pptx-check')
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.read(descriptor, 64) != b'tl-pptx-check':
                raise ContractError('Task write/read probe failed')
        finally:
            os.close(descriptor)
            current = os.stat(name, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise ContractError('Probe identity changed; cleanup incomplete')
            os.unlink(name)
        result['task_write_check'] = 'passed'
    return result


def delivery_notice(result, outputs=None):
    """Only a complete verified batch is complete. Never render internal paths."""
    items = result.get('files') or []
    exact = outputs is None or sorted(item.get('output', '') for item in items) == sorted(outputs)
    if (result.get('archive_status') == 'success' and items and exact
            and all(item.get('archive_status') in {'copied', 'already_archived'} for item in items)):
        return '归档完成'
    return '无需归档' if result.get('archive_status') == 'not_required' else '归档未完成'


def convert(inputs, output, notes=None):
    """Validate inputs, snapshot, assemble, publish and archive without overwrites."""
    from archive_delivery import archive_delivery
    from tl_assemble import assemble_pptx
    from tl_workspace import check_task_root
    check_task_root()
    bare_name(output, '.pptx')
    if not 1 <= len(inputs) <= MAX_SLIDES or len(set(inputs)) != len(inputs):
        raise ContractError('Provide one to fifty distinct SVG files in slide order')
    if os.path.lexists(output):
        raise ContractError('Output already exists; choose a new filename')
    sources = [read_local(name, '.svg') for name in inputs]
    if sum(map(len, sources)) > MAX_TOTAL_BYTES:
        raise ContractError('Combined SVG byte budget exceeded')
    checks = [validate_svg(data) for data in sources]
    if sum(item['resource_bytes'] for item in checks) > MAX_TOTAL_BYTES:
        raise ContractError('Combined decoded resource budget exceeded')
    staged = []
    cleanup = {'status': 'completed', 'cleanup_complete': True, 'retained': []}
    result = None
    failure = None
    try:
        for data in sources:
            # Register a unique, not-yet-existing path before creating it.
            name = f'tl-pptx-source-{uuid.uuid4().hex}.svg'
            record = {'name': name, 'identity': None}
            staged.append(record)
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            identity = os.fstat(descriptor)
            record['identity'] = (identity.st_dev, identity.st_ino)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
        # Upstream diagnostics cannot pollute the single JSON stdout contract.
        diagnostic_stream = io.StringIO()
        with redirect_stdout(diagnostic_stream):
            payload, verification = assemble_pptx([Path(item['name']) for item in staged],
                                                 resource_root=Path.cwd(), notes=notes)
        verification.pop('conversion_trace', None)
        publication = publish_exclusive(output, payload)
        result = {'status': 'verified', 'api_version': API_VERSION,
                  'publication': publication, 'validation': verification,
                  'source_count': len(inputs), 'sha256': hashlib.sha256(payload).hexdigest(),
                  'upstream_diagnostics_present': bool(diagnostic_stream.getvalue())}
        try:
            result['archive'] = archive_delivery([output], protected_inputs=inputs)
            result['archive']['archive_user_notice'] = delivery_notice(result['archive'], [output])
        except Exception as exc:  # Archive errors never erase a verified publication receipt.
            result['archive'] = {'archive_status': 'failed', 'files': [],
                                 'reason_code': 'archive_exception', 'error_type': type(exc).__name__}
    except Exception as exc:  # Preserve cleanup information for every failed conversion.
        failure = exc
    finally:
        for item in staged:
            if item['identity'] is None:
                continue
            try:
                current = os.stat(item['name'], follow_symlinks=False)
                if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != item['identity']:
                    raise ContractError('Staged input identity changed')
                os.unlink(item['name'])
            except OSError:
                cleanup['retained'].append(item['name'])
            except ContractError:
                cleanup['retained'].append(item['name'])
        cleanup['cleanup_complete'] = not cleanup['retained']
        cleanup['status'] = 'completed' if cleanup['cleanup_complete'] else 'incomplete'
    if failure is not None:
        return {'status': 'error', 'error_type': type(failure).__name__,
                'message': str(failure)[:1000], 'cleanup': cleanup, 'api_version': API_VERSION}, 1
    result['cleanup'] = cleanup
    complete = cleanup['cleanup_complete'] and delivery_notice(result['archive'], [output]) == '归档完成'
    return result, 0 if complete else 3


def run_artifact(output, suffix, protected, producer):
    """Publish one fully validated artifact, preserving archive failure state."""
    bare_name(output, suffix)
    if os.path.lexists(output):
        raise ContractError('Output already exists; choose a new filename')
    from tl_workspace import check_task_root, lifecycle, receipt
    check_task_root()
    with lifecycle() as journal:
        try:
            return _produce_artifact(output, suffix, protected, producer, journal)
        except Exception as exc:
            return {'status': 'error', 'error_type': type(exc).__name__,
                    'message': str(exc)[:1000], 'api_version': API_VERSION,
                    'cleanup': receipt(journal)}, 1


def _produce_artifact(output, suffix, protected, producer, journal):
    """Preserve validated outputs when cleanup or archiving is partial."""
    from archive_delivery import archive_delivery
    from tl_workspace import receipt
    diagnostic_stream = io.StringIO()
    with redirect_stdout(diagnostic_stream):
        payload, verification = producer()
    publication = publish_exclusive(output, payload, suffix=suffix)
    result = {'status': 'verified', 'api_version': API_VERSION,
              'publication': publication, 'validation': verification,
              'sha256': hashlib.sha256(payload).hexdigest(),
              'upstream_diagnostics_present': bool(diagnostic_stream.getvalue()),
              'cleanup': receipt(journal)}
    try:
        result['archive'] = archive_delivery([output], protected_inputs=protected)
        result['archive']['archive_user_notice'] = delivery_notice(result['archive'], [output])
    except Exception as exc:  # Archive errors never erase a verified publication receipt.
        result['archive'] = {'archive_status': 'failed', 'files': [],
                             'reason_code': 'archive_exception', 'error_type': type(exc).__name__}
    complete = result['cleanup']['cleanup_complete'] and delivery_notice(result['archive'], [output]) == '归档完成'
    return result, 0 if complete else 3


def build(manifest_name, output):
    """Build through complete upstream core using explicit frozen inputs."""
    from tl_build import build_package, load_manifest, prepare_build
    prepared = prepare_build(load_manifest(manifest_name))
    return run_artifact(output, '.pptx', [manifest_name, *prepared['snapshots']],
                        lambda: build_package(prepared))


def rebuild(manifest_name, output):
    """Restore a native workspace with explicit edits and page plan."""
    from tl_build import load_manifest
    from tl_roundtrip import rebuild_workspace
    manifest = load_manifest(manifest_name)
    if set(manifest) - {'workspace', 'edits', 'page_plan', 'notes', 'animation_config'}:
        raise ContractError('Unknown rebuild manifest field')
    workspace = manifest.get('workspace')
    bare_name(workspace, '.zip')
    edits = manifest.get('edits', {})
    if not isinstance(edits, dict):
        raise ContractError('Edits must be an authoring-name to task-filename mapping')
    protected = [manifest_name, workspace, *edits.values()]
    if manifest.get('page_plan'):
        protected.append(manifest['page_plan'])
    return run_artifact(output, '.pptx', protected,
                        lambda: rebuild_workspace(workspace, edits=edits, page_plan=manifest.get('page_plan'),
                                                  notes=manifest.get('notes'),
                                                  animation_config=manifest.get('animation_config')))


def template_operation(args):
    """Route template bundles without bypassing their retained integrity gate."""
    from tl_templates import (apply_template_bundles, create_template, import_template,
                              preview_template, register_template_bundle)
    if args.command == 'template-import':
        return run_artifact(args.output, '.zip', [args.input], lambda: import_template(args.input))
    if args.command == 'template-preview':
        return run_artifact(args.output, '.pptx', [args.input], lambda: preview_template(args.input))
    if args.command == 'template-apply':
        return run_artifact(args.output, '.zip', [args.project, *args.templates],
                            lambda: apply_template_bundles(args.project, args.templates))
    operation = create_template if args.command == 'template-create' else register_template_bundle
    return run_artifact(args.output, '.zip', [args.input],
                        lambda: operation(args.input, kind=args.kind, template_id=args.template_id))


def select_assets(manifest_name, output):
    """Materialize only explicitly selected IDs from known offline packs."""
    from tl_assetpacks import select_asset_packs
    from tl_build import load_manifest
    manifest = load_manifest(manifest_name)
    if set(manifest) != {'requests'} or not isinstance(manifest['requests'], list):
        raise ContractError('Asset manifest requires only a requests list')
    requests = manifest['requests']
    if any(not isinstance(item, dict) or not isinstance(item.get('pack'), str) for item in requests):
        raise ContractError('Each asset selection requires a pack filename')
    return run_artifact(output, '.zip', [manifest_name, *[item['pack'] for item in requests]],
                        lambda: select_asset_packs(requests))


def boolean_operation(svg_name, manifest_name, output):
    """Publish standalone root-coordinate Boolean paths, never replace source."""
    from tl_build import load_manifest
    from tl_geometry import boolean_document
    manifest = load_manifest(manifest_name)
    fonts = manifest.get('fonts', [])
    if not isinstance(fonts, list) or any(not isinstance(name, str) for name in fonts):
        raise ContractError('Fonts must be explicit task-root filenames')
    return run_artifact(output, '.svg', [svg_name, manifest_name, *fonts],
                        lambda: boolean_document(svg_name, manifest))


def main(argv=None):
    """Return one JSON result for success, controlled failure and argument errors."""
    parser = JsonArgumentParser(description='Create editable PPTX from validated SVG pages')
    sub = parser.add_subparsers(dest='command', required=True)
    check_parser = sub.add_parser('check')
    check_parser.add_argument('--write', action='store_true')
    conversion = sub.add_parser('convert')
    conversion.add_argument('output')
    conversion.add_argument('inputs', nargs='+')
    advanced = sub.add_parser('build')
    advanced.add_argument('output')
    advanced.add_argument('manifest')
    for command in ('analyze', 'import'):
        operation = sub.add_parser(command)
        operation.add_argument('output')
        operation.add_argument('input')
    restoration = sub.add_parser('rebuild')
    restoration.add_argument('output')
    restoration.add_argument('manifest')
    for command in ('template-import', 'template-preview', 'template-create', 'template-register'):
        operation = sub.add_parser(command)
        operation.add_argument('output')
        operation.add_argument('input')
        if command in ('template-create', 'template-register'):
            operation.add_argument('--kind', choices=('brand', 'style', 'layout', 'deck'), required=True)
            operation.add_argument('--template-id', required=True)
    applying = sub.add_parser('template-apply')
    applying.add_argument('output')
    applying.add_argument('project')
    applying.add_argument('templates', nargs='+')
    catalog = sub.add_parser('asset-query')
    catalog.add_argument('input')
    catalog.add_argument('--query', default='')
    catalog.add_argument('--limit', type=int, default=50)
    catalog.add_argument('--offset', type=int, default=0)
    selection = sub.add_parser('asset-select')
    selection.add_argument('output')
    selection.add_argument('manifest')
    geometry = sub.add_parser('boolean')
    geometry.add_argument('output')
    geometry.add_argument('input')
    geometry.add_argument('manifest')
    try:
        args = parser.parse_args(argv)
        if args.command == 'check':
            result, code = environment_check(args.write), 0
        elif args.command == 'asset-query':
            from tl_assetpacks import query_asset_pack
            environment_check(write=False)
            result = {'status': 'verified', 'api_version': API_VERSION,
                      'catalog': query_asset_pack(args.input, query=args.query, limit=args.limit, offset=args.offset)}
            code = 0
        else:
            environment_check(write=True)
            if args.command == 'boolean':
                result, code = boolean_operation(args.input, args.manifest, args.output)
            elif args.command == 'asset-select':
                result, code = select_assets(args.manifest, args.output)
            elif args.command == 'build':
                result, code = build(args.manifest, args.output)
            elif args.command == 'rebuild':
                result, code = rebuild(args.manifest, args.output)
            elif args.command.startswith('template-'):
                result, code = template_operation(args)
            elif args.command in ('analyze', 'import'):
                from tl_roundtrip import analyze_pptx, import_workspace
                operation = analyze_pptx if args.command == 'analyze' else import_workspace
                suffix = '.json' if args.command == 'analyze' else '.zip'
                result, code = run_artifact(args.output, suffix, [args.input], lambda: operation(args.input))
            else:
                result, code = convert(args.inputs, args.output)
    except ArgumentError as exc:
        result, code = {'status': 'error', 'error_type': 'ArgumentError',
                        'message': str(exc)[:1000], 'api_version': API_VERSION}, 2
    except Exception as exc:  # Keep unexpected library failures in the structured CLI contract.
        result, code = {'status': 'error', 'error_type': type(exc).__name__,
                        'message': str(exc)[:1000], 'api_version': API_VERSION}, 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
