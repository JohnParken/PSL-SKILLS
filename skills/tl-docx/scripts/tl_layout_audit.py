"""Read-only static layout checks; no rendering or full schema validation."""

from docx.oxml.ns import qn

from tl_package import OFFICE_REL


PARAGRAPH_SEQUENCE = (
    'pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers '
    'pBdr shd tabs suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE '
    'autoSpaceDN bidi adjustRightInd snapToGrid spacing ind contextualSpacing mirrorIndents '
    'suppressOverlap jc textDirection textAlignment textboxTightWrap outlineLvl divId cnfStyle '
    'rPr sectPr pPrChange'
).split()
PARAGRAPH_ORDER = {qn('w:' + name): index for index, name in enumerate(PARAGRAPH_SEQUENCE)}


def _number(node, attribute, default=None):
    if node is None:
        return default
    value = node.get(qn('w:' + attribute))
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return None


def _add(issues, code, location, message, level='warning'):
    issues.append({'level': level, 'code': code, 'location': location, 'message': message})


def _paragraph_properties(root, part, issues):
    for index, properties in enumerate(root.iter(qn('w:pPr'))):
        location = f'{part}:pPr[{index}]'
        sequence = [PARAGRAPH_ORDER[node.tag] for node in properties if node.tag in PARAGRAPH_ORDER]
        if len(sequence) != len(set(sequence)):
            _add(issues, 'DUPLICATE_PARAGRAPH_PROPERTY', location, 'Repeated singleton paragraph properties.', 'error')
        if sequence != sorted(sequence):
            _add(issues, 'PARAGRAPH_PROPERTY_ORDER', location, 'Paragraph properties are not in schema order.', 'error')
        tabs = properties.find(qn('w:tabs'))
        if tabs is not None:
            positions = [_number(tab, 'pos') for tab in tabs.findall(qn('w:tab'))]
            if None in positions or len(positions) != len(set(positions)) or positions != sorted(positions):
                _add(issues, 'INVALID_TAB_STOPS', location, 'Tab stops require ordered, unique integer positions.', 'error')


def _table_grid(table, location, issues):
    grid = table.find(qn('w:tblGrid'))
    columns = list(grid.findall(qn('w:gridCol'))) if grid is not None else []
    widths = [_number(column, 'w') for column in columns]
    if not columns:
        _add(issues, 'TABLE_GRID_UNCHECKED', location, 'No declared table grid; grid coverage cannot be checked.', 'info')
        return
    if any(width is None or width < 0 for width in widths):
        _add(issues, 'INVALID_TABLE_GRID_WIDTH', location, 'Grid column widths must be nonnegative integers.', 'error')
    previous = set()
    for row_index, row in enumerate(table.findall(qn('w:tr'))):
        row_location = f'{location}:row[{row_index}]'
        props = row.find(qn('w:trPr'))
        before = _number(props.find(qn('w:gridBefore')), 'val', 0) if props is not None else 0
        after = _number(props.find(qn('w:gridAfter')), 'val', 0) if props is not None else 0
        if before is None or after is None or min(before, after) < 0:
            _add(issues, 'INVALID_TABLE_GRID_OFFSET', row_location, 'Row grid offsets must be nonnegative integers.', 'error')
            previous = set()
            continue
        cursor = before
        active = set()
        for cell in row.findall(qn('w:tc')):
            props = cell.find(qn('w:tcPr'))
            span = _number(props.find(qn('w:gridSpan')), 'val', 1) if props is not None else 1
            if span is None or span < 1:
                _add(issues, 'INVALID_TABLE_GRID_SPAN', row_location, 'Cell grid span must be a positive integer.', 'error')
                break
            interval = (cursor, cursor + span)
            merge = props.find(qn('w:vMerge')) if props is not None else None
            if merge is not None:
                mode = merge.get(qn('w:val'), 'continue')
                if mode not in ('restart', 'continue') or mode == 'continue' and interval not in previous:
                    _add(issues, 'INVALID_VERTICAL_MERGE', row_location, 'Vertical merge must match the preceding row grid interval.', 'error')
                active.add(interval)
            cursor += span
        if cursor + after != len(columns):
            _add(issues, 'TABLE_GRID_COVERAGE', row_location, 'Physical cells and row offsets do not cover the declared grid.', 'error')
        previous = active


def _section_size(section, location, issues, gutter_at_top=False):
    page = section.find(qn('w:pgSz'))
    margins = section.find(qn('w:pgMar'))
    width, height = _number(page, 'w'), _number(page, 'h')
    left, right = _number(margins, 'left'), _number(margins, 'right')
    top, bottom = _number(margins, 'top'), _number(margins, 'bottom')
    gutter = _number(margins, 'gutter', 0)
    values = (width, height, left, right, top, bottom, gutter)
    for node, attributes in ((page, ('w', 'h')), (margins, ('left', 'right', 'top', 'bottom', 'gutter'))):
        if node is not None and any(node.get(qn('w:' + key)) is not None and _number(node, key) is None for key in attributes):
            _add(issues, 'INVALID_SECTION_PAGE', location, 'Explicit page dimensions and margins must be integers.', 'error')
            return None
    if None in values:
        _add(issues, 'SECTION_GEOMETRY_UNCHECKED', location, 'Missing or invalid page geometry; inherited/application defaults are not resolved.', 'info')
        return None
    if width <= 0 or height <= 0 or min(left, right, gutter) < 0:
        _add(issues, 'INVALID_SECTION_PAGE', location, 'Page size must be positive; horizontal margins and gutter nonnegative.', 'error')
        return None
    available = (width - left - right - (0 if gutter_at_top else gutter),
                 height - abs(top) - abs(bottom) - (gutter if gutter_at_top else 0))
    if min(available) <= 0:
        _add(issues, 'INVALID_CONTENT_REGION', location, 'Margins leave no positive content region.', 'error')
        return None
    return available


def _section_metadata(section, location, issues):
    numbering = section.find(qn('w:pgNumType'))
    # OOXML permits zero as a page-number start; do not reject it as a layout preference.
    start = _number(numbering, 'start', 0)
    if start is None or start < 0:
        _add(issues, 'INVALID_PAGE_NUMBER_START', location, 'Page-number start must be a nonnegative integer.', 'error')
    for kind in ('header', 'footer'):
        variants = [node.get(qn('w:type')) for node in section.findall(qn('w:' + kind + 'Reference'))]
        if len(variants) != len(set(variants)) or any(value not in ('default', 'first', 'even') for value in variants):
            _add(issues, 'INVALID_SECTION_REFERENCES', location, 'Header/footer reference variants must be valid and unique.', 'error')


def _section_items(items, section, size, location, issues):
    if size is None:
        return
    width, height = size
    columns = section.find(qn('w:cols'))
    count = _number(columns, 'num', 1)
    if columns is not None and (count != 1 or len(columns.findall(qn('w:col'))) > 1):
        _add(issues, 'MULTICOLUMN_LAYOUT_UNCHECKED', location, 'Column-relative object fit is not resolved; only whole-page bounds checked.', 'info')
    for index, item in enumerate(items):
        item_location = f'{location}:item[{index}]'
        # Only top-level tables can be compared directly with the section width.
        if item.tag == qn('w:tbl'):
            grid = item.find(qn('w:tblGrid'))
            widths = [_number(col, 'w') for col in grid.findall(qn('w:gridCol'))] if grid is not None else []
            if widths and all(value is not None and value >= 0 for value in widths) and sum(widths) > width:
                _add(issues, 'TABLE_WIDTH_RISK', item_location, 'Declared table grid exceeds the section content width; rendering may resize it.')
        for drawing in item.iter(qn('wp:inline')):
            extent = drawing.find(qn('wp:extent'))
            if extent is None:
                continue
            try:
                dimensions = (int(extent.get('cx', '')), int(extent.get('cy', '')))
            except ValueError:
                _add(issues, 'INVALID_IMAGE_EXTENT', item_location, 'Inline drawing extent must contain integer dimensions.', 'error')
                continue
            if min(dimensions) <= 0:
                _add(issues, 'INVALID_IMAGE_EXTENT', item_location, 'Inline drawing extent must be positive.', 'error')
            elif dimensions[0] > width * 635 or dimensions[1] > height * 635:
                _add(issues, 'INLINE_IMAGE_SIZE_RISK', item_location, 'Inline image exceeds the whole-section content width or height.')
        if any(True for _ in item.iter(qn('wp:anchor'))):
            _add(issues, 'FLOATING_DRAWING_UNCHECKED', item_location, 'Floating position, wrapping and overlap require visual review.', 'info')


def audit_layout(package):
    """Audit an already bounded Package without changing or extracting its bytes."""
    issues = []
    root = package.xml(package.main)
    roots = {package.main: root}
    gutter_at_top = False
    for relation in package.relations(package.main).values():
        if relation['type'] == OFFICE_REL + 'settings' and not relation['external'] and relation['target'] in package.names:
            setting = package.xml(relation['target']).find(qn('w:gutterAtTop'))
            gutter_at_top = setting is not None and setting.get(qn('w:val'), '1').lower() not in ('0', 'false', 'off')
        if relation['type'] in {OFFICE_REL + role for role in ('styles', 'header', 'footer')}:
            if not relation['external'] and relation['target'] in package.names:
                roots[relation['target']] = package.xml(relation['target'])
    for part, tree in roots.items():
        _paragraph_properties(tree, part, issues)
        for index, table in enumerate(tree.iter(qn('w:tbl'))):
            _table_grid(table, f'{part}:table[{index}]', issues)
    body = root.find(qn('w:body'))
    items = []
    sections = 0
    for child in body:
        if child.tag == qn('w:sectPr'):
            section = child
        else:
            items.append(child)
            section = child.find(qn('w:pPr') + '/' + qn('w:sectPr')) if child.tag == qn('w:p') else None
        if section is not None:
            location = f'{package.main}:section[{sections}]'
            _section_metadata(section, location, issues)
            size = _section_size(section, location, issues, gutter_at_top)
            _section_items(items, section, size, location, issues)
            items = []
            sections += 1
    if len(body) == 0 or body[-1].tag != qn('w:sectPr') or items:
        _add(issues, 'SECTION_END_UNCHECKED', package.main, 'Final section properties absent; remaining geometry is not checked.', 'info')
    return {
        'layout_validation': 'static_only', 'visual_validation': 'not_run',
        'checks_passed': not any(issue['level'] == 'error' for issue in issues),
        'issues': issues, 'sections_checked': sections,
        'checked_scope': ['main_section_geometry', 'top_level_table_width', 'inline_image_page_bounds',
                          'physical_table_grid', 'paragraph_properties', 'section_reference_variants'],
        'limitations': ['Not full OOXML schema validation or rendered pagination.',
                        'No inherited page defaults, floating overlap, column fit or cell-level image fit resolution.',
                        'Geometry inside content-control/revision wrappers and headers/footers is not resolved.'],
    }
