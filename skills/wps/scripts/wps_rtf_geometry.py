"""Conservative merge geometry for explicit, matching physical RTF cell grids."""

def recover_merges(rows, definitions):
    """Return inclusive merge rectangles; never alter or concatenate cell text."""
    if len(rows) != len(definitions) or not rows:
        raise ValueError('Missing row definitions')
    if sum(len(row) for row in rows) > 100000:
        raise ValueError("Merge geometry exceeds bounded recovery scope")
    bounds = [cell['right'] for cell in definitions[0]]
    if (not bounds or any(type(n) is not int or n <= 0 for n in bounds)
            or any(a >= b for a, b in zip(bounds, bounds[1:]))):
        raise ValueError('Non-increasing or invalid cell boundaries')
    spans = []
    vertical = []
    active = {}
    for row_index, (row, defs) in enumerate(zip(rows, definitions)):
        if len(row) != len(bounds) or [cell['right'] for cell in defs] != bounds:
            raise ValueError('Inconsistent row/cell grid; physical text retained')
        if any(cell.get("row_left", 0) != 0 for cell in defs):
            raise ValueError("Nonzero row origin outside normalized grid recovery; physical text retained")
        if any(cell.get("conflicting") for cell in defs):
            raise ValueError("Conflicting merge flags; physical cells retained")
        next_active = {}
        col = 0
        while col < len(defs):
            first = col
            horizontal = defs[col].get('h')
            if horizontal == 'continue':
                raise ValueError('Horizontal continuation without start')
            if horizontal == 'first':
                while col + 1 < len(defs) and defs[col + 1].get('h') == 'continue':
                    col += 1
                if col == first:
                    raise ValueError('Horizontal merge start without continuation')
            last = col
            vertical_flags = {cell.get('v') for cell in defs[first:last + 1]}
            if len(vertical_flags) != 1:
                raise ValueError('Ambiguous vertical flags across horizontal span')
            flag = vertical_flags.pop()
            key = (first, last)
            if flag == 'continue':
                if key not in active:
                    raise ValueError('Vertical continuation without matching preceding span')
                span = active[key]
                span[2] = row_index
                next_active[key] = span
            elif flag == 'first':
                span = [row_index, first, row_index, last]
                spans.append(span)
                vertical.append(span)
                next_active[key] = span
            elif first != last:
                spans.append([row_index, first, row_index, last])
            col += 1
        active = next_active
    if any(span[0] == span[2] for span in vertical):
        raise ValueError('Vertical merge start without continuation')
    if len(spans) > 256:
        raise ValueError("Merge geometry exceeds 256 spans")
    return spans, bounds
