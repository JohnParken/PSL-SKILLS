"""Maintenance-only repeatable CLI output benchmark; no runtime dependency.

From the repository root, use an available Python >=3.9 with Expat >=2.6:
python -I -S -B skills/et/scripts/test/benchmark_content_page.py --samples ./public_samples
The four optional originals must match docs/maintenance/et/et-real-samples.json.
Synthetic data is generated in an exclusive temporary directory and cleaned up.
Numbers include subprocess startup, report bytes (not tokens), and do not measure RSS.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(Path(__file__).resolve().parent)]
from test_wps_ooxml_sheet import package, T, R


def measure(directory, name, repeats):
    """Print complete JSON measurements without printing workbook data."""
    modes = [('default', []), ('summary', ['--summary']),
             ('all-content', ['--all-content']), ('full', ['--profile', 'full'])]
    for label, options in modes:
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            process = subprocess.run(
                [sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'et_cli.py'), 'run', './' + name, *options],
                cwd=directory, capture_output=True, check=False)
            times.append(time.perf_counter() - start)
            if process.returncode not in (0, 3):
                raise RuntimeError(f'{name}/{label} failed: {process.returncode}: {process.stderr!r}: {process.stdout!r}')
        value = json.loads(process.stdout)
        print(json.dumps({'sample': name, 'mode': label, 'exit': process.returncode,
                          'stdout_bytes': len(process.stdout), 'stderr_bytes': len(process.stderr),
                          'median_ms': round(statistics.median(times) * 1000, 2), 'repeats': repeats,
                          'schema': value.get('schema_version'), 'total_items': value.get('total_items'),
                          'returned_items': value.get('returned_items'), 'has_more': value.get('has_more')}, ensure_ascii=False))


def main():
    """Optionally benchmark pinned public originals, then a 50k-cell OOXML fixture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error('--repeats must be 1..20')
    if args.samples:
        manifest = SCRIPTS.parents[2] / 'docs' / 'maintenance' / 'et' / 'et-real-samples.json'
        samples = json.loads(manifest.read_text(encoding='utf-8'))['samples']
        for sample in samples:
            source = args.samples / sample['filename']
            raw = source.read_bytes()
            if len(raw) != sample['bytes'] or hashlib.sha256(raw).hexdigest() != sample['sha256']:
                raise ValueError('Public sample does not match pinned manifest: ' + sample['filename'])
            measure(args.samples.resolve(), source.name, args.repeats)
            if hashlib.sha256(source.read_bytes()).hexdigest() != sample['sha256']:
                raise ValueError('Input changed: ' + sample['filename'])
    xml = (f'<worksheet xmlns="{T}" xmlns:r="{R}"><sheetData>'
           + ''.join(f'<row r="{row}"><c r="A{row}" t="inlineStr"><is><t>row{row}</t></is></c></row>'
                     for row in range(1, 50001)) + '</sheetData></worksheet>')
    with tempfile.TemporaryDirectory(prefix='et-page-bench-') as task:
        Path(task, 'synthetic.et').write_bytes(package(overrides={'xl/worksheets/sheet1.xml': xml}))
        measure(task, 'synthetic.et', args.repeats)


if __name__ == '__main__':
    main()
