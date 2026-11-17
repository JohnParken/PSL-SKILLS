"""Maintenance-only pagination benchmark; excludes parsing and final output I/O.

Usage (replace <skill-root> with the confirmed Skill directory):
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/test/benchmark_page_budget.py"
The source path defaults to this test directory's sibling production entry.
The already allocated synthetic records are excluded from tracemalloc peaks.
"""

import argparse
import base64
import importlib.util
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc


def baseline_page(module, meta, records, budget, cursor, binding, continuation=None):
    """The pre-optimization algorithm, retained only as a measurement reference."""
    start = module.cursor_start(cursor, binding)
    if start and start >= len(records):
        raise module.ContractError('cursor_out_of_range', 'Read cursor is outside the selected records.',
                                   {'action': 'restart_read'})

    def frame(end):
        more = end < len(records)
        token = base64.urlsafe_b64encode(module.encode([binding, end])).decode('ascii') if more else None
        result = {**meta, 'start': start, 'end': end, 'total': len(records), 'has_more': more,
                  'next_cursor': token, 'records': records[start:end]}
        if continuation is not None and more:
            result['continuation'] = module.continuation_frame(continuation, token)
        return result

    complete = frame(len(records))
    if budget is None or len(module.encode(complete)) <= budget:
        return complete
    low, high = start + 1, len(records) - 1
    best = None
    while low <= high:
        middle = (low + high) // 2
        candidate = frame(middle)
        if len(module.encode(candidate)) <= budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        raise module.ContractError('output_budget_exceeded', 'Byte budget cannot hold metadata and the next complete record.',
                                   {'action': 'increase_budget_or_fragment', 'restart_required_for_fragments': True}, retryable=True)
    return best


def measure(operation, repeats):
    """Measure uninstrumented wall time separately from traced peak allocation."""
    times = []
    for _ in range(repeats):
        before = time.perf_counter()
        operation()
        times.append(time.perf_counter() - before)
    tracemalloc.start()
    try:
        operation()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {'median_seconds': statistics.median(times), 'peak_bytes': peak, 'seconds': times}


def main():
    """Validate result equality first, then print a reproducible JSON record."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-path', type=Path, default=Path(__file__).resolve().parents[1] / 'tl_docx.py')
    parser.add_argument('--records', type=int, default=100000)
    parser.add_argument('--budget', type=int, default=12000)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.records < 1 or args.budget < 512 or args.repeats < 1:
        parser.error('records/repeats must be positive; budget must be at least 512')
    source = args.source_path.resolve(strict=True)
    sys.path.insert(0, str(source.parent))
    specification = importlib.util.spec_from_file_location('measured_tl_docx', source)
    if specification is None or specification.loader is None:
        parser.error('source-path must be an importable Python file')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    records = [{'type': 'paragraph', 'text': '中文 α😀 ' + 'x' * 180, 'index': index} for index in range(args.records)]
    request = {'action': 'read', 'source': 'sample.docx', 'parameters': {'max_output_bytes': args.budget, 'content': True}}
    arguments = ({'status': 'ok'}, records, args.budget, None, 'binding', request)

    def baseline():
        return baseline_page(module, *arguments)

    def optimized():
        return module.page(*arguments)

    expected = baseline()
    actual = optimized()
    if actual != expected or module.encode(actual) != module.encode(expected):
        raise AssertionError('Benchmark refused: baseline and optimized outputs differ')
    result = {'python': sys.version, 'platform': platform.platform(), 'record_count': args.records,
              'budget': args.budget, 'repeats': args.repeats, 'outputs_identical': True,
              'page_records': actual['end'] - actual['start'], 'output_bytes': len(module.encode(actual)),
              'scope': 'page() only; excludes parsing, allocated records, final stdout encoding and I/O',
              'results': {'baseline': measure(baseline, args.repeats), 'optimized': measure(optimized, args.repeats)}}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
