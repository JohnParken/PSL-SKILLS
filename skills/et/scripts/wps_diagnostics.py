"""Additive extraction-scope summary; never copy document text into diagnostics."""

SUCCESS = {'ok', 'partial'}
LOCATION_KEYS = ('stream', 'part', 'offset', 'record_offset', 'payload_offset',
                 'cp', 'cp_start', 'cp_end', 'sheet', 'cell', 'path', 'location',
                 'stage')
MAX_DIAGNOSTIC_EVENTS = 1000
MAX_COVERAGE_LOCATIONS = 20
MAX_COVERAGE_LOCATION_KEYS = 10000
COVERAGE_LOCATION_KEYS = (
    'type', 'source', 'cp_units', 'status', 'table_index', 'marker', 'index', 'kind',
    'code', 'source_field', 'stream', 'part', 'offset', 'record_offset',
    'payload_offset', 'cp', 'cp_start', 'cp_end', 'sheet', 'cell', 'path',
    'location', 'stage', 'shape_index', 'spa_record_offset', 'plcf_offset',
)
SOURCE_LOCATION_KEYS = (
    'stream', 'part', 'offset', 'record_offset', 'payload_offset', 'cp',
    'cp_start', 'cp_end', 'sheet', 'cell', 'path', 'location', 'stage',
    'row_mark_cp', 'id', 'relationship_id', 'table_index', 'marker',
)


def _sequence(value):
    """Return an input list/tuple without treating strings as collections."""
    return value if isinstance(value, (list, tuple)) else ()


def _freeze(value):
    """Build a stable, hashable key from the small diagnostic values we retain."""
    if isinstance(value, dict):
        return tuple(sorted(((str(key), _freeze(item)) for key, item in value.items()),
                            key=lambda pair: pair[0]))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(_freeze(item) for item in value))
    try:
        hash(value)
    except TypeError:
        return (type(value).__name__, repr(value))
    return value


def _bounded_metadata(value, depth=0):
    """Copy only small scalar metadata used to identify an extraction location."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:256]
    if depth >= 2:
        return None
    if isinstance(value, dict):
        nested = {}
        for key in SOURCE_LOCATION_KEYS:
            if key in value:
                nested[key] = _bounded_metadata(value[key], depth + 1)
        return nested
    if isinstance(value, (list, tuple)):
        return [_bounded_metadata(item, depth + 1) for item in value[:20]]
    return None


def _coverage_location(item, additions=None):
    """Select recorded location metadata while excluding arbitrary prose fields."""
    location = {}
    if isinstance(item, dict):
        for key in COVERAGE_LOCATION_KEYS:
            if key in item:
                location[key] = _bounded_metadata(item[key])
    if additions:
        for key, value in additions.items():
            if key in COVERAGE_LOCATION_KEYS:
                location[key] = _bounded_metadata(value)
    return location


class _CoverageEntry:
    """Accumulate unique evidence locations with bounded retained output."""

    def __init__(self, category, state):
        self.category = category
        self.state = state
        self.evidence_count = 0
        self.locations = []
        self.locations_truncated = 0
        self._seen = set()

    def add(self, location, additions=None, evidence_count=1):
        location = _coverage_location(location, additions)
        key = _freeze(location)
        if key in self._seen:
            return
        if len(self._seen) < MAX_COVERAGE_LOCATION_KEYS:
            self._seen.add(key)
        self.evidence_count += max(1, evidence_count)
        if len(self.locations) < MAX_COVERAGE_LOCATIONS:
            self.locations.append(location)
        else:
            self.locations_truncated += 1

    def as_dict(self):
        return {
            'category': self.category,
            'state': self.state,
            'evidence_count': self.evidence_count,
            'locations': self.locations,
            'locations_truncated': self.locations_truncated,
        }


def _diagnostic_event(item, source_field):
    return {
        'code': item.get('code', 'reader_diagnostic'),
        'source_field': source_field,
        'location': {key: item[key] for key in LOCATION_KEYS if key in item},
    }


def _unread_state(code):
    if not isinstance(code, str):
        return None
    if code == 'drawn_shape_anchor_not_rendered':
        return 'not_decoded'
    tokens = set(code.lower().replace('-', '_').replace('.', '_').split('_'))
    if tokens.intersection({'omitted', 'unrequested', 'not_requested'}):
        return 'not_requested'
    if {'not', 'requested'}.issubset(tokens):
        return 'not_requested'
    if tokens.intersection({'unsupported', 'not_decoded', 'uninterpreted',
                             'unresolved', 'not_followed'}):
        return 'not_decoded'
    if {'not', 'decoded'}.issubset(tokens) or {'not', 'followed'}.issubset(tokens):
        return 'not_decoded'
    if 'unknown' in tokens:
        return 'unknown'
    return None


def _coverage(result, diagnostic_records):
    omitted_regions = _sequence(result.get('omitted_regions'))
    related_present = 'related_stories' in result
    related_stories = _sequence(result.get('related_stories'))

    related_state = ('not_requested' if omitted_regions else
                     'extracted' if related_present else 'unknown')
    related = _CoverageEntry('related_content', related_state)
    for index, region in enumerate(omitted_regions):
        additions = {'index': index}
        if isinstance(region, dict):
            for key in ('type', 'source', 'cp_units'):
                if key in region:
                    additions[key] = region[key]
        related.add(region if isinstance(region, dict) else {}, additions)
    for index, region in enumerate(related_stories):
        additions = {'index': index}
        if isinstance(region, dict):
            for key in ('type', 'source', 'status'):
                if key in region:
                    additions[key] = region[key]
        related.add(region if isinstance(region, dict) else {}, additions)

    binary_structures = []
    if 'binary_table_structure' in result:
        binary_structures.append((result.get('binary_table_structure'), 'main'))
    for region in related_stories:
        if isinstance(region, dict) and 'binary_table_structure' in region:
            binary_structures.append((region.get('binary_table_structure'),
                                      region.get('type', 'related')))
    binary_statuses = [structure.get('status') if isinstance(structure, dict) else None
                       for structure, _ in binary_structures]
    binary_state = ('unknown' if not binary_structures else
                    'extracted' if all(status == 'verified' for status in binary_statuses)
                    else 'not_decoded')
    binary = _CoverageEntry('binary_tables', binary_state)
    for structure, story_type in binary_structures:
        if not isinstance(structure, dict):
            structure = {}
        binary_status = structure.get('status')
        records = []
        for field in ('tables', 'rows', 'unresolved_markers'):
            for index, record in enumerate(_sequence(structure.get(field))):
                records.append((field, index, record))
        if records:
            for field, index, record in records:
                additions = {'status': binary_status or 'unknown', 'index': index,
                             'kind': field, 'type': story_type}
                if isinstance(record, dict):
                    additions.update({key: record[key] for key in (
                        'source', 'marker', 'table_index') if key in record})
                binary.add(record if isinstance(record, dict) else {}, additions)
        else:
            binary.add(structure, {'status': binary_status or 'unknown',
                                   'type': story_type})

    objects = _CoverageEntry('objects', 'unknown')
    unread = {
        state: _CoverageEntry('unread_content', state)
        for state in ('not_requested', 'not_decoded', 'unknown')
    }
    for record in diagnostic_records:
        item = record['item']
        source_field = record['source_field']
        code = item.get('code', 'reader_diagnostic')
        code_text = code if isinstance(code, str) else ''
        if code_text in ('picture_or_data_marker_not_decoded',
                         'drawn_shape_anchor_not_rendered'):
            objects.state = 'not_decoded'
            locations = _sequence(item.get('locations'))
            if locations:
                for index, marker_location in enumerate(locations):
                    objects.add(marker_location, {
                        'code': code_text, 'source_field': source_field,
                        'index': index,
                    })
            else:
                try:
                    marker_count = int(item.get('count', 1))
                except (TypeError, ValueError):
                    marker_count = 1
                objects.add(item, {
                    'code': code_text, 'source_field': source_field,
                }, evidence_count=marker_count)
        state = _unread_state(code)
        if state:
            unread[state].add(item, {
                'code': code_text, 'source_field': source_field,
            })

    coverage = [related.as_dict(), binary.as_dict(), objects.as_dict()]
    coverage.extend(entry.as_dict() for entry in unread.values()
                    if entry.evidence_count)
    return coverage


def summarize(result):
    status = result.get('status', 'invalid_or_unsupported')
    events = []
    event_keys = set()
    events_truncated = 0
    diagnostic_records = []

    def add_diagnostics(items, source_field):
        nonlocal events_truncated
        for item in _sequence(items):
            if not isinstance(item, dict):
                continue
            record = {'item': item, 'source_field': source_field}
            diagnostic_records.append(record)
            event = _diagnostic_event(item, source_field)
            key = _freeze(event)
            if key in event_keys:
                continue
            if len(events) >= MAX_DIAGNOSTIC_EVENTS:
                events_truncated += 1
                continue
            event_keys.add(key)
            events.append(event)

    for field in ('issues', 'diagnostics', 'container_issues'):
        add_diagnostics(result.get(field, []), field)
    if status not in SUCCESS:
        failure_event = {'code': status, 'source_field': 'failure',
                         'location': result.get('location', {})}
        failure_key = _freeze(failure_event)
        if failure_key not in event_keys:
            if len(events) >= MAX_DIAGNOSTIC_EVENTS:
                events_truncated += 1
            else:
                event_keys.add(failure_key)
                events.append(failure_event)

    for index, region in enumerate(_sequence(result.get('related_stories'))):
        if isinstance(region, dict):
            for field in ('issues', 'diagnostics'):
                add_diagnostics(region.get(field, []),
                                'related_stories[{}].{}'.format(index, field))
    for index, sheet in enumerate(_sequence(result.get('sheets'))):
        if isinstance(sheet, dict):
            for field in ('issues', 'diagnostics'):
                add_diagnostics(sheet.get(field, []),
                                'sheets[{}].{}'.format(index, field))

    regions = []
    for region in _sequence(result.get('related_stories')):
        if not isinstance(region, dict):
            continue
        regions.append({'type': region.get('type'), 'status': region.get('status'),
                        'source': region.get('source', {}),
                        'empty': region.get('empty'),
                        'anchor_status': region.get('anchor_status', 'unknown')})
    visibility = result.get('visibility', {})
    if not isinstance(visibility, dict):
        visibility = {}
    counts = {}
    by_region = {}
    runs = _sequence(visibility.get('runs'))
    for run in runs:
        if not isinstance(run, dict):
            continue
        action = run.get('action', 'unknown')
        state = run.get('state', 'unknown')
        source = run.get('source', {})
        part = source.get('part', 'unknown') if isinstance(source, dict) else 'unknown'
        key = str(action) + ':' + str(state)
        counts[key] = counts.get(key, 0) + 1
        region = by_region.setdefault(part, {'recorded_runs': 0, 'decisions': {}})
        region['recorded_runs'] += 1
        region['decisions'][key] = region['decisions'].get(key, 0) + 1
    result['reading_assessment'] = {
        'schema_version': 'wps_reading_assessment_v1',
        'outcome': status,
        'completeness': 'within_declared_scope' if status == 'ok' else 'partial' if status == 'partial' else 'no_content_delivered',
        'scope': result.get('scope', 'stored_cells_subset' if 'sheets' in result else 'unspecified'),
        'full_format_verified': False,
        'events': events,
        'events_truncated': events_truncated,
        'regions': regions,
        'visibility': {'policy': visibility.get('policy', 'not_available'),
            'recorded_run_decisions': counts, 'by_part': by_region,
            'positions': 'See original visibility.runs source paths; not duplicated',
            'coverage': 'recorded decisions only; unrecorded runs are not inferred'},
        'coverage': _coverage(result, diagnostic_records),
        'position_policy': 'Only recorded positions are reported; absent positions are unknown',
        'text_policy': 'No omitted or excluded document text is copied into this summary'}
    if 'cell_value_coverage' in result:
        result['reading_assessment']['cell_values'] = {
            'coverage': result['cell_value_coverage'],
            'omissions_affect_cell_values': result.get('omissions_affect_cell_values'),
            'formula_cache_policy': 'Present caches unverified; missing caches never inferred',
            'scope': 'Supported BIFF cells in declared worksheets only; not private-stream or full-format equivalence'}
    return result
