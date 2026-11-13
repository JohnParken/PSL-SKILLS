"""Read-only static pagination-risk checks for top-level DOCX body blocks."""

from docx.oxml.ns import qn


WORD_P = qn('w:p')
WORD_PPR = qn('w:pPr')
WORD_R = qn('w:r')
WORD_RPR = qn('w:rPr')
WORD_STYLE = qn('w:style')
WORD_STYLE_ID = qn('w:styleId')
WORD_STYLE_TYPE = qn('w:type')
WORD_STYLE_DEFAULT = qn('w:default')
WORD_BASED_ON = qn('w:basedOn')
WORD_OUTLINE_LEVEL = qn('w:outlineLvl')
WORD_PAGE_BREAK_BEFORE = qn('w:pageBreakBefore')
WORD_KEEP_NEXT = qn('w:keepNext')
WORD_PAGE_BREAK = qn('w:br')
WORD_BREAK_TYPE = qn('w:type')
WORD_TEXT = qn('w:t')
WORD_LAST_RENDERED_BREAK = qn('w:lastRenderedPageBreak')
WORD_SECTION_PROPERTIES = qn('w:sectPr')
WORD_SECTION_TYPE = qn('w:type')
WORD_DOC_DEFAULTS = qn('w:docDefaults')
WORD_PPR_DEFAULT = qn('w:pPrDefault')
WORD_VAL = qn('w:val')

FALSE_VALUES = frozenset({'0', 'false', 'off'})
OUTLINE_LEVELS = frozenset(str(level) for level in range(9))
BUILTIN_HEADINGS = frozenset(f'Heading{level}' for level in range(1, 10))
TRANSPARENT_TAGS = frozenset(
    {
        WORD_P,
        WORD_R,
        qn('w:hyperlink'),
        qn('w:smartTag'),
        qn('w:customXml'),
        qn('w:sdt'),
        qn('w:sdtContent'),
        qn('w:ins'),
        qn('w:del'),
        qn('w:moveFrom'),
        qn('w:moveTo'),
        qn('w:fldSimple'),
        qn('w:dir'),
        qn('w:bdo'),
        qn('w:proofErr'),
    }
)
MARKER_TAGS = frozenset(
    {
        qn('w:bookmarkStart'),
        qn('w:bookmarkEnd'),
        qn('w:commentRangeStart'),
        qn('w:commentRangeEnd'),
        qn('w:permStart'),
        qn('w:permEnd'),
    }
)

PAGINATION_CODES = frozenset(
    {
        'LEADING_PAGE_BREAK',
        'REPEATED_PAGE_BREAK',
        'TRAILING_PAGE_BREAK',
        'MULTIPLE_PAGE_BREAKS_IN_PARAGRAPH',
        'INLINE_PAGE_BREAK_RISK',
        'PAGE_BREAK_SECTION_CONFLICT',
        'REDUNDANT_HEADING_PAGE_BREAK',
        'REDUNDANT_PARAGRAPH_PAGE_BREAK',
        'SECTION_HEADING_PAGE_BREAK',
        'HEADING_SEPARATED_FROM_CONTENT',
        'HEADING_WITHOUT_CONTENT',
        'HEADING_ORPHAN_RISK',
    }
)


def _enabled(element):
    """Return the effective boolean value represented by a Word property."""
    return element is not None and element.get(WORD_VAL, '1').lower() not in FALSE_VALUES


def _style_definitions(styles_root):
    """Map paragraph style IDs to their parent ID and paragraph properties."""
    if styles_root is None:
        return {}

    definitions = {}
    for style in styles_root.findall(WORD_STYLE):
        style_id = style.get(WORD_STYLE_ID)
        if not style_id or style.get(WORD_STYLE_TYPE, 'paragraph') != 'paragraph':
            continue
        parent = style.find(WORD_BASED_ON)
        parent_id = parent.get(WORD_VAL) if parent is not None else None
        definitions[style_id] = (parent_id, style.find(WORD_PPR))
    return definitions


def _default_paragraph_style_id(styles_root):
    """Return the style ID marked as the default paragraph style, if any."""
    if styles_root is None:
        return None
    for style in styles_root.findall(WORD_STYLE):
        if style.get(WORD_STYLE_TYPE) != 'paragraph':
            continue
        value = style.get(WORD_STYLE_DEFAULT)
        if value is not None and value.lower() not in FALSE_VALUES:
            return style.get(WORD_STYLE_ID)
    return None


def _style_chain(style_id, styles):
    """Yield a paragraph style and its basedOn ancestors without cycling."""
    visited = set()
    current = style_id
    while current and current not in visited:
        visited.add(current)
        yield current
        current = styles.get(current, (None, None))[0]


def _default_paragraph_properties(styles_root):
    """Return document-wide default paragraph properties when present."""
    if styles_root is None:
        return None
    defaults = styles_root.find(WORD_DOC_DEFAULTS)
    if defaults is None:
        return None
    paragraph_default = defaults.find(WORD_PPR_DEFAULT)
    if paragraph_default is None:
        return None
    return paragraph_default.find(WORD_PPR)


def _paragraph_style_id(paragraph, default_style_id=None):
    """Return the paragraph style ID, including Word's default style fallback."""
    properties = paragraph.find(WORD_PPR)
    style = properties.find(qn('w:pStyle')) if properties is not None else None
    return style.get(WORD_VAL) if style is not None else default_style_id


def _effective_property(paragraph, property_tag, styles, defaults, default_style_id):
    """Resolve a paragraph property through direct, inherited, and default values."""
    direct_properties = paragraph.find(WORD_PPR)
    direct = direct_properties.find(property_tag) if direct_properties is not None else None
    if direct is not None:
        return _enabled(direct)

    style_id = _paragraph_style_id(paragraph, default_style_id)
    for current in _style_chain(style_id, styles):
        style_properties = styles.get(current, (None, None))[1]
        inherited = style_properties.find(property_tag) if style_properties is not None else None
        if inherited is not None:
            return _enabled(inherited)

    default = defaults.find(property_tag) if defaults is not None else None
    return _enabled(default)


def _is_outline_level(element):
    """Return whether an outline-level property denotes a heading level."""
    if element is None:
        return False
    return element.get(WORD_VAL) in OUTLINE_LEVELS


def _is_heading(paragraph, styles, default_style_id):
    """Recognize built-in heading styles and inherited outline levels."""
    direct_properties = paragraph.find(WORD_PPR)
    direct = direct_properties.find(WORD_OUTLINE_LEVEL) if direct_properties is not None else None
    if direct is not None:
        return _is_outline_level(direct)

    style_id = _paragraph_style_id(paragraph, default_style_id)
    for current in _style_chain(style_id, styles):
        style_properties = styles.get(current, (None, None))[1]
        outline_level = (
            style_properties.find(WORD_OUTLINE_LEVEL)
            if style_properties is not None
            else None
        )
        if outline_level is not None:
            return _is_outline_level(outline_level)
        if current in BUILTIN_HEADINGS:
            return True
    return False


def _is_page_break(element):
    """Return whether a run break is an explicit page break."""
    return element.get(WORD_BREAK_TYPE, 'textWrapping') == 'page'


def _contains_non_break_content(element):
    """Detect substantive paragraph content besides explicit page breaks."""
    for child in element:
        if child.tag in (WORD_PPR, WORD_RPR, WORD_LAST_RENDERED_BREAK):
            continue
        if child.tag == WORD_PAGE_BREAK:
            if not _is_page_break(child):
                return True
            continue
        if child.tag == WORD_TEXT:
            if child.text:
                return True
            continue
        if child.tag in MARKER_TAGS:
            continue
        if child.tag in TRANSPARENT_TAGS:
            if _contains_non_break_content(child):
                return True
            continue
        return True
    return False


def _page_break_info(paragraph):
    """Return explicit page-break count and whether the paragraph is standalone."""
    breaks = [
        element
        for element in paragraph.iter(WORD_PAGE_BREAK)
        if _is_page_break(element)
    ]
    if not breaks:
        return 0, False
    return len(breaks), not _contains_non_break_content(paragraph)


def _section_properties(paragraph):
    """Return section properties attached to a paragraph, if present."""
    properties = paragraph.find(WORD_PPR)
    return properties.find(WORD_SECTION_PROPERTIES) if properties is not None else None


def _section_start_type(section_properties):
    """Return the break type that starts the section represented by properties."""
    if section_properties is None:
        return 'nextPage'
    section_type = section_properties.find(WORD_SECTION_TYPE)
    return section_type.get(WORD_VAL, 'nextPage') if section_type is not None else 'nextPage'


def _next_section_type(children, index, body):
    """Resolve the next sectPr, which controls the section after this break."""
    for child in children[index + 1:]:
        if child.tag != WORD_P:
            continue
        section_properties = _section_properties(child)
        if section_properties is not None:
            return _section_start_type(section_properties)
    return _section_start_type(body.find(WORD_SECTION_PROPERTIES))


def _issue(part, index, code, message):
    """Create one stable pagination warning record."""
    return {
        'level': 'warning',
        'code': code,
        'location': f'{part}:body[{index}]',
        'message': message,
    }


def audit_pagination(body, styles_root, part: str) -> list[dict]:
    """Return static pagination warnings for direct children of a document body."""
    if body is None:
        return []

    styles = _style_definitions(styles_root)
    defaults = _default_paragraph_properties(styles_root)
    default_style_id = _default_paragraph_style_id(styles_root)
    children = [child for child in body if child.tag != WORD_SECTION_PROPERTIES]
    if not children:
        return []

    issues = []
    kinds = []
    for index, child in enumerate(children):
        if child.tag != WORD_P:
            kinds.append('content')
            continue

        if _section_properties(child) is not None:
            kinds.append('section')
            continue

        break_count, standalone = _page_break_info(child)
        if break_count > 1:
            issues.append(
                _issue(
                    part,
                    index,
                    'MULTIPLE_PAGE_BREAKS_IN_PARAGRAPH',
                    'Multiple explicit page breaks in one paragraph may add blank pages.',
                )
            )
        if break_count and not standalone:
            issues.append(
                _issue(
                    part,
                    index,
                    'INLINE_PAGE_BREAK_RISK',
                    'An explicit page break is mixed with paragraph content.',
                )
            )

        if standalone and break_count:
            kinds.append('page_break')
        elif _is_heading(child, styles, default_style_id):
            kinds.append('heading')
            if not _effective_property(
                child, WORD_KEEP_NEXT, styles, defaults, default_style_id
            ):
                issues.append(
                    _issue(
                        part,
                        index,
                        'HEADING_ORPHAN_RISK',
                        'Heading is not configured to stay with the following paragraph.',
                    )
                )
        else:
            kinds.append('content')

    for index, kind in enumerate(kinds):
        previous = kinds[index - 1] if index else None
        following = kinds[index + 1] if index + 1 < len(kinds) else None
        child = children[index]

        if kind == 'page_break':
            if index == 0:
                issues.append(
                    _issue(
                        part,
                        index,
                        'LEADING_PAGE_BREAK',
                        'A manual page break at the beginning may create a blank first page.',
                    )
                )
            if previous == 'page_break':
                issues.append(
                    _issue(
                        part,
                        index,
                        'REPEATED_PAGE_BREAK',
                        'Adjacent manual page breaks may create an unnecessary blank page.',
                    )
                )
            if index == len(kinds) - 1:
                issues.append(
                    _issue(
                        part,
                        index,
                        'TRAILING_PAGE_BREAK',
                        'A final manual page break may produce a blank last page.',
                    )
                )
            if following in ('heading', 'content') and children[index + 1].tag == WORD_P:
                if _effective_property(
                    children[index + 1],
                    WORD_PAGE_BREAK_BEFORE,
                    styles,
                    defaults,
                    default_style_id,
                ):
                    code = (
                        'REDUNDANT_HEADING_PAGE_BREAK'
                        if following == 'heading'
                        else 'REDUNDANT_PARAGRAPH_PAGE_BREAK'
                    )
                    issues.append(
                        _issue(
                            part,
                            index,
                            code,
                            'A manual break duplicates pageBreakBefore on the next paragraph.',
                        )
                    )

        elif kind == 'heading':
            if following in ('page_break', 'section'):
                issues.append(
                    _issue(
                        part,
                        index,
                        'HEADING_SEPARATED_FROM_CONTENT',
                        'A break immediately after a heading may separate it from its body text.',
                    )
                )
            elif following is None:
                issues.append(
                    _issue(
                        part,
                        index,
                        'HEADING_WITHOUT_CONTENT',
                        'The document ends with a heading and no following content.',
                    )
                )

        elif kind == 'section':
            section_type = _next_section_type(children, index, body)
            if section_type != 'continuous':
                if previous == 'page_break' or following == 'page_break':
                    issues.append(
                        _issue(
                            part,
                            index,
                            'PAGE_BREAK_SECTION_CONFLICT',
                            'An adjacent manual break may duplicate a page-starting section break.',
                        )
                    )
                if following in ('heading', 'content') and children[index + 1].tag == WORD_P:
                    if _effective_property(
                        children[index + 1],
                        WORD_PAGE_BREAK_BEFORE,
                        styles,
                        defaults,
                        default_style_id,
                    ):
                        issues.append(
                            _issue(
                                part,
                                index,
                                'SECTION_HEADING_PAGE_BREAK',
                                'A page-starting section duplicates pageBreakBefore on its first paragraph.',
                            )
                        )

    return issues
