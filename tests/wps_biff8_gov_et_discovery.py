"""Maintenance-only bounded government ET attachment evidence discovery.

The URLs are direct attachments from official jt.zs.gov.cn publication pages.
Inspect actual content, avoid outputting user/business cell values, and keep all
network I/O outside the runtime WPS Skill. Never change source document bytes.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_FILE_BYTES = 32 * 1024 * 1024
TARGET_HOST = 'jt.zs.gov.cn'
SAMPLES = (
    {'id': 'zs_2026_transport_rental',
     'page': 'https://jt.zs.gov.cn/xxml/zwgk/czxx/zbggxx/content/post_2632463.html',
     'url': 'https://jt.zs.gov.cn/attachment/0/572/572379/2632463.et',
     'sha256': 'fec59f9e22694cba7403bd96505225a45075f7f5c1a9801472e20ac8e39e280a',
     'expected_cells': 32, 'expected_table_style_elements': []},
    {'id': 'zs_2026_vehicle_purchase',
     'page': 'https://jt.zs.gov.cn/xxml/zwgk/czxx/zbggxx/content/post_2603613.html',
     'url': 'https://jt.zs.gov.cn/attachment/0/564/564802/2603613.et',
     'sha256': 'ae032129a2d67b383abc391c17820769537936e8d97487ec2fdf8465cbe2e9b2',
     'expected_cells': 43, 'expected_table_style_elements': [7, 10]},
)


def _fetch(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname != TARGET_HOST or parts.username:
        raise ValueError('Non-government origin rejected')
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0 (BIFF8 source validation; read-only)'})
    with urlopen(request, timeout=18) as reply:
        final = urlsplit(reply.geturl())
        if final.scheme != 'https' or final.hostname != TARGET_HOST:
            raise ValueError('Redirect outside government host')
        stated = reply.headers.get('Content-Length')
        if stated is not None and int(stated) > MAX_FILE_BYTES:
            raise ValueError('Declared size exceeds limit')
        data = reply.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError('Downloaded content exceeds limit')
        return data


def _container_kind(data):
    if data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        return 'CFB_OLE'
    if data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        return 'ZIP_OOXML_CANDIDATE'
    if data.startswith(b'%PDF-'):
        return 'PDF_WRONGLY_NAMED'
    if data.lstrip()[:32].lower().startswith((b'<!doctype html', b'<html')):
        return 'HTML_WRONGLY_NAMED'
    return 'UNKNOWN'


def _codepage_inventory(data):
    # Python -I omits the repository from import search paths.
    scripts = Path(__file__).resolve().parents[1] / 'skills' / 'wps' / 'scripts'
    sys.path.insert(0, str(scripts))
    from wps_access import load_compound
    from wps_biff8 import records, bof
    compound = load_compound(data, 'data')
    names = [x for x in compound.streams
             if len(x) == 1 and x[0].casefold() in ('book', 'workbook')]
    if len(names) != 1:
        raise ValueError('No unique Workbook stream')
    stream = compound.read_stream(names[0])
    items = records(stream)
    bof(items[0], 5)
    pages = []
    counts = {}
    table_style_headers = []
    for offset, kind, payload in items[1:]:
        if kind == 0x000A:
            break
        counts[hex(kind)] = counts.get(hex(kind), 0) + 1
        if kind in (0x088F, 0x0890) and len(table_style_headers) < 8:
            # Non-secret, bounded structural prefix for comparison with MS-XLS:
            # FrtHeader, style flags/count, table-element type/size/index.
            table_style_headers.append({'record_offset': offset, 'record_id': hex(kind),
                                        'payload_bytes': len(payload),
                                        'header_prefix_hex': payload[:24].hex()})
        if kind == 0x0042:
            pages.append({'record_offset': offset,
                          'raw_length': len(payload),
                          'declared_codepage': struct.unpack('<H', payload)[0]
                                               if len(payload) == 2 else None})
    return {'workbook_stream_sha256': hashlib.sha256(stream).hexdigest(),
            'declared_codepages': pages,
            'table_style_record_headers': table_style_headers,
            'global_record_ids': counts,
            'global_record_count': sum(counts.values())}


def _probe(item):
    result = {
        'id': item['id'], 'source_page': item['page'],
        'attachment_url': item['url'], 'website_domain_verified': True,
        'producer_and_version_verified': False, 'effective_format_verified': False}
    try:
        data = _fetch(item['url'])
    except (OSError, ValueError) as exc:
        return {**result, 'download': 'failed', 'reason_type': type(exc).__name__,
                'reason': str(exc)[:160]}
    if hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError('Official attachment hash changed; frozen expectations not applicable')
    kind = _container_kind(data)
    result.update(download='ok', bytes=len(data),
                  sha256=hashlib.sha256(data).hexdigest(), actual_container=kind)
    if kind == 'CFB_OLE':
        try:
            result['inventory'] = _codepage_inventory(data)
        except (OSError, ValueError, struct.error) as exc:
            result['inventory'] = {'status': 'failed',
                                   'reason_type': type(exc).__name__,
                                   'message': str(exc)[:150]}
    scripts = Path(__file__).resolve().parents[1] / 'skills' / 'wps' / 'scripts'
    sys.path.insert(0, str(scripts))
    from read_wps import read_bytes
    try:
        extracted = read_bytes(data)
        result.update(reader_status=extracted.get('status'),
                      format=extracted.get('format'),
                      sheet_count=len(extracted.get('sheets', ())),
                      cell_count=extracted.get('cell_count'),
                      observed_table_style_element_counts=[
                          group['declared_elements']
                          for group in extracted.get('table_style_sources', ())],
                      issue_codes=[issue.get('code') for issue in extracted.get('issues', ())[:12]
                                   if isinstance(issue, dict)])
    except (OSError, ValueError, struct.error) as exc:
        result.update(reader_status='rejected',
                      reader_error_type=type(exc).__name__,
                      reader_error=str(exc)[:160],
                      reader_location=getattr(exc, 'location', {}))
    # A maintenance-only, source-specific assertion. A changed attachment or
    # source format must be reviewed; it cannot silently pass a new baseline.
    if (result.get('reader_status') != 'partial'
            or result.get('actual_container') != 'CFB_OLE'
            or result.get('cell_count') != item['expected_cells']
            or result.get('observed_table_style_element_counts')
               != item['expected_table_style_elements']
            or result.get('inventory', {}).get('declared_codepages')
               != [{'record_offset': 152, 'raw_length': 2, 'declared_codepage': 1200}]):
        result['frozen_subset_acceptance'] = 'mismatch'
    else:
        result['frozen_subset_acceptance'] = 'matched'
    return result


def main():
    items = [_probe(item) for item in SAMPLES]
    report = {'schema_version': 'wps_biff8_public_government_inventory_v1',
              'total': len(items),
              'downloaded': sum(x['download'] == 'ok' for x in items),
              'cfb_files': sum(x.get('actual_container') == 'CFB_OLE' for x in items),
              'supported_reads': sum(x.get('reader_status') in ('ok','partial') for x in items),
              'codepage_evidence_only': True,
              'frozen_subset_matches': sum(x.get('frozen_subset_acceptance') == 'matched' for x in items),
              'complete_native_compatibility_verified': False,
              'samples': items}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['frozen_subset_matches'] == len(SAMPLES) else 1


if __name__ == '__main__':
    raise SystemExit(main())
