"""Offline parser development: bounded real ET Name-record discovery.

Maintenance-only HTTPS fetch of explicitly linked public government .et
attachments; no cell value/name plaintext is logged, stored, executed or
distributed. Uses independent BIFF record framing for Name classification.
The shipped WPS skill never downloads data or requires this script.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 100000
MAX_NAMES = 512
# Original identities first obtained from official pages; never silently
# treat an altered attachment as the same independent validation sample.
PINNED_SHA256 = {
    'zhongshan_2026_grain_form': '9633e7dff8e74afa2662b074b97477925d92114b861a5dd2a117ac622f73175a',
    'zhongshan_2025_seed_application': '81f6898330ec161f934089076f07a70b9ae708ca481c00769e5a9675ad30855a',
    'zhongshan_2025_seed_summary': '6158a191ae9d30e7eaec5d5f0d99ef8f9ffef646c0a2d7b37769f7bf841d0c9d',
    'zhongshan_2025_seed_implementation': 'b8cc3efe264efe792db016e0b319620646853422cc97ba03f15047f774219f66',
    'zhongshan_2026_land_adjustment': 'dacf6889a4c701beaca859e9874a51320f4a8747b075a1b695b59c13eec0a620',
    'zhongshan_2025_potato_registration': '27cb219ebec0a371c6af9ea7ace0520ce0ae2c4e698a193c89ad04cdeb23bc57',
    'shantou_2026_digital_product_form': '068c45ef82b698eee7642803fcc378d828cf9965b05efef3a0ff4d0d0fd52aef',
    'jilin_2026_intangible_heritage_targets': '22fbce711375701fca9713565c7a8b0c6b66929bf5ea4a1a9044d8c953fe6206',
    'hangzhou_2026_vessel_maintenance': '2c97cc505ce3746f4ce13995dae0161baea3594d9600d6ae5d8c8a611569bfc1',
}
APPROVED_HOSTS = frozenset((
    'nync.zs.gov.cn',
    'www.shantou.gov.cn',
    'czt.jl.gov.cn',
    'www.zj.msa.gov.cn',
))
SOURCES = (
    ('zhongshan_2026_grain_form',
     'https://nync.zs.gov.cn/sy/gsl/content/post_2612662.html',
     'https://nync.zs.gov.cn/attachment/0/567/567707/2612662.et'),
    ('zhongshan_2025_seed_application',
     'https://nync.zs.gov.cn/zwgk/snbt/nyscfzzj/content/post_2484249.html',
     'https://nync.zs.gov.cn/attachment/0/528/528552/2484249.et'),
    ('zhongshan_2025_seed_summary',
     'https://nync.zs.gov.cn/zwgk/snbt/nyscfzzj/content/post_2484249.html',
     'https://nync.zs.gov.cn/attachment/0/528/528554/2484249.et'),
    ('zhongshan_2025_seed_implementation',
     'https://nync.zs.gov.cn/zwgk/snbt/nyscfzzj/content/post_2484249.html',
     'https://nync.zs.gov.cn/attachment/0/528/528555/2484249.et'),
    ('zhongshan_2026_land_adjustment',
     'https://nync.zs.gov.cn/sy/snzj/sbtz/content/post_2541220.html',
     'https://nync.zs.gov.cn/attachment/0/545/545445/2541220.et'),
    ('zhongshan_2025_potato_registration',
     'https://nync.zs.gov.cn/gkmlpt/content/2/2567/post_2567413.html',
     'https://nync.zs.gov.cn/attachment/0/553/553445/2567413.et'),
    ('shantou_2026_digital_product_form',
     'https://www.shantou.gov.cn/jxj/zljs/zxqyszhzxcssd/content/post_2504055.html',
     'https://www.shantou.gov.cn/attachment/0/147/147823/2504055.et'),
    ('jilin_2026_intangible_heritage_targets',
     'https://czt.jl.gov.cn/xwfb/tzgg/202606/t20260618_3643692.html',
     'https://czt.jl.gov.cn/xwfb/tzgg/202606/P020260618357278401688.et'),
    ('hangzhou_2026_vessel_maintenance',
     'https://www.zj.msa.gov.cn/HZ/tzgg/202605/t20260528_15029.html',
     'https://www.zj.msa.gov.cn/HZ/tzgg/202605/P020260528583514447101.et'),
)


def _approved(url):
    candidate = urlsplit(url)
    return (candidate.scheme == 'https'
            and candidate.hostname in APPROVED_HOSTS
            and candidate.username is None and candidate.password is None)


def get_source(url):
    if not _approved(url):
        raise ValueError('Unapproved source URL')
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0 (WPS skill format validation)'})
    with urlopen(request, timeout=14) as response:
        if not _approved(response.geturl()):
            raise ValueError('Redirect outside the approved government origin set')
        declared = response.headers.get('Content-Length')
        if declared and int(declared) > MAX_BYTES:
            raise ValueError('HTTP response size exceeds bound')
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('Attachment exceeds 8 MiB bound')
        return content


def biff_globals(content):
    """Independent BIFF8 record framing, before the first globals EOF."""
    if not isinstance(content, bytes) or len(content) > MAX_BYTES:
        raise ValueError('Workbook stream over budget')
    offset = 0
    first = True
    for _ in range(MAX_RECORDS):
        if offset + 4 > len(content):
            raise ValueError('Globals record header truncated or EOF missing')
        record_id, length = struct.unpack_from('<HH', content, offset)
        if length > 8224 or offset + 4 + length > len(content):
            raise ValueError('Globals record payload truncated')
        if first:
            if record_id != 0x0809 or length != 16:
                raise ValueError('Not BIFF8 globals BOF')
            first = False
        payload = content[offset+4:offset+4+length]
        yield offset, record_id, payload
        offset += 4 + length
        if record_id == 0x000A:
            return
    raise ValueError('Globals record budget exceeded')


def inspect_name(payload):
    """Only source-fixed fields and token boundaries. No names or cell values."""
    result = {'record_length': len(payload)}
    if len(payload) < 15:
        return {**result, 'classification': 'truncated_fixed_header'}
    flags, key, chars, formula_length, reserved, itab = struct.unpack_from('<HBBHHH', payload, 0)
    result.update(
        macro_flags=bool(flags & 0x000E),
        hidden=bool(flags & 0x0001),
        builtin=bool(flags & 0x0020),
        raw_flag_bits=hex(flags),
        scope_ordinal=itab,
        name_length_chars=chars,
        formula_length=formula_length,
        name_uses_utf16=payload[14] == 1,
        name_header_options_raw=payload[14],
        reserved_fields_zero=reserved == 0 and payload[10:14] == bytes(4),
        shortcut_zero=key == 0,
    )
    if payload[14] not in (0, 1):
        return {**result, 'classification': 'other_unicode_string_flags'}
    end = 15 + chars * (2 if payload[14] else 1)
    if end + formula_length != len(payload):
        return {**result, 'classification': 'variable_tail_or_length_mismatch'}
    builtin = payload[15] if (flags & 0x0020) and chars == 1 and end == 16 else None
    result['builtin_id_raw'] = hex(builtin) if builtin is not None else None
    formula = payload[end:]
    result['first_formula_token'] = hex(formula[0]) if formula else None
    if len(formula) == 11 and formula[0] in (0x3b,0x5b,0x7b):
        form = 'one_PtgArea3d'
    elif (len(formula) == 23 and formula[0] in (0x3b,0x5b,0x7b)
          and formula[11] in (0x3b,0x5b,0x7b) and formula[22] == 0x10):
        form = 'two_PtgArea3d_plus_PtgUnion'
    else:
        form = 'unrecognized_or_complex_token_sequence'
    result['formula_framing'] = form
    if builtin == 0x07:
        kind = 'Print_Titles'
    elif builtin == 0x0D:
        kind = '_FilterDatabase'
    elif builtin == 0x06:
        kind = 'Print_Area'
    elif builtin is not None:
        kind = 'other_builtin_name'
    else:
        kind = 'ordinary_or_external_name'
    result['classification'] = kind
    result['formula_executed'] = False
    result['external_reference_validated'] = False
    return result


def parse_attempt(compound):
    from wps_biff8 import read_container
    try:
        result = read_container(compound, compatibility='data')
        names = [entry for entry in result.get('uninterpreted_metadata', [])
                 if entry.get('record_id') == '0x18']
        return {'status': result.get('status'),
                'sheets': len(result.get('sheets', [])),
                'cells': result.get('cell_count'),
                'name_retained': len(names),
                'name_classes': sorted({entry['name'] for entry in names}),
                'partial_diagnostics': sorted({item.get('code')
                                                for item in result.get('issues', [])
                                                if isinstance(item, dict) and item.get('code')})}
    except (OSError, ValueError, OverflowError) as exc:
        return {'status': 'rejected', 'error_class': type(exc).__name__,
                'error_message': str(exc)[:120]}


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/et/scripts'))
    from wps_access import load_compound

    observations = []
    for name, official_page, direct_url in SOURCES:
        item = {'id': name, 'official_page': official_page, 'attachment': direct_url}
        try:
            data = get_source(direct_url)
            item['download_status'] = 'downloaded'
            item['byte_count'] = len(data)
            item['sha256'] = hashlib.sha256(data).hexdigest()
            item['matches_pinned_sha256'] = (
                item['sha256'] == PINNED_SHA256[name])
            if not item['matches_pinned_sha256']:
                item['parse_scope'] = 'changed_public_original_not_tested'
                observations.append(item)
                continue
            item['magic'] = ('CFB_OLE' if data.startswith(bytes.fromhex('d0cf11e0a1b11ae1'))
                             else 'ZIP' if data.startswith(b'PK\x03\x04') else 'other')
            if item['magic'] == 'CFB_OLE':
                try:
                    compound = load_compound(data, 'data')
                    streams = [entry for entry in compound.streams if
                               len(entry) == 1 and entry[0].casefold() in ('book', 'workbook')]
                    if len(streams) != 1:
                        item['parse_scope'] = 'not_one_root_workbook'
                    else:
                        raw_workbook = compound.read_stream(streams[0])
                        named = []
                        for offset, code, payload in biff_globals(raw_workbook):
                            if code == 0x0018:
                                if len(named) >= MAX_NAMES:
                                    raise ValueError('Independent Name source budget exceeded')
                                named.append({'offset': offset, **inspect_name(payload)})
                        item['name_count'] = len(named)
                        item['name_sources'] = named
                        item['parser'] = parse_attempt(compound)
                        item['parse_scope'] = 'BIFF8_candidate'
                except (ValueError, OSError, OverflowError) as exc:
                    item['parse_scope'] = 'CFB_or_BIFF_rejected'
                    item['parse_error_type'] = type(exc).__name__
                    item['parse_error'] = str(exc)[:120]
        except (ValueError, OSError, TimeoutError, OverflowError) as exc:
            item['download_status'] = 'download_failed'
            item['download_error_type'] = type(exc).__name__
            item['download_error'] = str(exc)[:120]
        observations.append(item)

    summary = {'schema_version': 'biff8_name_real_source_discovery_v1',
               'probe_kind': 'nonexecuting independent bounded BIFF8 record observation',
               'candidate_count': len(observations),
               'downloaded': sum(item.get('download_status') == 'downloaded'
                                 for item in observations),
               'cfb_count': sum(item.get('magic') == 'CFB_OLE' for item in observations),
               'pinned_sha256_matches': sum(item.get('matches_pinned_sha256') is True
                                             for item in observations),
               'source_sha256_mismatches': sum(item.get('matches_pinned_sha256') is False
                                               for item in observations),
               'files_with_name_records': sum(item.get('name_count', 0) > 0
                                              for item in observations),
               'files_with_Print_Titles': sum(any(
                   record.get('classification') == 'Print_Titles'
                   for record in item.get('name_sources', [])) for item in observations),
               'files_with_FilterDatabase': sum(any(
                   record.get('classification') == '_FilterDatabase'
                   for record in item.get('name_sources', [])) for item in observations),
               'files_with_PtgUnion': sum(any(
                   record.get('formula_framing') == 'two_PtgArea3d_plus_PtgUnion'
                   for record in item.get('name_sources', [])) for item in observations),
               'all_name_text_and_cell_values_omitted': True,
               'producer_software_verified': False,
               'results': observations}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary['downloaded'] and not summary['source_sha256_mismatches'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
