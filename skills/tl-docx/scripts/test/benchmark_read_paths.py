"""Synthetic report benchmark; compare only sibling-index strategy in fresh processes.

Run with the skill's declared dependencies and -I -B. Results include full-report
hashes, report wall time, process wall time and process peak RSS. No user inputs,
external programs, disk DOCX files, or persistent caches are read or written.
"""
import argparse
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time
from unittest.mock import patch
from zipfile import ZipFile, ZIP_STORED

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_read
from tl_package import Package


def sample(count):
    """Build an independent OPC package with exactly count plain body paragraphs."""
    document = Document()
    raw = BytesIO()
    document.save(raw)
    with ZipFile(BytesIO(raw.getvalue())) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    paragraphs = ''.join(f'<w:p><w:r><w:t>Synthetic paragraph {i}</w:t></w:r></w:p>' for i in range(count))
    parts['word/document.xml'] = (
        f'<w:document xmlns:w="{tl_read.W_NS}"><w:body>{paragraphs}</w:body></w:document>'
    ).encode('utf-8')
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_STORED) as target:
        for name, value in parts.items():
            target.writestr(name, value)
    return Package(output.getvalue())


def measure(count, strategy):
    """Time report extraction; fixture setup is outside the timed region."""
    package = sample(count)
    started = time.perf_counter()
    if strategy == 'legacy':
        with patch.object(tl_read.ReportReader, '_path', lambda self, node: tl_read._path(node)):
            result = tl_read.report(package)
    else:
        result = tl_read.report(package)
    elapsed = time.perf_counter() - started
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True).encode('utf-8')
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == 'darwin':
        peak /= 1024
    # Linux ru_maxrss can inherit a pre-exec high-water mark; VmHWM is this image.
    if sys.platform.startswith('linux'):
        for line in Path('/proc/self/status').read_text(encoding='utf-8').splitlines():
            if line.startswith('VmHWM:'):
                peak = int(line.split()[1])
                break
    return {'paragraphs': count, 'strategy': strategy, 'report_seconds': elapsed,
            'peak_rss_kib': peak, 'report_bytes': len(payload),
            'report_sha256': sha256(payload).hexdigest()}


def main():
    """Run bounded fresh-process repetitions and verify exact result hashes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[250, 2500, 10000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--child', choices=['legacy', 'indexed'])
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10 or any(not 1 <= size <= 10000 for size in args.sizes):
        parser.error('repeats must be 1..10; sizes must be 1..10000')
    if args.child:
        print(json.dumps(measure(args.sizes[0], args.child)))
        return
    rows = []
    for count in args.sizes:
        for strategy in ('legacy', 'indexed'):
            for _ in range(args.repeats):
                started = time.perf_counter()
                child = subprocess.run(
                    [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                     '--child', strategy, '--sizes', str(count)],
                    capture_output=True, check=True, timeout=60,
                    env=dict(os.environ, PATH=''),
                )
                row = json.loads(child.stdout)
                row['process_seconds'] = time.perf_counter() - started
                rows.append(row)
        matching = [row for row in rows if row['paragraphs'] == count]
        if len({row['report_sha256'] for row in matching}) != 1:
            raise AssertionError('Full reports differ between strategies/repetitions')
    summary = []
    for count in args.sizes:
        for strategy in ('legacy', 'indexed'):
            selected = [row for row in rows if row['paragraphs'] == count and row['strategy'] == strategy]
            summary.append({'paragraphs': count, 'strategy': strategy, **{
                name: statistics.median(row[name] for row in selected)
                for name in ('report_seconds', 'process_seconds', 'peak_rss_kib', 'report_bytes')
            }})
    print(json.dumps({'platform': platform.platform(), 'python': sys.version,
                      'scope': 'report API, not CLI pagination or cache; synthetic only',
                      'summary': summary, 'runs': rows}, indent=2))


if __name__ == '__main__':
    main()
