"""ET overview fallback composed over the shared, unchanged paginated runtime."""
from wps_access import ParseError
from wps_read_view import project as shared_project


def project(result, data, options, **view_options):
    """Keep full diagnostics normally; explicitly summarize only oversized overviews."""
    try:
        return shared_project(result, data, options, **view_options)
    except ParseError as exc:
        if (not view_options.get('summary') or exc.status != 'output_view_error'
                or not str(exc).startswith('Summary/diagnostics exceed output budget')):
            raise
    source = dict(result)
    overview = {'details_omitted': True, 'scope': 'overview_only_not_full_diagnostics',
                'source_issues_truncated': source.get('issues_truncated'),
                'retrieve_details': 'full read or a larger output budget', 'fields': {}}
    for field in ('issues', 'diagnostics'):
        entries = source.pop(field, [])
        counts = {}
        for entry in entries:
            code = entry.get('code', 'reader_diagnostic') if isinstance(entry, dict) else 'unstructured_diagnostic'
            code = str(code)
            counts[code] = counts.get(code, 0) + 1
        sample = list(counts.items())[:20]
        overview['fields'][field] = {
            'source_entries': len(entries), 'distinct_codes': len(counts),
            'code_samples': [{'code': code[:120], 'code_truncated': len(code) > 120, 'entries': count} for code, count in sample],
            'codes_omitted': max(0, len(counts) - len(sample)),
            'count_basis': 'diagnostic entries, not affected cells or aggregated occurrences'}
    assessment = source.get('reading_assessment')
    if isinstance(assessment, dict):
        keep = ('schema_version', 'outcome', 'completeness', 'scope', 'full_format_verified', 'cell_values')
        source['reading_assessment'] = {key: assessment[key] for key in keep if key in assessment}
        overview['assessment_fields_omitted'] = [key for key in assessment if key not in keep]
        overview['source_assessment_events_truncated'] = assessment.get('events_truncated')
    # Keep this explicit omission notice inside the shared size checks. The
    # final envelope is shorter when the redundant issue wrapper is removed.
    source['issues'] = [{'code': 'summary_diagnostics_projected', 'diagnostic_summary': overview}]
    view = shared_project(source, data, options, **view_options)
    view['diagnostic_summary'] = view.pop('issues')[0]['diagnostic_summary']
    return view
