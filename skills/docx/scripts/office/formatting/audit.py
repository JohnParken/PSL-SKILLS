"""Deterministic OOXML layout-risk audit; never claims visual/page verification."""
from collections import Counter

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
ORDER = (
    'pStyle', 'keepNext', 'keepLines', 'pageBreakBefore', 'framePr', 'widowControl',
    'numPr', 'suppressLineNumbers', 'pBdr', 'shd', 'tabs', 'suppressAutoHyphens',
    'kinsoku', 'wordWrap', 'overflowPunct', 'topLinePunct', 'autoSpaceDE',
    'autoSpaceDN', 'bidi', 'adjustRightInd', 'snapToGrid', 'spacing', 'ind',
    'contextualSpacing', 'mirrorIndents', 'suppressOverlap', 'jc', 'textDirection',
    'textAlignment', 'textboxTightWrap', 'outlineLvl',
)
ORDER_INDEX = {W + name: index for index, name in enumerate(ORDER)}


def _integer(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def audit_roots(roots, *, profile=None):
    """Analyze validated XML roots. The caller validates package security first."""
    issues = []

    def issue(level, code, location, message):
        issues.append({'level': level, 'code': code, 'location': location, 'message': message})

    for part in ('word/document.xml', 'word/styles.xml'):
        root = roots.get(part)
        if root is None:
            issue('error', 'MISSING_XML_PART', part, 'Required layout part missing')
            continue
        for index, ppr in enumerate(root.iter(W + 'pPr')):
            labels = [child.tag for child in ppr]
            seen = Counter(tag for tag in labels if tag in ORDER_INDEX)
            if any(n > 1 for n in seen.values()):
                issue('error', 'DUPLICATE_PARAGRAPH_PROPERTY', f'{part}:pPr[{index}]',
                      'Paragraph contains duplicate singleton properties')
            order = [ORDER_INDEX[tag] for tag in labels if tag in ORDER_INDEX]
            if order != sorted(order):
                issue('error', 'PARAGRAPH_PROPERTY_ORDER', f'{part}:pPr[{index}]',
                      'Paragraph properties are out of OOXML schema order')
            tabs = ppr.find(W + 'tabs')
            if tabs is not None:
                positions = [_integer(tab.get(W + 'pos')) for tab in tabs.findall(W + 'tab')]
                if any(v is None for v in positions) or positions != sorted(set(positions)):
                    issue('error', 'INVALID_TAB_STOPS', f'{part}:pPr[{index}]',
                          'Tab positions must be valid, ordered and unique')

    document = roots.get('word/document.xml')
    styles = roots.get('word/styles.xml')
    if document is not None:
        body = document.find(W + 'body')
        # Match each body segment with the section closing it. A paragraph
        # pPr/sectPr describes that paragraph's (preceding) section; the last
        # body/sectPr describes the final section.
        sections = []
        segment = []
        for child in body if body is not None else []:
            segment.append(child)
            sect = (child.find(W + 'pPr/' + W + 'sectPr') if child.tag == W + 'p'
                    else child if child.tag == W + 'sectPr' else None)
            if sect is not None:
                page = sect.find(W + 'pgSz')
                margins = sect.find(W + 'pgMar')
                width = _integer(page.get(W + 'w')) if page is not None else None
                height = _integer(page.get(W + 'h')) if page is not None else None
                left = _integer(margins.get(W + 'left')) if margins is not None else None
                right = _integer(margins.get(W + 'right')) if margins is not None else None
                top = _integer(margins.get(W + 'top')) if margins is not None else None
                bottom = _integer(margins.get(W + 'bottom')) if margins is not None else None
                location = f'word/document.xml:section[{len(sections)}]'
                if None in (width, height, left, right, top, bottom) or width <= 0 or height <= 0:
                    issue('error', 'INVALID_SECTION_PAGE', location, 'Section page dimensions or margins are missing')
                    usable_width = None
                else:
                    usable_width = width - left - right
                    if usable_width <= 0 or height - top - bottom <= 0:
                        issue('error', 'INVALID_CONTENT_WIDTH', location,
                              'Section margins leave no usable content region')
                pg_num = sect.find(W + 'pgNumType')
                if pg_num is not None:
                    start_num = pg_num.get(W + 'start')
                    if start_num is not None and (_integer(start_num) is None or _integer(start_num) < 1):
                        issue('error', 'INVALID_PAGE_NUMBER_START', location, 'Page numbering must start at 1 or later')
                for kind in ('header', 'footer'):
                    refs = [ref.get(W + 'type') for ref in sect.findall(W + kind + 'Reference')]
                    if len(refs) != len(set(refs)) or any(x not in ('default', 'first', 'even') for x in refs):
                        issue('error', 'INVALID_SECTION_REFERENCES', location,
                              'A section may use each header/footer variant only once')
                sections.append((segment[:], usable_width))
                segment.clear()
        if not sections or body is None or body[-1].tag != W + 'sectPr' or segment:
            issue('error', 'INVALID_SECTION_END', 'word/document.xml',
                  'Final section must end with body/sectPr')
        # Apply the proper width to each table or image in its owning section.
        for section_index, (children, usable_width) in enumerate(sections):
            if usable_width is None or usable_width <= 0:
                continue
            for index, child in enumerate(children):
                if child.tag == W + 'tbl':
                    grid = child.find(W + 'tblGrid')
                    widths = [] if grid is None else [_integer(col.get(W + 'w')) for col in grid.findall(W + 'gridCol')]
                    if widths and all(v is not None for v in widths) and sum(widths) > usable_width:
                        issue('warning', 'TABLE_WIDTH_RISK',
                              f'word/document.xml:section[{section_index}]:table[{index}]',
                              'Table grid is wider than available section width')
                for extent in child.iter(WP + 'extent'):
                    cx = _integer(extent.get('cx'))
                    if cx is not None and cx > usable_width * 635:
                        issue('warning', 'IMAGE_WIDTH_RISK',
                              f'word/document.xml:section[{section_index}]:item[{index}]',
                              'Image width is larger than available section width')

        bookmark_names = set()
        bookmark_ids = set()
        for bookmark in document.iter(W + 'bookmarkStart'):
            name = bookmark.get(W + 'name')
            ident = bookmark.get(W + 'id')
            if not name or name.lower() in bookmark_names or not ident or ident in bookmark_ids:
                issue('error', 'INVALID_BOOKMARK', 'word/document.xml',
                      'Bookmarks need unique case-insensitive names and numeric IDs')
            if name:
                bookmark_names.add(name.lower())
            if ident:
                bookmark_ids.add(ident)
        import re
        for field in document.iter(W + 'instrText'):
            match = re.match(r'\s*(?:REF|PAGEREF)\s+([A-Za-z][A-Za-z0-9_]{0,39})\s', field.text or '')
            if match and match.group(1).lower() not in bookmark_names:
                issue('error', 'UNRESOLVED_CROSS_REFERENCE', 'word/document.xml',
                      'Cross-reference points to a missing bookmark')

        # Inspect the physical table grid, not just the visual row/cell count.
        # A vertical continuation must cover the identical grid interval.
        for table_index, table in enumerate(document.iter(W + 'tbl')):
            grid = table.find(W + 'tblGrid')
            column_count = len(grid.findall(W + 'gridCol')) if grid is not None else 0
            previous_merges = set()
            for row_index, row in enumerate(table.findall(W + 'tr')):
                trpr = row.find(W + 'trPr')
                if trpr is not None and any(trpr.find(W + tag) is not None for tag in ('gridBefore', 'gridAfter')):
                    issue('info', 'TABLE_GRID_OFFSETS_UNCHECKED',
                          f'word/document.xml:table[{table_index}]:row[{row_index}]',
                          'Rows using gridBefore/gridAfter are outside this static grid check')
                    previous_merges = set()
                    continue
                position = 0
                current_merges = set()
                for cell in row.findall(W + 'tc'):
                    properties = cell.find(W + 'tcPr')
                    span_node = properties.find(W + 'gridSpan') if properties is not None else None
                    span = 1 if span_node is None else _integer(span_node.get(W + 'val'))
                    location = f'word/document.xml:table[{table_index}]:row[{row_index}]'
                    if span is None or span < 1 or column_count and position + span > column_count:
                        issue('error', 'INVALID_TABLE_GRID_SPAN', location,
                              'Cell spans an invalid number of table grid columns')
                        break
                    merge = properties.find(W + 'vMerge') if properties is not None else None
                    if merge is not None:
                        key = (position, span)
                        value = merge.get(W + 'val', 'continue')
                        if value == 'continue' and key not in previous_merges:
                            issue('error', 'INVALID_VERTICAL_MERGE', location,
                                  'Vertical merge continuation does not match the previous row')
                        elif value not in ('continue', 'restart'):
                            issue('error', 'INVALID_VERTICAL_MERGE', location,
                                  'Unknown vertical merge value')
                        current_merges.add(key)
                    position += span
                if column_count and position != column_count:
                    issue('error', 'TABLE_GRID_COVERAGE',
                          f'word/document.xml:table[{table_index}]:row[{row_index}]',
                          'Table row does not cover exactly its declared grid columns')
                previous_merges = current_merges

        # Respect explicit paragraph overrides and Word's basedOn style chain.
        # A heading whose keepNext is inherited is not an orphan risk.
        from .pagination import _effective_property, _styles
        style_info = _styles(styles)
        for index, paragraph in enumerate(document.iter(W + 'p')):
            ppr = paragraph.find(W + 'pPr')
            if ppr is None:
                continue
            style = ppr.find(W + 'pStyle')
            sid = style.get(W + 'val') if style is not None else None
            if sid and sid.startswith('Heading') and sid[7:].isdigit():
                if not _effective_property(paragraph, 'keepNext', style_info):
                    issue('warning', 'HEADING_ORPHAN_RISK', f'word/document.xml:paragraph[{index}]',
                          'Heading is not configured to stay with the next paragraph')
    from .pagination import audit_pagination
    audit_pagination(roots, issue)
    return {
        'structure_valid': not any(i['level'] == 'error' for i in issues),
        'profile': profile,
        'layout_validation': 'static_only',
        'visual_validation': 'not_run',
        'issues': issues,
    }
