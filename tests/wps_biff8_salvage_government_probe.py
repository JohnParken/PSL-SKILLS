"""Maintenance-only real government ET attachments: exact-host, bounded, read-only.

Each source URL is explicitly linked from the corresponding official government
publication page. Files are kept in memory, never bundled or printed. This
observation is not independent formula, value, style or WPS producer validation.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
ALLOWED_HOSTS = frozenset({
    'nync.zs.gov.cn', 'www.shantou.gov.cn',
    'www.zs.gov.cn', 'sf.sz.gov.cn',
})
SAMPLES = [
    {
        'id': 'zhongshan_2027_area_adjustment',
        'expected_sha256': '472f8852375710b64539640868bdee9f0c6cf72d72e6e625406c57462b4089f0',
        'official_page': 'https://nync.zs.gov.cn/zwgk/tzgg/content/post_2641174.html',
        'url': 'https://nync.zs.gov.cn/attachment/0/575/575053/2641174.et',
    },
    {
        'id': 'zhongshan_2026_pollution_project_summary',
        'expected_sha256': '35fb708c95f2f957b72aad5514827b9a29a6fd16d994f42e51e6015288302d89',
        'official_page': 'https://nync.zs.gov.cn/zwgk/tzgg/content/post_2645850.html',
        'url': 'https://nync.zs.gov.cn/attachment/0/576/576489/2645850.et',
    },
    {
        'id': 'zhongshan_2026_performance_target',
        'expected_sha256': 'ea9e7bca553415d632dd931726f2b88ae7a474b1f6b62df9facb54c4ce358690',
        'official_page': 'https://nync.zs.gov.cn/zwgk/tzgg/content/post_2645850.html',
        'url': 'https://nync.zs.gov.cn/attachment/0/576/576490/2645850.et',
    },
    {
        'id': 'shantou_2026_project_application',
        'expected_sha256': 'c4e9aa6284f25465e875f9ead6f23211b5e575f87e025abba69234406debecfd',
        'official_page': 'https://www.shantou.gov.cn/stsswbj/gkmlpt/content/2/2485/post_2485187.html',
        'url': 'https://www.shantou.gov.cn/stsswbj/attachment/0/143/143275/2485187.et',
    },
    {
        'id': 'shantou_2026_project_summary',
        'expected_sha256': 'ff0408b950ce22f598196cc342fce64b08fbd7ba3d596c8fbdaed6ee99881ac2',
        'official_page': 'https://www.shantou.gov.cn/stsswbj/gkmlpt/content/2/2485/post_2485187.html',
        'url': 'https://www.shantou.gov.cn/stsswbj/attachment/0/143/143277/2485187.et',
    },
    {
        'id': 'zhongshan_low_altitude_supply_demand',
        'expected_sha256': '2ccc6cca516b5936a3330d634536ed3fab71cf762616cf018b5b42ff84161cad',
        'official_page': 'https://www.zs.gov.cn/zsfgj/gkmlpt/content/2/2632/post_2632406.html',
        'url': 'https://www.zs.gov.cn/zsfgj/attachment/0/572/572365/2632406.et',
    },
    {
        'id': 'shenzhen_mediation_application_form',
        'official_page': 'https://sf.sz.gov.cn/xxgk/xxgkml/gsgg/content/post_12990789.html',
        'url': 'https://sf.sz.gov.cn/attachment/1/1750/1750176/12990789.et',
    },
]


def _approved(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.hostname in ALLOWED_HOSTS
            and not parts.username and not parts.password)


def _download(url):
    if not _approved(url):
        raise ValueError('Unapproved government download URL')
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0 (read-only ET format audit)'})
    with urlopen(request, timeout=16) as response:
        if not _approved(response.geturl()):
            raise ValueError('Attachment redirects outside official host allowlist')
        length = response.headers.get('Content-Length')
        if length and int(length) > MAX_ATTACHMENT_BYTES:
            raise ValueError('Declared response size exceeds 16MiB budget')
        data = response.read(MAX_ATTACHMENT_BYTES + 1)
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError('Downloaded file exceeds 16MiB budget')
        return data


def _magic(data):
    if data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        return 'CFB_OLE'
    if data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        return 'ZIP_OOXML_CANDIDATE'
    if data.startswith(b'%PDF-'):
        return 'PDF'
    if data.lstrip()[:24].lower().startswith((b'<!doctype html', b'<html')):
        return 'HTML_NOT_ET'
    return 'UNKNOWN'


def _read_summary(container, salvage):
    from wps_biff8 import read_container
    try:
        result = read_container(container, compatibility='data',
                                salvage_unknown_metadata=salvage)
        cells = {sheet['name']: sheet['cells'] for sheet in result['sheets']}
        return ({'status': result['status'], 'cell_count': result['cell_count'],
                 'sheet_count': len(result['sheets']),
                 'formula_cache_count': sum(
                     bool(cell.get('formula')) for sheet in result['sheets']
                     for cell in sheet['cells'].values()),
                 'ordinary_static_name_count': sum(
                     x.get('name') == 'OrdinaryStaticInternalAreaName'
                     for x in result.get('uninterpreted_metadata', [])),
                 'ordinary_two_area_union_count': sum(
                     x.get('name') == 'OrdinaryStaticInternalAreaName'
                     and x.get('structure', {}).get('ptg_union_raw_present') is True
                     for x in result.get('uninterpreted_metadata', [])),
                 'print_titles_static_count': sum(
                     x.get('name') == 'PrintTitlesStatic'
                     for x in result.get('uninterpreted_metadata', [])),
                 'filter_database_static_count': sum(
                     x.get('name') == 'FilterDatabaseStatic'
                     for x in result.get('uninterpreted_metadata', [])),
                 'issue_codes': sorted({item.get('code') for item in result.get('issues', [])
                                        if isinstance(item, dict) and item.get('code')}),
                 'salvage_unknown_record_count': result.get('salvage_unknown_record_count'),
                 'cell_value_coverage': result.get('cell_value_coverage'),
                 'omissions_affect_cell_values': result.get('omissions_affect_cell_values')},
                cells)
    except (OSError, ValueError, OverflowError) as exc:
        return ({'status': 'rejected',
                 'error_type': type(exc).__name__,
                 'error_message': str(exc)[:150],
                 'error_location': getattr(exc, 'location', {})}, None)


def _record_header_evidence(container, stream_name, location):
    """One independently bounded BIFF Name header; never print the name text.

    The first 14 Name payload bytes are header fields, not the variable-length
    user-defined name or formula expression. Retain only structure and digest.
    """
    if not isinstance(location, dict):
        return None
    position = location.get('record_offset')
    if type(position) is not int or position < 0:
        return None
    data = container.read_stream(stream_name)
    if position + 4 > len(data):
        return {'status': 'record_outside_source'}
    identifier, length = struct.unpack_from('<HH', data, position)
    if identifier != 0x0018 or length > 8224 or position + 4 + length > len(data):
        return {'status': 'not_bounded_name_record'}
    raw = data[position+4:position+4+length]
    layout = {'status':'not_yet_bounded'}
    if len(raw) >= 15:
        flags, key, name_length, formula_length, reserved, sheet = struct.unpack_from(
            '<HBBHHH', raw, 0)
        name_options = raw[14]
        if name_options in (0, 1):
            name_byte_count = name_length * (2 if name_options else 1)
            end_name = 15 + name_byte_count
            if end_name + formula_length == len(raw):
                formula = raw[end_name:]
                layout = {
                    'status':'fixed_layout_complete',
                    'grbit_raw':flags,
                    'macro_flag_set':bool(flags & 0x000e),
                    'name_character_count':name_length,
                    'utf16_name_flag':bool(name_options),
                    'formula_length':formula_length,
                    'local_sheet_ordinal':sheet,
                    'reserved_fields_zero':reserved == 0 and raw[10:14] == bytes(4),
                    'formula_first_token_raw':hex(formula[0]) if formula else None,
                    'formula_token_11_byte_area3d_candidate':
                        len(formula) == 11 and formula[0] in (0x3b, 0x5b, 0x7b),
                    'formula_ref_index_raw':
                        struct.unpack_from('<H',formula,1)[0] if len(formula) == 11
                        and formula[0] in (0x3b, 0x5b, 0x7b) else None,
                    'name_identifier_like': (
                        (lambda value: (bool(value) and
                            (value[0].isalpha() or value[0] == '_') and
                            all(char.isalnum() or char in '._' for char in value)))(
                                raw[15:end_name].decode(
                                    'utf-16le' if name_options else 'latin1'))
                        if name_length else False),
                    'relative_coordinate_flags_clear': (
                        not (struct.unpack_from('<H',formula,7)[0] & 0xC000
                             or struct.unpack_from('<H',formula,9)[0] & 0xC000)
                        if len(formula) == 11 and formula[0] in (0x3b, 0x5b, 0x7b)
                        else None),
                }
    # The name's actual Unicode text and the formula's full bytes are never
    # logged; output only bounded arithmetic and an independent hash.
    return {'status': 'bounded_source_name_record',
            'record_offset': position,
            'record_id': '0x18',
            'payload_length': length,
            'first_14_header_bytes_hex': raw[:14].hex(),
            'record_payload_sha256': hashlib.sha256(raw).hexdigest(),
            'bounded_name_layout':layout,
            'complete_name_or_formula_text_read': False,
            'external_reference_semantics_verified': False,
            'macro_semantics_verified': False}


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from wps_biff8_name_oracle import count_known_cell_records
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'et' / 'scripts'))
    from wps_access import load_compound
    results = []
    for sample in SAMPLES:
        report = {'id': sample['id'], 'official_page': sample['official_page'],
                  'official_attachment_url': sample['url'],
                  'producer_software_verified': False,
                  'full_semantic_content_verified': False}
        try:
            data = _download(sample['url'])
            report.update(download='success', bytes=len(data),
                          sha256=hashlib.sha256(data).hexdigest(),
                          magic=_magic(data))
            expected = sample.get('expected_sha256')
            if expected:
                report['source_sha256_matches_pinned'] = (
                    report['sha256'] == expected)
                if not report['source_sha256_matches_pinned']:
                    report['read_scope'] = 'source_changed_from_pinned'
                    results.append(report)
                    continue
            if report['magic'] == 'CFB_OLE':
                try:
                    compound = load_compound(data, 'data')
                    candidates = [p for p in compound.streams
                                  if len(p) == 1 and p[0].lower() in ('book', 'workbook')]
                    if len(candidates) != 1:
                        report['read_scope'] = 'not_single_workbook_stream'
                    else:
                        normal, normal_cells = _read_summary(compound, False)
                        salvage, salvage_cells = _read_summary(compound, True)
                        report.update(read_scope='biff8_candidate',
                                      normal=normal, salvage=salvage)
                        if sample['id'] == 'zhongshan_low_altitude_supply_demand':
                            report['name_record_source_evidence'] = (
                                _record_header_evidence(
                                    compound, candidates[0], {'record_offset':13817}))
                            try:
                                census=count_known_cell_records(
                                    compound.read_stream(candidates[0]))
                                report['independent_known_record_census']=census
                                report['independent_census_matches_reader_count']=(
                                    normal.get('status') in ('partial','ok') and
                                    normal.get('cell_count') == census['known_record_cell_count'])
                            except ValueError as exc:
                                report['independent_census_error_type'] = type(exc).__name__
                                report['independent_census_error'] = str(exc)[:130]
                        if normal_cells is not None and salvage_cells is not None:
                            report['saved_cell_values_equal'] = normal_cells == salvage_cells
                        if sample['id'] == 'zhongshan_low_altitude_supply_demand':
                            # The exact pinned file failed on Name 0x18 in
                            # previous production-compatible builds. This is a
                            # mandatory regression gate only after SHA matches:
                            # two sheets, 126 known cell source records, one
                            # non-executed ordinary static internal name.
                            evidence=report.get('name_record_source_evidence', {})
                            layout=evidence.get('bounded_name_layout', {})
                            report['historical_name_failure_now_readable'] = (
                                normal.get('status') == 'partial'
                                and normal.get('cell_count') == 126
                                and normal.get('sheet_count') == 2
                                and normal.get('ordinary_static_name_count') == 1
                                and salvage.get('status') == 'partial'
                                and report.get('saved_cell_values_equal') is True
                                and report.get('independent_census_matches_reader_count') is True
                                and evidence.get('record_payload_sha256') ==
                                    '40161c6c8cc4728f9ae56568190a83b2420973f02d997371acdb208981927167'
                                and layout.get('formula_first_token_raw') == '0x3b'
                                and layout.get('relative_coordinate_flags_clear') is True
                                and layout.get('macro_flag_set') is False
                                and layout.get('name_identifier_like') is True)
                            if not report['historical_name_failure_now_readable']:
                                raise ValueError('Pinned real nonmacro static Name acceptance or '
                                                 'independent cell census regressed')
                        report['newly_recovered_rejection'] = (
                            normal['status'] == 'rejected'
                            and salvage['status'] in ('ok', 'partial'))
                except (ValueError, OSError) as exc:
                    report['read_scope'] = 'container_rejected'
                    report['container_error_type'] = type(exc).__name__
                    report['container_error'] = str(exc)[:150]
            else:
                report['read_scope'] = 'not_CFB_BIFF8'
        except (ValueError, OSError, TimeoutError, OverflowError) as exc:
            report.update(download='failed', network_error_type=type(exc).__name__,
                          network_error=str(exc)[:140])
        results.append(report)
    response = {
        'schema_version': 'government_et_best_effort_probe_v1',
        'scope': 'real official ET links, format and limited parser diagnostics only',
        'candidate_count': len(results),
        'downloaded': sum(x.get('download') == 'success' for x in results),
        'cfb_count': sum(x.get('magic') == 'CFB_OLE' for x in results),
        'normal_readable': sum(x.get('normal', {}).get('status') in ('ok', 'partial')
                               for x in results),
        'salvage_readable': sum(x.get('salvage', {}).get('status') in ('ok', 'partial')
                                for x in results),
        'newly_recovered_rejections': sum(x.get('newly_recovered_rejection') is True
                                           for x in results),
        'historical_name_rejection_now_readable': sum(
            x.get('historical_name_failure_now_readable') is True for x in results),
        'all_cell_values_independently_verified': False,
        'results': results,
    }
    target = next((x for x in results
                   if x['id'] == 'zhongshan_low_altitude_supply_demand'), None)
    target_accepted = bool(
        target and target.get('download') == 'success'
        and target.get('source_sha256_matches_pinned') is True
        and target.get('historical_name_failure_now_readable') is True)
    response['pinned_real_static_name_gate'] = (
        'passed' if target_accepted else
        'not_run_download_failed' if target and target.get('download') == 'failed'
        else 'failed')
    print(json.dumps(response, ensure_ascii=False, indent=2))
    # Fail CI if this pinned target cannot be downloaded or if it regresses.
    # Other optional sources retain distinct download/parser diagnostics.
    return 0 if target_accepted else 1


if __name__ == '__main__':
    raise SystemExit(main())
