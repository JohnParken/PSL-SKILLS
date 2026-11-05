"""Version-one bounded, lossless JSON value transport; no executable objects."""
import base64
from decimal import Decimal
import json

PROTOCOL = 'psl_capabilities_v1'
MAX_FRAME = 128 * 1024 * 1024


def pack(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('Nonfinite Decimal')
        return {'$decimal': str(value)}
    if isinstance(value, bytes):
        return {'$bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, tuple):
        return {'$tuple': [pack(item) for item in value]}
    if isinstance(value, dict):
        return {'$map': [[pack(key), pack(item)] for key, item in value.items()]}
    if isinstance(value, list):
        return [pack(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    raise ValueError('Unsupported wire value: ' + type(value).__name__)


def unpack(value, depth=0):
    if depth > 64:
        raise ValueError('Wire nesting exceeds 64')
    if isinstance(value, list):
        return [unpack(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) != 1:
            raise ValueError('Invalid tagged value')
        tag, data = next(iter(value.items()))
        if tag == '$decimal' and isinstance(data, str) and len(data) <= 4096:
            result = Decimal(data)
            if not result.is_finite():
                raise ValueError('Nonfinite Decimal')
            return result
        if tag == '$bytes' and isinstance(data, str):
            return base64.b64decode(data, validate=True)
        if tag == '$tuple' and isinstance(data, list):
            return tuple(unpack(item, depth + 1) for item in data)
        if tag == '$map' and isinstance(data, list):
            result = {}
            for pair in data:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError('Invalid map pair')
                key, item = (unpack(part, depth + 1) for part in pair)
                if key in result:
                    raise ValueError('Duplicate map key')
                result[key] = item
            return result
        raise ValueError('Unknown wire tag')
    if value is None or type(value) in (str, bool, int):
        return value
    raise ValueError('Unsupported wire scalar')


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def dumps(value):
    raw = json.dumps(pack(value), ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_FRAME:
        raise ValueError('Wire frame exceeds 128 MiB')
    return raw


def loads(raw):
    if len(raw) > MAX_FRAME:
        raise ValueError('Wire frame exceeds 128 MiB')
    return unpack(json.loads(raw, object_pairs_hook=unique))
