"""Validated OOXML section layouts; Python standard library only.

A section_break block begins a *new* section. The preceding section's
sectPr belongs to the break paragraph, the final sectPr to the body.
"""
from __future__ import annotations
import math

PAGE_KEYS = frozenset({'width_mm', 'height_mm', 'orientation', 'margin_mm',
                       'margin_top_mm', 'margin_bottom_mm', 'margin_left_mm', 'margin_right_mm'})
SECTION_KEYS = frozenset({'header', 'footer', 'headers', 'footers', 'page_numbers',
                         'different_first_page', 'first_page_numbers',
                         'page_number_start', 'page_number_format', 'page'})
BREAK_TYPES = {'next_page': 'nextPage', 'continuous': 'continuous',
               'even_page': 'evenPage', 'odd_page': 'oddPage'}
PAGE_NUMBER_FORMATS = frozenset({'decimal', 'upperRoman', 'lowerRoman', 'upperLetter', 'lowerLetter'})
VARIANTS = frozenset({'default', 'first', 'even'})


def _decimal(value, label, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be a finite number between {minimum} and {maximum}')
    return float(value)


def _page(spec, previous, label, twips):
    if not isinstance(spec, dict) or set(spec) - PAGE_KEYS:
        raise ValueError(f'{label} must be a page object containing only supported fields')
    page = (previous or {'width_mm': 210., 'height_mm': 297.,
                         'margin_top_mm': 25.4, 'margin_bottom_mm': 25.4,
                         'margin_left_mm': 31.8, 'margin_right_mm': 31.8}).copy()
    if 'width_mm' in spec:
        page['width_mm'] = _decimal(spec['width_mm'], label+'.width_mm', 1, 558.8)
    if 'height_mm' in spec:
        page['height_mm'] = _decimal(spec['height_mm'], label+'.height_mm', 1, 558.8)
    if 'margin_mm' in spec:
        base = _decimal(spec['margin_mm'], label+'.margin_mm', 0, 250)
        for side in ('top', 'bottom', 'left', 'right'):
            page[f'margin_{side}_mm'] = base
    for side in ('top', 'bottom', 'left', 'right'):
        key = f'margin_{side}_mm'
        if key in spec:
            page[key] = _decimal(spec[key], label+'.'+key, 0, 250)
    orientation = spec.get('orientation')
    if orientation is not None:
        if orientation not in {'portrait', 'landscape'} or not isinstance(orientation, str):
            raise ValueError(f'{label}.orientation must be portrait or landscape')
        if (orientation == 'landscape') != (page['width_mm'] > page['height_mm']):
            page['width_mm'], page['height_mm'] = page['height_mm'], page['width_mm']
    sized = {'width': twips(page['width_mm']), 'height': twips(page['height_mm'])}
    for side in ('top', 'bottom', 'left', 'right'):
        sized[side] = twips(page[f'margin_{side}_mm'])
    if sized['width'] - sized['left'] - sized['right'] <= 0 or sized['height'] - sized['top'] - sized['bottom'] <= 0:
        raise ValueError(f'{label} margins must leave positive width and height for content after rounding')
    return page, sized


def parse_section(source, previous, label, *, twips, text_validator, odd_even_headers=False):
    """Validate a section's changed attributes, inheriting layout and running content.

    page_number_start is intentionally not inherited: absent means continuation.
    """
    if not isinstance(source, dict) or set(source) - SECTION_KEYS:
        raise ValueError(f'{label} contains unsupported section properties')
    prev = previous or {}
    result = {k: v.copy() if isinstance(v, dict) else v for k, v in prev.items()}
    result['page_mm'], result['page_twips'] = _page(source.get('page', {}), prev.get('page_mm'), label+'.page', twips)
    result['available_width_twips'] = result['page_twips']['width'] - result['page_twips']['left'] - result['page_twips']['right']
    result['available_height_twips'] = result['page_twips']['height'] - result['page_twips']['top'] - result['page_twips']['bottom']
    result['headers'] = dict(prev.get('headers', {'default': None, 'first': None, 'even': None}))
    result['footers'] = dict(prev.get('footers', {'default': None, 'first': None, 'even': None}))
    for name in ('header', 'footer'):
        if name in source:
            result[name+'s']['default'] = text_validator(source[name], f'{label}.{name}')
    for kind in ('headers', 'footers'):
        if kind not in source:
            continue
        choices = source[kind]
        if not isinstance(choices, dict) or set(choices) - VARIANTS:
            raise ValueError(f'{label}.{kind} must contain only default, first and even variants')
        for variant, value in choices.items():
            result[kind][variant] = None if value is None else text_validator(value, f'{label}.{kind}.{variant}')
    for field in ('page_numbers', 'different_first_page', 'first_page_numbers'):
        result[field] = prev.get(field, False)
        if field in source:
            if type(source[field]) is not bool:
                raise ValueError(f'{label}.{field} must be a boolean')
            result[field] = source[field]
    result['page_number_start'] = None
    if 'page_number_start' in source:
        value = source['page_number_start']
        if type(value) is not int or not 1 <= value <= 32767:
            raise ValueError(f'{label}.page_number_start must be an integer from 1 to 32767')
        result['page_number_start'] = value
    result['page_number_format'] = prev.get('page_number_format', 'decimal')
    if 'page_number_format' in source:
        value = source['page_number_format']
        if type(value) is not str or value not in PAGE_NUMBER_FORMATS:
            raise ValueError(f'{label}.page_number_format must be one of {sorted(PAGE_NUMBER_FORMATS)}')
        result['page_number_format'] = value
    # Explicitly disabling the title-page variant drops inherited first-page
    # overrides, rather than making the caller clear each field manually.
    if source.get('different_first_page') is False:
        for name in ('headers', 'footers'):
            if 'first' not in source.get(name, {}):
                result[name]['first'] = None
        if 'first_page_numbers' not in source:
            result['first_page_numbers'] = False
    if not result['different_first_page'] and result['first_page_numbers']:
        raise ValueError(f'{label}.first_page_numbers requires different_first_page')
    if not result['different_first_page'] and result['headers'].get('first') is not None:
        raise ValueError(f'{label}.headers.first requires different_first_page')
    if not result['different_first_page'] and result['footers'].get('first') is not None:
        raise ValueError(f'{label}.footers.first requires different_first_page')
    if not odd_even_headers and (result['headers'].get('even') is not None or result['footers'].get('even') is not None):
        raise ValueError(f'{label}.headers/footers.even requires odd_even_headers')
    return result
