"""Advisory WordprocessingML pagination checks (no layout engine required).

The algorithm scans top-level document-body elements only, so a page break in
an unrelated table cell/header cannot be mistaken for a section boundary. It
inspects explicit XML intent, *not* actual Word/WPS page numbers. Warnings do
not mutate source documents and must not prevent the legacy create flow.
"""

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

PAGINATION_CODES = frozenset({
    'LEADING_PAGE_BREAK', 'REPEATED_PAGE_BREAK', 'TRAILING_PAGE_BREAK',
    'HEADING_ORPHAN_RISK',
    'PAGE_BREAK_SECTION_CONFLICT', 'REDUNDANT_HEADING_PAGE_BREAK',
    'SECTION_HEADING_PAGE_BREAK', 'HEADING_SEPARATED_FROM_CONTENT',
    'HEADING_WITHOUT_CONTENT', 'REDUNDANT_PARAGRAPH_PAGE_BREAK',
    'MULTIPLE_PAGE_BREAKS_IN_PARAGRAPH',
    'INLINE_PAGE_BREAK_RISK',
})


def _enabled(element):
    return element is not None and element.get(W + 'val', '1').lower() not in ('0', 'false', 'off')


def _styles(root):
    """Map paragraph style ID -> (basedOn ID, pPr) for effective inherited props."""
    if root is None:
        return {}
    result = {}
    for style in root.findall(W + 'style'):
        identifier = style.get(W + 'styleId')
        parent = style.find(W + 'basedOn')
        if identifier:
            result[identifier] = (parent.get(W + 'val') if parent is not None else None,
                                  style.find(W + 'pPr'))
    return result


def _effective_property(paragraph, prop, styles):
    ppr = paragraph.find(W + 'pPr')
    direct = ppr.find(W + prop) if ppr is not None else None
    if direct is not None:
        return _enabled(direct)
    style = ppr.find(W + 'pStyle') if ppr is not None else None
    sid = style.get(W + 'val') if style is not None else None
    visited = set()
    while sid and sid not in visited:
        visited.add(sid)
        parent, properties = styles.get(sid, (None, None))
        candidate = properties.find(W + prop) if properties is not None else None
        if candidate is not None:
            return _enabled(candidate)
        sid = parent
    return False


def _heading(paragraph, styles):
    ppr = paragraph.find(W + 'pPr')
    if ppr is None:
        return False
    style = ppr.find(W + 'pStyle')
    sid = style.get(W + 'val', '') if style is not None else ''
    if sid.startswith('Heading') and sid[7:].isdigit():
        return True
    if ppr.find(W + 'outlineLvl') is not None:
        return True
    visited = set()
    while sid and sid not in visited:
        visited.add(sid)
        based_on, properties = styles.get(sid, (None, None))
        if properties is not None and properties.find(W + 'outlineLvl') is not None:
            return True
        sid = based_on
    return False


def _section_type(sectpr):
    if sectpr is None:
        return 'nextPage'
    element = sectpr.find(W + 'type')
    return element.get(W + 'val', 'nextPage') if element is not None else 'nextPage'


def _paragraph_break_info(p):
    breaks = [x for x in p.iter(W + 'br') if x.get(W + 'type', 'textWrapping') == 'page']
    # Last rendered breaks (Word pagination cache) are intentionally ignored.
    if not breaks:
        return 0, False, False
    # A page-break-only paragraph may have pPr and rPr, but no substantive
    # content other than explicit page breaks. Ignore run-level formatting.
    allowed = {W + 'p', W + 'pPr', W + 'r', W + 'rPr', W + 'br'}
    standalone = all(node.tag in allowed for node in p.iter())
    return len(breaks), standalone, not standalone


def audit_pagination(roots, issue):
    """Emit human-readable warning(level, code, location, message) callbacks."""
    document = roots.get('word/document.xml')
    if document is None:
        return
    body = document.find(W + 'body')
    if body is None:
        return
    styles = _styles(roots.get('word/styles.xml'))
    children = [item for item in body if item.tag != W + 'sectPr']
    if not children:
        return

    kinds = []
    for index, child in enumerate(children):
        location = f'word/document.xml:body[{index}]'
        if child.tag != W + 'p':
            kinds.append('content')
            continue
        ppr = child.find(W + 'pPr')
        if ppr is not None and ppr.find(W + 'sectPr') is not None:
            kinds.append('section')
            continue
        break_count, standalone, inline = _paragraph_break_info(child)
        if break_count > 1:
            issue('warning', 'MULTIPLE_PAGE_BREAKS_IN_PARAGRAPH', location,
                  'Multiple explicit page breaks in one paragraph may create blank pages')
        if inline:
            issue('warning', 'INLINE_PAGE_BREAK_RISK', location,
                  'An explicit page break is mixed with paragraph content; review rendered layout')
        kinds.append('page_break' if break_count and standalone else 'heading' if _heading(child, styles) else 'content')

    for index, kind in enumerate(kinds):
        location = f'word/document.xml:body[{index}]'
        prev = kinds[index - 1] if index else None
        nxt = kinds[index + 1] if index + 1 < len(kinds) else None
        if kind == 'page_break':
            if index == 0:
                issue('warning', 'LEADING_PAGE_BREAK', location,
                      'A manual page break at the beginning may create a blank first page')
            if prev == 'page_break':
                issue('warning', 'REPEATED_PAGE_BREAK', location,
                      'Adjacent manual page breaks may create an unnecessary blank page')
            if index == len(kinds) - 1:
                issue('warning', 'TRAILING_PAGE_BREAK', location,
                      'A final manual page break may produce a blank last page')
            if nxt in ('heading', 'content') and children[index + 1].tag == W + 'p' and \
                    _effective_property(children[index + 1], 'pageBreakBefore', styles):
                heading = nxt == 'heading'
                issue('warning', 'REDUNDANT_HEADING_PAGE_BREAK' if heading else 'REDUNDANT_PARAGRAPH_PAGE_BREAK',
                      location, 'Manual break before a paragraph already using pageBreakBefore is redundant')
        elif kind == 'heading':
            if nxt in ('page_break', 'section'):
                issue('warning', 'HEADING_SEPARATED_FROM_CONTENT', location,
                      'A break immediately after a heading may separate it from its body text')
            elif nxt is None:
                issue('warning', 'HEADING_WITHOUT_CONTENT', location,
                      'The document ends with a heading and no following content')
        elif kind == 'section':
            # In WordprocessingML the section-ending paragraph stores the *old*
            # sectPr; the new section break type is found on the NEXT sectPr.
            subsequent_sectpr = None
            for other in children[index + 1:]:
                if other.tag == W + 'p':
                    prop = other.find(W + 'pPr/' + W + 'sectPr')
                    if prop is not None:
                        subsequent_sectpr = prop
                        break
            if subsequent_sectpr is None:
                subsequent_sectpr = body.find(W + 'sectPr')
            break_type = _section_type(subsequent_sectpr)
            if break_type != 'continuous':
                if prev == 'page_break' or nxt == 'page_break':
                    issue('warning', 'PAGE_BREAK_SECTION_CONFLICT', location,
                          'A manual break adjacent to a page-starting section may add blank pages')
                if nxt in ('heading', 'content') and children[index + 1].tag == W + 'p' and \
                        _effective_property(children[index + 1], 'pageBreakBefore', styles):
                    issue('warning', 'SECTION_HEADING_PAGE_BREAK', location,
                          'The new section already starts on a new page; pageBreakBefore may be redundant')
