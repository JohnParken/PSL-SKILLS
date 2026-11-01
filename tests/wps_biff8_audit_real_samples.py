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
    from verify_et_samples import verify, check_fixed_values

    manifest = root / 'skills' / 'wps' / 'references' / 'et-real-samples.json'
    entries = json.loads(manifest.read_text(encoding='utf-8'))['samples']
    frozen_expectations = json.loads(
        manifest.with_name('real-value-expectations.json').read_text(
            encoding='utf-8'))['samples']
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
            # The experimental mode must also be tested on actual bytes. Each
            # historical original already passes the normal reader; agreement
            # proves *non-regression*, not recovery of newly unsupported files.
            salvage = read_file(path, compatibility='data',
                                salvage_unknown_metadata=True)
            if not salvage.get('salvage_requested'):
                raise ValueError('Real sample opt-in was not acknowledged')
            if salvage.get('saved_value_completeness_certified') is not False:
                raise ValueError('Experimental salvage must not certify completeness')
            if salvage.get('salvage_unknown_record_count') != 0:
                raise ValueError('Pinned sample changed: unexpected unknown records')
            if (salvage['sheets'] != book['sheets']
                    or salvage['date_system'] != book['date_system']
                    or salvage['cell_count'] != book['cell_count']
                    or salvage['cell_value_coverage'] != book['cell_value_coverage']
                    or salvage['omissions_affect_cell_values']
                       != book['omissions_affect_cell_values']):
                raise ValueError('Opt-in changed source values, cached formula status, '
                                 'or baseline sheet semantics')
            # Frozen expectations were created by the independent source
            # record oracle rather than computed by the candidate reader.
            expected = frozen_expectations[entry['filename']]
            if expected['sha256'] != original_sha:
                raise ValueError('Frozen expectation hash mismatched pinned source')
            original_values = check_fixed_values(book, expected)
            salvage_values = check_fixed_values(salvage, expected)
            if original_values != salvage_values:
                raise ValueError('Opt-in stored-value expectations differ')
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
                'static_print_titles_count': sum(
                    item.get('name') == 'PrintTitlesStatic'
                    for item in book.get('uninterpreted_metadata', [])),
                'static_filter_database_count': sum(
                    item.get('name') == 'FilterDatabaseStatic'
                    for item in book.get('uninterpreted_metadata', [])),
                'ordinary_two_area_union_count': sum(
                    item.get('name') == 'OrdinaryStaticInternalAreaName'
                    and item.get('structure', {}).get('ptg_union_raw_present') is True
                    for item in book.get('uninterpreted_metadata', [])),
                'producer_verified': False, 'effective_styles_verified': False,
                'best_effort_salvage_opt_in_checked': True,
                'best_effort_unknown_records_encountered': 0,
                'frozen_values_checked_with_salvage': salvage_values['semantic_values_checked'],
                'frozen_formula_caches_checked_with_salvage': salvage_values['formula_caches_checked'],
            })
    print(json.dumps({
        'status': 'passed_stored_value_and_audit_subset',
        'sample_count': len(entries),
        'independent_saved_values_checked': total_values,
        'unverified_formula_caches_checked': total_caches,
        'salvage_opt_in_real_originals_checked': len(reports),
        'salvage_opt_in_frozen_values_matched': sum(
            x['frozen_values_checked_with_salvage'] for x in reports),
        'salvage_opt_in_formula_caches_matched': sum(
            x['frozen_formula_caches_checked_with_salvage'] for x in reports),
        'salvage_newly_recovered_real_rejections': 0,
        'real_office_producer_version_confirmed': False,
        'formula_recalculation_performed': False,
        'effective_format_visual_validation': False,
        'samples': reports,
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
