"""Normalize rectangular table grids with explicit, non-overlapping cell spans.

All occupied placeholders must be JSON null. Each vertical continuation has an
actual OOXML cell, while horizontal continuations do not.
"""


def normalize_merged_grid(rows, label):
    height, width = len(rows), len(rows[0])
    owner = [[None] * width for _ in range(height)]
    result = [[None] * width for _ in range(height)]
    has_merges = False
    for row_index, values in enumerate(rows):
        for column, value in enumerate(values):
            position = (row_index, column)
            if owner[row_index][column] is not None:
                if value is not None:
                    raise ValueError(f'{label}.rows[{row_index}][{column}] must be null beneath a merged cell')
                continue
            if value is None:
                raise ValueError(f'{label}.rows[{row_index}][{column}] is null but is not covered by a merge')
            if not isinstance(value, (str, dict)):
                raise ValueError(f'{label}.rows[{row_index}][{column}] must be a string or object')
            if isinstance(value, dict) and 'v_merge' in value:
                raise ValueError(f'{label}.rows[{row_index}][{column}].v_merge is internal and cannot be supplied')
            col_span = value.get('col_span', 1) if isinstance(value, dict) else 1
            row_span = value.get('row_span', 1) if isinstance(value, dict) else 1
            for field, span in (('col_span', col_span), ('row_span', row_span)):
                if type(span) is not int or not 1 <= span <= 256:
                    raise ValueError(f'{label}.rows[{row_index}][{column}].{field} must be an integer from 1 to 256')
            if row_index + row_span > height or column + col_span > width:
                raise ValueError(f'{label}.rows[{row_index}][{column}] merge exceeds table grid')
            for r in range(row_index, row_index + row_span):
                for c in range(column, column + col_span):
                    if owner[r][c] is not None:
                        raise ValueError(f'{label}.rows[{row_index}][{column}] overlaps another merged cell')
                    owner[r][c] = position
            if row_span > 1 or col_span > 1:
                has_merges = True
                result[row_index][column] = {**value, 'col_span': col_span,
                                             'row_span': row_span} if isinstance(value, dict) else value
                for r in range(row_index + 1, row_index + row_span):
                    result[r][column] = {'text': '', 'image': None,
                                         'col_span': col_span, 'v_merge': 'continue'}
            else:
                result[row_index][column] = value
    return result, has_merges
