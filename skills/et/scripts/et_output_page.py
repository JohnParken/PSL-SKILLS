"""Optional stdout-budget pages; calculations always precede output selection."""
import base64
from copy import deepcopy
import hashlib
import json

from et_table_analysis import analyze
from et_table_preview import preview
from wps_read_view import encoded, fail, validate_bytes

PAGE_REVISION = 1


def _fits(value, budget):
    # Stop counting early rather than materializing a potentially huge JSON string.
    size = 1  # print's newline
    encoder = json.JSONEncoder(ensure_ascii=False, default=str, allow_nan=False, separators=(',', ':'))
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode('utf-8'))
        if size > budget:
            return False
    return True


def _binding(query, is_preview):
    canonical = json.dumps({'query': query, 'preview': is_preview}, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def page(source, data, query, *, is_preview=False, max_bytes=12000, cursor=None):
    """Recalculate the full selection each call, then page rows and groups only.

    The original offset is the beginning of requested output coverage. Limits are
    per-page maxima; zero disables that result stream, not its calculations.
    """
    validate_bytes(max_bytes)
    if max_bytes is None:
        fail('A byte budget is required for table output pagination')
    digest = hashlib.sha256(data).hexdigest()
    binding = _binding(query, is_preview)
    effective = deepcopy(query)
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError()
            token = json.loads(base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True))
            if (not isinstance(token, dict) or set(token) != {'source', 'scope', 'revision', 'offsets'}
                    or type(token['revision']) is not int or token['revision'] != PAGE_REVISION
                    or token['source'] != digest or token['scope'] != binding):
                raise ValueError()
            offsets = token['offsets']
            expected = {'offset'} if is_preview else {'offset', 'group_offset'}
            if not isinstance(offsets, dict) or set(offsets) != expected:
                raise ValueError()
            if any(type(value) is not int or value < query.get(key, 0) for key, value in offsets.items()):
                raise ValueError()
            effective.update(offsets)
        except (ValueError, TypeError, UnicodeError, KeyError, RecursionError):
            fail('Invalid cursor or source/query/mode/revision changed; restart table pagination')
    # Existing resource limits, full-region quality and aggregate logic stay here.
    output = preview(source, effective) if is_preview else analyze(source, effective)
    output.update(query=deepcopy(query), source_sha256=digest, output_page_revision=PAGE_REVISION)
    streams = [('rows', 'row_page', 'offset', 'limit', 10 if is_preview else 100)]
    if not is_preview:
        streams.append(('groups', 'group_page', 'group_offset', 'group_limit', 100))
    candidates = {}
    coverage = {}
    for name, page_key, offset_key, limit_key, default_limit in streams:
        info = output[page_key]
        requested = query.get(limit_key, default_limit) > 0
        if requested and info['offset'] > info['total']:
            fail('Requested output offset exceeds result total')
        candidates[name] = output[name]
        output[name] = []
        coverage[name] = {'requested': requested, 'from_offset': query.get(offset_key, 0),
                          'scope': 'result_items_after_initial_offset' if requested else 'not_requested'}
    output['coverage'] = coverage

    def finish():
        offsets = {}
        more = False
        for name, page_key, offset_key, _, _ in streams:
            info = output[page_key]
            end = info['offset'] + len(output[name])
            remaining = coverage[name]['requested'] and end < info['total']
            info.update(returned=len(output[name]), has_more=remaining,
                        next_offset=end if remaining else None,
                        truncated=info['offset'] > 0 or end < info['total'])
            offsets[offset_key] = end
            more = more or remaining
        output['has_more'] = more
        output['next_cursor'] = None
        if more:
            token = {'source': digest, 'scope': binding, 'revision': PAGE_REVISION, 'offsets': offsets}
            output['next_cursor'] = base64.urlsafe_b64encode(encoded(token).encode('utf-8')).decode('ascii')
        if is_preview:
            coordinates = {cell['coordinate'] for row in output['rows'] for cell in row['cells'].values()}
            for merge in output['merges']:
                merge['anchor_in_page'] = merge['anchor'] in coordinates
            output['scope']['returned_positions'] = len(output['rows']) * len(output['columns'])

    # A terminal page loses its cursor. Its size is not monotone in row count,
    # so try the complete bounded candidate page before prefix fitting.
    for name, *_ in streams:
        output[name] = candidates[name]
    finish()
    if _fits(output, max_bytes):
        return output
    for name, *_ in streams:
        output[name] = []
    finish()
    if not _fits(output, max_bytes):
        fail('Table diagnostics/metadata exceed max-output-bytes; increase the budget')
    # Rows get priority; groups continue on later pages if the shared budget fills.
    # Binary search bounds serialization work while never splitting a row/group.
    for name, _, _, _, _ in streams:
        low, high = 0, len(candidates[name])
        while low < high:
            middle = (low + high + 1) // 2
            output[name] = candidates[name][:middle]
            finish()
            if _fits(output, max_bytes):
                low = middle
            else:
                high = middle - 1
        output[name] = candidates[name][:low]
        finish()
    if output['has_more'] and not any(output[name] for name, *_ in streams):
        fail('A single result row/group plus required metadata exceeds max-output-bytes; increase the budget')
    if not _fits(output, max_bytes):
        fail('Final table page exceeds max-output-bytes')
    return output
