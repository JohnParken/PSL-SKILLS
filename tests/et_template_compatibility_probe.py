"""Opt-in maintenance HTTPS probe; no binary originals or cell values are saved.

Run from the repository root with -I -S -B and --download. The independent
source oracle imports no ET parser. Downloads require unchanged pinned hashes;
normal skill execution never calls this maintenance-only program.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import struct
import sys
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from wps_source_oracle import oracle, workbook_stream, records
sys.path.insert(0, str(ROOT / 'skills' / 'et' / 'scripts'))
from read_et import read_bytes
sys.path.insert(0, str(ROOT / 'skills/et/scripts/test'))
from verify_et_samples import check_fixed_values

BASE = ('https://raw.githubusercontent.com/winunix/wps-office-templates/'
        '64c6bd7628e7e3d88bed2fbdf6cbf7828c0762a7/'
        'deb_package/opt/kingsoft/wps-office/office6/mui/pt_BR/templates/et/')
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
NEW_SAMPLES = (
    {'path': 'Business/Amortization.ett',
     'sha256': '498f0d61d65544ec3389e8f18c838059a2e332de72a35dd8e90f2f737a0d772d',
     'oracle_sha256': 'e4b87024594158ce856ea6a10eec09a9d649350184762e952c7b88b1de496973',
     'expected_cells': 260, 'expected_values': 35, 'expected_caches': 1,
     'expected_cell_names': 8, 'expected_conditional_groups': 2},
    {'path': 'Personal/Home Analysis.ett',
     'sha256': 'a5b164d2866ae6d794b7da5cb18c96a7edfade2dc50a2a642fe6477ed8407050',
     'oracle_sha256': 'c4251900caa180b7ab78ee88d34c5596df1e74480b1d00b01f4dc9facc4a3ee8',
     'expected_cells': 309, 'expected_values': 35, 'expected_caches': 1,
     'expected_cell_names': 0, 'expected_conditional_groups': 1},
    {'path': 'Business/Start-up Expenses.ett',
     'sha256': '71f0a34a74a17989c3dd20edc64d741bf6f10e5c189148043e7f6c2354aff149',
     'oracle_sha256': '4af2a7a37d6af56716d5b152bab41d41668e422c5bf5ae61f253a1036c7af8d1',
     'expected_cells': 520, 'expected_values': 162, 'expected_caches': 24,
     'expected_cell_names': 0, 'expected_conditional_groups': 1,
     'expected_note_drawing': {'drawing_bytes': 1056, 'drawing_fragments': 16, 'object_count': 8,
                               'record_count': 56,
                               'drawing_sha256': '897511a94ebbaed4d8cbec9e748ff787f36151184583b95ffe52c6835721490e'}},
)


class NoRedirect(HTTPRedirectHandler):
    """Do not follow unknown destinations or downgrade HTTPS."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Sample redirects are not accepted')


def download(url, digest):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname != 'raw.githubusercontent.com' or parts.username or parts.port:
        raise ValueError('Only pinned raw.githubusercontent.com HTTPS samples are accepted')
    with build_opener(NoRedirect).open(url, timeout=20) as reply:
        size = reply.headers.get('Content-Length')
        if size is not None and int(size) > MAX_DOWNLOAD_BYTES:
            raise ValueError('Declared sample size exceeds budget')
        data = reply.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Original sample size/hash mismatch')
    return data


def source_cell_count(data):
    """Count recognized cell records independently, including stored blank cells."""
    stream = workbook_stream(data)
    sheet_starts = []
    for kind, payload, _ in records(stream, 0):
        if kind == 0x85:
            sheet_starts.append(struct.unpack_from('<I', payload)[0])
        if kind == 0x0A:
            break
    total = 0
    for start in sheet_starts:
        nested = 0
        for kind, payload, offset in records(stream, start):
            if kind == 0x809 and offset != start:
                nested += 1
            if kind == 0x0A:
                if nested:
                    nested -= 1
                    continue
                break
            if nested:
                continue
            if kind in (0xFD, 0x203, 0x6, 0x27E, 0x201, 0x205, 0x204):
                total += 1
            elif kind in (0xBD, 0xBE):
                first = struct.unpack_from('<H', payload, 2)[0]
                last = struct.unpack_from('<H', payload, len(payload) - 2)[0]
                width = 6 if kind == 0xBD else 2
                if last < first or len(payload) != 6 + width * (last - first + 1):
                    raise ValueError('Invalid independent multi-cell framing')
                total += last - first + 1
    return total


def verify_new(entry):
    url = BASE + quote(entry['path'])
    data = download(url, entry['sha256'])
    # Freeze independent observations before asking the target reader for a result.
    expected = oracle(data)['expected']
    oracle_digest = hashlib.sha256(json.dumps(expected, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode('utf-8')).hexdigest()
    if 'oracle_sha256' in entry and oracle_digest != entry['oracle_sha256']:
        raise ValueError('Independent source expectations changed')
    report = {'sample': entry['path'], 'url': url, 'sha256': entry['sha256'], 'bytes': len(data),
              'oracle_sha256': oracle_digest, 'oracle_values': sum(map(len, expected.values())),
              'source_cell_count': source_cell_count(data)}
    expected_opaque = {}
    if 'expected_note_drawing' in entry:
        source_records = list(records(workbook_stream(data), 0))
        drawing = b''.join(payload for kind, payload, _ in source_records if kind == 0xEC)
        if hashlib.sha256(drawing).hexdigest() != entry['expected_note_drawing']['drawing_sha256']:
            raise ValueError('Independent drawing fragment hash changed')
        # Pinned source offsets observed independently before target parsing.
        expected_opaque = {offset: (kind, payload) for kind, payload, offset in source_records
                           if 15324 <= offset < 18300 or offset in (18542, 18564)}
        if len(expected_opaque) != 58:
            raise ValueError('Pinned note and validation source boundaries changed')
    result = read_bytes(data)
    actual = {sheet['name']: sheet['cells'] for sheet in result['sheets']}
    checked = cached = 0
    for name, cells in expected.items():
        for address, value in cells.items():
            cell = actual[name][address]
            truth = Decimal(value['value']) if value['type'] == 'number' else value['value']
            if cell['type'] != value['type'] or cell['value'] != truth:
                raise ValueError('Independent saved-value mismatch')
            if value.get('formula'):
                if 'formula' not in cell or cell['cache_status'] != 'unverified':
                    raise ValueError('Formula cache provenance mismatch')
                cached += 1
            checked += 1
    if result['cell_count'] != report['source_cell_count'] or result['status'] != 'partial':
        raise ValueError('Cell count or partial status changed')
    cell_names = sum(item['name'] == 'OrdinaryStaticInternalCellName' for item in result['uninterpreted_metadata'])
    conditional_groups = sum(item['name'] == 'CondFmtOpaque' for item in result['uninterpreted_metadata'])
    observed = (result['cell_count'], checked, cached, cell_names, conditional_groups)
    frozen = tuple(entry[key] for key in ('expected_cells', 'expected_values', 'expected_caches',
                                         'expected_cell_names', 'expected_conditional_groups'))
    if observed != frozen:
        raise ValueError('Frozen source-derived observations changed')
    if expected_opaque:
        drawings = result.get('note_drawings', [])
        if len(drawings) != 1 or any(drawings[0].get(key) != value for key, value in entry['expected_note_drawing'].items()):
            raise ValueError('Note drawing framing differs from frozen source observations')
        retained = {item['offset']: (int(item['record_id'], 16), bytes.fromhex(item['payload_hex']))
                    for item in result['uninterpreted_metadata'] if item['offset'] in expected_opaque}
        if retained != expected_opaque:
            raise ValueError('Retained note/validation metadata differs from independent source bytes')
        report['opaque_source_records_matched'] = len(retained)
        report['note_drawing'] = entry['expected_note_drawing']
    if result['reading_assessment']['full_format_verified'] or result['formulas_executed']:
        raise ValueError('Read-only/noncertifying boundary changed')
    if hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise ValueError('Original in-memory bytes changed')
    return {**report, 'status': 'partial', 'saved_values_compared': checked,
            'unverified_formula_caches_compared': cached, 'cells': result['cell_count'],
            'static_cell_names': cell_names, 'conditional_groups': conditional_groups,
            'input_bytes_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Explicitly permit the bounded maintenance HTTPS reads')
    parser.add_argument('--include-baseline', action='store_true')
    args = parser.parse_args()
    if not args.download:
        parser.error('--download is required; this maintenance command uses HTTPS')
    reports = []
    passed = True
    for entry in NEW_SAMPLES:
        try:
            reports.append(verify_new(entry))
        except (OSError, ValueError, KeyError, AssertionError, struct.error) as error:
            passed = False
            reports.append({'sample': entry['path'], 'status': 'failed',
                            'error_type': type(error).__name__, 'message': str(error)})
    if args.include_baseline:
        refs = ROOT / 'docs/maintenance/et'
        samples = json.loads((refs / 'et-real-samples.json').read_text(encoding='utf-8'))['samples']
        expectations = json.loads((refs / 'real-value-expectations.json').read_text(encoding='utf-8'))['samples']
        for entry in samples:
            try:
                data = download(entry['download_url'], entry['sha256'])
                result = read_bytes(data)
                values_checked = check_fixed_values(result, expectations[entry['filename']])
                if result['status'] != 'partial' or result['reading_assessment']['full_format_verified']:
                    raise ValueError('Baseline partial/noncertifying boundary changed')
                reports.append({'sample': entry['filename'], 'sha256': entry['sha256'], 'status': result['status'], **values_checked})
            except (OSError, ValueError, KeyError) as error:
                passed = False
                reports.append({'sample': entry['filename'], 'status': 'failed', 'message': str(error)})
    print(json.dumps({'status': 'passed' if passed else 'failed',
                      'platform': platform.platform(), 'python': platform.python_version(),
                      'isolated': bool(sys.flags.isolated), 'no_site': bool(sys.flags.no_site),
                      'oracle_file_sha256': hashlib.sha256((ROOT / 'tests' / 'wps_source_oracle.py').read_bytes()).hexdigest(),
                      'samples': reports}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
