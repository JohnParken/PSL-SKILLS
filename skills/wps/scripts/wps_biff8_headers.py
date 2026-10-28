# SPDX-License-Identifier: MIT
"""Standalone BIFF header helpers extracted from xlsx_headers/stdlib_xlsx.
Maintenance source: PSL-SKILLS xlsx pure address/header algorithm, 2026-10-25.
No native OOXML loading or editing is bundled here.
"""
import re
MAX_CELLS = 200000

def column_name(number):
    result = ''
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(65 + digit) + result
    return result

def address(ref):
    if not isinstance(ref, str):
        raise ValueError("Cell addresses must be strings")
    match = re.fullmatch(r"([A-Za-z]{1,3})([1-9][0-9]{0,6})", ref)
    if not match:
        raise ValueError("Invalid cell address: " + ref)
    col = 0
    for char in match[1].upper():
        col = col * 26 + ord(char) - 64
    row = int(match[2])
    if col > 16384 or row > 1048576:
        raise ValueError("Cell address outside XLSX bounds: " + ref)
    return row, col


def header_bounds(spec, first_row, last_row):
    if not isinstance(spec, str) or not re.fullmatch(r'[1-9][0-9]{0,6}(?::[1-9][0-9]{0,6})?', spec):
        raise ValueError('header_rows must be an explicit row or row range, such as 3:5')
    ends = [int(item) for item in spec.split(':')]
    start, end = ends[0], ends[-1]
    if not first_row <= start <= end <= last_row:
        raise ValueError('Header rows must be fully inside the selected region')
    return start, end


def reconstruct(book, sheet, bounds, header_rows, read_cell, exclude=(), include_spanning=False, merge_nodes=None):
    r1, c1, r2, c2 = bounds
    start, end = header_bounds(header_rows, r1, r2)
    if any(type(row) is not int or not start <= row <= end for row in exclude):
        raise ValueError('Excluded header rows must lie inside header_rows')
    excluded = set(exclude)
    if len(excluded) == end-start+1:
        raise ValueError('At least one header row must remain')
    if merge_nodes is None:
        raise ValueError('Explicit merge nodes required for BIFF headers')
    merges = merge_nodes
    # Match the bounded BIFF reader capacity; only intersecting header merges
    # are expanded below, within the independently checked selected-cell budget.
    if len(merges) > 65536:
        raise ValueError('More than 65536 merged regions')
    occupied = {}
    contexts = []
    for node in merges:
        ref = node.get('ref', '')
        parts = ref.split(':')
        if len(parts) != 2:
            raise ValueError('Malformed merged region: ' + ref)
        top, left = address(parts[0])
        bottom, right = address(parts[1])
        if bottom < top or right < left or (bottom, right) == (top, left):
            raise ValueError('Malformed merged region: ' + ref)
        if bottom < start or top > end or right < c1 or left > c2:
            continue
        if top < start or bottom > end:
            raise ValueError('Merge crosses the declared header boundary: ' + ref)
        if left < c1 or right > c2:
            raise ValueError('Region clips a header merge; select the complete merge: ' + ref)
        if (bottom-top+1)*(right-left+1) > MAX_CELLS:
            raise ValueError('Merged header exceeds cell limit')
        anchor = column_name(left)+str(top)
        spanning = left == c1 and right == c2 and left < right and top == bottom
        if spanning:
            contexts.append({'row': top, 'source': anchor, 'merge': ref,
                             'included_in_path': include_spanning and top not in excluded,
                             'reason': 'full-width merged heading; semantic role requires explicit choice'})
        for row in range(top, bottom+1):
            for col in range(left, right+1):
                key = (row, col)
                if key in occupied:
                    raise ValueError('Overlapping header merges at ' + column_name(col)+str(row))
                coord = column_name(col)+str(row)
                cell = book.cells[sheet].get(coord, {})
                if coord != anchor and (cell.get('formula') is not None or cell.get('value') not in (None, '')):
                    raise ValueError('Non-anchor merged header cell contains data: ' + coord)
                occupied[key] = (anchor, ref, spanning)
    columns = []
    for col in range(c1, c2+1):
        letter = column_name(col)
        levels, path, seen_sources = [], [], set()
        for row in range(start, end+1):
            coordinate = letter+str(row)
            source, merge, spanning = occupied.get((row, col), (coordinate, None, False))
            kind, value = read_cell(source)
            role = 'excluded' if row in excluded else 'spanning_heading' if spanning and not include_spanning else 'label'
            component = {'type': kind, 'value': value}
            levels.append({'row': row, 'coordinate': coordinate, 'source': source,
                           'merge': merge, 'propagated': coordinate != source,
                           'role': role, **component})
            if role == 'label' and source not in seen_sources:
                path.append(component)
                seen_sources.add(source)
        columns.append({'id': 'col:'+letter, 'column': letter, 'path': path, 'levels': levels})
    return {'rows': {'first': start, 'last': end}, 'excluded_rows': sorted(excluded),
            'ignored_leading_rows': list(range(r1, start)), 'data_first_row': end+1,
            'columns': columns, 'context_headings': contexts,
            'policy': 'explicit rows; merge-only propagation; blanks retained; vertical merge sources appear once in path'}


