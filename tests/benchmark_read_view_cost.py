"""Maintenance-only synthetic read-view costs; does not measure model/host tokens.

Run from the repository root with Python 3.10+, -I -S -B. Output defaults to stdout;
--output writes only a new repository-relative JSON evidence file. No bank samples,
network, installation, office application, or runtime snapshot cache is involved.
"""
import argparse
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import re
import shutil
import struct
import tempfile
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'skills/xlsx/scripts'))
sys.path.insert(0, str(ROOT / 'skills/wps/scripts'))
import xlsx_read_view
import wps_read_view
from stdlib_xlsx import Workbook


def fixture(count):
    """Independent, deterministic values; no WPS producer/provenance assertion."""
    values = {f'A{i}': {'value': str(i).zfill(12)} for i in range(1, count + 1)}
    book = Workbook.create({'sheets': [{'name': 'Data', 'cells': values}, {'name': 'Empty', 'cells': {}}]})
    source = {'status': 'partial', 'partial': True, 'schema_version': 'biff8_readonly_v1',
              'issues': [{'code': 'synthetic_metadata_omission', 'message': 'diagnostic ' * 100}],
              'reading_assessment': {'completeness': 'partial', 'full_format_verified': False},
              'sheets': [{'name': 'Data', 'cells': {key: {'type': 'text', 'value': value['value']} for key, value in values.items()}},
                         {'name': 'Empty', 'cells': {}}]}
    return book, source


def run_case(skill, count, page_size, summary=False):
    book, source = fixture(count)
    module = xlsx_read_view if skill == 'xlsx' else wps_read_view
    encode_name = 'encode' if skill == 'xlsx' else 'encoded'
    sort_name = 'coordinates' if skill == 'xlsx' else 'position'
    original = getattr(module, encode_name)
    measurements = {'encoder_calls': 0, 'encoder_characters': 0}
    def counted(value):
        text = original(value)
        measurements['encoder_calls'] += 1
        measurements['encoder_characters'] += len(text)
        return text
    cursor = None
    pages = 0
    output_characters = 0
    output_bytes = 0
    diagnostic_characters = 0
    returned = []
    start = time.perf_counter()
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, encode_name, counted))
        sorting = stack.enter_context(patch.object(module, sort_name, wraps=getattr(module, sort_name)))
        while True:
            if skill == 'xlsx':
                view = module.read_view(book, b'fixed synthetic source', summary=summary, page_size=page_size,
                                        cursor=cursor, max_chars=2000000)
            else:
                view = module.project(source, b'fixed synthetic source', {}, summary=summary, page_size=page_size,
                                      cursor=cursor, max_chars=2000000)
            wire = original(view) + '\n'
            pages += 1
            output_characters += len(wire)
            output_bytes += len(wire.encode('utf-8'))
            diagnostic_characters += len(original(view['issues'])) if 'issues' in view else 0
            returned.extend((item['cell'], item['data']['value']) for item in view.get('items', []))
            cursor = view['next_cursor']
            if cursor is None:
                break
            if pages > count + 1:
                raise RuntimeError('Pagination failed to make bounded progress')
        measurements['coordinate_calls'] = sorting.call_count
    elapsed = time.perf_counter() - start
    if not summary and returned != [(f'A{i}', str(i).zfill(12)) for i in range(1, count + 1)]:
        raise AssertionError('Missing, duplicated or changed paginated values')
    return {'skill': skill, 'cells': count, 'page_size': page_size, 'summary': summary,
            'pages': pages, 'output_characters': output_characters, 'output_utf8_bytes': output_bytes,
            'repeated_diagnostic_characters': diagnostic_characters, 'seconds': elapsed, **measurements}


def cli_cases(commit=None):
    """Small full CLI traversal, including subprocess startup and source parsing."""
    sys.path.insert(0, str(ROOT / 'skills/et/scripts/test'))
    from test_wps_biff8 import workbook, rec, cell
    from test_wps_cfb import synthetic_cfb
    rows = []
    with tempfile.TemporaryDirectory(prefix='read-view-cli-') as directory:
        base = Path(directory)
        task = base / 'task'
        task.mkdir()
        book, _ = fixture(40)
        book.save(task / 'data.xlsx')
        body = b''.join(rec(0x203, cell(i, 0) + struct.pack('<d', i + 1)) for i in range(40))
        (task / 'data.et').write_bytes(synthetic_cfb({'Workbook': workbook([body], extra_global=rec(0x42, struct.pack('<H', 936)))}))
        for skill, script, input_name in (('xlsx', 'stdlib_xlsx.py', 'data.xlsx'), ('wps', 'read_wps.py', 'data.et')):
            install = base / skill
            shutil.copytree(ROOT / 'skills' / skill / 'scripts', install / 'scripts', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            assets = ROOT / 'skills' / skill / 'assets'
            if assets.is_dir():
                shutil.copytree(assets, install / 'assets')
            if commit:
                relative = f'skills/{skill}/scripts/{skill}_read_view.py'
                (install / 'scripts' / (skill + '_read_view.py')).write_bytes(
                    subprocess.check_output(['git', 'show', commit + ':' + relative], cwd=ROOT))
            command = [sys.executable, '-I', '-S', '-B', str(install / 'scripts' / script)]
            if skill == 'xlsx':
                command.append('read')
            command.append('./' + input_name)
            for size in (1, 10, 40, None):
                cursor = None
                refs = []
                characters = 0
                byte_count = 0
                calls = 0
                exit_codes = set()
                started = time.perf_counter()
                while True:
                    flags = ['--summary'] if size is None else ['--page-size', str(size)]
                    flags += ['--max-output-chars', '2000000']
                    if cursor is not None:
                        flags += ['--cursor', cursor]
                    child = subprocess.run(command + flags, cwd=task, capture_output=True, text=True, encoding='utf-8', timeout=30)
                    if child.returncode != (0 if skill == 'xlsx' else 3):
                        raise AssertionError('Unexpected synthetic CLI outcome: ' + child.stderr + child.stdout)
                    view = json.loads(child.stdout)
                    calls += 1
                    characters += len(child.stdout)
                    byte_count += len(child.stdout.encode('utf-8'))
                    exit_codes.add(child.returncode)
                    refs.extend((item['cell'], item['data']['value']) for item in view.get('items', []))
                    cursor = view['next_cursor']
                    if cursor is None:
                        break
                    if calls > 40:
                        raise AssertionError('CLI pagination made no bounded progress')
                if size is not None:
                    expected = [(f'A{i}', str(i).zfill(12) if skill == 'xlsx' else str(i)) for i in range(1, 41)]
                    if refs != expected:
                        raise AssertionError('CLI traversal lost, duplicated or changed values')
                rows.append({'skill': skill, 'page_size': size, 'summary': size is None, 'calls': calls,
                             'seconds': time.perf_counter() - started, 'output_characters': characters,
                             'output_utf8_bytes': byte_count, 'exit_codes': sorted(exit_codes),
                             'source_sha256': hashlib.sha256((task / input_name).read_bytes()).hexdigest()})
    return rows


def baseline_modules(commit):
    """Load only the two trusted historical repo modules, without changing the checkout."""
    if not re.fullmatch(r'[0-9a-fA-F]{7,40}', commit):
        raise ValueError('baseline-ref must be a hexadecimal repository commit')
    loaded = []
    with tempfile.TemporaryDirectory(prefix='read-view-baseline-') as directory:
        for skill in ('xlsx', 'wps'):
            relative = f'skills/{skill}/scripts/{skill}_read_view.py'
            source = subprocess.check_output(['git', 'show', commit + ':' + relative], cwd=ROOT)
            path = Path(directory) / (skill + '_read_view.py')
            path.write_bytes(source)
            spec = importlib.util.spec_from_file_location('cost_baseline_' + skill, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.benchmark_source_sha256 = hashlib.sha256(source).hexdigest()
            loaded.append(module)
    return loaded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    global xlsx_read_view, wps_read_view
    parser.add_argument('--output')
    parser.add_argument('--include-cli', action='store_true', help='also measure startup plus full parsing in isolated copies')
    parser.add_argument('--baseline-ref', help='read only historical projection modules from this hex commit')
    parser.add_argument('--label', default='measurement')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error('repeats must be 1..10')
    if args.baseline_ref:
        xlsx_read_view, wps_read_view = baseline_modules(args.baseline_ref)
    results = []
    for skill in ('xlsx', 'wps'):
        for count, size, summary in [(100, 100, False), (200, 200, False), (400, 400, False),
                                     (40, 1, False), (40, 10, False), (40, 40, False), (400, 200, True)]:
            runs = [run_case(skill, count, size, summary) for _ in range(args.repeats)]
            row = dict(runs[0])
            row['seconds_runs'] = [x['seconds'] for x in runs]
            row['seconds_median'] = statistics.median(row['seconds_runs'])
            row.pop('seconds')
            row['repeats'] = args.repeats
            results.append(row)
    report = {'label': args.label, 'platform': platform.platform(), 'python': sys.version,
              'baseline_ref': args.baseline_ref,
              'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'method': 'Already-parsed synthetic result projections; per-page sorting/encoding included; parsing and subprocess startup excluded.',
              'limits': 'Not real WPS samples, model/host tokens, production acceptance or cross-platform guarantee. Complete traversal checked against independent values.',
              'tokenizer_available': {name: importlib.util.find_spec(name) is not None for name in ('tiktoken', 'tokenizers')},
              'sources': {f'skills/{skill}/scripts/{skill}_read_view.py':
                          getattr(module, 'benchmark_source_sha256', None) or hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                          for skill, module in (('xlsx', xlsx_read_view), ('wps', wps_read_view))}, 'results': results}
    if args.include_cli:
        report['cli_results'] = cli_cases(args.baseline_ref)
        report['cli_limits'] = 'One timed traversal per case, 40 cells; synthetic inputs, startup/parsing included, install copy and fixture creation excluded.'
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        output = Path(args.output)
        if output.is_absolute() or '..' in output.parts:
            parser.error('output must be a new repository-relative evidence path')
        with (ROOT / output).open('x', encoding='utf-8') as handle:
            handle.write(text)
    else:
        print(text, end='')
    print('synthetic-cost-benchmark-ok', file=sys.stderr)


if __name__ == '__main__':
    main()
