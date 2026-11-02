"""Ordered WordprocessingML paragraph-property serializer.

The order below follows CT_PPrBase / CT_PPr. This module has no dependency on
create_docx, so generation and future editing can share it safely.
"""
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

PARAGRAPH_FORMAT_FIELDS = frozenset({
    'keep_with_next', 'keep_together', 'widow_control', 'page_break_before',
    'left_indent_pt', 'right_indent_pt', 'first_line_indent_pt',
    'hanging_indent_pt', 'tab_stops',
})


def q(local):
    return '{' + W + '}' + local


def points_to_twips(value):
    return str(int(value * 20 + 0.5))


def append_paragraph_properties(parent, formatting, *, keep_next=False,
                                outline_level=None, num_id=None, list_level=0):
    """Append only CT_PPr children, in schema order; pStyle must already exist."""
    for key, tag, fallback in (
        ('keep_with_next', 'keepNext', True if keep_next else None),
        ('keep_together', 'keepLines', None),
        ('page_break_before', 'pageBreakBefore', None),
        ('widow_control', 'widowControl', None),
    ):
        value = formatting.get(key, fallback)
        if value is not None:
            ET.SubElement(parent, q(tag), {q('val'): '1' if value else '0'})

    if num_id is not None:
        num_props = ET.SubElement(parent, q('numPr'))
        ET.SubElement(num_props, q('ilvl'), {q('val'): str(list_level)})
        ET.SubElement(num_props, q('numId'), {q('val'): str(num_id)})

    if 'tab_stops' in formatting and formatting['tab_stops']:
        tabs = ET.SubElement(parent, q('tabs'))
        for stop in formatting['tab_stops']:
            ET.SubElement(tabs, q('tab'), {
                q('val'): stop['align'], q('pos'): points_to_twips(stop['position_pt']),
            })

    if any(key in formatting for key in (
        'space_before_pt', 'space_after_pt', 'line_spacing_pt', 'line_spacing_multiple',
    )):
        attrs = {}
        if 'space_before_pt' in formatting:
            attrs[q('before')] = points_to_twips(formatting['space_before_pt'])
        if 'space_after_pt' in formatting:
            attrs[q('after')] = points_to_twips(formatting['space_after_pt'])
        if 'line_spacing_pt' in formatting:
            attrs[q('line')] = points_to_twips(formatting['line_spacing_pt'])
            attrs[q('lineRule')] = 'exact'
        elif 'line_spacing_multiple' in formatting:
            attrs[q('line')] = str(int(formatting['line_spacing_multiple'] * 240 + 0.5))
            attrs[q('lineRule')] = 'auto'
        ET.SubElement(parent, q('spacing'), attrs)

    attrs = {}
    for key, tag in (
        ('left_indent_pt', 'left'), ('right_indent_pt', 'right'),
        ('first_line_indent_pt', 'firstLine'), ('hanging_indent_pt', 'hanging'),
    ):
        if key in formatting:
            attrs[q(tag)] = points_to_twips(formatting[key])
    if 'first_line_chars' in formatting:
        attrs[q('firstLineChars')] = str(int(formatting['first_line_chars'] * 100 + 0.5))
    if attrs:
        ET.SubElement(parent, q('ind'), attrs)
    if 'align' in formatting:
        value = 'both' if formatting['align'] == 'justify' else formatting['align']
        ET.SubElement(parent, q('jc'), {q('val'): value})
    if outline_level is not None:
        ET.SubElement(parent, q('outlineLvl'), {q('val'): str(outline_level)})
