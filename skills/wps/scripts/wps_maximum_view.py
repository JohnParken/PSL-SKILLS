"""Keep current and stored revision views separate in maximum reading results."""
from wps_access import ParseError

CONTENT_FIELDS = ('text', 'paragraphs', 'tables', 'blocks', 'related_stories')


def _partial(result, code, message):
    result['status'] = 'partial'
    result['partial'] = True
    field = 'diagnostics' if result.get('format') == 'ooxml-word' else 'issues'
    result.setdefault(field, []).append({'code': code, 'message': message, 'stage': 'revision_view'})


def complete_maximum(data, result, *, revisions):
    """Return accepted text when supported; never present mixed revisions as current."""
    result['reading_mode'] = 'maximum'
    result['maximum_reading'] = {
        'stored_text_view': 'accepted-plus-separate-stored-revision-content',
        'related_content': 'requested', 'field_policy': 'cached_result-not-executed',
        'object_policy': 'placeholder-not-decoded', 'layout_verified': False,
        'producer_profile_verified': False,
    }
    if result.get('status') not in ('ok', 'partial'):
        result['content_view'] = 'unavailable'
        return result
    if result.get('format') == 'ms_doc_subset':
        result['maximum_reading'].update(chart_policy='officeart_cache_only',
                                         object_policy='placeholder-with-optional-stored-png',
                                         stored_text_view='stored-text-revisions-unresolved')
        result['content_view'] = 'stored_text_not_verified_current'
        result['current_view_available'] = False
        _partial(result, 'current_revision_view_unresolved',
                 'Stored MS-DOC text may include deleted or inserted revisions; current wording cannot be verified')
        return result
    if result.get('current_view_available') is False:
        result['content_view'] = 'stored_text_not_verified_current'
        result['maximum_reading']['stored_text_view'] = 'stored-text-revisions-unresolved'
        _partial(result, 'current_revision_view_unresolved',
                 'Current wording could not be verified; only clearly labeled stored text is available')
        return result
    if revisions == 'all':
        result['content_view'] = 'all_stored_revisions_not_current'
        result['current_view_available'] = False
        result['maximum_reading']['stored_text_view'] = 'all-stored-revisions-not-current'
        _partial(result, 'all_revision_text_not_current',
                 'Explicit all view includes deleted and inserted text together; do not interpret it as current wording')
        return result
    result['content_view'] = 'accepted_supported_revision_view'
    result['current_view_available'] = True
    stored = {}
    try:
        if result.get('format') == 'ooxml-word':
            from wps_ooxml import read_bytes
            stored = read_bytes(data, revisions='all', include_related=True, recover_textboxes=True)
        elif result.get('format') == 'rtf':
            from wps_rtf_read import parse_rtf_bytes
            stored = parse_rtf_bytes(data, revisions='all', hidden_text='include',
                                     object_policy='placeholder', include_related=True)
        else:
            return result
        if stored.get('status') not in ('ok', 'partial'):
            raise ValueError('Stored revision view unavailable: ' + str(stored.get('status')))
    except (ParseError, ValueError) as exc:
        result['revision_content'] = {'status': 'unavailable', 'view': 'all_stored_not_current',
                                      'reason': str(exc),
                                      'source_status': getattr(exc, 'status', stored.get('status')),
                                      'location': getattr(exc, 'location', None),
                                      'diagnostics': stored.get('diagnostics', stored.get('issues', []))}
        _partial(result, 'stored_revision_content_unreadable',
                 'Current supported text was retained; the separate stored revision view could not be read')
        return result
    changed = any(result.get(key) != stored.get(key) for key in CONTENT_FIELDS)
    if changed:
        result['revision_content'] = {
            'status': stored['status'], 'view': 'all_stored_revisions_not_current',
            'interpretation': 'Separate complete stored view, not a list of changes or current wording',
            'content': {key: stored[key] for key in CONTENT_FIELDS if key in stored},
            'diagnostics': stored.get('diagnostics', stored.get('issues', [])),
        }
        _partial(result, 'separate_stored_revision_content',
                 'Current text and the separate stored revision view differ; deleted content is not part of current text')
    else:
        result['revision_content'] = {'status': 'partial' if stored['status'] == 'partial' else 'no_content_difference',
                                      'diagnostics': stored.get('diagnostics', stored.get('issues', [])),
                                      'view': 'all_stored_revisions_not_current',
                                      'claim': 'Compared supported content only; not a full revision inventory'}
        if stored['status'] == 'partial':
            _partial(result, 'stored_revision_view_partial',
                     'The stored revision view has known gaps even where supported text matches')
    return result


def read_rtf_maximum(data, revisions=None):
    """Fallback only for known unavailable revision filtering, not damaged structure."""
    from wps_rtf_read import parse_rtf_bytes
    try:
        return parse_rtf_bytes(data, revisions=revisions or 'accepted', hidden_text='include',
                               object_policy='placeholder', include_related=True)
    except ParseError as exc:
        recoverable = {
            'Incomplete RTF style reference',
            'RTF stylesheet revision properties are not reconstructed',
            'Unknown or historical RTF style properties are not reconstructed',
            'RTF historical/default properties or alternate view are unsupported',
            'Simultaneous deleted and revised RTF text is ambiguous',
        }
        if revisions is not None or exc.status != 'unsupported' or str(exc) not in recoverable:
            raise
        result = parse_rtf_bytes(data, revisions='all', hidden_text='include',
                                 object_policy='placeholder', include_related=True)
        result['current_view_available'] = False
        result['revision_view_reason'] = str(exc)
        return result
