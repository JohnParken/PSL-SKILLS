"""Measure synthetic content projection only, not DOCX parse/CLI or streaming."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_read_content import content_records


def projection_fixture(kind, count):
    """Construct independent location-addressed records without user documents."""
    records = []
    for index in range(count):
        base = f'/w:document[1]/w:body[1]/w:tbl[{index + 1}]'
        if kind == 'paragraph':
            records.append({'type': 'paragraph', 'part': 'word/document.xml', 'path': base,
                            'text': 'Synthetic paragraph ' + str(index),
                            'numbering': {'label': str(index) + '.', 'level': 0, 'num_id': '1'},
                            'fields': [{'text': str(index), 'instruction': 'PAGE'}]})
            continue
        row = {'type': 'table_row', 'part': 'word/document.xml', 'path': base + '/w:tr[1]',
               'table_index': index + 1, 'row_index': 1, 'cells': []}
        records.append(row)
        for column in range(1, 7 if kind == 'table' else 2):
            path = base + f'/w:tr[1]/w:tc[{column}]'
            text = f'Cell {index} column {column}'
            row['cells'].append({'column': column, 'colspan': 1, 'path': path, 'text': text, 'vmerge': None})
            records.append({'type': 'paragraph', 'part': 'word/document.xml', 'path': path + '/w:p[1]',
                            'table_index': index + 1, 'row_index': 1, 'column': column, 'text': text})
        if kind == 'nested':
            nested_path = base + '/w:tr[1]/w:tc[1]/w:tbl[1]'
            nested_text = f'Nested {index}'
            row['cells'][0]['text'] += '\n' + nested_text
            records.extend([
                {'type': 'table', 'part': 'word/document.xml', 'path': nested_path,
                 'table_index': count + index + 1, 'parent_table_index': index + 1, 'parent_row': 1, 'parent_column': 1},
                {'type': 'paragraph', 'part': 'word/document.xml', 'path': nested_path + '/w:tr[1]/w:tc[1]/w:p[1]',
                 'table_index': count + index + 1, 'row_index': 1, 'column': 1, 'text': nested_text},
            ])
    return records


def child_measure(kind, count, details):
    """Measure projection, retaining input and output until RSS observation."""
    records = projection_fixture(kind, count)
    before = sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    started = time.perf_counter()
    output = content_records(records, details=details)
    elapsed = time.perf_counter() - started
    payload = json.dumps(output, ensure_ascii=False, sort_keys=True).encode('utf-8')
    after = sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    if before != after:
        raise AssertionError('Projection mutated source records')
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == 'darwin':
        peak /= 1024
    elif sys.platform.startswith('linux'):
        for line in Path('/proc/self/status').read_text(encoding='utf-8').splitlines():
            if line.startswith('VmHWM:'):
                peak = int(line.split()[1])
                break
    return {'kind': kind, 'count': count, 'details': details, 'seconds': elapsed,
            'peak_rss_kib': peak, 'output_records': len(output), 'output_bytes': len(payload),
            'output_sha256': sha256(payload).hexdigest()}


def main():
    """Run three fresh processes per case, returning exact response fingerprints."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', choices=['paragraph', 'table', 'nested'])
    parser.add_argument('--count', type=int, default=250)
    parser.add_argument('--details', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.count <= 10000:
        parser.error('count must be 1..10000')
    if args.child:
        print(json.dumps(child_measure(args.child, args.count, args.details)))
        return
    results = []
    for kind, count in [('paragraph', 250), ('paragraph', 2500), ('paragraph', 10000),
                        ('table', 100), ('table', 1000), ('nested', 50), ('nested', 500), ('nested', 2000)]:
        for details in (False, True):
            runs = []
            for _ in range(3):
                command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                           '--child', kind, '--count', str(count)]
                if details:
                    command.append('--details')
                completed = subprocess.run(command, check=True, capture_output=True, timeout=60,
                                           env=dict(os.environ, PATH=''))
                runs.append(json.loads(completed.stdout))
            if len({run['output_sha256'] for run in runs}) != 1:
                raise AssertionError('Projection is not deterministic')
            results.append({**runs[0], 'seconds': statistics.median(run['seconds'] for run in runs),
                            'peak_rss_kib': statistics.median(run['peak_rss_kib'] for run in runs)})
    print(json.dumps({'platform': sys.platform, 'python': sys.version, 'repeats': 3,
                      'scope': 'projection only, immutable synthetic input, process peak RSS', 'results': results}, indent=2))


if __name__ == '__main__':
    main()
