"""Independently constrain addressed run formatting and cell text changes.

This verifier does not call the editing setters. It compares complete edited-part
XML trees, preserving element order, text, attributes and namespace bindings.
XML declarations, attribute ordering and ZIP representation are not compared.
"""

from docx.oxml.ns import qn
from lxml import etree


ERROR = 'format_run preservation guard rejected an unexpected XML change'


def _require(condition):
    if not condition:
        raise ValueError(ERROR)


def _metadata(node):
    # CT_P and CT_R override .text with aggregated visible text; compare raw XML.
    return node.tag, dict(node.attrib), etree._Element.text.__get__(node), node.tail, node.nsmap  # pylint: disable=protected-access


def verify_xml_preserved(before, after, *, target_pair=None):
    """Compare XML iteratively, optionally excluding one separately checked run."""
    pending = [(before, after)]
    while pending:
        left, right = pending.pop()
        if target_pair is not None and left is target_pair[0]:
            _require(right is target_pair[1])
            continue
        _require(_metadata(left) == _metadata(right) and len(left) == len(right))
        pending.extend(zip(left, right))


def _one(parent, tag):
    matches = parent.findall(qn('w:' + tag))
    _require(len(matches) <= 1)
    return matches[0] if matches else None


def _merge_origin(table, operation):
    """Independently verify the requested origin coordinate and rectangular extent."""
    expected = operation['expected_merge']
    _require(isinstance(expected, dict) and set(expected) == {'grid_column', 'colspan', 'rowspan'})
    _require(all(type(value) is int and value >= (0 if key == 'grid_column' else 1) for key, value in expected.items()))
    rows = table.findall(qn('w:tr'))
    selected_row = operation['row_index']
    selected_cell = operation['cell_index']
    _require(type(selected_row) is int and 0 <= selected_row < len(rows))
    observed = []
    for row in rows[selected_row:]:
        positions = []
        column = 0
        for cell in row.findall(qn('w:tc')):
            props = _one(cell, 'tcPr')
            span_node = _one(props, 'gridSpan') if props is not None else None
            span_text = span_node.get(qn('w:val'), '') if span_node is not None else '1'
            _require(bool(span_text) and span_text.isascii() and span_text.isdecimal())
            span = int(span_text)
            _require(span > 0)
            merge = _one(props, 'vMerge') if props is not None else None
            marker = merge.get(qn('w:val'), 'continue') if merge is not None else None
            _require(props is None or _one(props, 'hMerge') is None)
            positions.append((column, span, marker))
            column += span
        observed.append(positions)
    _require(type(selected_cell) is int and 0 <= selected_cell < len(observed[0]))
    column, span, marker = observed[0][selected_cell]
    _require(marker in (None, 'restart'))
    count = 1
    if marker == 'restart':
        for positions in observed[1:]:
            overlap = [item for item in positions if item[0] < column + span and item[0] + item[1] > column]
            if len(overlap) == 1 and overlap[0] == (column, span, 'continue'):
                count += 1
            else:
                _require(all(item[2] != 'continue' for item in overlap))
                break
    _require(expected == {'grid_column': column, 'colspan': span, 'rowspan': count})
    _require(span > 1 or count > 1)


def _target(root, operation):
    if operation['type'] in ('format_story_run', 'replace_story_run_text'):
        _require(root.tag in (qn('w:hdr'), qn('w:ftr')))
        container = root
    else:
        container = _one(root, 'body')
        _require(container is not None)
    if operation['type'] in ('format_cell_run', 'replace_cell_run_text'):
        if 'expected_merge' in operation:
            tables = container.findall(qn('w:tbl'))
            table_index = operation['table_index']
            _require(type(table_index) is int and 0 <= table_index < len(tables))
            _merge_origin(tables[table_index], operation)
        for tag, key in (('tbl', 'table_index'), ('tr', 'row_index'), ('tc', 'cell_index')):
            candidates = container.findall(qn('w:' + tag))
            position = operation[key]
            _require(type(position) is int and 0 <= position < len(candidates))
            container = candidates[position]
    paragraphs = container.findall(qn('w:p'))
    paragraph_index = operation['paragraph_index']
    _require(type(paragraph_index) is int and 0 <= paragraph_index < len(paragraphs))
    runs = paragraphs[paragraph_index].findall(qn('w:r'))
    run_index = operation['run_index']
    _require(type(run_index) is int and 0 <= run_index < len(runs))
    return runs[run_index]


def _changes(formatting, old_props):
    """Map the public request to permitted attribute values, not setter output."""
    changes = {}
    for key, value in formatting.items():
        if key in ('bold', 'italic'):
            changes['b' if key == 'bold' else 'i'] = {'val': None if value else '0'}
        elif key == 'underline':
            changes['u'] = {'val': 'single' if value else 'none'}
        elif key == 'color':
            changes['color'] = {'val': value.upper(), 'themeColor': None, 'themeTint': None, 'themeShade': None}
        elif key == 'size_pt':
            # python-docx stores ordinary sizes as truncated half-points after
            # converting to integer EMU; the existing complex-size rule rounds.
            changes['sz'] = {'val': str(int(int(value * 12700) / 12700 * 2))}
            if old_props is not None and _one(old_props, 'szCs') is not None:
                changes['szCs'] = {'val': str(round(value * 2))}
        elif key in ('font_ascii', 'font_east_asia'):
            attributes = ('ascii', 'hAnsi') if key == 'font_ascii' else ('eastAsia',)
            fonts = changes.setdefault('rFonts', {})
            for name in attributes:
                fonts[name] = value
                fonts[name + 'Theme'] = None
        else:
            raise ValueError(ERROR)
    return {qn('w:' + tag): {qn('w:' + key): value for key, value in attributes.items()}
            for tag, attributes in changes.items()}


def _property(before, after, attributes):
    """Allow only specified attributes, retaining all other property payload."""
    expected = dict(before.attrib) if before is not None else {}
    for key, value in attributes.items():
        if value is None:
            expected.pop(key, None)
        else:
            expected[key] = value
    _require(dict(after.attrib) == expected)
    if before is None:
        _require(after.text is None and after.tail is None and len(after) == 0)
        _require(after.nsmap == after.getparent().nsmap)
    else:
        _require((before.tag, before.text, before.tail, before.nsmap) ==
                 (after.tag, after.text, after.tail, after.nsmap))
        _require(len(before) == len(after))
        for left, right in zip(before, after):
            verify_xml_preserved(left, right)


def _properties(before, after, changes):
    _require(after is not None)
    if before is None:
        _require(not after.attrib and after.text is None and after.tail is None)
        _require(after.nsmap == after.getparent().nsmap)
    else:
        _require(_metadata(before) == _metadata(after))
    originals = list(before) if before is not None else []
    new_tags = set()
    for tag, attributes in changes.items():
        old = [child for child in originals if child.tag == tag]
        new = after.findall(tag)
        _require(len(old) <= 1 and len(new) == 1)
        _property(old[0] if old else None, new[0], attributes)
        if not old:
            new_tags.add(tag)
    surviving = [child for child in after if child.tag not in new_tags]
    _require(len(originals) == len(surviving))
    for left, right in zip(originals, surviving):
        _require(left.tag == right.tag)
        if left.tag not in changes:
            verify_xml_preserved(left, right)


def verify_format_change(before, after, operation):
    """Verify one sequential format operation against a pre-edit snapshot."""
    old_run, new_run = _target(before, operation), _target(after, operation)
    _require(_metadata(old_run) == _metadata(new_run))
    old_props, new_props = _one(old_run, 'rPr'), _one(new_run, 'rPr')
    _require(new_props is not None)
    old_children, new_children = list(old_run), list(new_run)
    if old_props is None:
        _require(new_children[0] is new_props)
        new_children = new_children[1:]
    else:
        _require(old_children.index(old_props) == new_children.index(new_props))
        old_children.remove(old_props)
        new_children.remove(new_props)
    _require(len(old_children) == len(new_children))
    for left, right in zip(old_children, new_children):
        verify_xml_preserved(left, right)
    _properties(old_props, new_props, _changes(operation['format'], old_props))
    verify_xml_preserved(before, after, target_pair=(old_run, new_run))


def _text_payload(value):
    """Describe requested Word text nodes without invoking the editing setters."""
    expected = []
    buffer = []

    def flush():
        if buffer:
            content = ''.join(buffer)
            attributes = {qn('xml:space'): 'preserve'} if content.strip() != content else {}
            expected.append((qn('w:t'), attributes, content))
            buffer.clear()

    for character in value:
        if character in ('\t', '\n'):
            flush()
            expected.append((qn('w:tab' if character == '\t' else 'w:br'), {}, None))
        else:
            buffer.append(character)
    flush()
    return expected


def verify_cell_text_change(before, after, operation):
    """Allow only declared cell/story run text, retaining properties and all other XML."""
    old_run, new_run = _target(before, operation), _target(after, operation)
    _require(_metadata(old_run) == _metadata(new_run))
    old_props, new_props = _one(old_run, 'rPr'), _one(new_run, 'rPr')
    _require((old_props is None) == (new_props is None))
    if old_props is not None:
        _require(list(old_run).index(old_props) == 0 and list(new_run).index(new_props) == 0)
        verify_xml_preserved(old_props, new_props)
    payload = [node for node in new_run if node is not new_props]
    expected = _text_payload(operation['text'])
    _require(len(payload) == len(expected))
    for node, (tag, attributes, content) in zip(payload, expected):
        _require((node.tag, dict(node.attrib), node.text) == (tag, attributes, content))
        _require(node.tail is None and len(node) == 0 and node.nsmap == new_run.nsmap)
    verify_xml_preserved(before, after, target_pair=(old_run, new_run))
