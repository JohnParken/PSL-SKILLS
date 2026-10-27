"""Read-only, offline checks of exact public ET/ETT samples listed in the manifest."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_biff8 import read_file


def check_fixed_values(result, expectation):
    """Compare stored typed values with independently frozen source observations."""
    sheets = {sheet['name']: sheet['cells'] for sheet in result['sheets']}
    checked = formula_caches = 0
    for name, cells in expectation['sheets'].items():
        if name not in sheets:
            raise ValueError('Expected source sheet missing: ' + name)
        for address, expected in cells.items():
            actual = sheets[name].get(address)
            if actual is None or actual['type'] != expected['type']:
                raise ValueError('Stored value type differs at ' + name + '!' + address)
            value = Decimal(expected['value']) if expected['type'] == 'number' else expected['value']
            if actual['value'] != value:
                raise ValueError('Stored value differs at ' + name + '!' + address)
            if expected.get('formula'):
                if 'formula' not in actual or actual.get('cache_status') != 'unverified':
                    raise ValueError('Expected unverified formula cache at ' + name + '!' + address)
                formula_caches += 1
            checked += 1
    return {'semantic_values_checked': checked, 'formula_caches_checked': formula_caches}


def verify(directory, chart_policy="reject"):
    manifest = Path(__file__).resolve().parent.parent / 'references' / 'et-real-samples.json'
    entries = json.loads(manifest.read_text(encoding='utf-8'))['samples']
    expected_path = manifest.with_name('real-value-expectations.json')
    expectations = json.loads(expected_path.read_text(encoding='utf-8'))['samples']
    reports = []
    for entry in entries:
        if chart_policy == 'opaque':
            entry = {**entry, **entry.get('opaque_expectations', {})}
        path = directory / entry['filename']
        report = {'filename': entry['filename'], 'passed': False}
        try:
            with path.open('rb') as stream:
                data = stream.read(32 * 1024 * 1024 + 1)
            if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Sample length/SHA-256 differs from the original download')
            try:
                result = read_file(path, compatibility='legacy_excel_clsid', chart_policy=chart_policy)
            except ValueError as exc:
                if entry['expected_exit'] != 1 or entry['error_contains'] not in str(exc):
                    raise
                report['observed'] = 'expected_unsupported'
                report['message'] = str(exc)
            else:
                if entry['expected_exit'] != 0:
                    raise ValueError('Unsupported sample unexpectedly accepted; investigate before updating expectations')
                sheets = result['sheets']
                metrics = {
                    'sheets': [sheet['name'] for sheet in sheets],
                    'cell_count': result['cell_count'],
                    'merge_count': sum(len(sheet['merges']) for sheet in sheets),
                    'formula_count': sum('formula' in cell for sheet in sheets for cell in sheet['cells'].values()),
                }
                metrics['chart_count'] = len(result['embedded_charts'])
                if any(metrics[key] != entry.get(key, 0) for key in metrics):
                    raise ValueError('Extracted sheet/cell/merge/formula metrics changed')
                if 'chart_sha256' in entry and [c['sha256'] for c in result['embedded_charts']] != entry['chart_sha256']:
                    raise ValueError('Retained chart hash changed')
                for address, text in entry['text_checks'].items():
                    if sheets[0]['cells'].get(address, {}).get('value') != text:
                        raise ValueError('Text spot check changed at ' + address)
                if result['native_et_ett_parsed'] or result['wps_compatibility_verified']:
                    raise ValueError('Subset results must not claim full native WPS acceptance')
                expectation = expectations[entry['filename']]
                if expectation['sha256'] != entry['sha256']:
                    raise ValueError('Frozen value expectations refer to different source bytes')
                semantic = check_fixed_values(result, expectation)
                report.update(observed='biff8_subset_extracted', **metrics, **semantic)
            with path.open('rb') as stream:
                after = stream.read(32 * 1024 * 1024 + 1)
            if hashlib.sha256(after).hexdigest() != entry['sha256']:
                raise ValueError('Original sample changed during verification')
            report['passed'] = True
        except (OSError, ValueError) as exc:
            report['message'] = str(exc)
        reports.append(report)
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Task-relative directory containing separately obtained original samples')
    parser.add_argument("--chart-policy", choices=("reject", "opaque"), default="reject")
    args = parser.parse_args()
    reports = verify(args.directory, args.chart_policy)
    passed = all(report['passed'] for report in reports)
    print(json.dumps({'status': 'passed' if passed else 'failed', 'samples': reports}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
