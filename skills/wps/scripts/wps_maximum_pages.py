"""Lossless bounded transport of a maximum result; not streaming document parsing.

Cursor hashes detect mismatches/corruption, not malicious re-signing: no secret is
stored or assumed. A receiver must verify contiguous offsets and the final hash.
"""
import base64
import hashlib
import json
from wps_access import ParseError

PROJECTION_REVISION = 1
MAX_RESULT_BYTES = 256 * 1024 * 1024
MAX_PAGE_CHARS = 262144
MAX_ASSEMBLY_PAGES = 65536


def canonical(value):
    """Stable JSON for fingerprints and result reconstruction."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(',', ':'), default=str)


def fail(message):
    raise ParseError('output_view_error', {'stage': 'maximum_output_view'}, message)


def _cursor_token(fields):
    token = dict(fields)
    token['checksum'] = hashlib.sha256(canonical(fields).encode('utf-8')).hexdigest()
    return base64.urlsafe_b64encode(canonical(token).encode('utf-8')).decode('ascii')


def _decode(cursor):
    try:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError()
        raw = base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True)
        token = json.loads(raw)
        expected = {'revision', 'source', 'options', 'result', 'next', 'prefix', 'page_size', 'checksum'}
        if not isinstance(token, dict) or set(token) != expected:
            raise ValueError()
        checksum = token.pop('checksum')
        if checksum != hashlib.sha256(canonical(token).encode('utf-8')).hexdigest():
            raise ValueError()
        if (type(token['revision']) is not int or token['revision'] != PROJECTION_REVISION
                or type(token['next']) is not int or token['next'] <= 0
                or type(token['page_size']) is not int):
            raise ValueError()
        return token
    except (ValueError, TypeError, UnicodeError, KeyError, RecursionError):
        fail('Invalid or altered cursor; restart the maximum read')
    return None


def _chunks(result):
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True,
                               separators=(',', ':'), default=str)
    yield from encoder.iterencode(result)


def project(result, data, options, *, page_size=8000, cursor=None,
            max_chars=12000, max_bytes=12000):
    """Slice canonical JSON by Unicode characters with a UTF-8 envelope budget."""
    if not isinstance(data, bytes) or not isinstance(result, dict) or not isinstance(options, dict):
        fail('Expected immutable source bytes, result object and parse-options object')
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_CHARS:
        fail('page-size must be 1..262144 canonical JSON characters')
    if type(max_chars) is not int or not 512 <= max_chars <= 2 * 1024 * 1024:
        fail('max-output-chars must be 512..2097152')
    if type(max_bytes) is not int or not 512 <= max_bytes <= 16 * 1024 * 1024:
        fail('max-output-bytes must be 512..16777216')
    source = hashlib.sha256(data).hexdigest()
    try:
        binding = hashlib.sha256(canonical(options).encode('utf-8')).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        fail('Parse options cannot be represented as canonical UTF-8 JSON')
    token = _decode(cursor) if cursor is not None else None
    start = token['next'] if token else 0
    if token and (token['source'] != source or token['options'] != binding or token['page_size'] != page_size):
        fail('Source, parse options or page-size changed; restart the maximum read')
    digest = hashlib.sha256()
    prefix = hashlib.sha256()
    total = total_bytes = 0
    fragment_parts = []
    try:
        for chunk in _chunks(result):
            encoded = chunk.encode('utf-8')
            digest.update(encoded)
            total_bytes += len(encoded)
            if total_bytes > MAX_RESULT_BYTES:
                fail('Maximum paged result exceeds the 256 MiB transport limit')
            end = total + len(chunk)
            if total < start:
                prefix.update(chunk[:min(len(chunk), start - total)].encode('utf-8'))
            left, right = max(start, total), min(start + page_size, end)
            if right > left:
                fragment_parts.append(chunk[left-total:right-total])
            total = end
    except ParseError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        fail('Result cannot be represented as canonical UTF-8 JSON: ' + type(exc).__name__)
    result_hash = digest.hexdigest()
    if start >= total or token and (token['result'] != result_hash or token['prefix'] != prefix.hexdigest()):
        fail('Result, cursor offset or prefix changed; restart the maximum read')
    available = ''.join(fragment_parts)
    view = {
        'schema_version': 'wps_maximum_page_v1', 'projection_revision': PROJECTION_REVISION,
        'status': result.get('status', 'invalid_or_unsupported'),
        'source_status': result.get('status', 'invalid_or_unsupported'),
        'partial': result.get('partial', False),
        'coverage': 'complete_maximum_result_json_transport',
        'source_sha256': source, 'result_sha256': result_hash, 'options_sha256': binding,
        'total_chars': total, 'total_utf8_bytes': total_bytes, 'start': start,
        'offset_unit': 'unicode_characters_in_canonical_json_not_document_pages',
        'payload_encoding': 'canonical_json_fragment',
        'diagnostics_delivery': 'all_source_diagnostics_in_payload_stream',
        'cursor_integrity': 'sha256_consistency_not_authenticated',
    }

    def finish(count):
        end = start + count
        fragment = available[:count]
        cumulative = prefix.copy()
        cumulative.update(fragment.encode('utf-8'))
        more = end < total
        view.update(payload_fragment=fragment, end=end, returned_chars=count,
                    chunk_sha256=hashlib.sha256(fragment.encode('utf-8')).hexdigest(),
                    prefix_sha256=cumulative.hexdigest(), has_more=more,
                    page_is_last=not more, single_page_complete=start == 0 and not more,
                    truncated=more, next_cursor=None)
        if more:
            view['next_cursor'] = _cursor_token({
                'revision': PROJECTION_REVISION, 'source': source, 'options': binding,
                'result': result_hash, 'next': end, 'prefix': cumulative.hexdigest(),
                'page_size': page_size,
            })
        text = canonical(view) + '\n'
        return len(text) <= max_chars and len(text.encode('utf-8')) <= max_bytes

    # A terminal page has no cursor and can fit where its shorter prefix cannot.
    if len(available) == total-start and finish(len(available)):
        return view
    if not finish(0):
        fail('Maximum page envelope exceeds output budget; increase character/byte budgets')
    low, high = 0, len(available)
    while low < high:
        middle = (low + high + 1) // 2
        if finish(middle):
            low = middle
        else:
            high = middle - 1
    if low == 0:
        fail('No payload character fits output budget; increase character/byte budgets')
    if not finish(low):
        fail('Maximum page exceeds output budget')
    return view


def read_page(data, *, page_size=8000, cursor=None, max_output_chars=12000,
              max_output_bytes=12000, **options):
    """Public in-memory entry; each continuation reparses the immutable input."""
    from read_wps import read_bytes
    options = dict(options)
    if options.get('reading_mode', 'maximum') != 'maximum':
        fail('Maximum pages require reading_mode=maximum')
    options['reading_mode'] = 'maximum'
    result = read_bytes(data, **options)
    return project(result, data, options, page_size=page_size, cursor=cursor,
                   max_chars=max_output_chars, max_bytes=max_output_bytes)


def assemble_pages(pages):
    """Verify ordered pages from zero before reconstructing the complete result."""
    fragments = []
    identity = None
    expected_start = 0
    digest = hashlib.sha256()
    final_seen = False
    total_bytes = 0
    keys = ('source_sha256', 'result_sha256', 'options_sha256', 'total_chars', 'total_utf8_bytes',
            'source_status', 'partial')
    for page_index, page in enumerate(pages):
        if page_index >= MAX_ASSEMBLY_PAGES:
            fail('Maximum assembly page-count limit exceeded; use larger pages')
        if (not isinstance(page, dict) or page.get('schema_version') != 'wps_maximum_page_v1'
                or page.get('projection_revision') != PROJECTION_REVISION or final_seen):
            fail('Invalid, reordered or extra maximum page')
        current = tuple(page.get(key) for key in keys)
        if identity is None:
            identity = current
        if current != identity or page.get('status') != page.get('source_status'):
            fail('Maximum pages have mixed sources, results or options')
        text = page.get('payload_fragment')
        if (not isinstance(text, str) or type(page.get('start')) is not int
                or page['start'] != expected_start or type(page.get('end')) is not int
                or page['end'] != expected_start + len(text) or not text
                or page.get('returned_chars') != len(text)):
            fail('Maximum page payload is missing, repeated or out of order')
        try:
            raw = text.encode('utf-8')
        except UnicodeError:
            fail('Maximum page payload is not valid UTF-8')
        total_bytes += len(raw)
        if total_bytes > MAX_RESULT_BYTES:
            fail('Maximum assembled result exceeds transport limit')
        if hashlib.sha256(raw).hexdigest() != page.get('chunk_sha256'):
            fail('Maximum page chunk hash mismatch')
        digest.update(raw)
        if digest.hexdigest() != page.get('prefix_sha256'):
            fail('Maximum page prefix hash mismatch')
        expected_start = page['end']
        if type(page.get('has_more')) is not bool:
            fail('Maximum page continuation state missing')
        if page['has_more']:
            token = _decode(page.get('next_cursor'))
            if (token['next'] != expected_start or token['prefix'] != digest.hexdigest()
                    or token['source'] != page['source_sha256'] or token['result'] != page['result_sha256']
                    or token['options'] != page['options_sha256']):
                fail('Maximum page continuation does not match its payload')
        else:
            if page.get('next_cursor') is not None:
                fail('Final maximum page cannot have a continuation cursor')
            final_seen = True
        fragments.append(text)
    if (not identity or not final_seen or expected_start != identity[3]
            or total_bytes != identity[4] or digest.hexdigest() != identity[1]):
        fail('Maximum result is incomplete or final hash/length does not match')
    try:
        result = json.loads(''.join(fragments))
    except (ValueError, TypeError, RecursionError) as exc:
        fail('Reassembled maximum result is not JSON: ' + type(exc).__name__)
    if not isinstance(result, dict):
        fail('Reassembled maximum result must be an object')
    if result.get('status', 'invalid_or_unsupported') != identity[5] or result.get('partial', False) != identity[6]:
        fail('Maximum page source status disagrees with the reassembled result')
    return result
