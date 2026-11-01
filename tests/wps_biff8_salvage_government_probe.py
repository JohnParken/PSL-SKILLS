"""Maintenance-only real government ET attachments: exact-host, bounded, read-only.

Each source URL is explicitly linked from the corresponding official government
publication page. Files are kept in memory, never bundled or printed. This
observation is not independent formula, value, style or WPS producer validation.
"""
import hashlib
import json
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
        'official_page': 'https://nync.zs.gov.cn/zwgk/tzgg/content/post_2641174.html',
        'url': 'https://nync.zs.gov.cn/attachment/0/575/575053/2641174.et',
    },
    {
        'id': 'zhongshan_2026_pollution_project_summary',
        'official_page': 'https://nync.zs.gov.cn/zwgk/tzgg/content/post_2645850.html',
        'url': 'https://nync.zs.gov.cn/attachment/0/576/576489/2645850.et',
    },
    {
        'id': 'zhongshan_2026_performance_target',
        'official_page': 'https://nync.zs.gov.cn/zwgk/tzgg/content/post_2645850.html',
        'url': 'https://nync.zs.gov.cn/attachment/0/576/576490/2645850.et',
    },
    {
        'id': 'shantou_2026_project_application',
        'official_page': 'https://www.shantou.gov.cn/stsswbj/gkmlpt/content/2/2485/post_2485187.html',
        'url': 'https://www.shantou.gov.cn/stsswbj/attachment/0/143/143275/2485187.et',
    },
    {
        'id': 'shantou_2026_project_summary',
        'official_page': 'https://www.shantou.gov.cn/stsswbj/gkmlpt/content/2/2485/post_2485187.html',
        'url': 'https://www.shantou.gov.cn/stsswbj/attachment/0/143/143277/2485187.et',
    },
    {
        'id': 'zhongshan_low_altitude_supply_demand',
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


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'wps' / 'scripts'))
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
                        if normal_cells is not None and salvage_cells is not None:
                            report['saved_cell_values_equal'] = normal_cells == salvage_cells
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
        'all_cell_values_independently_verified': False,
        'results': results,
    }
    print(json.dumps(response, ensure_ascii=False, indent=2))
    # Network availability is measured distinctly from parser support.
    return 0 if response['downloaded'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
