"""Load only bundled document profiles; never interpret a user path as a profile."""
from pathlib import Path
import json

_PROFILE_DIR = Path(__file__).resolve().parents[3] / 'assets' / 'profiles'
KNOWN_PROFILES = frozenset({
    'official-basic', 'business-report', 'academic', 'presentation-material',
})
_PROFILE_KEYS = frozenset({'theme', 'page', 'styles', 'table_format'})


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('profile contains duplicate key: ' + key)
        result[key] = value
    return result


def apply_profile(source):
    """Return a new spec with profile defaults; user fields win recursively.

    None of the bundled profiles has access to document blocks or output paths.
    Existing specs without `profile` are passed through unchanged.
    """
    if not isinstance(source, dict) or 'profile' not in source:
        return source
    name = source['profile']
    if not isinstance(name, str) or name not in KNOWN_PROFILES:
        raise ValueError('profile must be one of: ' + ', '.join(sorted(KNOWN_PROFILES)))
    path = _PROFILE_DIR / (name + '.json')
    try:
        data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('cannot read bundled profile: ' + name) from exc
    if not isinstance(data, dict) or set(data) - _PROFILE_KEYS:
        raise ValueError('bundled profile contains unsupported root fields')
    result = {}
    for key, value in data.items():
        if key in {'page', 'styles', 'table_format'} and not isinstance(value, dict):
            raise ValueError('bundled profile has invalid object: ' + key)
        result[key] = value
    for key, value in source.items():
        if key in {'page', 'styles', 'table_format'} and isinstance(result.get(key), dict) and isinstance(value, dict):
            merged = result[key].copy()
            if key == 'page' and 'margin_mm' in value:
                for side in ('top', 'bottom', 'left', 'right'):
                    merged.pop('margin_' + side + '_mm', None)
            for subkey, subvalue in value.items():
                if key == 'styles' and isinstance(merged.get(subkey), dict) and isinstance(subvalue, dict):
                    layer = merged[subkey].copy()
                    # One user override must displace conflicting profile defaults.
                    if 'line_spacing_pt' in subvalue:
                        layer.pop('line_spacing_multiple', None)
                    if 'line_spacing_multiple' in subvalue:
                        layer.pop('line_spacing_pt', None)
                    for indent in ('first_line_chars', 'first_line_indent_pt', 'hanging_indent_pt'):
                        if indent in subvalue:
                            for alternate in ('first_line_chars', 'first_line_indent_pt', 'hanging_indent_pt'):
                                if alternate != indent:
                                    layer.pop(alternate, None)
                    merged[subkey] = {**layer, **subvalue}
                else:
                    merged[subkey] = subvalue
            result[key] = merged
        else:
            result[key] = value
    return result
