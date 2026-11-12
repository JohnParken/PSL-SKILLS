"""Bounded JSON serialization and optional output projections."""
import hashlib
import io
import json
import os
from wps_access import ParseError

# Content-first default: large workbooks are common and a 16 MiB cap discarded
# fully parsed data. The bound still prevents unbounded stdout/JSON construction;
# callers that need more can use --output or raise --max-json-bytes explicitly.
MAX_JSON_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_JSON_OUTPUT_BYTES_LIMIT = 256 * 1024 * 1024

_CELL_DROP_KEYS = ('raw_numeric_hex', 'raw_numeric_text', 'record_id', 'record_offset',
                   'source_record_id')
_INTERPRET_KEEP_KEYS = ('status', 'kind', 'interpreted_value', 'display_text', 'date_system',
                        'format_id', 'format_code', 'reason', 'selected_section',
                        'currency_symbol', 'multiplier', 'scale_divisor')


def _compact_cell(cell):
    for key in _CELL_DROP_KEYS:
        cell.pop(key, None)
    interpreted = cell.get('numeric_interpretation')
    if isinstance(interpreted, dict):
        cell['numeric_interpretation'] = {key: interpreted[key] for key in _INTERPRET_KEEP_KEYS
                                          if key in interpreted}
    formula = cell.get('formula')
    if isinstance(formula, dict):
        formula.pop('ancillary_hex', None)
    if 'custom_format_raw' in cell:
        cell.pop('custom_format_raw', None)
        cell['custom_format_raw_omitted'] = True
    encoding = cell.get('text_encoding')
    if isinstance(encoding, dict) and 'raw_text' in encoding:
        encoding.pop('raw_text', None)
        encoding['raw_text_omitted'] = True
    return cell


def compact_result(result):
    """Reduce provenance-heavy fields for delivery while keeping values and caches.

    This is an explicit opt-in / budget fallback projection, not the default
    schema. Saved values, types, cache status, merges, visibility and issues are
    unchanged; raw byte evidence and per-cell record offsets are dropped and
    uninterpreted metadata payload hex is replaced by record counts.
    """
    if not isinstance(result, dict):
        return result
    result['output_projection'] = {
        'kind': 'compact_stored_values', 'raw_evidence_complete': False,
        'saved_values_unchanged': True,
        'omitted_fields': ['per_cell_raw_numeric_bytes_and_record_offsets',
                           'numeric_interpretation_provenance', 'formula_ancillary_hex',
                           'uninterpreted_metadata_payloads',
                           'rich_string_run_and_phonetic_raw_bytes',
                           'ooxml_text_encoding_raw_strings',
                           'per_cell_custom_format_text_use_custom_number_formats']}
    sheets = result.get('sheets')
    if isinstance(sheets, list):
        for sheet in sheets:
            if not isinstance(sheet, dict):
                continue
            cells = sheet.get('cells')
            if isinstance(cells, dict):
                for cell in cells.values():
                    if isinstance(cell, dict):
                        _compact_cell(cell)
    raw_metadata = result.get('uninterpreted_metadata')
    if isinstance(raw_metadata, list):
        summary = {}
        for item in raw_metadata:
            if not isinstance(item, dict):
                continue
            key = (item.get('scope'), item.get('record_id'), item.get('name'))
            entry = summary.setdefault(key, {'scope': item.get('scope'),
                                             'record_id': item.get('record_id'),
                                             'name': item.get('name'), 'count': 0})
            entry['count'] += 1
        result['uninterpreted_metadata'] = [dict(value) for value in summary.values()]
        result['uninterpreted_metadata_compacted'] = True
    shared = result.get('shared_string_formatting')
    if isinstance(shared, list):
        for item in shared:
            if isinstance(item, dict):
                item.pop('runs_raw_hex', None)
    phonetics = result.get('shared_string_phonetics')
    if isinstance(phonetics, list):
        for item in phonetics:
            if isinstance(item, dict):
                item.pop('raw_ext_hex', None)
    encoding = result.get('shared_string_encoding')
    if isinstance(encoding, list):
        for item in encoding:
            if isinstance(item, dict) and 'raw_text' in item:
                item.pop('raw_text', None)
                item['raw_text_omitted'] = True
    return result


def encode_result(result, *, limit=MAX_JSON_OUTPUT_BYTES):
    """Bound serialization before stdout; never emit a truncated JSON document."""
    output = io.StringIO()
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, default=str, allow_nan=False)
    for chunk in encoder.iterencode(result):
        size += len(chunk.encode('utf-8'))
        if size > limit:
            raise ParseError('resource_limit', {'stage': 'output',
                                                'limit_bytes': limit},
                             'JSON output exceeds the configured byte budget')
        output.write(chunk)
    return output.getvalue()


def write_result_file(name, result, *, limit=MAX_JSON_OUTPUT_BYTES_LIMIT):
    """Write a complete JSON result to a new task-root file, streamed and bounded.

    `name` is a bare filename in the current task directory. The file is created
    exclusively (never overwritten). Returns manifest facts only, not content.
    """
    if not isinstance(name, str) or not name or name in ('.', '..') or any(char in name for char in ('/', '\\', '\x00')):
        raise ParseError('unsupported', {'stage': 'output'}, 'Output name must be a bare filename')
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ParseError('unsupported', {'stage': 'output', 'file': name},
                         'Output file already exists; choose a new bare filename') from exc
    digest = hashlib.sha256()
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, default=str, allow_nan=False)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            for chunk in encoder.iterencode(result):
                raw = chunk.encode('utf-8')
                size += len(raw)
                if size > limit:
                    raise ParseError('resource_limit', {'stage': 'output', 'limit_bytes': limit},
                                     'JSON output exceeds the configured byte budget')
                digest.update(raw)
                handle.write(raw)
            if size + 1 > limit:
                raise ParseError('resource_limit', {'stage': 'output', 'limit_bytes': limit},
                                 'JSON output exceeds the configured byte budget')
            handle.write(b'\n')
            digest.update(b'\n')
            handle.flush()
            os.fsync(handle.fileno())
            size += 1
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return {'file': name, 'bytes': size, 'sha256': digest.hexdigest()}
