"""Read-only, offline checks of exact public ET/ETT samples listed in the manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_biff8 import read_file


def verify(directory):
    manifest = Path(__file__).resolve().parent.parent / 'references' / 'et-real-samples.json'
    entries = json.loads(manifest.read_text(encoding='utf-8'))['samples']
    reports = []
    for entry in entries:
        path = directory / entry['filename']
        report = {'filename': entry['filename'], 'passed': False}
        try:
            with path.open('rb') as stream:
                data = stream.read(32 * 1024 * 1024 + 1)
            if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Sample length/SHA-256 differs from the original download')
            try:
                result = read_file(path, compatibility='legacy_excel_clsid')
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
                if any(metrics[key] != entry[key] for key in metrics):
                    raise ValueError('Extracted sheet/cell/merge/formula metrics changed')
                for address, text in entry['text_checks'].items():
                    if sheets[0]['cells'].get(address, {}).get('value') != text:
                        raise ValueError('Text spot check changed at ' + address)
                if result['native_et_ett_parsed'] or result['wps_compatibility_verified']:
                    raise ValueError('Subset results must not claim full native WPS acceptance')
                report.update(observed='biff8_subset_extracted', **metrics)
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
    args = parser.parse_args()
    reports = verify(args.directory)
    passed = all(report['passed'] for report in reports)
    print(json.dumps({'status': 'passed' if passed else 'failed', 'samples': reports}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
