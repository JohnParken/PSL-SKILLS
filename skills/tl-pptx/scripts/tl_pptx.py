"""Standalone, bounded SVG-to-native-PPTX entry point."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import sys
import secrets
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


def deployment_versions():
    """Verify the seven approved direct distributions against their exact pins."""
    pins = {
        'Pillow': '12.2.0', 'XlsxWriter': '3.2.9', 'typing-extensions': '4.14.0',
        'lxml': '6.0.4', 'PyYAML': '6.0.2', 'PyMuPDF': '1.24.2', 'python-pptx': '1.0.2',
    }
    actual = {}
    for name, expected in pins.items():
        try:
            actual[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise ContractError(f'{name}=={expected} is required but not installed') from None
        if actual[name] != expected:
            raise ContractError(f'{name}=={expected} is required; installed {actual[name]}')
    return actual


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
    versions = deployment_versions()
    if not hasattr(os, 'O_NOFOLLOW'):
        raise ContractError('No-follow file operations are required')
    from tl_capabilities import capability_status
    result = {'status': 'environment-ok', 'api_version': API_VERSION,
              'optional_capabilities': capability_status(),
              'dependencies': versions, 'task_write_check': 'not_requested',
              'artifact_validation': 'not_run'}
    if write:
        name = f'tl-pptx-probe-{secrets.token_hex(16)}.tmp'
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
    """Validate inputs, snapshot privately, assemble, publish and archive without overwrites."""
    from archive_delivery import archive_delivery
    from tl_assemble import assemble_pptx
    from tl_workspace import check_task_root, lifecycle, private_workspace, receipt
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
    with lifecycle() as journal:
        try:
            with private_workspace(prefix='tl-pptx-convert-') as workspace:
                root = workspace.resolve()
                staged = []
                for index, data in enumerate(sources, 1):
                    target = root / f'slide_{index:02d}.svg'
                    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(descriptor, 'wb') as handle:
                        handle.write(data)
                    staged.append(target)
                # Upstream diagnostics cannot pollute the single JSON stdout contract.
                diagnostic_stream = io.StringIO()
                with redirect_stdout(diagnostic_stream):
                    payload, verification = assemble_pptx(staged, resource_root=Path.cwd(), notes=notes)
            verification.pop('conversion_trace', None)
            publication = publish_exclusive(output, payload)
            result = {'status': 'verified', 'api_version': API_VERSION,
                      'publication': publication, 'validation': verification,
                      'source_count': len(inputs), 'sha256': hashlib.sha256(payload).hexdigest(),
                      'upstream_diagnostics_present': bool(diagnostic_stream.getvalue()),
                      'cleanup': receipt(journal)}
            try:
                result['archive'] = archive_delivery([output], protected_inputs=inputs)
                result['archive']['archive_user_notice'] = delivery_notice(result['archive'], [output])
            except Exception as exc:  # Archive errors never erase a verified publication receipt.
                result['archive'] = {'archive_status': 'failed', 'files': [],
                                     'reason_code': 'archive_exception', 'error_type': type(exc).__name__}
            complete = result['cleanup']['cleanup_complete'] and delivery_notice(result['archive'], [output]) == '归档完成'
            return result, 0 if complete else 3
        except Exception as exc:  # Preserve cleanup information for every failed conversion.
            return {'status': 'error', 'error_type': type(exc).__name__,
                    'message': str(exc)[:1000], 'cleanup': receipt(journal),
                    'api_version': API_VERSION}, 1


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


def text_outline_operation(manifest_name, output):
    """Turn explicitly requested, authorized simple text into native shape paths."""
    from tl_build import load_manifest
    from tl_text_outline import outline_text
    spec = load_manifest(manifest_name)
    if not isinstance(spec, dict) or not isinstance(spec.get('font'), str):
        raise ContractError('text-outline requires a JSON object and explicit authorized font')
    def produce():
        payload = outline_text(spec)
        validate_svg(payload)
        return payload, {'representation': 'editable_shape_paths_not_text',
                         'typography': 'simple_per_glyph_no_shaping',
                         'font_license_verification': 'operator_attestation'}
    return run_artifact(output, '.svg', [manifest_name, spec['font']], produce)


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


def _finite_number(value, flag, *, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ArgumentError(f'{flag} must be a finite number')
    if (low is not None and value < low) or (high is not None and value > high):
        raise ArgumentError(f'{flag} must be between {low} and {high}')
    return float(value)


def svg_check_operation(inputs, *, quick=False):
    """Run bounded read-only SVG quality inspection for 1..50 pages inside one private workspace."""
    from xml.etree import ElementTree as ET
    from svg_quality.checker import SVGQualityChecker
    from tl_workspace import check_task_root, lifecycle, private_workspace, receipt
    if isinstance(inputs, str):
        inputs = [inputs]
    if not 1 <= len(inputs) <= MAX_SLIDES or len(set(inputs)) != len(inputs):
        raise ContractError('Provide one to fifty distinct SVG files in slide order')
    check_task_root()
    sources = [read_local(name, '.svg') for name in inputs]
    if sum(map(len, sources)) > MAX_TOTAL_BYTES:
        raise ContractError('Combined SVG byte budget exceeded')
    checks = [validate_svg(data) for data in sources]
    if sum(item['resource_bytes'] for item in checks) > MAX_TOTAL_BYTES:
        raise ContractError('Combined decoded resource budget exceeded')
    viewboxes = [' '.join((ET.fromstring(data).get('viewBox') or '').split()) for data in sources]
    cross_errors = []
    if len(inputs) > 1 and any(vb != viewboxes[0] for vb in viewboxes[1:]):
        cross_errors.append(f'SVG viewBox mismatch across pages: expected {viewboxes[0]!r}')
    pages = []
    with lifecycle() as journal:
        with private_workspace(prefix='tl-pptx-svgcheck-') as workspace:
            root = workspace.resolve()
            staged = []
            for name, data in zip(inputs, sources):
                target = root / name
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(data)
                staged.append((name, target))
            with redirect_stdout(io.StringIO()):
                checker = SVGQualityChecker(quick_generate=quick)
                for name, target in staged:
                    report = checker.check_file(str(target))
                    report.pop('path', None)
                    report['file'] = name
                    pages.append(report)
    validation = {
        'passed': all(page['passed'] for page in pages) and not cross_errors,
        'page_count': len(pages),
        'cross_page_viewbox_consistent': not cross_errors,
        'cross_page_errors': cross_errors,
        'pages': pages,
    }
    if len(pages) == 1:
        validation.update(pages[0])
    return {'status': 'verified', 'api_version': API_VERSION,
            'validation': validation, 'cleanup': receipt(journal)}, 0


_MEASURE_FIELDS = frozenset({
    'id', 'text', 'size', 'max_width', 'family', 'weight',
    'letter_spacing', 'include_headroom', 'x', 'y', 'dy', 'anchor',
})
_MEASURE_WEIGHTS = frozenset({
    'normal', 'bold', '100', '200', '300', '400', '500', '600', '700', '800', '900',
})


def _measure_one(spec, *, measure_text, text_box, wrap_text):
    unknown = set(spec) - _MEASURE_FIELDS
    if unknown:
        raise ArgumentError(f'Unknown text-measure field: {", ".join(sorted(unknown))}')
    text = spec.get('text')
    if not isinstance(text, str) or not text or len(text) > 100000:
        raise ArgumentError('TEXT must contain 1 to 100000 characters')
    size = _finite_number(spec.get('size'), 'size', low=1, high=512)
    family = spec.get('family', 'Calibri')
    if not isinstance(family, str) or not family or len(family) > 256:
        raise ArgumentError('family must be a non-empty bounded string')
    weight = spec.get('weight', 'normal')
    if not isinstance(weight, str) or weight not in _MEASURE_WEIGHTS:
        raise ArgumentError('Unsupported font weight')
    spacing = _finite_number(spec.get('letter_spacing', 0.0), 'letter_spacing', low=-100, high=500)
    include_headroom = spec.get('include_headroom', True)
    if type(include_headroom) is not bool:
        raise ArgumentError('include_headroom must be a boolean')
    x_raw, y_raw = spec.get('x'), spec.get('y')
    if (x_raw is None) != (y_raw is None):
        raise ArgumentError('x and y must be supplied together')
    anchor = spec.get('anchor', 'start')
    if anchor not in {'start', 'middle', 'end'}:
        raise ArgumentError('anchor must be start, middle or end')
    style = dict(size=size, family=family, weight=weight,
                 letter_spacing=spacing, include_headroom=include_headroom)
    single_width = round(measure_text(text, **style), 2)
    measurement = {'single_line_width': single_width, 'size': size, 'family': family,
                   'weight': weight, 'include_headroom': include_headroom}
    if 'id' in spec:
        ident = spec['id']
        if not isinstance(ident, str) or not ident or len(ident) > 128:
            raise ArgumentError('id must be bounded non-empty text')
        measurement['id'] = ident
    dy_raw = spec.get('dy')
    dy_value = _finite_number(dy_raw, 'dy', low=1, high=2000) if dy_raw is not None else round(size * 1.35, 2)
    max_width_raw = spec.get('max_width')
    if max_width_raw is not None:
        max_width = _finite_number(max_width_raw, 'max_width', low=1, high=100000)
        lines, widths, oversized = wrap_text(text, max_width=max_width, **style)
        measurement.update({
            'max_width': max_width, 'dy': dy_value, 'lines': lines,
            'line_widths': [round(value, 2) for value in widths],
            'line_count': len(lines),
            'estimated_height': round((len(lines) - 1) * dy_value + 1.2 * size, 2),
            'oversized_tokens': [{'token': token, 'width': round(width, 2)} for token, width in oversized],
        })
        box_width = max(widths) if widths else 0.0
        line_count = len(lines)
    else:
        box_width = single_width
        line_count = 1
    if x_raw is not None:
        x_val = _finite_number(x_raw, 'x', low=-100000, high=100000)
        y_val = _finite_number(y_raw, 'y', low=-100000, high=100000)
        bounds = text_box(x=x_val, baseline_y=y_val, size=size, lines=line_count,
                          dy=dy_value, width=box_width, anchor=anchor)
        measurement['bounds'] = {key: round(value, 2) for key, value in bounds.items()}
    return measurement


def text_measure_operation(args):
    """Estimate offline text width, greedy wrapping and bounds for one string or batch manifest."""
    from text_measure import measure_text, text_box, wrap_text
    helpers = dict(measure_text=measure_text, text_box=text_box, wrap_text=wrap_text)
    if args.manifest is not None:
        if args.text is not None or args.size is not None:
            raise ArgumentError('--manifest cannot be combined with positional TEXT or --size')
        from tl_build import load_manifest
        manifest = load_manifest(args.manifest)
        if set(manifest) - {'defaults', 'items'} or not isinstance(manifest.get('items'), list):
            raise ArgumentError('text-measure manifest requires an items list and optional defaults')
        defaults = manifest.get('defaults', {})
        if not isinstance(defaults, dict) or 'id' in defaults or 'text' in defaults:
            raise ArgumentError('text-measure defaults must be an object without id/text')
        items = manifest['items']
        if not 1 <= len(items) <= 500 or any(not isinstance(item, dict) for item in items):
            raise ArgumentError('text-measure manifest requires 1 to 500 item objects')
        results = [_measure_one({**defaults, **item}, **helpers) for item in items]
        return {'status': 'verified', 'api_version': API_VERSION,
                'count': len(results), 'measurements': results}, 0
    if args.text is None or args.size is None:
        raise ArgumentError('Provide TEXT with --size, or pass --manifest MANIFEST.json')
    spec = {
        'text': args.text, 'size': args.size, 'family': args.family, 'weight': args.weight,
        'letter_spacing': args.letter_spacing, 'include_headroom': not args.no_headroom,
        'max_width': args.max_width, 'x': args.x, 'y': args.y, 'dy': args.dy, 'anchor': args.anchor,
    }
    return {'status': 'verified', 'api_version': API_VERSION,
            'measurement': _measure_one(spec, **helpers)}, 0


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
    letters = sub.add_parser('text-outline')
    letters.add_argument('output')
    letters.add_argument('manifest')
    geometry = sub.add_parser('boolean')
    geometry.add_argument('output')
    geometry.add_argument('input')
    geometry.add_argument('manifest')
    svg_check = sub.add_parser('svg-check')
    svg_check.add_argument('inputs', nargs='+')
    svg_check.add_argument('--quick', action='store_true')
    measure = sub.add_parser('text-measure')
    measure.add_argument('text', nargs='?', default=None)
    measure.add_argument('--manifest', default=None)
    measure.add_argument('--size', type=float, default=None)
    measure.add_argument('--max-width', type=float, default=None)
    measure.add_argument('--family', default='Calibri')
    measure.add_argument('--weight', default='normal',
                         choices=('normal', 'bold', '100', '200', '300', '400', '500', '600', '700', '800', '900'))
    measure.add_argument('--letter-spacing', type=float, default=0.0)
    measure.add_argument('--no-headroom', action='store_true')
    measure.add_argument('--x', type=float, default=None)
    measure.add_argument('--y', type=float, default=None)
    measure.add_argument('--dy', type=float, default=None)
    measure.add_argument('--anchor', choices=('start', 'middle', 'end'), default='start')
    try:
        args = parser.parse_args(argv)
        if args.command == 'check':
            result, code = environment_check(args.write), 0
        elif args.command == 'text-measure':
            environment_check(write=False)
            result, code = text_measure_operation(args)
        elif args.command == 'asset-query':
            from tl_assetpacks import query_asset_pack
            environment_check(write=False)
            result = {'status': 'verified', 'api_version': API_VERSION,
                      'catalog': query_asset_pack(args.input, query=args.query, limit=args.limit, offset=args.offset)}
            code = 0
        else:
            environment_check(write=True)
            if args.command == 'svg-check':
                result, code = svg_check_operation(args.inputs, quick=args.quick)
            elif args.command == 'text-outline':
                result, code = text_outline_operation(args.manifest, args.output)
            elif args.command == 'boolean':
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
