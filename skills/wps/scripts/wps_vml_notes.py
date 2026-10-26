"""Bounded Transitional VML Note anchors, never drawing execution or rendering."""
import re
VML = 'urn:schemas-microsoft-com:vml'
EXCEL = 'urn:schemas-microsoft-com:office:excel'
OFFICE = 'urn:schemas-microsoft-com:office:office'
VML_TYPE = 'application/vnd.openxmlformats-officedocument.vmlDrawing'
MAX_SHAPES = 4096
MAX_VML_NODES = 50000


def read_note_anchors(package, path, budget, comments):
    from wps_ooxml import fromstring, read_part
    from wps_ooxml_sheet import need, integer
    root = fromstring(read_part(package, path), budget=budget)
    need(root.tag == 'xml' and not root.attrib and not (root.text or '').strip(), 'Unsupported VML root')
    nodes = list(root.iter())
    need(len(nodes) <= MAX_VML_NODES, 'VML node limit exceeded')
    allowed = {VML: {'shape', 'shapetype', 'stroke', 'path', 'fill', 'shadow', 'textbox'},
               OFFICE: {'shapelayout', 'idmap'},
               EXCEL: {'ClientData', 'MoveWithCells', 'SizeWithCells', 'Anchor', 'AutoFill', 'Row', 'Column', 'Visible', 'RowHidden', 'ColHidden'}}
    # Textboxes are often XHTML div containers, but must contain no alternate text.
    for node in nodes[1:]:
        if node.tag in ('div', '{http://www.w3.org/1999/xhtml}div'):
            need(not node.attrib or set(node.attrib) <= {'style'}, 'Unsupported VML textbox attribute')
        else:
            need(node.tag.startswith('{') and node.tag[1:].split('}', 1)[0] in allowed and
                 node.tag.split('}', 1)[1] in allowed[node.tag[1:].split('}', 1)[0]], 'Unsupported VML element')
        for key, value in node.attrib.items():
            local = key.rsplit('}', 1)[-1].lower()
            allowed_attrs = {
                'shape': {'id', 'type', 'style', 'fillcolor', 'strokecolor', 'strokeweight', '{' + OFFICE + '}insetmode'},
                'shapetype': {'id', 'coordsize', '{' + OFFICE + '}spt', 'path'},
                'stroke': {'joinstyle'}, 'path': {'gradientshapeok', '{' + OFFICE + '}connecttype'},
                'fill': {'color2'}, 'shadow': {'on', 'color', 'obscured'},
                'textbox': {'style'}, 'shapelayout': {'{' + VML + '}ext'},
                'idmap': {'{' + VML + '}ext', 'data'}, 'ClientData': {'ObjectType'}, 'div': {'style'}}
            node_name = node.tag.rsplit('}', 1)[-1]
            need(key in allowed_attrs.get(node_name, set()) and len(value) <= 32767, 'Unsupported VML attribute')
            need('url(' not in value.lower() and 'javascript:' not in value.lower(), 'Unsupported VML active attribute')
        if node.tag not in ('{' + EXCEL + '}' + name for name in ('Anchor', 'Row', 'Column', 'AutoFill')):
            need(not (node.text or '').strip(), 'Unsupported VML text content')
        need(not (node.tail or '').strip(), 'Unexpected VML tail text')
    shapes = root.findall('{' + VML + '}shape')
    need(len(shapes) <= MAX_SHAPES, 'VML shape limit exceeded')
    need(len(shapes) == sum(node.tag == '{' + VML + '}shape' for node in nodes), 'Nested VML shapes unsupported')
    need(sum(node.tag == '{' + EXCEL + '}ClientData' for node in nodes) == len(shapes), 'Unbound VML ClientData')
    expected = {note['cell']: note for note in comments}
    seen = set(); ids = set()
    for index, shape in enumerate(shapes):
        identity = shape.get('id')
        need(identity and identity not in ids, 'Missing/duplicate VML shape ID'); ids.add(identity)
        if shape.get('type') is not None:
            need(re.fullmatch(r'#[A-Za-z_][A-Za-z0-9_.-]{0,255}', shape.get('type')) is not None, 'Unsupported VML shape type target')
        clients = shape.findall('{' + EXCEL + '}ClientData')
        need(len(clients) == 1 and clients[0].attrib == {'ObjectType': 'Note'}, 'Only VML Note ClientData supported')
        client = clients[0]
        def one(name):
            found = client.findall('{' + EXCEL + '}' + name)
            need(len(found) == 1 and not len(found[0]) and not found[0].attrib, 'Missing/duplicate VML ' + name)
            return found[0].text or ''
        row = integer(one('Row').strip(), 1048575)
        col = integer(one('Column').strip(), 16383)
        number = col + 1; letters = ''
        while number:
            number, digit = divmod(number - 1, 26); letters = chr(65 + digit) + letters
        cell = letters + str(row + 1)
        need(cell in expected and cell not in seen, 'Unbound/duplicate VML comment cell'); seen.add(cell)
        raw = one('Anchor')
        fields = [value.strip() for value in raw.split(',')]
        need(len(fields) == 8, 'Invalid VML anchor length')
        values = [integer(value, maximum) for value, maximum in zip(fields, (16383, 1000000, 1048575, 1000000, 16383, 1000000, 1048575, 1000000))]
        need((values[0], values[1]) <= (values[4], values[5]) and (values[2], values[3]) <= (values[6], values[7]), 'Reversed VML anchor')
        expected[cell]['anchor'] = {'cell_row_zero_based': row, 'cell_column_zero_based': col,
            'rectangle_raw': raw, 'components': values, 'units_interpreted': False,
            'source': {'part': path, 'path': '/xml/shape[' + str(index + 1) + ']/ClientData'},
            'shape_id': identity, 'shape_type_raw': shape.get('type'), 'shape_type_resolved': False, 'layout_decoded': False}
    need(seen == set(expected), 'Missing VML anchor for comment')
