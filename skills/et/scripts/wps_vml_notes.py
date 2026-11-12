"""Bounded best-effort Transitional VML note anchors; never drawing execution."""
import re
from wps_ooxml import IssueLog, split_tag

VML = 'urn:schemas-microsoft-com:vml'
EXCEL = 'urn:schemas-microsoft-com:office:excel'
OFFICE = 'urn:schemas-microsoft-com:office:office'
VML_TYPE = 'application/vnd.openxmlformats-officedocument.vmlDrawing'
MAX_SHAPES = 4096
MAX_VML_NODES = 50000
ACTIVE_MARKERS = ('javascript:', 'vbscript:', 'url(', 'http:', 'https:')


def _local(tag):
    return split_tag(tag)[1]


def _namespace(tag):
    return split_tag(tag)[0]


def _child_text(parent, namespace, name):
    found = [child for child in parent
             if child.tag == '{' + namespace + '}' + name]
    if len(found) != 1 or len(found[0]):
        return None
    return (found[0].text or '').strip()


def _anchor_values(raw):
    fields = [value.strip() for value in raw.split(',')]
    if len(fields) != 8:
        raise ValueError('VML anchor does not have eight components')
    maximums = (16383, 1000000, 1048575, 1000000, 16383, 1000000, 1048575, 1000000)
    values = []
    for value, maximum in zip(fields, maximums):
        if re.fullmatch('[0-9]{1,10}', value) is None or int(value) > maximum:
            raise ValueError('VML anchor component outside supported bounds')
        values.append(int(value))
    if (values[0], values[1]) > (values[4], values[5]) or (values[2], values[3]) > (values[6], values[7]):
        raise ValueError('VML anchor rectangle is reversed')
    return values


def _note_anchor(shape, client, path, index, report, sheet):
    """Build one anchor record; return (cell, anchor) or (None, None)."""
    from wps_ooxml_sheet import integer
    row_raw = _child_text(client, EXCEL, 'Row')
    column_raw = _child_text(client, EXCEL, 'Column')
    anchor_raw = _child_text(client, EXCEL, 'Anchor')
    if row_raw is None or column_raw is None or anchor_raw is None:
        report.add('vml_anchor_not_interpreted', 'Note ClientData lacks bounded Row/Column/'
                   'Anchor text', sheet=sheet, part=path, shape_index=index)
        return None, None
    try:
        row = integer(row_raw, 1048575)
        col = integer(column_raw, 16383)
        values = _anchor_values(anchor_raw)
    except ValueError as exc:
        report.add('vml_anchor_not_interpreted', 'Note anchor is outside the bounded integer '
                   'subset', sheet=sheet, part=path, shape_index=index, reason=str(exc)[:120])
        return None, None
    number = col + 1
    letters = ''
    while number:
        number, digit = divmod(number - 1, 26)
        letters = chr(65 + digit) + letters
    cell = letters + str(row + 1)
    anchor = {'cell_row_zero_based': row, 'cell_column_zero_based': col,
              'rectangle_raw': anchor_raw, 'components': values, 'units_interpreted': False,
              'source': {'part': path,
                         'path': '/xml/shape[' + str(index + 1) + ']/ClientData'},
              'shape_id': shape.get('id'), 'shape_type_raw': shape.get('type'),
              'shape_type_resolved': False, 'layout_decoded': False}
    return cell, anchor


def _check_vml_nodes(root, report, sheet, path):
    """Classify VML nodes; never fail and never execute active attributes."""
    allowed = {VML: {'shape', 'shapetype', 'stroke', 'path', 'fill', 'shadow', 'textbox'},
               OFFICE: {'shapelayout', 'idmap'},
               EXCEL: {'ClientData', 'MoveWithCells', 'SizeWithCells', 'Anchor', 'AutoFill',
                       'Row', 'Column', 'Visible', 'RowHidden', 'ColHidden'}}
    nodes = list(root.iter())
    if len(nodes) > MAX_VML_NODES:
        report.add('vml_node_limit_reached', 'Only the bounded VML prefix is inspected',
                   sheet=sheet, part=path, kept=MAX_VML_NODES, declared=len(nodes))
        nodes = nodes[:MAX_VML_NODES]
    for node in nodes[1:]:
        namespace, name = _namespace(node.tag), _local(node.tag)
        if namespace in ('http://www.w3.org/1999/xhtml', ''):
            if name != 'div':
                report.add('vml_element_not_interpreted', 'Non-VML drawing element is ignored',
                           sheet=sheet, part=path, element=name[:80])
            elif (node.text or '').strip():
                report.add('vml_textbox_text_not_interpreted', 'Textbox alternate text is '
                           'recorded only as an unrendered drawing text', sheet=sheet,
                           part=path)
        elif namespace not in allowed or name not in allowed[namespace]:
            report.add('vml_element_not_interpreted', 'Unknown VML element is ignored',
                       sheet=sheet, part=path, element=(namespace + '}' + name)[:120])
        for value in node.attrib.values():
            lowered = str(value).lower()
            if any(marker in lowered for marker in ACTIVE_MARKERS):
                report.add('vml_active_attribute_not_executed', 'VML active content is recorded '
                           'only as an unexecuted raw attribute', sheet=sheet, part=path,
                           element=name[:80])
    return nodes


def read_note_anchors(package, path, budget, comments, report=None, reader=None, sheet=None):
    """Attach saved Note anchors to comment records; degradation is non-fatal."""
    from wps_ooxml import fromstring, read_part
    if report is None:
        report = IssueLog()
    sheet = sheet if sheet is not None else path
    read = read_part if reader is None else reader
    root = fromstring(read(package, path), budget=budget)
    if _local(root.tag) != 'xml':
        report.add('vml_root_not_interpreted', 'VML root is not the saved xml container',
                   sheet=sheet, part=path, element=_local(root.tag)[:80])
    nodes = _check_vml_nodes(root, report, sheet, path)
    shapes = [node for node in nodes if node.tag == '{' + VML + '}shape']
    if len(shapes) > MAX_SHAPES:
        report.add('vml_shape_limit_reached', 'Only the bounded VML shape prefix is interpreted',
                   sheet=sheet, part=path, kept=MAX_SHAPES, declared=len(shapes))
        shapes = shapes[:MAX_SHAPES]
    expected = {note['cell']: note for note in comments if isinstance(note.get('cell'), str)}
    seen = set()
    ids = set()
    for index, shape in enumerate(shapes):
        identity = shape.get('id')
        if not identity or identity in ids:
            report.add('vml_shape_not_interpreted', 'VML shape without a unique saved ID is '
                       'ignored', sheet=sheet, part=path, shape_index=index)
            continue
        ids.add(identity)
        clients = [node for node in shape.iter() if node.tag == '{' + EXCEL + '}ClientData']
        if len(clients) != 1:
            report.add('vml_shape_not_interpreted', 'VML shape does not have exactly one '
                       'ClientData element', sheet=sheet, part=path, shape_index=index)
            continue
        client = clients[0]
        object_type = client.get('ObjectType')
        if object_type != 'Note':
            report.add('vml_client_data_not_note', 'Only VML Note ClientData is interpreted',
                       sheet=sheet, part=path, shape_index=index, object_type=object_type)
            continue
        cell, anchor = _note_anchor(shape, client, path, index, report, sheet)
        if cell is None:
            continue
        if cell not in expected:
            report.add('vml_anchor_without_comment', 'VML note anchor is not attached because '
                       'no saved comment matches its cell', sheet=sheet, part=path, cell=cell)
            continue
        if cell in seen:
            report.add('duplicate_vml_anchor_skipped', 'Second saved anchor for the same cell is '
                       'not attached', sheet=sheet, cell=cell)
            continue
        seen.add(cell)
        expected[cell]['anchor'] = anchor
    missing = set(expected) - seen
    if missing:
        report.add('vml_anchor_missing_for_comment', 'Comments are retained without note layout '
                   'anchors', sheet=sheet, part=path, count=len(missing),
                   cells=','.join(sorted(missing)[:8]))
    return None
