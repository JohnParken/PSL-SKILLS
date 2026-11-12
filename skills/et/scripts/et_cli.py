"""Stable ET capability entry; legacy read_et CLI and API remain unchanged."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from et_cli_protocol import PROTOCOL, MAX_FRAME, dumps, loads
from read_et import read_bytes
from wps_result import encode_result
from wps_access import DEFAULT_FILE_COMPATIBILITY, ParseError


def metadata(result):
    """Project a fully parsed producer result without inventing coverage assertions."""
    schema = result.get('schema_version')
    parsed = {'biff8_readonly_v1': 'biff_parsed',
              'ooxml_spreadsheet_readonly_v1': 'spreadsheet_parsed'}.get(schema)
    if parsed is None or result.get(parsed) is not True or result.get('status') not in ('ok', 'partial'):
        raise ValueError('Metadata requires a successful supported ET spreadsheet schema')
    sheets = result.get('sheets')
    if not isinstance(sheets, list) or not 1 <= len(sheets) <= 128:
        raise ValueError('Invalid ET metadata sheet list')
    names = []
    for sheet in sheets:
        if (not isinstance(sheet, dict) or not isinstance(sheet.get('name'), str)
                or not 1 <= len(sheet['name']) <= 31 or sheet['name'] in names):
            raise ValueError('Invalid or duplicate ET metadata sheet name')
        names.append(sheet['name'])
    return {'schema_version': 'workbook_metadata_v1', 'sheet_names': names, 'native': False,
            'source_status': result['status'], 'source_issues': deepcopy(result.get('issues', [])),
            'source_coverage': {key: deepcopy(result[key]) for key in
                                ('cell_value_coverage', 'omissions_affect_cell_values', 'reading_assessment') if key in result},
            'input_schema': schema}


def extraction_payload(result, identity, code):
    """Keep the complete schema within the actual Base64 protocol envelope."""
    envelope = {'protocol': PROTOCOL, 'id': identity, 'ok': True,
                'result': {'extraction': b'', 'exit_code': code}}
    overhead = len(dumps(envelope))
    # Base64 uses four bytes per three source bytes, including final padding.
    limit = max(0, (MAX_FRAME - overhead) // 4 * 3)
    return {'extraction': encode_result(result, limit=limit).encode('utf-8')}


def encode_error(identity, error):
    """Return one complete error frame even when detailed diagnostics are large."""
    response = {'protocol': PROTOCOL, 'id': identity, 'ok': False, 'error': error}
    try:
        return dumps(response)
    except (ValueError, TypeError):
        response['error'] = {
            'code': 'ResponseSerializationError', 'status': 'resource_limit',
            'message': 'Response exceeds wire budget or cannot be encoded; details omitted.',
            'location': {'stage': 'protocol_output', 'diagnostics_omitted': True}}
        try:
            return dumps(response)
        except (ValueError, TypeError):
            # Invalid or exceptionally large request IDs must not prevent a
            # bounded error. No successful response is emitted without its ID.
            response['id'] = None
            return dumps(response)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'run':
        from read_et import main as read_main
        sys.argv = [str(Path(__file__).with_name('read_et.py')), *sys.argv[2:]]
        return read_main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('capabilities', 'read', 'metadata', 'run'))
    args, remaining = parser.parse_known_args()
    if remaining:
        parser.error('Unexpected arguments')
    if args.command == 'capabilities':
        print(json.dumps({'protocol': PROTOCOL, 'interface_version': 1, 'skill': 'et',
                          'operations': ['read', 'metadata'], 'metadata_schema': 'workbook_metadata_v1', 'result': 'full_original_extraction', 'max_frame_bytes': MAX_FRAME}), flush=True)
        return 0
    identity = None
    code = 1
    try:
        request = loads(sys.stdin.buffer.read(MAX_FRAME + 1))
        request_identity = request.get('id')
        if set(request) != {'protocol', 'id', 'operation', 'parameters'} or request['protocol'] != PROTOCOL or request['operation'] != args.command:
            raise ValueError('Unsupported protocol or request fields')
        if type(request_identity) is not int or request_identity < 0:
            raise ValueError('Request id must be a nonnegative integer')
        identity = request_identity
        p = request['parameters']
        if (not isinstance(p, dict) or not {'raw', 'source'} <= set(p) or set(p) - {'raw', 'source', 'compatibility'} or not isinstance(p['raw'], bytes)
                or not isinstance(p['source'], str) or len(p['raw']) > 64 * 1024 * 1024):
            raise ValueError('Invalid ET read request or input exceeds 64 MiB')
        result = read_bytes(p['raw'], source=p['source'], compatibility=p.get('compatibility', DEFAULT_FILE_COMPATIBILITY))
        status = result.get('status')
        code = 0 if status == 'ok' else 3 if status == 'partial' else 1
        if args.command == 'metadata':
            # Metadata is a bounded projection: do not serialize the complete
            # extraction just to return sheet names.
            payload = {'metadata': metadata(result)}
        else:
            payload = extraction_payload(result, identity, code)
        response = {'protocol': PROTOCOL, 'id': identity, 'ok': True, 'result': {**payload, 'exit_code': code}}
        try:
            encoded = dumps(response)
        except (ValueError, TypeError) as exc:
            raise ParseError('resource_limit', {'stage': 'protocol_output', 'limit_bytes': MAX_FRAME},
                             'Response cannot fit in the protocol frame') from exc
    except Exception as exc:
        code = 1
        encoded = encode_error(identity, {'code': type(exc).__name__, 'message': str(exc),
                                          'status': getattr(exc, 'status', None),
                                          'location': getattr(exc, 'location', None)})
    sys.stdout.buffer.write(encoded + b'\n')
    sys.stdout.buffer.flush()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
