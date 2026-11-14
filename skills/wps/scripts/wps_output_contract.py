"""Write-side suffix/signature checks; input readers retain content-based routing."""
from pathlib import Path


OUTPUT_SUFFIXES = {'rtf': {'.rtf', '.wps'}, 'ms_doc_subset': {'.doc', '.wps'}}
FORMAT_LABELS = {'rtf': 'RTF-stream WPS Compatible Document', 'ms_doc_subset': 'MS-DOC / WPS Binary CFB'}
FIB_NAMES = {'0xc1': 'Word 97', '0xd9': 'Word 2000', '0x101': 'Word 2002', '0x10c': 'Word 2003', '0x112': 'Word 2007'}
DIAGNOSTIC_FIELDS = ('issues', 'diagnostics', 'omitted_regions', 'reading_assessment', 'scope', 'container_issues',
                     'compatibility_mode', 'text_policy', 'field_policy', 'object_policy', 'fields_evaluated', 'objects_decoded',
                     'geometry_issues_truncated')
RTF_EXPORT_PARTIAL_CODES = frozenset({'rtf_destination_skipped', 'rtf_field_cached_result_unverified',
                                     'rtf_merge_geometry_not_reconstructed', 'rtf_merge_covered_content_retained'})


def require_output_path(path, output_format):
    """Reject misleading output names before any file or directory is created."""
    suffixes = OUTPUT_SUFFIXES.get(output_format)
    if suffixes is None or Path(path).suffix.lower() not in suffixes:
        allowed = ', '.join(sorted(suffixes or ()))
        raise ValueError(f'{output_format} output requires {allowed}; changing a suffix does not convert the format')


def readback_summary(payload, returncode=None):
    """Adapt both MS-DOC strings and RTF/OOXML paragraph objects without hiding partial."""
    if not isinstance(payload, dict):
        return {'status': 'readback-error', 'error': 'Reader JSON must be an object', 'returncode': returncode}
    status = payload.get('status')
    result = {key: payload[key] for key in DIAGNOSTIC_FIELDS if key in payload}
    result.update(reader_status=status, format=payload.get('format'), partial=status == 'partial')
    if returncode is not None:
        result['returncode'] = returncode
    if status not in ('ok', 'partial') or (returncode is not None and returncode != (0 if status == 'ok' else 3)):
        result.update(status='readback-error', error=payload.get('message', 'Reader status/exit code does not confirm a readable result'))
        return result
    paragraphs = payload.get('paragraphs')
    if not isinstance(paragraphs, list):
        result.update(status='readback-error', error='Reader paragraphs must be an array')
        return result
    texts = []
    for paragraph in paragraphs:
        text = paragraph.get('text') if isinstance(paragraph, dict) else paragraph
        if not isinstance(text, str):
            result.update(status='readback-error', error='Reader paragraph must be text or an object with text')
            return result
        texts.append(text)
    result.update(status='readback-partial' if status == 'partial' else 'readback-ok',
                  paragraph_count=len(texts), preview=[text[:100] for text in texts[:5]],
                  layout_verification='not_run', full_format_verified=False)
    return result


def inspect_output_bytes(data, path, expected_format=None):
    """Validate a publishable WPS/DOC candidate with the existing bounded reader."""
    from read_wps import ParseError, read_bytes
    container = ('rtf' if data.lstrip().startswith(b'{\\rtf') else 'cfb' if data.startswith(bytes.fromhex('d0cf11e0a1b11ae1'))
                 else 'zip' if data.startswith((b'PK\x03\x04', b'PK\x05\x06')) else 'unknown')
    result = {'valid': False, 'file_size': len(data), 'container': container, 'native_wps': False, 'full_format_verified': False}
    try:
        payload = read_bytes(data, reading_mode='standard')
        actual = payload.get('format')
        result.update(detected_format=actual, output_format=actual, format=FORMAT_LABELS.get(actual, actual),
                      extension_matches=Path(path).suffix.lower() in OUTPUT_SUFFIXES.get(actual, ()), readback=readback_summary(payload))
        if payload.get('status') not in ('ok', 'partial'):
            raise ValueError(payload.get('message', 'Reader rejected candidate'))
        if expected_format is not None and actual != expected_format:
            raise ValueError(f'Expected {expected_format}, detected {actual}')
        require_output_path(path, actual)
        if actual == 'rtf':
            # The legacy DOCX mapper may retain physical cells without certifying
            # merged geometry. Disclose that limitation; never publish recovered
            # truncation/encoding damage. Creation/editing keep their stricter gate.
            issues = payload.get('issues', [])
            if payload['status'] == 'partial' and (
                not issues or payload.get('geometry_issues_truncated') or
                any(item.get('code') not in RTF_EXPORT_PARTIAL_CODES for item in issues)
            ):
                raise ValueError('Candidate requires structural or text recovery; publication stopped')
            result['has_font_table'] = b'\\fonttbl' in data
        if actual == 'ms_doc_subset':
            version = payload.get('fib_base_version')
            result['word_document'] = {'fib': {'wIdent': '0xa5ec', 'nFib': version,
                                               'version_name': FIB_NAMES.get(version, 'unknown')}}
        result.update(valid=True, partial=payload.get('status') == 'partial', output_format=actual,
                      binary_doc=actual == 'ms_doc_subset', extension_matches=True)
    except (ParseError, ValueError, RuntimeError) as exc:
        result.update(error=str(exc), reader_status=getattr(exc, 'status', None))
    return result


def require_output_bytes(data, path, expected_format):
    result = inspect_output_bytes(data, path, expected_format)
    if not result['valid']:
        raise ValueError(result['error'])
    return result
