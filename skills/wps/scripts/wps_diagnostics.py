"""Additive extraction-scope summary; never copy document text into diagnostics."""
SUCCESS = {'ok', 'partial'}
LOCATION_KEYS = ('stream','part','offset','record_offset','payload_offset','cp','cp_start','cp_end','sheet','cell','path','location','stage')

def summarize(result):
    status = result.get('status', 'invalid_or_unsupported')
    events = []
    for field in ('issues','diagnostics','container_issues'):
        for item in result.get(field, []):
            if not isinstance(item, dict):
                continue
            event = {'code':item.get('code','reader_diagnostic'),
                     'source_field':field,
                     'location':{key:item[key] for key in LOCATION_KEYS if key in item}}
            if event not in events:
                events.append(event)
    if status not in SUCCESS:
        events.append({'code':status,'source_field':'failure',
                       'location':result.get('location',{})})
    regions = []
    for region in result.get('related_stories', []):
        regions.append({'type':region.get('type'),'status':region.get('status'),
                        'source':region.get('source',{}),
                        'empty':region.get('empty'),
                        'anchor_status':region.get('anchor_status','unknown')})
    visibility = result.get('visibility', {})
    counts = {}
    by_region = {}
    runs = visibility.get('runs', [])
    for run in runs:
        action = run.get('action', 'unknown')
        state = run.get('state', 'unknown')
        part = run.get('source', {}).get('part', 'unknown')
        key = action + ':' + state
        counts[key] = counts.get(key, 0) + 1
        region = by_region.setdefault(part, {'recorded_runs': 0, 'decisions': {}})
        region['recorded_runs'] += 1
        region['decisions'][key] = region['decisions'].get(key, 0) + 1
    result['reading_assessment'] = {
        'schema_version':'wps_reading_assessment_v1',
        'outcome':status,
        'completeness':'within_declared_scope' if status=='ok' else 'partial' if status=='partial' else 'no_content_delivered',
        'scope':result.get('scope','stored_cells_subset' if 'sheets' in result else 'unspecified'),
        'full_format_verified':False,
        'events':events,'regions':regions,
        'visibility': {'policy': visibility.get('policy', 'not_available'),
            'recorded_run_decisions': counts, 'by_part': by_region,
            'positions': 'See original visibility.runs source paths; not duplicated',
            'coverage': 'recorded decisions only; unrecorded runs are not inferred'},
        'position_policy':'Only recorded positions are reported; absent positions are unknown',
        'text_policy':'No omitted or excluded document text is copied into this summary'}
    return result
