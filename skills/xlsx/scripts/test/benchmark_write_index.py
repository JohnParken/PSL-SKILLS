#!/usr/bin/env python3
"""Maintenance-only synthetic before/after write benchmark; no output files.

Pass the saved pre-change stdlib_xlsx.py and current script. Both use the
current script's sibling runtime modules/assets, isolating the core change.
This is not a universal speedup claim or a ZIP-I/O/real-user workload benchmark.
"""
import argparse
import hashlib
from pathlib import Path
import platform
import pyexpat
import statistics
import sys
import time
import types


def load_core(path, runtime, name):
    source = path.read_bytes()
    module = types.ModuleType(name)
    # Use identical sibling modules/templates, varying only the saved core source.
    module.__file__ = str(runtime)
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return vars(module)['Workbook'], hashlib.sha256(source).hexdigest()


def spec(cells):
    return {'sheets': [{'name': 'Data', 'cells': cells}]}


def benchmark(before, after, rows, repeats):
    old, before_hash = load_core(before, after, 'xlsx_before')
    new, after_hash = load_core(after, after, 'xlsx_after')
    creation = spec({f'{col}{row}': {'value': row} for row in range(1, rows + 1) for col in 'ABC'})
    formulas = spec({
        **{f'A{row}': {'value': row} for row in range(1, rows + 1)},
        **{f'B{row}': {'formula': f'A{row}*2'} for row in range(1, rows + 1)},
    })
    base = new.create(formulas).parts
    patch = spec({f'A{row}': {'value': row + 1} for row in range(1, rows + 1)})
    result = {
        'environment': {'platform': platform.platform(), 'machine': platform.machine(),
                        'python': sys.version, 'expat': pyexpat.EXPAT_VERSION},
        'source_sha256': {'before': before_hash, 'after': after_hash},
        'rows': rows, 'create_cells': rows * 3, 'source_cells': rows * 2,
        'patch_cells': rows, 'formula_count': rows, 'repeats': repeats,
        'scope': ('Synthetic in-memory operations, no ZIP I/O. Create includes template read; '
                  'patch/calculate exclude Workbook construction. Fresh indexes included. '
                  'Same current sibling runtime/assets for both versions. Default Python GC.'),
        'operations': {},
    }
    for operation in ('create', 'patch', 'calculate'):
        metric = {}
        outputs = []
        for label, cls in (('before', old), ('after', new)):
            samples = []
            for _ in range(repeats):
                book = cls(dict(base)) if operation != 'create' else None
                start = time.perf_counter()
                if operation == 'create':
                    book = cls.create(creation)
                elif operation == 'patch':
                    book.patch(patch)
                else:
                    report = book.calculate()
                    assert report['calculated_count'] == rows
                samples.append(time.perf_counter() - start)
            metric[label + '_seconds'] = samples
            metric[label + '_median_seconds'] = statistics.median(samples)
            outputs.append(book.parts)
        assert outputs[0] == outputs[1], operation + ' package-part parity failed'
        metric['parts_equal'] = True
        metric['median_ratio_before_over_after'] = metric['before_median_seconds'] / metric['after_median_seconds']
        result['operations'][operation] = metric
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', type=Path, default=Path(__file__).resolve().parents[1] / 'stdlib_xlsx.py')
    parser.add_argument('--rows', type=int, default=3000)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.rows < 1 or args.rows > 60000 or args.repeats < 1:
        parser.error('rows must be 1..60000 and repeats must be positive')
    import json
    print(json.dumps(benchmark(args.before.resolve(), args.after.resolve(), args.rows, args.repeats), indent=2))


if __name__ == '__main__':
    main()
