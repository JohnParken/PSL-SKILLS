"""Maintenance-only before/after benchmarks on identical synthetic workbook bytes.

Run from a confirmed repository root with --before-root and --after-root pointing
at complete source snapshots. Inputs are created once; no model tokens measured.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import pyexpat
import statistics
import subprocess
import sys
import tempfile
import time


def worker(root, scenario, rows, task):
    os.chdir(task)
    sys.path.insert(0, str(root / 'skills/table-workflow/scripts'))
    from workflow_runtime import Dependencies
    from table_workflow import Workflow
    dependency = Dependencies(str(root / 'skills/xlsx'))
    try:
        started = time.perf_counter()
        flow = Workflow(dependency)
        if scenario == 'names':
            result = flow.names({'path': 'names.xlsx'})
            assert result['names'] == ['Data'] and result['source_status'] == 'native_loaded'
        else:
            spec = {'path': 'target.xlsx', 'sha256': hashlib.sha256(Path('target.xlsx').read_bytes()).hexdigest(),
                    'sheet': 'Data', 'range': 'A1:C' + str(rows + 1), 'header_rows': '1', 'keys': ['A', 'B'],
                    'columns': [{'column': 'C', 'metric': 'amount', 'unit': '元', 'period': '2026-10', 'decimals': 2}]}
            _, _, keys, _ = flow.target(spec)
            from decimal import Decimal
            assert len(keys) == rows and keys[2] == (('text', '000000'), ('number', Decimal('0.125')))
            assert keys[rows + 1] == (('text', str(rows - 1).zfill(6)), ('number', Decimal('0.125')))
        elapsed = time.perf_counter() - started
        print(json.dumps({'seconds': elapsed, 'transport': dependency.client.metrics}))
    finally:
        dependency.close()


def generate(root, rows, names_rows, task):
    os.chdir(task)
    sys.path.insert(0, str(root / 'skills/xlsx/scripts'))
    from decimal import Decimal
    from stdlib_xlsx import Workbook
    cells = {'A1': {'value': '编号'}, 'B1': {'value': '类别'}, 'C1': {'value': '金额'}}
    for i in range(rows):
        cells['A' + str(i + 2)] = {'value': str(i).zfill(6)}
        cells['B' + str(i + 2)] = {'value': Decimal('0.125')}
    Workbook.create({'sheets': [{'name': 'Data', 'cells': cells}]}).save('target.xlsx')
    cells = {col + str(i): {'value': i} for i in range(1, names_rows + 1) for col in ('A', 'B')}
    Workbook.create({'sheets': [{'name': 'Data', 'cells': cells}]}).save('names.xlsx')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-root', type=Path)
    parser.add_argument('--after-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--rows', type=int, default=3000)
    parser.add_argument('--names-rows', type=int, default=10000)
    parser.add_argument('--worker', choices=('names', 'target', 'generate'))
    parser.add_argument('--task', type=Path)
    args = parser.parse_args()
    if args.worker:
        if args.worker == 'generate':
            generate(args.after_root, args.rows, args.names_rows, args.task)
        else:
            worker(args.after_root, args.worker, args.rows, args.task)
        return
    if not args.before_root or not 1 <= args.repeats <= 20 or not 1 <= args.rows <= 10000:
        parser.error('Supply baseline root; repeats=1..20 and rows=1..10000')
    roots = {'before': args.before_root.resolve(), 'after': args.after_root.resolve()}
    script = Path(__file__).resolve()
    result = {'platform': platform.platform(), 'python': sys.version.split()[0], 'expat': pyexpat.EXPAT_VERSION,
              'repeats': args.repeats, 'target_rows': args.rows, 'target_key_columns': 2, 'names_stored_cells': args.names_rows * 2,
              'scope': 'Workflow.names/target: immutable input snapshot, CLI startup, complete parsing, IPC and result validation; '
                       'excludes dependency self-check/capability startup, fixture generation, ZIP writing and model/host usage',
              'source_hashes': {}, 'scenarios': {}}
    for label, root in roots.items():
        files = ['skills/xlsx/scripts/stdlib_xlsx.py', 'skills/xlsx/scripts/xlsx_cli.py',
                 'skills/table-workflow/scripts/table_workflow.py', 'skills/table-workflow/scripts/workflow_runtime.py']
        result['source_hashes'][label] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
    with tempfile.TemporaryDirectory(prefix='psl-large-benchmark-') as task:
        command = [sys.executable, '-I', '-S', '-B', str(script), '--task', task, '--rows', str(args.rows), '--names-rows', str(args.names_rows)]
        subprocess.run([*command, '--worker', 'generate', '--after-root', str(roots['after'])], check=True, capture_output=True)
        result['input_sha256'] = {name: hashlib.sha256((Path(task) / name).read_bytes()).hexdigest() for name in ('target.xlsx', 'names.xlsx')}
        for scenario in ('target', 'names'):
            result['scenarios'][scenario] = {}
            for label, root in roots.items():
                records = []
                for _ in range(args.repeats):
                    child = subprocess.run([*command, '--worker', scenario, '--after-root', str(root)], check=True, capture_output=True, text=True)
                    records.append(json.loads(child.stdout))
                result['scenarios'][scenario][label] = {'runs': records, 'median_seconds': statistics.median(record['seconds'] for record in records)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
