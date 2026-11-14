"""Validated creation profiles, typography overrides, and page normalization."""

from copy import deepcopy
import math
import re

from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from tl_create_layout import apply_run_overrides, apply_paragraph_defaults, apply_run_format

STYLE_NAMES = {'body', 'title', 'subtitle', 'heading1', 'heading2', 'heading3', 'heading4', 'table_body', 'table_header'}
ALIGNMENTS = {'left': WD_ALIGN_PARAGRAPH.LEFT, 'center': WD_ALIGN_PARAGRAPH.CENTER,
              'right': WD_ALIGN_PARAGRAPH.RIGHT, 'justify': WD_ALIGN_PARAGRAPH.JUSTIFY}
FLAGS = {'bold', 'keep_with_next', 'keep_together', 'widow_control', 'page_break_before'}
NUMBERS = {
    'size_pt': (1, 200), 'first_line_chars': (0, 20), 'line_spacing_pt': (1, 400),
    'line_spacing_multiple': (0.5, 10), 'space_before_pt': (0, 400), 'space_after_pt': (0, 400),
    'left_indent_pt': (0, 400), 'right_indent_pt': (0, 400),
    'first_line_indent_pt': (0, 400), 'hanging_indent_pt': (0, 400),
}
FONTS = {'font_east_asia', 'font_latin'}
PAGE_MM = {'width_mm', 'height_mm', 'margin_mm', 'margin_top_mm', 'margin_bottom_mm', 'margin_left_mm', 'margin_right_mm'}


def number(value, name, low, high):
    """Reject non-finite values and booleans before unit conversion."""
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be a finite number between {low} and {high}')
    return value


def validate_format(value, name='format'):
    """Validate a partial paragraph/character format without coercion."""
    if not isinstance(value, dict) or value.keys() - FLAGS - NUMBERS.keys() - FONTS - {'align', 'color'}:
        raise ValueError(f'{name} has unsupported fields or is not an object')
    for key, item in value.items():
        if key in FLAGS and type(item) is not bool:
            raise ValueError(f'{name}.{key} must be boolean')
        if key in NUMBERS:
            number(item, f'{name}.{key}', *NUMBERS[key])
        if key in FONTS and (not isinstance(item, str) or not item.strip() or len(item) > 64
                             or any(ord(char) < 32 for char in item)):
            raise ValueError(f'{name}.{key} must be a nonempty single-line font name')
        if key == 'align' and (not isinstance(item, str) or item not in ALIGNMENTS):
            raise ValueError(f'{name}.align has an unsupported value')
        if key == 'color' and (not isinstance(item, str) or re.fullmatch('[0-9a-fA-F]{6}', item) is None):
            raise ValueError(f'{name}.color must contain six RGB digits')
    if {'line_spacing_pt', 'line_spacing_multiple'} <= value.keys():
        raise ValueError(f'{name} cannot set fixed and multiple line spacing together')
    if len(value.keys() & {'first_line_chars', 'first_line_indent_pt', 'hanging_indent_pt'}) > 1:
        raise ValueError(f'{name} cannot mix first-line indentation modes')


def merge_format(base, override):
    """Replace exclusive modes across layers while preserving other fields."""
    result = dict(base)
    for group in ({'line_spacing_pt', 'line_spacing_multiple'},
                  {'first_line_chars', 'first_line_indent_pt', 'hanging_indent_pt'}):
        if group & override.keys():
            for key in group:
                result.pop(key, None)
    result.update(override)
    return result


def profile_defaults(name):
    """Return fixed independent presets; no resource or adjacent Skill lookup."""
    if not isinstance(name, str) or name not in {'official-basic', 'business-report', 'academic', 'presentation-material'}:
        raise ValueError('Unsupported profile')
    theme = 'official' if name == 'official-basic' else 'academic' if name == 'academic' else 'business'
    result = {'theme': theme, 'styles': {'body': {'widow_control': True}}}
    styles = result['styles']
    if name == 'official-basic':
        for level in range(1, 5):
            styles[f'heading{level}'] = {'keep_with_next': True, 'keep_together': True}
    elif name == 'academic':
        styles['body'].update(size_pt=12, line_spacing_multiple=1.5, first_line_chars=2)
        for level, before, after in ((1, 18, 12), (2, 12, 6), (3, 10, 4), (4, 8, 4)):
            styles[f'heading{level}'] = {'space_before_pt': before, 'space_after_pt': after, 'keep_together': True}
    elif name == 'business-report':
        styles['body'].update(size_pt=11, line_spacing_multiple=1.35, space_after_pt=6)
        result['page'] = {'margin_top_mm': 22, 'margin_bottom_mm': 22, 'margin_left_mm': 24, 'margin_right_mm': 24}
        for level, before, after in ((1, 18, 10), (2, 14, 7), (3, 10, 5), (4, 8, 4)):
            styles[f'heading{level}'] = {'space_before_pt': before, 'space_after_pt': after, 'keep_together': True}
        styles['table_body'] = {'size_pt': 10.5, 'keep_together': True}
        styles['table_header'] = {'size_pt': 10.5, 'keep_together': True}
        result['table_format'] = {'preset': 'three_line', 'cell_margin_top_mm': 1.5, 'cell_margin_bottom_mm': 1.5}
    else:
        styles['body'].update(size_pt=12, line_spacing_multiple=1.4, space_after_pt=8)
        styles['heading1'] = {'size_pt': 19, 'space_before_pt': 18, 'space_after_pt': 10, 'keep_together': True}
        styles['heading2'] = {'size_pt': 15, 'space_before_pt': 12, 'space_after_pt': 8, 'keep_together': True}
        styles['table_body'] = {'size_pt': 10.5}
        styles['table_header'] = {'size_pt': 10.5}
        result['table_format'] = {'preset': 'zebra'}
    return result


def normalize_page(page, defaults=None):
    """Normalize millimetre aliases; explicit single sides beat uniform margins."""
    if not isinstance(page, dict):
        raise ValueError('page must be an object')
    result = dict(page)
    for key in PAGE_MM & page.keys():
        number(page[key], 'page.' + key, 1 if key in {'width_mm', 'height_mm'} else 0, 1000)
    margins = dict((defaults or {}).get('margins_cm', {}))
    if 'margin_mm' in page:
        margins.update({side: page['margin_mm'] / 10 for side in ('top', 'bottom', 'left', 'right')})
    old_margins = page.get('margins_cm', {})
    if not isinstance(old_margins, dict):
        raise ValueError('page.margins_cm must be an object')
    for side in ('top', 'bottom', 'left', 'right'):
        alias = 'margin_' + side + '_mm'
        if alias in page:
            if side in old_margins and old_margins[side] != page[alias] / 10:
                raise ValueError('Conflicting centimetre and millimetre page margins')
            margins[side] = page[alias] / 10
    margins.update(old_margins)
    if margins:
        result['margins_cm'] = margins
    for key in PAGE_MM - {'width_mm', 'height_mm'}:
        result.pop(key, None)
    return result


def normalize_spec(spec):
    """Resolve preset layers without changing caller input or legacy defaults."""
    if not isinstance(spec, dict):
        raise ValueError('spec must be an object')
    result = deepcopy(spec)
    preset = profile_defaults(result.pop('profile')) if 'profile' in result else {}
    if 'theme' not in result and 'theme' in preset:
        result['theme'] = preset['theme']
    styles = result.get('styles', {})
    if not isinstance(styles, dict) or styles.keys() - STYLE_NAMES:
        raise ValueError('styles contains unsupported style names')
    resolved = deepcopy(preset.get('styles', {}))
    for name, value in styles.items():
        validate_format(value, 'styles.' + name)
        resolved[name] = merge_format(resolved.get(name, {}), value)
    if resolved:
        result['styles'] = resolved
    preset_page = normalize_page(preset.get('page', {}))
    result['page'] = normalize_page(result.get('page', {}), preset_page)
    if 'table_format' in preset:
        given = result.get('table_format', {})
        if not isinstance(given, dict):
            raise ValueError('table_format must be an object')
        result['table_format'] = dict(preset['table_format'], **given)
    if isinstance(result.get('blocks'), list):
        result['blocks'] = [normalize_page(block) if isinstance(block, dict) and block.get('type') == 'section_break' else block
                            for block in result['blocks']]
    return result


def apply_format(paragraph, formatting):
    """Apply already validated formatting to this paragraph and its text runs."""
    para_format = paragraph.paragraph_format
    if 'align' in formatting:
        paragraph.alignment = ALIGNMENTS[formatting['align']]
    for flag in FLAGS - {'bold'}:
        if flag in formatting:
            setattr(para_format, flag, formatting[flag])
    for key, attr in (('space_before_pt', 'space_before'), ('space_after_pt', 'space_after'),
                      ('left_indent_pt', 'left_indent'), ('right_indent_pt', 'right_indent')):
        if key in formatting:
            setattr(para_format, attr, Pt(formatting[key]))
    if 'line_spacing_pt' in formatting:
        para_format.line_spacing = Pt(formatting['line_spacing_pt'])
    elif 'line_spacing_multiple' in formatting:
        para_format.line_spacing = formatting['line_spacing_multiple']
    indentation = formatting.keys() & {'first_line_chars', 'first_line_indent_pt', 'hanging_indent_pt'}
    if indentation:
        props = paragraph._p.get_or_add_pPr()  # pylint: disable=protected-access
        ind = props.get_or_add_ind()
        for attribute in ('firstLine', 'firstLineChars', 'hanging', 'hangingChars'):
            ind.attrib.pop(qn('w:' + attribute), None)
        if 'first_line_chars' in formatting:
            ind.set(qn('w:firstLineChars'), str(round(formatting['first_line_chars'] * 100)))
        else:
            ind.set(qn('w:firstLineChars'), '0')
            ind.set(qn('w:hangingChars'), '0')
            para_format.first_line_indent = Pt(formatting.get('first_line_indent_pt', -formatting.get('hanging_indent_pt', 0)))
    for run in paragraph.runs:
        apply_run_overrides(run, formatting)


ROLE_STYLES = {'body': 'Body Text', 'title': 'Title', 'subtitle': 'Subtitle',
               **{f'heading{level}': f'Heading {level}' for level in range(1, 10)},
               'table_body': 'Table Body', 'table_header': 'Table Header'}


def install_role_styles(document, theme, styles):
    """Store theme/profile/role formatting in real paragraph styles based on Normal.

    A detached paragraph adapts the shared formatting helpers, avoiding a second
    formatting implementation or an extra document paragraph. Role-specific
    defaults remain explicit, as in docx; unspecified properties inherit Normal.
    """
    for role, name in ROLE_STYLES.items():
        style = document.styles[name] if name in document.styles else document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = document.styles['Normal']
        paragraph = Paragraph(OxmlElement('w:p'), document._body)  # pylint: disable=protected-access
        kind = 'heading' if role.startswith('heading') else role
        level = int(role[-1]) if kind == 'heading' else None
        apply_paragraph_defaults(paragraph, theme, kind)
        run = paragraph.add_run()
        apply_run_format(run, theme, kind, level)
        if kind in {'heading', 'title', 'subtitle'}:
            apply_run_overrides(run, {'color': '1A365D' if theme == 'business' else '000000'})
        if role == 'table_header':
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run.bold = True
        apply_format(paragraph, styles.get(role, {}))
        element = style.element
        for tag, generated in (('pPr', paragraph._p.pPr), ('rPr', run._r.rPr)):  # pylint: disable=protected-access
            old = element.find(qn('w:' + tag))
            replacement = deepcopy(old) if old is not None else OxmlElement('w:' + tag)
            # Keep unmanaged native properties in their original order. Generated
            # managed properties use python-docx's schema-aware insertion helpers.
            if generated is not None:
                for child in generated:
                    previous = replacement.find(child.tag)
                    if previous is not None:
                        replacement.remove(previous)
                    local_name = child.tag.rsplit('}', 1)[-1]
                    insert = getattr(replacement, '_insert_' + local_name)
                    insert(deepcopy(child))
            if old is not None:
                element.remove(old)
            element.append(replacement)
        if level is not None:
            element.get_or_add_pPr().get_or_add_outlineLvl().val = level - 1


def use_role_style(paragraph, role):
    """Remove generator defaults, retaining structural tabs/numbering/bookmarks."""
    props = paragraph._p.pPr  # pylint: disable=protected-access
    if props is not None:
        for child in list(props):
            if child.tag in {qn('w:' + tag) for tag in
                             ('spacing', 'ind', 'jc', 'keepNext', 'keepLines', 'widowControl', 'pageBreakBefore')}:
                props.remove(child)
    for run in paragraph.runs:
        props = run._r.rPr  # pylint: disable=protected-access
        if props is not None:
            for child in list(props):
                if child.tag in {qn('w:' + tag) for tag in ('rFonts', 'sz', 'szCs', 'b', 'color')}:
                    props.remove(child)
    paragraph.style = ROLE_STYLES[role]


def apply_text_format(paragraph, block, _styles):
    """Reference the native role and apply only explicit local overrides."""
    kind = block['type']
    name = 'body' if kind == 'paragraph' else f"heading{block['level']}" if kind == 'heading' else kind
    use_role_style(paragraph, name)
    formatting = dict(block.get('format', {}))
    formatting.update({key: block[key] for key in FLAGS | {'align'} if key in block})
    apply_format(paragraph, formatting)
    if 'runs' in block:
        # Field helpers add more than one run, so use text items' original positions.
        offset = 0
        for item in block['runs']:
            if 'field' in item:
                offset += 5 if 'display' in item else 4
            else:
                if 'tab' not in item:
                    apply_run_overrides(paragraph.runs[offset], item)
                offset += 1


def apply_table_styles(table, block, _styles):
    """Apply table typography once per physical cell, keeping merged content."""
    headers = block.get('header_rows', 1 if block.get('header', False) else 0)
    seen = set()
    for row_index, row in enumerate(table.rows):
        for cell in row.cells:
            if cell._tc in seen:  # pylint: disable=protected-access
                continue
            seen.add(cell._tc)  # pylint: disable=protected-access
            style_name = 'table_header' if row_index < headers else 'table_body'
            local_name = 'header_format' if row_index < headers else 'body_format'
            formatting = block.get(local_name, {})
            for paragraph in cell.paragraphs:
                use_role_style(paragraph, style_name)
                apply_format(paragraph, formatting)
