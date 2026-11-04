"""Bounded, factual coverage reports; no quality score or business inference."""
from collections import Counter
from copy import deepcopy


def header_quality(header):
    if header is None:
        return {'declared': False, 'inferred': False}
    empty, unavailable, identities = [], [], {}
    for column in header['columns']:
        path = column['path']
        meaningful = [part for part in path if part['type'] not in ('blank', 'missing', 'unavailable')
                      and not (part['type'] == 'text' and part['value'] == '')]
        if any(part['type'] == 'unavailable' for part in path):
            unavailable.append(column['column'])
        elif not meaningful:
            empty.append(column['column'])
        else:
            identity = tuple((part['type'], part['value']) for part in path)
            identities.setdefault(identity, []).append(column['column'])
    return {'declared': True, 'inferred': False, 'empty_label_columns': empty,
            'unavailable_label_columns': unavailable,
            'duplicate_label_columns': [columns for columns in identities.values() if len(columns) > 1],
            'physical_column_ids_preserved': True}


def column_quality(cells, summary, sample_limit):
    counts, samples = Counter(), {}
    partition = {'number_included': 0, 'unavailable': 0, 'blank': 0, 'missing': 0, 'non_numeric_usable': 0}
    usable_types = set()

    def observe(code, cell):
        counts[code] += 1
        coordinates = samples.setdefault(code, [])
        if len(coordinates) < sample_limit:
            coordinates.append(cell['coordinate'])

    for cell in cells:
        state, kind = cell['analysis_state'], cell['type']
        if state != 'usable':
            partition['unavailable'] += 1
            observe(state, cell)
        elif kind in ('blank', 'missing'):
            partition[kind] += 1
            observe(kind, cell)
        else:
            usable_types.add(kind)
            if kind == 'number':
                partition['number_included'] += 1
            else:
                partition['non_numeric_usable'] += 1
                observe('non_numeric_value', cell)
                if kind == 'text' and cell['value'] == '':
                    observe('empty_text', cell)
        if cell['formula'] and state == 'usable':
            observe('unverified_cache', cell)
        if 'propagated_from' in cell:
            observe('propagated_text', cell)
        if cell.get('merge_anchor', cell['coordinate']) != cell['coordinate']:
            observe('merged_non_anchor', cell)
    return {'positions': summary['positions'], 'usable_nonblank_values': summary['count'],
            'numeric_coverage': {'numerator': summary['numeric_count'], 'denominator': summary['positions'],
                                 'partition': partition},
            'usable_value_types': sorted(usable_types), 'mixed_usable_types': len(usable_types) > 1,
            'numeric_mean_inexact': summary['mean_inexact'],
            'findings': [{'code': code, 'positions': count, 'sample_coordinates': samples[code],
                          'samples_truncated': count > len(samples[code])} for code, count in sorted(counts.items())]}


def quality_report(result, table, rows, profiles, counts, group_by, unavailable_groups, sample_limit):
    ordered = sorted(rows, key=lambda row: row['source_row'])
    return {'schema_version': 'et_table_quality_v1', 'assessment': 'observations_only_no_correctness_score',
            'scope': 'all matched data rows, after row exclusions, filters and distinct; before preview pagination',
            'source_status': result['status'], 'sample_limit_per_finding': sample_limit,
            'sample_order': 'ascending original physical row', 'row_counts': deepcopy(counts),
            'empty_match': not rows, 'columns': {
                column: column_quality((row['cells'][column] for row in ordered), profiles[column], sample_limit)
                for column in table['columns']},
            'header': header_quality(table['header']),
            'group_coverage': {'requested': bool(group_by), 'matched_rows': len(rows),
                               'grouped_rows': len(rows) - unavailable_groups if group_by else None,
                               'unavailable_key_rows_excluded': unavailable_groups},
            'business_semantics_verified': False, 'formula_freshness_verified': False}
