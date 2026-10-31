"""Maintenance-only read-only acceptance of pinned ET/ETT public originals.

Downloads only explicitly pinned raw.githubusercontent.com URLs from the
existing repository manifest; verifies SHA-256 before local parsing and again
afterwards. No sample is bundled, uploaded, modified or printed as cell data.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_SAMPLE_BYTES = 8 * 1024 * 1024


def fetch_original(entry, destination):
    url = entry['download_url']
    origin = urlsplit(url)
    if (origin.scheme != 'https' or origin.hostname != 'raw.githubusercontent.com'
            or origin.username or origin.password):
        raise ValueError('Source outside pinned official GitHub raw host')
    filename = entry['filename']
    if not filename.isascii() or not filename.replace('_', '').replace('.', '').isalnum():
        raise ValueError('Unsafe sample filename')
    if entry['bytes'] > MAX_SAMPLE_BYTES:
        raise ValueError('Pinned sample exceeds budget')
    with urlopen(Request(url, headers={'User-Agent': 'wps-biff8-maintenance-readonly'}), timeout=20) as response:
        final = urlsplit(response.geturl())
        if final.scheme != 'https' or final.hostname != 'raw.githubusercontent.com':
            raise ValueError('Unapproved source redirect')
        data = response.read(MAX_SAMPLE_BYTES + 1)
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != entry['bytes'] or digest != entry['sha256']:
        raise ValueError('Public original hash or size changed: ' + filename)
    path = destination / filename
    with path.open('xb') as output:
        output.write(data)
    return path


def main():
    root = Path(__file__).resolve().parents[1]
    scripts = root / 'skills' / 'wps' / 'scripts'
    sys.path.insert(0, str(scripts))
    from wps_biff8 import read_file
    from wps_biff8_audit import audit_biff8
    from verify_et_samples import verify

    manifest = root / 'skills' / 'wps' / 'references' / 'et-real-samples.json'
    entries = json.loads(manifest.read_text(encoding='utf-8'))['samples']
    if len(entries) != 4:
        raise ValueError('Expected four already-known pinned ET/ETT samples')
    with tempfile.TemporaryDirectory(prefix='wps-biff8-real-') as temp:
        directory = Path(temp)
        paths = [fetch_original(entry, directory) for entry in entries]
        checks = verify(directory, compatibility='data')
        if len(checks) != 4 or not all(x.get('passed') for x in checks):
            raise ValueError('Independent frozen stored-cell expectation verifier failed')
        total_values = sum(x.get('semantic_values_checked', 0) for x in checks)
        total_caches = sum(x.get('formula_caches_checked', 0) for x in checks)
        if (total_values, total_caches) != (198, 33):
            raise ValueError('Pinned source-value expectation totals changed')
        reports = []
        for entry, path in zip(entries, paths):
            original_sha = entry['sha256']
            book = read_file(path, compatibility='data')
            audit = audit_biff8(book)
            assert audit['schema_version'] == 'biff8_stored_evidence_audit_v1'
            assert audit['native_et_ett_producer_verified'] is False
            assert audit['full_format_verified'] is False
            assert audit['calculation_metadata']['saved_caches_current'] == 'unknown'
            assert all(x['effective_style'] == 'unknown' for x in audit['xf_ancestry'])
            assert all(x['effective_font'] == 'unknown' for x in audit['font_sources'])
            if hashlib.sha256(path.read_bytes()).hexdigest() != original_sha:
                raise ValueError('Original sample changed after read')
            reports.append({
                'filename': entry['filename'], 'sha256': original_sha,
                'formula_count': audit['formula_caches']['total_formulas'],
                'rich_sst_count': len(audit['rich_format_runs']),
                'font_source_count': len(audit['font_sources']),
                'calc_record_count': len(audit['calculation_metadata']['records']),
                'xf_source_count': len(audit['xf_ancestry']),
                'producer_verified': False, 'effective_styles_verified': False,
            })
    print(json.dumps({
        'status': 'passed_stored_value_and_audit_subset',
        'sample_count': len(entries),
        'independent_saved_values_checked': total_values,
        'unverified_formula_caches_checked': total_caches,
        'real_office_producer_version_confirmed': False,
        'formula_recalculation_performed': False,
        'effective_format_visual_validation': False,
        'samples': reports,
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
