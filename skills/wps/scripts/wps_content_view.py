"""Content-focused CLI projection; original read/read_bytes machine schema stays intact."""
from collections import Counter
from wps_chart_presentation import display_number


def configure(args, parser):
    """Explicit narrow/maximum modes retain the historical detailed interface."""
    args.profile = args.profile or ('content' if args.reading_mode == 'content' and not args.binary_paragraph_sources else 'full')
    if args.profile == 'content' and (args.reading_mode == 'standard' or args.include_image_bytes or args.binary_paragraph_sources):
        parser.error('--profile content requires content/maximum without image bytes; use --profile full for advanced fields')


def _present(value):
    return value is not None and value != [] and value != {} and value != ''


def _pick(value, keys):
    return {key: value[key] for key in keys if key in value and _present(value[key])}



def _owned_tables(story):
    tables = story.get('tables', [])
    if 'body_blocks' not in story:
        return tables
    owned = {block['table_id'] for block in story['body_blocks'] if block['type'] == 'table'}
    # The producer appends parents before descendants; related-story tables are
    # not included merely because they share a source package or text.
    for table in tables:
        if table.get('parent_table_id') in owned:
            owned.add(table['id'])
    return [table for table in tables if table.get('id') in owned]


def _body(story):
    tables = _owned_tables(story)
    if not tables or 'body_blocks' not in story:
        return None
    numbers = {table['id']: index + 1 for index, table in enumerate(tables)}
    return [{'text': block['text']} if block['type'] == 'paragraph' else {'table': numbers[block['table_id']]}
            for block in story['body_blocks']]

def _tables(story):
    output = []
    tables = _owned_tables(story)
    if not tables and story.get('binary_table_structure', {}).get('rows'):
        structure = story['binary_table_structure']
        rows = structure['rows']
        tables = [{**group, 'rows': rows[group['first_row_index']:group['first_row_index'] + group['row_count']]}
                  for group in structure.get('tables', [])]
        if not tables:
            tables = [{'rows': rows, 'status': 'table_grouping_unknown'}]
    ids = {table.get('id'): index + 1 for index, table in enumerate(tables) if table.get('id')}
    for index, table in enumerate(tables):
        item = {'table': index + 1, 'rows': []}
        if table.get('parent_table_id') in ids:
            item['parent_table'] = ids[table['parent_table_id']]
        if table.get('status') not in (None, 'ok'):
            item['status'] = table['status']
        item.update(_pick(table, ('grouping_status', 'merge_status')))
        for kind in ('horizontal_merges', 'vertical_merges'):
            if table.get(kind):
                item[kind] = [_pick(merge, ('row_index', 'row_count', 'first_cell', 'cell_count', 'cells', 'status'))
                              for merge in table[kind]]
        for row in table.get('rows', []):
            cells = row.get('cells', []) if isinstance(row, dict) else row
            output_row = []
            for cell in cells:
                value = _pick(cell, ('text', 'column', 'grid_span', 'v_merge', 'horizontal_merge', 'vertical_merge'))
                if not value:
                    value = {'text': ''}
                nested = [ids[key] for key in cell.get('nested_table_ids', []) if key in ids]
                if nested:
                    value.pop('text', None)
                    value['content'] = [{'text': cell['paragraphs'][block['paragraph_index']]['text']}
                                        if block['kind'] == 'paragraph' else {'table': ids[block['table_id']]}
                                        for block in cell.get('blocks', [])]
                output_row.append(value)
            item['rows'].append(output_row)
        output.append(item)
    return output



def _nested_rows(rows):
    """Keep verified nested cell ownership without exporting binary CP/debug data."""
    output = []
    for row in rows:
        cells = []
        for cell in row.get('cells', []):
            item = {'text': cell.get('text', '')}
            children = []
            for run in cell.get('nested_tables', []):
                fragments = run.get('row_fragments', [])
                for group in run.get('tables', []):
                    start = group['first_row_index']
                    children.append({'grouping': group['grouping_status'], 'merges': 'unknown',
                                     'rows': _nested_rows(fragments[start:start + group['row_count']])})
            if children:
                item['nested_tables'] = children
            cells.append(item)
        output.append(cells)
    return output


def _nested_content(story):
    evidence = story.get('binary_table_structure', {}).get('nested_row_evidence', {})
    rows = _nested_rows(evidence.get('rows', []))
    if not rows:
        return None
    return {'scope': 'verified_row_fragments_not_complete_outer_tables', 'rows': rows}

def _channel(channel):
    if not channel:
        return None
    output = []
    for cache in channel.get('caches', []):
        item = {'points': []}
        if cache.get('format_code'):
            item['format'] = cache['format_code']
        for point in cache.get('points', []):
            value = {'index': point['index'], 'value': point['raw_value']}
            displayed, _ = display_number(point['raw_value'], cache.get('format_code'))
            if displayed != point['raw_value']:
                value['display'] = displayed
            item['points'].append(value)
        if cache.get('missing_index_ranges'):
            item['missing_ranges'] = cache['missing_index_ranges']
        if not cache.get('coverage_complete'):
            item['coverage'] = 'incomplete_or_unknown'
        output.append(item)
    text = channel.get('rich_text') or channel.get('literal_values')
    result = {}
    if text:
        result['text'] = ''.join(value or '' for value in text)
    if output:
        result['saved_data'] = output
    if not result:
        result['status'] = 'missing_or_unsupported'
    return result


def _charts(result):
    output = []
    for chart in result.get('ooxml_charts', {}).get('charts', []):
        item = {'chart': len(output) + 1, 'source': 'saved_chart_values_not_refreshed', 'series': []}
        if chart.get('title'):
            item['title'] = _channel(chart['title'])
        for series in chart.get('series', []):
            item['series'].append({name: _channel(value) for name, value in series.items() if value is not None})
        if chart.get('limitations'):
            item['notes'] = chart['limitations']
        output.append(item)
    for chart in result.get('binary_charts', {}).get('charts', []):
        item = {'chart': len(output) + 1, 'source': 'saved_chart_values_not_refreshed'}
        item.update(_pick(chart, ('title', 'status', 'reason')))
        series_output = []
        for series in chart.get('presentation', {}).get('series', []):
            value = _pick(series, ('series_name', 'chart_type', 'missing_fields', 'missing_value_ranges', 'missing_category_ranges'))
            value['rows'] = [_pick(row, ('index', 'category', 'raw_value', 'display_value', 'missing_fields', 'value_source'))
                             for row in series.get('rows', [])]
            series_output.append(value)
        if series_output:
            item['series'] = series_output
        if chart.get('issues'):
            item['notes'] = chart['issues']
        output.append(item)
    return output


def _notes(result):
    counts = Counter()
    for issue in result.get('diagnostics', []) + result.get('issues', []):
        counts[issue.get('code', 'unclassified_reading_gap')] += 1
    notes = [{'code': code, 'occurrences': count} for code, count in sorted(counts.items())]
    if result.get('revision_content', {}).get('content'):
        notes.append({'code': 'stored_revision_content_separate', 'detail': 'Deleted/stored alternatives are not mixed into current text; use --profile full when needed'})
    if result.get('omitted_regions'):
        notes.append({'code': 'omitted_regions', 'count': len(result['omitted_regions'])})
    return notes


def project_content(result):
    """Keep useful text, table semantics, chart values and concise coverage gaps."""
    if result.get('status') not in ('ok', 'partial'):
        failure = _pick(result, ('status', 'message', 'partial', 'content_view'))
        if 'message' not in failure and result.get('diagnostics'):
            failure['message'] = result['diagnostics'][0].get('message', 'Read failed; use --profile full for details')
        return failure
    output = {'schema_version': 'wps_content_v1', 'profile': 'content', 'status': result['status'],
              'reading_mode': result.get('reading_mode', 'content'), 'partial': result.get('partial', False)}
    output.update(_pick(result, ('format', 'content_view', 'current_view_available', 'text')))
    tables = _tables(result)
    if tables:
        output['tables'] = tables
    body = _body(result)
    if body is not None:
        output['body'] = body
        output.pop('text', None)
    nested = _nested_content(result)
    if nested:
        output['nested_content'] = nested
    related = []
    for index, story in enumerate(result.get('related_stories', [])):
        item = {'region': index + 1, 'type': story.get('type', 'related'), 'text': story.get('text')}
        tables = _tables(story)
        if tables:
            item['tables'] = tables
        body = _body(story)
        if body is not None:
            item['body'] = body
            item.pop('text', None)
        nested = _nested_content(story)
        if nested:
            item['nested_content'] = nested
        if story.get('source', {}).get('alternate_content'):
            item['view'] = 'unresolved_stored_alternative'
        related.append(item)
    if related:
        output['related_content'] = related
    if result.get('format') == 'rtf' and result.get('tables') and result.get('blocks'):
        output['body'] = [{'text': result['paragraphs'][block['index']]['text']} if block['type'] == 'paragraph'
                          else {'table': block['index'] + 1} for block in result['blocks']]
        output.pop('text', None)
    objects = []
    for item in result.get('drawing_objects', {}).get('objects', []):
        text = _pick(item.get('officeart', {}), ('wordart_text', 'description'))
        if text:
            objects.append(text)
    if objects:
        output['object_text'] = objects
    charts = _charts(result)
    if charts:
        output['charts'] = charts
    notes = _notes(result)
    if notes:
        output['notes'] = notes
    output['scope'] = 'Supported stored content; layout, OCR, field freshness and visual series are not verified'
    return output
