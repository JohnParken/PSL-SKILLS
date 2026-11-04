"""Cardinality-checked dimension enrichment of validated typed regions."""
from copy import deepcopy

from xlsx_analysis import statistics
from xlsx_headers import resolve_column
from xlsx_match import index_rows


class LookupError(ValueError):
    """Retain lookup quality diagnostics without exporting full source rows."""

    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def lookup_regions(left, right, config):
    """Append dimension columns, preserving every fact row exactly once.

    Both tuples must originate in region_from_book, with original metadata and
    explicit row selection retained. This function is not a JSON input adapter.
    Unmatched facts, null/mixed key types and duplicate dimensions are refused.
    """
    required = {'left_keys', 'right_keys', 'columns', 'relationship', 'max_rows'}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError('Lookup requires exactly left_keys/right_keys/columns/relationship/max_rows')
    if config['relationship'] not in ('one_to_one', 'many_to_one'):
        raise ValueError('Lookup relationship must be one_to_one or many_to_one')
    maximum = config['max_rows']
    if type(maximum) is not int or not 1 <= maximum <= 10000:
        raise ValueError('Lookup max_rows must be 1..10000')
    if not left[2] or len(left[2]) > maximum:
        raise ValueError('Lookup fact rows must be nonempty and within max_rows')
    sides = []
    diagnostics = {}
    for name, table in (('left', left), ('right', right)):
        if not isinstance(table[3].get('header_schema'), dict):
            raise ValueError('Lookup regions require explicit reconstructed header schemas')
        selectors = config[name + '_keys']
        if not isinstance(selectors, list) or not 1 <= len(selectors) <= 8:
            raise ValueError('Lookup needs 1..8 keys on each side')
        keys = [resolve_column(item, table[0], table[3]['header_schema']) for item in selectors]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate lookup key selector')
        side = {'rows': table[2], 'indices': [table[0].index(key) for key in keys]}
        index, by_row, quality = index_rows(side, 'never')
        quality['key_types'] = [sorted({row[column][0] for _, row in table[2]}) for column in side['indices']]
        diagnostics[name] = quality
        sides.append((index, by_row, keys))
    if len(sides[0][2]) != len(sides[1][2]):
        raise LookupError('Lookup key arity differs', diagnostics)
    if diagnostics['left']['null_key_rows'] or diagnostics['right']['null_key_rows']:
        raise LookupError('Lookup keys must not be blank', diagnostics)
    left_types, right_types = diagnostics['left']['key_types'], diagnostics['right']['key_types']
    if left_types != right_types or any(len(kinds) != 1 for kinds in left_types):
        raise LookupError('Lookup key types must be homogeneous and identical; no coercion', diagnostics)
    if diagnostics['right']['duplicate_key_groups'] or (config['relationship'] == 'one_to_one' and diagnostics['left']['duplicate_key_groups']):
        raise LookupError('Lookup cardinality violated; duplicate keys', diagnostics)
    selections = config['columns']
    if not isinstance(selections, list) or not 1 <= len(selections) <= 32:
        raise ValueError('Lookup needs 1..32 selected dimension columns')
    columns = list(left[0])
    headers = list(left[1])
    schema = deepcopy(left[3]['header_schema'])
    selected = []
    for item in selections:
        if not isinstance(item, dict) or set(item) != {'column', 'as'}:
            raise ValueError('Lookup columns require column/as')
        alias = item['as']
        if not isinstance(alias, str) or not alias.strip() or len(alias) > 100:
            raise ValueError('Lookup column alias must be nonempty text within 100 characters')
        column = 'lookup:' + alias
        path = [{'type': 'text', 'value': alias}]
        if column in columns or any(entry['path'] == path for entry in schema['columns']):
            raise ValueError('Lookup alias collides with an existing column/path')
        right_column = resolve_column(item['column'], right[0], right[3]['header_schema'])
        selected.append(right[0].index(right_column))
        columns.append(column)
        headers.append(('text', alias))
        schema['columns'].append({'id': 'col:' + column, 'column': column, 'path': path, 'levels': [],
                                  'lookup_source_column': right_column})
    if len(columns) * len(left[2]) > 200000:
        raise ValueError('Enriched lookup region exceeds 200000 cells')
    rows, lineage, unmatched, matched_right = [], [], [], set()
    right_index = sides[1][0]
    for number, values in left[2]:
        matches = right_index.get(sides[0][1][number], [])
        if not matches:
            unmatched.append(number)
            continue
        dimension_row, dimension_values = matches[0]
        matched_right.add(dimension_row)
        rows.append((number, list(values) + [dimension_values[index] for index in selected]))
        lineage.append({'left_row': number, 'right_row': dimension_row})
    diagnostics.update(unmatched_left_rows=len(unmatched), unmatched_left_row_sample=unmatched[:20],
                       unused_right_rows=len(right[2]) - len(matched_right))
    if unmatched:
        raise LookupError('Unmatched lookup fact keys; no rows dropped or filled with zero', diagnostics)
    if len(rows) != len(left[2]) or any(number != prior[0] or values[:len(left[0])] != prior[1]
                                      for (number, values), prior in zip(rows, left[2])):
        raise LookupError('Lookup changed fact rows or cells', diagnostics)
    amounts = []
    for column, name in enumerate(left[0]):
        before = statistics([row[column] for _, row in left[2]])
        after = statistics([row[column] for _, row in rows])
        if before != after:
            raise LookupError('Lookup numeric conservation failed', diagnostics)
        amounts.append({'column': name, 'numeric_count': before['count'], 'before_sum': before['sum'],
                        'after_sum': after['sum'], 'status': 'passed'})
    metadata = {**left[3], 'header_schema': schema}
    table = (columns, headers, rows, metadata)
    report = {'status': 'enriched', 'relationship': config['relationship'], 'input_rows': len(left[2]), 'output_rows': len(rows),
              'diagnostics': diagnostics, 'row_lineage': lineage, 'fact_conservation': amounts,
              'dimension_columns_are_attributes': True, 'unmatched_policy': 'reject'}
    return table, report
