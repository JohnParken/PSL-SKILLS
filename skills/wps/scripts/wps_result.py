"""Bounded JSON serialization shared by the command-line readers."""
import io
import json
from wps_access import ParseError

MAX_JSON_OUTPUT_BYTES = 16 * 1024 * 1024

def encode_result(result, *, limit=MAX_JSON_OUTPUT_BYTES):
    """Bound serialization before stdout; never emit a truncated JSON document."""
    output = io.StringIO()
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, default=str, allow_nan=False)
    for chunk in encoder.iterencode(result):
        size += len(chunk.encode('utf-8'))
        if size > limit:
            raise ParseError('resource_limit', {'stage': 'output'}, 'JSON output exceeds 16 MiB')
        output.write(chunk)
    return output.getvalue()

