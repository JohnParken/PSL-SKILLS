"""Deterministic stored-value filtering, statistics and grouping; standard library."""
from collections import Counter
from copy import deepcopy
from decimal import Context, Decimal, Inexact, ROUND_HALF_EVEN, localcontext

from et_table_data import count_states, extract_table, need, number, source_context, validate_query
from et_table_quality import quality_report

MAX_GROUPS = 10000
MAX_PREVIEW_ROWS = 10000
MAX_METRICS = 32
QUERY_KEYS = {'sheet', 'range', 'header_rows', 'formula_policy', 'merge_policy', 'select', 'filters',
              'sort', 'distinct_by', 'group_by', 'aggregates', 'offset', 'limit', 'group_offset', 'group_limit', 'exclude_rows', 'quality_sample_limit'}
METRICS = {'rows', 'count', 'count_distinct', 'sum', 'mean', 'median', 'min', 'max'}
TYPE_ORDER = {'number': 0, 'text': 1, 'boolean': 2}


def integer_option(query, name, default, maximum):
    value = query.get(name, default)
    need(type(value) is int and 0 <= value <= maximum, name + ' is outside the supported integer range')
    return value


def column_list(query, name, columns, default=(), maximum=256):
    selected = query.get(name, list(default))
    need(isinstance(selected, list) and len(selected) <= maximum and all(isinstance(col, str) and col in columns for col in selected),
         name + ' must list selected physical column IDs such as A or C')
    need(len(set(selected)) == len(selected), name + ' contains duplicate columns')
    return selected


def key(cell):
    return cell['type'], cell['value']


def available(cell):
    return cell['analysis_state'] == 'usable' and cell['type'] not in ('blank', 'missing')


def prepare_filters(query, columns):
    filters = query.get('filters', [])
    need(isinstance(filters, list) and len(filters) <= 16, 'At most 16 filters are supported')
    output = []
    for condition in filters:
        need(isinstance(condition, dict) and condition.get('column') in columns, 'Filter requires a physical column')
        operation = condition.get('op')
        if operation in ('is_blank', 'is_missing', 'is_unavailable'):
            need(set(condition) == {'column', 'op'}, 'Unary filters accept only column and op')
            output.append(condition.copy())
            continue
        need(operation in ('eq', 'ne', 'lt', 'le', 'gt', 'ge', 'contains'), 'Unsupported filter operator')
        need(set(condition) == {'column', 'op', 'type', 'value'}, 'Comparison filters require column/op/type/value only')
        kind, value = condition['type'], condition['value']
        need(isinstance(kind, str) and kind in TYPE_ORDER, 'Filter must explicitly specify number/text/boolean')
        if kind == 'number':
            value = number(value)
        elif kind == 'boolean':
            need(type(value) is bool and operation in ('eq', 'ne'), 'Booleans support typed equality only')
        else:
            need(isinstance(value, str) and len(value) <= 32767, 'Filter text must be a bounded string')
        need(operation != 'contains' or kind == 'text', 'contains requires text')
        output.append({**condition, 'value': value})
    return output


def matches(row, filters):
    for condition in filters:
        cell = row['cells'][condition['column']]
        operation = condition['op']
        if operation == 'is_unavailable':
            passed = cell['analysis_state'] != 'usable'
        elif operation in ('is_blank', 'is_missing'):
            passed = cell['analysis_state'] == 'usable' and cell['type'] == operation[3:]
        elif not available(cell) or cell['type'] != condition['type']:
            passed = False
        else:
            left, right = cell['value'], condition['value']
            if operation == 'contains':
                passed = right in left
            elif operation == 'eq':
                passed = left == right
            elif operation == 'ne':
                passed = left != right
            elif operation == 'lt':
                passed = left < right
            elif operation == 'le':
                passed = left <= right
            elif operation == 'gt':
                passed = left > right
            else:
                passed = left >= right
        if not passed:
            return False
    return True


def exact_sum(numbers):
    # Numeric bounds plus 200000 cells require fewer than 3310 significant
    # digits even across extreme cancellation. Trap unexpected rounding.
    with localcontext(Context(prec=4096)) as context:
        context.traps[Inexact] = True
        return sum(numbers, Decimal(0))


def numeric_summary(numbers):
    if not numbers:
        return {'numeric_count': 0, 'sum': None, 'min': None, 'max': None, 'mean': None, 'median': None,
                'mean_precision': 34, 'mean_rounding': 'ROUND_HALF_EVEN', 'mean_inexact': False}
    ordered = sorted(numbers)
    total = exact_sum(numbers)
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)) as context:
        context.traps[Inexact] = False
        context.clear_flags()
        mean = total / len(numbers)
        inexact = context.flags[Inexact]
    middle = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[middle]
    else:
        with localcontext(Context(prec=4096)) as context:
            context.traps[Inexact] = True
            median = (ordered[middle - 1] + ordered[middle]) / 2
    return {'numeric_count': len(numbers), 'sum': total, 'min': ordered[0], 'max': ordered[-1],
            'mean': mean, 'median': median, 'mean_precision': 34, 'mean_rounding': 'ROUND_HALF_EVEN', 'mean_inexact': inexact}


def profile(cells):
    usable = [cell for cell in cells if available(cell)]
    return {'positions': len(cells), 'types': dict(Counter(cell['type'] for cell in cells)),
            'analysis_states': dict(Counter(cell['analysis_state'] for cell in cells)),
            'count': len(usable), 'count_distinct': len({key(cell) for cell in usable}),
            'formula_positions': sum(cell['formula'] for cell in cells),
            'formula_caches_used': sum(cell['formula'] and available(cell) for cell in cells),
            **numeric_summary([cell['value'] for cell in usable if cell['type'] == 'number'])}


def prepare_metrics(query, columns):
    metrics = query.get('aggregates', [])
    need(isinstance(metrics, list) and len(metrics) <= MAX_METRICS, 'At most 32 aggregate measures are supported')
    aliases = set()
    for metric in metrics:
        need(isinstance(metric, dict) and isinstance(metric.get('op'), str) and metric['op'] in METRICS, 'Unknown aggregate operation')
        name = metric.get('as')
        need(isinstance(name, str) and 1 <= len(name) <= 80 and name not in aliases, 'Aggregate aliases must be nonempty and unique')
        aliases.add(name)
        expected = {'as', 'op'} if metric['op'] == 'rows' else {'as', 'op', 'column'}
        need(set(metric) == expected and (metric['op'] == 'rows' or metric['column'] in columns), 'Invalid aggregate fields/column')
    return metrics


def aggregate_rows(rows, metrics):
    summaries = {}
    output = {}
    for metric in metrics:
        operation = metric['op']
        if operation == 'rows':
            output[metric['as']] = {'op': operation, 'value': len(rows)}
            continue
        column = metric['column']
        if column not in summaries:
            summaries[column] = profile([row['cells'][column] for row in rows])
        summary = summaries[column]
        output[metric['as']] = {'op': operation, 'column': column, 'value': summary[operation],
                                'usable_count': summary['count'], 'numeric_count': summary['numeric_count'],
                                'types': summary['types'], 'analysis_states': summary['analysis_states']}
        if operation == 'mean':
            output[metric['as']].update(precision=34, rounding='ROUND_HALF_EVEN', inexact=summary['mean_inexact'])
    return output


def process_rows(rows, query, columns):
    filters = prepare_filters(query, columns)
    filtered = [row for row in rows if matches(row, filters)]
    distinct = column_list(query, 'distinct_by', columns, maximum=16)
    duplicates = unavailable_distinct = 0
    if distinct:
        seen, unique = set(), []
        for row in filtered:
            cells = [row['cells'][column] for column in distinct]
            if any(cell['analysis_state'] != 'usable' for cell in cells):
                unique.append(row)
                unavailable_distinct += 1
                continue
            identity = tuple(key(cell) for cell in cells)
            if identity not in seen:
                seen.add(identity)
                unique.append(row)
            else:
                duplicates += 1
        filtered = unique
    sorting = query.get('sort', [])
    need(isinstance(sorting, list) and len(sorting) <= 8, 'At most 8 sort keys are supported')
    sort_columns = set()
    for sort in reversed(sorting):
        need(isinstance(sort, dict) and set(sort) <= {'column', 'descending'} and sort.get('column') in columns,
             'Sort requires a physical column and optional descending boolean')
        column, descending = sort['column'], sort.get('descending', False)
        need(type(descending) is bool and column not in sort_columns, 'Sort directions must be boolean and columns unique')
        sort_columns.add(column)
        ready = [row for row in filtered if available(row['cells'][column])]
        nulls = [row for row in filtered if not available(row['cells'][column])]
        ready.sort(key=lambda row: (TYPE_ORDER[row['cells'][column]['type']], row['cells'][column]['value']), reverse=descending)
        filtered = ready + nulls
    return filtered, {'input_rows': len(rows), 'filtered_out_rows': len(rows) - len(filtered) - duplicates,
                      'duplicate_rows_removed': duplicates, 'distinct_key_unavailable_rows_preserved': unavailable_distinct,
                      'matched_rows': len(filtered)}


def analyze(result, query):
    """Analyze one explicit region; original result and query remain unchanged."""
    validate_query(query, QUERY_KEYS)
    table = extract_table(result, query)
    columns = table['columns']
    selected = column_list(query, 'select', columns, default=columns)
    need(selected, 'select must contain at least one column')
    group_by = column_list(query, 'group_by', columns, maximum=8)
    metrics = prepare_metrics(query, columns)
    offset = integer_option(query, 'offset', 0, 200000)
    limit = integer_option(query, 'limit', 100, MAX_PREVIEW_ROWS)
    group_offset = integer_option(query, 'group_offset', 0, MAX_GROUPS)
    group_limit = integer_option(query, 'group_limit', 100, MAX_PREVIEW_ROWS)
    sample_limit = integer_option(query, 'quality_sample_limit', 5, 20)
    rows, counts = process_rows(table['rows'], query, columns)
    counts.update(input_rows=table['input_rows'], explicitly_excluded_rows=len(table['excluded_source_rows']))
    profiles = {column: profile([row['cells'][column] for row in rows]) for column in columns}
    groups = {}
    unavailable_groups = 0
    if group_by:
        for row in rows:
            cells = [row['cells'][column] for column in group_by]
            if any(cell['analysis_state'] != 'usable' for cell in cells):
                unavailable_groups += 1
                continue
            identity = tuple(key(cell) for cell in cells)
            if identity not in groups:
                need(len(groups) < MAX_GROUPS, 'Group count exceeds 10000', 'resource_limit')
                groups[identity] = []
            groups[identity].append(row)
    grouped = []
    for identity, members in list(groups.items())[group_offset:group_offset + group_limit]:
        grouped.append({'keys': [{'column': column, 'type': item[0], 'value': item[1]} for column, item in zip(group_by, identity)],
                        'row_count': len(members), 'measures': aggregate_rows(members, metrics)})
    preview = [{'source_row': row['source_row'], 'cells': {column: row['cells'][column] for column in selected}}
               for row in rows[offset:offset + limit]]
    matching_states = count_states(cell for row in rows for cell in row['cells'].values())
    issues = [{'code': state, 'positions': count, 'scope': 'selected_rectangle_before_processing',
               'matched_data_positions': matching_states.get(state, 0)} for state, count in table['state_counts'].items() if count]
    if unavailable_groups:
        issues.append({'code': 'group_key_unavailable_rows_excluded', 'rows': unavailable_groups})
    source = source_context(result)
    partial = result['status'] == 'partial' or bool(issues)
    return {'schema_version': 'et_table_analysis_v1', 'status': 'partial' if partial else 'ok', 'partial': partial,
            'source': source, 'query': deepcopy(query), 'analysis_issues': issues,
            'scope': {'sheet': query['sheet'], 'range': query['range'], 'data_first_row': table['data_first_row'],
                      'selected_positions': table['selected_positions'], 'formula_policy': table['formula_policy'],
                      'merge_policy': table['merge_policy'], 'filled_text_positions': table['filled_text_positions'],
                      'excluded_source_rows': table['excluded_source_rows'],
                      'saved_visibility': 'all selected physical rows, including hidden and filtered rows',
                      'calculation_basis': 'stored typed values only; styles, dates, units and formula freshness not inferred'},
            'columns': selected, 'header': table['header'], 'merges': table['merges'], 'row_counts': counts,
            'profiles': profiles, 'totals': aggregate_rows(rows, metrics),
            'quality_report': quality_report(result, table, rows, profiles, counts, group_by, unavailable_groups, sample_limit),
            'rows': preview, 'row_page': {'offset': offset, 'returned': len(preview), 'total': len(rows),
                                         'truncated': offset > 0 or offset + len(preview) < len(rows)},
            'groups': grouped, 'group_page': {'offset': group_offset, 'returned': len(grouped), 'total': len(groups),
                                              'unavailable_key_rows': unavailable_groups,
                                              'truncated': group_offset > 0 or group_offset + len(grouped) < len(groups)},
            'statistics_scope': 'all matching data rows after explicit row exclusions, filters and distinct, before preview pagination',
            'formulas_executed': False, 'external_links_followed': False, 'source_modified': False}
