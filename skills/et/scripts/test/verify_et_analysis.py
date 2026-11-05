"""Offline maintenance check of frozen original-template analysis observations.

Expectations came from the independent repository source oracle before running
the analysis engine. Original binaries are not distributed or downloaded here.
"""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import argparse
from fractions import Fraction
import hashlib
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from et_table_analysis import analyze
from et_table_preview import preview
from read_et import read_bytes
from wps_access import read_input

SAMPLES = {
    '1124b720ad792c52cf5a5e39cd99bbebd512b200523070dd3fd035ea37af427b': {
        'name': 'Balance_Sheet.ett', 'column': 'B', 'region': 'B8:B52',
        'expected_sha256': 'd9a1e97b27120f3ba19223236364b96f73ea1223ecb3fc040dbe957f2780917e'},
    '23d27b4e72eb4c83dd888593df2552d9d4134749762918baeffa57b97799a9eb': {
        'name': 'Schedule.ett', 'column': 'C', 'region': 'C4:C15',
        'expected_sha256': '685f446189eaf97caf7176aa4fa8d6c4ce2f67e0237526fc9d229d38a9273429'},
    '71f0a34a74a17989c3dd20edc64d741bf6f10e5c189148043e7f6c2354aff149': {
        'name': 'Start-up Expenses.ett', 'column': 'C', 'region': 'C7:C71',
        'expected_sha256': '97f1b594bb6cbbb16a5f425e9fb4a4da2674178a5c8ac66420399df6c2a17dd2'},
}
STARTUP_SHA256 = '71f0a34a74a17989c3dd20edc64d741bf6f10e5c189148043e7f6c2354aff149'
STARTUP_ROWS_EXPECTATION_SHA256 = '10657378e3b15a62b94a7ccbdcfd16e883417ea1202a49e493958b2a695ca1db'
SCHEDULE_SHA256 = '23d27b4e72eb4c83dd888593df2552d9d4134749762918baeffa57b97799a9eb'
SCHEDULE_NONZERO_EXPECTATION_SHA256 = 'b565d02005e5097ee4cc63be00c57368c4a7fca1443fb30117a4b4aa1035b537'


def observation_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode('utf-8')).hexdigest()


def verify_schedule_nonzero(source):
    """Compare nonzero values, rounded means, quality and preview to frozen evidence."""
    name = source['sheets'][0]['name']
    columns = ('C', 'E', 'F', 'G')
    query = {'sheet': name, 'range': 'C4:G15', 'group_by': ['C'],
             'aggregates': [{'op': 'sum', 'column': column, 'as': column} for column in columns]}
    cached = analyze(source, {**query, 'formula_policy': 'cached'})
    default = analyze(source, query)
    view = preview(source, {'sheet': name, 'range': 'C1:G4'})
    observed_columns = {}
    for column in columns:
        summary = cached['profiles'][column]
        quality = cached['quality_report']['columns'][column]
        findings = {finding['code']: finding for finding in quality['findings']}
        observed_columns[column] = {
            'numeric_count': summary['numeric_count'],
            'nonzero_count': sum(row['cells'][column]['value'] != 0 for row in cached['rows']),
            'cache_count': summary['formula_caches_used'],
            **{operation + '_fraction': str(Fraction(summary[operation])) for operation in ('sum', 'min', 'max', 'median', 'mean')},
            'mean_inexact': summary['mean_inexact'],
            'numeric_coverage': {key: quality['numeric_coverage'][key] for key in ('numerator', 'denominator')},
            'quality_unverified_positions': findings.get('unverified_cache', {}).get('positions', 0),
            'default_numeric_count': default['profiles'][column]['numeric_count'],
            'default_formula_excluded': default['profiles'][column]['analysis_states'].get('formula_excluded', 0),
        }
    values = [[row['cells'][column]['coordinate'], str(Fraction(row['cells'][column]['value'])), row['cells'][column]['formula']]
              for row in cached['rows'] for column in columns]
    preview_cells = [cell for row in view['rows'] for cell in row['cells'].values() if cell['type'] not in ('missing', 'blank')]
    preview_values = [[cell['coordinate'], cell['type'], str(Fraction(cell['value'])) if cell['type'] == 'number' else cell['value'], cell['formula']]
                      for cell in preview_cells]
    group_values = [[str(Fraction(group['keys'][0]['value'])), [str(Fraction(group['measures'][column]['value'])) for column in columns]]
                    for group in cached['groups']]
    observed = {'columns': observed_columns, 'numeric_values_sha256': observation_digest(values),
                'preview_known_coordinates': [cell['coordinate'] for cell in preview_cells],
                'preview_values_sha256': observation_digest(preview_values),
                'group_count': cached['group_page']['total'], 'group_sums_sha256': observation_digest(group_values)}
    digest = observation_digest(observed)
    if digest != SCHEDULE_NONZERO_EXPECTATION_SHA256 or not all(result['status'] == 'partial' for result in (cached, default, view)):
        raise ValueError('Nonzero numeric/quality/preview results differ from frozen independent source observations')
    return {'status': 'passed', 'expectation_sha256': digest, 'nonzero_values_matched': 48,
            'numeric_caches_matched': 12, 'groups_matched': 12, 'preview_values_matched': len(preview_cells),
            'exact_fraction_statistics_matched': True, 'independently_rounded_means_matched': True,
            'default_and_cached_policy_coverage_matched': True}


def verify_startup_rows(source):
    """Check frozen source observations; never infer business row roles at runtime."""
    name = source['sheets'][0]['name']
    excluded = [11, 24, 31, 39, 48]
    query = {'sheet': name, 'range': 'C7:C48', 'formula_policy': 'cached',
             'filters': [{'column': 'C', 'op': 'ge', 'type': 'number', 'value': '-1e1100'}]}
    raw = analyze(source, query)
    detail = analyze(source, {**query, 'exclude_rows': excluded})
    heading = analyze(source, {'sheet': name, 'range': 'B1:D3', 'header_rows': '1'})
    merged = analyze(source, {'sheet': name, 'range': 'F2:G2', 'formula_policy': 'cached', 'merge_policy': 'fill_text'})
    columns = heading['header']['columns'][1:]
    if columns[0]['path'] != columns[1]['path'] or not all(result['status'] == 'partial' for result in (raw, detail, heading, merged)):
        raise ValueError('Original merged-header or partial boundary changed')
    observed = {
        'excluded_source_rows': detail['scope']['excluded_source_rows'],
        'raw_numeric_count': raw['profiles']['C']['numeric_count'],
        'raw_cache_count': raw['profiles']['C']['formula_caches_used'],
        'numeric_source_rows': [row['source_row'] for row in detail['rows']],
        'detail_numeric_count': detail['profiles']['C']['numeric_count'],
        'detail_cache_count': detail['profiles']['C']['formula_caches_used'],
        'detail_sum_fraction': str(Fraction(detail['profiles']['C']['sum'])),
        'header_value_sha256': observation_digest(columns[0]['path'][0]),
        'header_sources': [column['levels'][0]['source'] for column in columns],
        'header_merge': columns[0]['levels'][0]['merge'],
        'merge_anchor_numeric_count': merged['profiles']['F']['numeric_count'],
        'merge_nonanchor_numeric_count': merged['profiles']['G']['numeric_count'],
        'merge_anchor_cache_count': merged['profiles']['F']['formula_caches_used'],
        'merge_anchor_sum_fraction': str(Fraction(merged['profiles']['F']['sum'])),
    }
    digest = observation_digest(observed)
    if digest != STARTUP_ROWS_EXPECTATION_SHA256:
        raise ValueError('Row-selection or merged-cell statistics differ from frozen independent source observations')
    return {'expectation_sha256': digest, 'status': 'passed', 'excluded_rows': len(excluded),
            'detail_numeric_count_matched': observed['detail_numeric_count'], 'detail_cache_count_matched': observed['detail_cache_count'],
            'numeric_row_positions_matched': True, 'header_values_and_sources_matched': True,
            'merged_numeric_anchor_not_repeated': True, 'fraction_sums_matched': True}


def verify_bytes(data):
    digest = hashlib.sha256(data).hexdigest()
    if digest not in SAMPLES:
        raise ValueError('Original hash is not in the frozen analysis sample set')
    sample = SAMPLES[digest]
    source = read_bytes(data)
    if len(source['sheets']) != 1:
        raise ValueError('Frozen original worksheet count changed')
    column = sample['column']
    result = analyze(source, {'sheet': source['sheets'][0]['name'], 'range': sample['region'], 'formula_policy': 'cached',
                             'group_by': [column], 'filters': [{'column': column, 'op': 'ge', 'type': 'number', 'value': '-1e1100'}],
                             'aggregates': [{'op': 'sum', 'column': column, 'as': 'total'}], 'limit': 0, 'group_limit': 10000})
    profile = result['profiles'][column]
    observed = {'column': column, 'region': sample['region'], 'numeric_count': profile['numeric_count'],
                'cached_count': profile['formula_caches_used'], 'distinct_numbers': result['group_page']['total'],
                'sum_fraction': str(Fraction(profile['sum']))}
    observed_digest = hashlib.sha256(json.dumps(observed, sort_keys=True).encode('utf-8')).hexdigest()
    grouped_sum = sum(Fraction(group['measures']['total']['value']) for group in result['groups'])
    if observed_digest != sample['expected_sha256'] or grouped_sum != Fraction(profile['sum']):
        raise ValueError('Statistics differ from frozen independent source observations')
    if result['status'] != 'partial' or result['formulas_executed'] or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Read-only or partial boundary changed')
    report = {'sample': sample['name'], 'sha256': digest, 'expectation_sha256': observed_digest,
              'numeric_count_matched': profile['numeric_count'], 'cached_count_matched': profile['formula_caches_used'],
              'distinct_groups_matched': result['group_page']['total'], 'fraction_sum_matched': True, 'status': 'partial'}
    if digest == STARTUP_SHA256:
        report['row_selection_validation'] = verify_startup_rows(source)
    if digest == SCHEDULE_SHA256:
        report['nonzero_quality_preview_validation'] = verify_schedule_nonzero(source)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='+', help='Task-relative original ET/ETT paths; no originals are downloaded')
    args = parser.parse_args()
    reports = []
    failed = False
    for path in args.inputs:
        try:
            reports.append(verify_bytes(read_input(path)))
        except (OSError, ValueError) as error:
            failed = True
            reports.append({'input': path, 'status': 'failed', 'message': str(error)})
    print(json.dumps({'status': 'failed' if failed else 'passed', 'samples': reports}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
