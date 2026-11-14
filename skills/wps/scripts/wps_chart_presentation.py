"""Bounded, non-executing chart value presentation; never resolves references."""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from wps_binary_chart_cache import ChartCacheLimit

_SIMPLE_FORMAT = re.compile(r'0(?:\.(0{1,8}))?(%)?\Z')


def display_number(raw, format_code):
    """Return display text and exact supported-format status, retaining raw input."""
    if raw is None:
        return None, 'missing'
    if not format_code or format_code == 'General':
        return raw, 'raw'
    match = _SIMPLE_FORMAT.fullmatch(format_code)
    if match is None:
        return raw, 'format_not_rendered'
    try:
        number = Decimal(raw)
        if not number.is_finite() or abs(number.adjusted()) > 100:
            return raw, 'format_not_rendered'
        digits = len(match.group(1) or '')
        with localcontext() as context:
            context.prec = 128
            if match.group(2):
                number *= 100
            number = number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
            rendered = format(number, f'.{digits}f')
        return rendered + ('%' if match.group(2) else ''), 'simple_number_format'
    except (InvalidOperation, ValueError, TypeError):
        return raw, 'format_not_rendered'


def _point_map(channel):
    # Do not turn ambiguous repeated indices into a plausible-looking row.
    result, duplicates = {}, set()
    for point in channel.get('points', []):
        index = point['index']
        if index in result:
            duplicates.add(index)
        result[index] = point['value']
    for index in duplicates:
        result[index] = None
    return result


def add_presentation(chart, budget):
    """Add a derived view; original channels, precision and partial stay intact.

    Rows align by explicit point index, not zip/list order. A declared missing
    range is retained rather than expanded into potentially billions of rows.
    Output duplication is charged to the document's shared chart text budget.
    """
    views = []
    for series in chart.get('series', []):
        categories, values = series['categories'], series['values']
        cat_map, val_map = _point_map(categories), _point_map(values)
        value_points = {point['index']: point for point in values.get('points', [])}
        indices = sorted(cat_map.keys() | val_map.keys())
        rows = []
        for index in indices:
            category, raw = cat_map.get(index), val_map.get(index)
            displayed, display_status = display_number(raw, values.get('format_code'))
            row = {'index': index, 'category': category, 'raw_value': raw,
                   'display_value': displayed, 'display_status': display_status,
                   'value_source': value_points.get(index, {}).get(
                       'source', {}).get('kind', 'stored_chart_value' if raw is not None else 'unavailable'),
                   'missing_fields': [key for key, value in
                                      (('category', category), ('value', raw)) if value is None]}
            for value in (category, raw, displayed):
                if value is not None:
                    budget.charge_text(len(value.encode('utf-8')))
            rows.append(row)
        if len(rows) > budget.MAX_POINTS:
            raise ChartCacheLimit('Chart presentation row limit exceeded')
        missing = []
        if categories.get('status') != 'complete':
            missing.append('categories')
        if values.get('status') != 'complete':
            missing.append('values')
        effective_missing = [field for field, channel in
                             (('categories', categories), ('values', values))
                             if channel.get('status') != 'complete' and
                             channel.get('workbook_enrichment', {}).get('status') != 'stored_values']
        views.append({'series_index': series.get('index'), 'series_order': series.get('order'),
                      'series_name': series.get('name'), 'chart_type': series['chart_type'],
                      'value_source': values.get('provenance',
                                                 'stored_inline_values' if values.get('source') == 'numLit'
                                                 else 'stored_chart_cache'),
                      'value_status': values['status'], 'cache_missing_fields': missing,
                      'missing_fields': effective_missing,
                      'workbook_enrichment': values.get('workbook_enrichment'),
                      'effective_missing_value_indices': values.get('effective_missing_indices'),
                      'rows': rows, 'missing_value_ranges': values.get('missing_ranges'),
                      'missing_category_ranges': categories.get('missing_ranges'),
                      'values_refreshed': False, 'visual_series_verified': False})
    chart['presentation'] = {'schema_version': 'wps_chart_presentation_v1',
                             'source_policy': 'saved_values_only_no_refresh_or_inference',
                             'series': views,
                             'external_source_followed': False,
                             'workbook_status': chart.get('workbook_status', 'unknown')}
