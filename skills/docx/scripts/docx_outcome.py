"""Conservative machine actions; never infer retry safety from exception wording."""


def failure(code, message, *, action='inspect_task', **fields):
    return {'status': 'failed', 'error_code': code, 'next_action': action,
            'message': str(message), **fields}


def spec_identity(spec):
    import hashlib
    import json
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()
