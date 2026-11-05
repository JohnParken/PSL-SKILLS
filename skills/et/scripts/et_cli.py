"""Stable ET capability entry; legacy read_et CLI and API remain unchanged."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from et_cli_protocol import PROTOCOL, MAX_FRAME, dumps, loads
from read_et import read_bytes, encode_result


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'run':
        from read_et import main as read_main
        sys.argv = [str(Path(__file__).with_name('read_et.py')), *sys.argv[2:]]
        return read_main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('capabilities', 'read', 'run'))
    args, remaining = parser.parse_known_args()
    if remaining:
        parser.error('Unexpected arguments')
    if args.command == 'capabilities':
        print(json.dumps({'protocol': PROTOCOL, 'interface_version': 1, 'skill': 'et',
                          'operations': ['read'], 'result': 'full_original_extraction', 'max_frame_bytes': MAX_FRAME}), flush=True)
        return 0
    identity = None
    code = 1
    try:
        request = loads(sys.stdin.buffer.read(MAX_FRAME + 1))
        identity = request.get('id')
        if set(request) != {'protocol', 'id', 'operation', 'parameters'} or request['protocol'] != PROTOCOL or request['operation'] != 'read':
            raise ValueError('Unsupported protocol or request fields')
        if type(identity) is not int or identity < 0:
            raise ValueError('Request id must be a nonnegative integer')
        p = request['parameters']
        if (set(p) - {'raw', 'source', 'compatibility'} or not isinstance(p['raw'], bytes)
                or not isinstance(p['source'], str) or len(p['raw']) > 64 * 1024 * 1024):
            raise ValueError('Invalid ET read request or input exceeds 64 MiB')
        result = read_bytes(p['raw'], source=p['source'], compatibility=p.get('compatibility', 'data'))
        # Reuse the original producer budget and JSON schema; no derived view.
        raw = encode_result(result).encode('utf-8')
        status = result.get('status')
        code = 0 if status == 'ok' else 3 if status == 'partial' else 1
        response = {'protocol': PROTOCOL, 'id': identity, 'ok': True, 'result': {'extraction': raw, 'exit_code': code}}
    except Exception as exc:
        response = {'protocol': PROTOCOL, 'id': identity, 'ok': False,
                    'error': {'code': type(exc).__name__, 'message': str(exc), 'status': getattr(exc, 'status', None),
                              'location': getattr(exc, 'location', None)}}
    sys.stdout.buffer.write(dumps(response) + b'\n')
    sys.stdout.buffer.flush()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
