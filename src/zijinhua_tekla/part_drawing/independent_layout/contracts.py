"""Finite JSON and immutable evidence envelopes for the stage-0 boundary."""

import hashlib
import json
import math


class ContractError(ValueError):
    """A named input failure that must not become a feasible layout."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__(f'{code}: {detail}')


def require(condition, code, detail):
    if not condition:
        raise ContractError(code, detail)


def _json_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        require(math.isfinite(value), 'NONFINITE', 'JSON contains a nonfinite number')
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item)
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for item in value.values():
            _json_value(item)
        return
    raise ContractError('JSON_TYPE', f'Unsupported JSON value: {type(value).__name__}')


def canonical_json(value):
    _json_value(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def seal(value):
    """Detach nested mutable inputs and bind every field except the digest itself."""
    require('digest' not in value, 'DIGEST_ALREADY_PRESENT', 'Do not reseal an existing envelope')
    result = json.loads(canonical_json(value))
    result['digest'] = digest(result)
    return result


def check_seal(value):
    require(isinstance(value, dict) and 'digest' in value, 'DIGEST_MISSING', 'Expected sealed evidence')
    body = {k: v for k, v in value.items() if k != 'digest'}
    require(digest(body) == value['digest'], 'DIGEST_MISMATCH', 'Evidence content changed')


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_KEY', key)
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=pairs)
    _json_value(result)
    return result


def positive(value, name):
    require(type(value) in (int, float) and math.isfinite(value) and value > 0,
            'INVALID_NUMBER', name)
    return value
