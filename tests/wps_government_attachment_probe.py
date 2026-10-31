"""Maintenance-only inventory of government-published .wps attachments.

All URLs below were followed from the corresponding government website pages.
Downloads are restricted to HTTPS government domains, bounded, never executed,
and never bundled in the runtime WPS Skill or printed as document text.
This proves official website publication, not which editor created the bytes.
"""
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024
TIMEOUT_SECONDS = 18

SAMPLES = [
    {
        'id': 'luohu_contract',
        'page': 'https://www.szlh.gov.cn/lhrlzyj/gkmlpt/content/10/10445/post_10445513.html',
        'url': 'https://www.szlh.gov.cn/lhrlzyj/attachment/1/1249/1249495/10445513.wps',
        'anchors': ['劳动合同'],
    },
    {
        'id': 'luohu_simple_contract',
        'page': 'https://www.szlh.gov.cn/lhrlzyj/gkmlpt/content/10/10445/post_10445513.html',
        'url': 'https://www.szlh.gov.cn/lhrlzyj/attachment/1/1249/1249497/10445513.wps',
        'anchors': ['劳动合同'],
    },
    {
        'id': 'zhongshan_construction_form',
        'page': 'https://jsj.zs.gov.cn/gkmlpt/content/2/2656/post_2656565.html',
        'url': 'https://jsj.zs.gov.cn/attachment/0/579/579834/2656565.wps',
        'anchors': ['智能建造', '申报表'],
    },
    {
        'id': 'zhongshan_construction_summary',
        'page': 'https://jsj.zs.gov.cn/gkmlpt/content/2/2656/post_2656565.html',
        'url': 'https://jsj.zs.gov.cn/attachment/0/579/579835/2656565.wps',
        'anchors': ['汇总表'],
    },
    {
        'id': 'zhongshan_environment_disclosure',
        'page': 'https://zsepb.zs.gov.cn/gkmlpt/content/2/2629/post_2629998.html',
        'url': 'https://zsepb.zs.gov.cn/attachment/0/571/571979/2629998.wps',
        'anchors': ['清洁生产', '信息公开'],
    },
    {
        'id': 'zhongshan_construction_supporting_material',
        'page': 'https://jsj.zs.gov.cn/gkmlpt/content/2/2656/post_2656565.html',
        'url': 'https://jsj.zs.gov.cn/attachment/0/579/579836/2656565.wps',
        'anchors': ['佐证材料'],
    },
    {
        'id': 'zhongshan_environment_company_list',
        'page': 'https://zsepb.zs.gov.cn/gkmlpt/content/2/2629/post_2629998.html',
        'url': 'https://zsepb.zs.gov.cn/attachment/0/571/571978/2629998.wps',
        'anchors': ['清洁生产'],
    },
    {
        'id': 'zhongshan_environment_commitment',
        'page': 'https://zsepb.zs.gov.cn/gkmlpt/content/2/2629/post_2629998.html',
        'url': 'https://zsepb.zs.gov.cn/attachment/0/571/571980/2629998.wps',
        'anchors': ['承诺'],
    },
    {
        'id': 'zhongshan_environment_authorization',
        'page': 'https://zsepb.zs.gov.cn/gkmlpt/content/2/2629/post_2629998.html',
        'url': 'https://zsepb.zs.gov.cn/attachment/0/571/571981/2629998.wps',
        'anchors': ['委托'],
    },
    {
        'id': 'shenzhen_finance_request',
        'page': 'https://szfb.sz.gov.cn/gkmlpt/mguide',
        'url': 'https://szfb.sz.gov.cn/attachment/1/1661/1661793/12555364.wps',
        'anchors': ['政府信息公开', '申请'],
    },
]


# Frozen byte identities and established *subset* behavior. These are
# regression expectations, not WPS Office independent visual-format oracles.
PINNED_CFB_BASELINES = {
    'zhongshan_construction_form': (
        'ed84dcf9415e2d388d4e335dbcf301f2173137928bd0ecfee4f3f5c6e20895ee', 10),
    'zhongshan_construction_summary': (
        '7c21746d3d3d022aafd4aee80da0504f1f2b94663eedaf872c815c779d1404d8', 3),
    'zhongshan_environment_disclosure': (
        '13564403b0b1b4ebc3911fb87e94b526f88f2db0ccfeb43fa513d87a85b5a87e', 40),
    'zhongshan_construction_supporting_material': (
        '3ccedb9e26d89410796508531912169559c0a0258303a085075e69829762375c', 36),
    'zhongshan_environment_company_list': (
        '28b509c9032dc45cf2c14f55075f53abaff5989ad9c5fe740dc06771098b2be6', 4),
    'zhongshan_environment_commitment': (
        '4149d510b0e41c28b439b58880112ea42c821f13cb29ddddc2df9566de7ebb70', 12),
    'zhongshan_environment_authorization': (
        '29b2f1683b2ae97c9c6b9a2febbca0d36e6436f339b59aacf776dedc66308ba4', 10),
}


def _baseline_status(item):
    baseline = PINNED_CFB_BASELINES.get(item['id'])
    if baseline is None:
        return 'not_in_frozen_corpus'
    if item.get('retrieval') != 'downloaded':
        return 'not_checked_download_unavailable'
    expected_sha, paragraphs = baseline
    if item.get('sha256') != expected_sha or item.get('magic') != 'CFB_OLE':
        return 'mismatch_file_identity'
    if (item.get('read_status') != 'partial'
            or item.get('format') != 'ms_doc_subset'
            or item.get('paragraph_count') != paragraphs):
        return 'mismatch_reader_contract'
    if item['id'] == 'zhongshan_construction_supporting_material':
        if 'drawn_shape_anchor_not_rendered' not in item.get('diagnostic_codes', ()):
            return 'mismatch_shape_diagnostic'
    elif (item.get('independent_story_comparison', {}).get('status') != 'matched'
          or not all(item.get('anchor_checks', {}).values())):
        return 'mismatch_independent_story_or_anchor'
    return 'verified_subset_regression'


def _official_https(url):
    info = urlsplit(url)
    host = info.hostname or ''
    return info.scheme == 'https' and (host == 'gov.cn' or host.endswith('.gov.cn'))


def _fetch_bounded(url):
    if not _official_https(url):
        raise ValueError('not_official_https')
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; archival validation; no document execution)'})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        if not _official_https(response.geturl()):
            raise ValueError('redirect_outside_official_domains')
        length_header = response.headers.get('Content-Length')
        if length_header and int(length_header) > MAX_ATTACHMENT_BYTES:
            raise ValueError('content_length_exceeds_budget')
        content = response.read(MAX_ATTACHMENT_BYTES + 1)
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ValueError('body_exceeds_budget')
        return content, response.headers.get('Content-Type', '')


def _magic(data):
    if data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        return 'CFB_OLE'
    if data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        return 'ZIP_OOXML_CANDIDATE'
    if data.startswith(b'%PDF-'):
        return 'PDF_MISLABELLED'
    if data.lstrip().lower().startswith((b'<!doctype html', b'<html', b'<?xml')):
        return 'WEB_OR_XML_CONTENT'
    return 'UNKNOWN'


def inspect(item, read_bytes):
    result = {
        'id': item['id'], 'official_page': item['page'],
        'official_attachment_url': item['url'],
        'publication_provenance': 'government_site_link_verified',
        'producer_software_verified': False, 'full_format_verified': False,
    }
    try:
        data, mimetype = _fetch_bounded(item['url'])
        result.update({'retrieval': 'downloaded', 'bytes': len(data),
                       'sha256': hashlib.sha256(data).hexdigest(),
                       'magic': _magic(data), 'content_type': mimetype})
        if result['magic'] not in ('CFB_OLE', 'ZIP_OOXML_CANDIDATE'):
            result['read_status'] = 'not_a_supported_container'
            return result
        try:
            doc = read_bytes(data, compatibility='data', object_policy='placeholder')
        except Exception as exc:
            result.update({'read_status': 'reader_rejected',
                           'error_type': type(exc).__name__,
                           'error_status': getattr(exc, 'status', 'unknown'),
                           'error_message': str(exc)[:160],
                           'error_location': {key: val for key, val in getattr(exc, 'location', {}).items()
                                              if key in ('stream', 'offset', 'stage') and isinstance(val, (str, int))}})
            return result
        status, fmt = doc.get('status'), doc.get('format')
        body = doc.get('text')
        result.update({'read_status': status, 'format': fmt,
                       'ms_doc_binary': result['magic'] == 'CFB_OLE' and fmt == 'ms_doc_subset',
                       'paragraph_count': len(doc.get('paragraphs', [])),
                       'anchor_checks': {anchor: anchor in body if isinstance(body, str) else False
                                         for anchor in item['anchors']},
                       'diagnostic_codes': [issue.get('code') for issue in doc.get('issues', [])[:15]
                                            if isinstance(issue, dict)]})
        if result['ms_doc_binary'] and isinstance(body, str):
            # Independent FIB/CLX/Pcdt algorithm; reuse only the bounded CFB
            # container implementation, never the target MS-DOC text parser.
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from wps_public_story_oracle import independently_decode_main
                independent = independently_decode_main(
                    data, Path(__file__).resolve().parents[1] / 'skills' / 'wps' / 'scripts')
                text_match = hashlib.sha256(body.encode('utf-8')).hexdigest() == independent['text_sha256']
                paragraphs = doc.get('paragraphs', [])
                paragraph_hash = hashlib.sha256(json.dumps(
                    paragraphs, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
                paragraphs_match = paragraph_hash == independent['paragraphs_sha256']
                result['independent_story_comparison'] = {
                    'status': 'matched' if text_match and paragraphs_match else 'mismatch',
                    'text_equal': text_match, 'paragraphs_equal': paragraphs_match,
                    'main_units': independent['main_units'],
                    'piece_count': independent['piece_count'],
                    'independent_paragraph_count': len(independent['paragraphs'])}
            except (ValueError, KeyError, UnicodeError, IndexError, OSError) as exc:
                result['independent_story_comparison'] = {
                    'status': 'not_supported_by_independent_checker',
                    'error_type': type(exc).__name__,
                    'error_detail': str(exc)[:100]}

    except Exception as exc:
        result.update({'retrieval': 'failed', 'error_type': type(exc).__name__,
                       'error_summary': str(exc)[:120]})
    return result


def main():
    scripts_dir = Path(__file__).resolve().parents[1] / 'skills' / 'wps' / 'scripts'
    sys.path.insert(0, str(scripts_dir))
    from read_wps import read_bytes
    findings = [inspect(item, read_bytes) for item in SAMPLES]
    for finding in findings:
        finding['frozen_baseline_status'] = _baseline_status(finding)
    report = {
        'schema_version': 'wps_gov_attachment_probe_v1',
        'checked_by': 'maintenance_ci_only',
        'sample_count': len(findings),
        'downloaded': sum(r.get('retrieval') == 'downloaded' for r in findings),
        'cfb_count': sum(r.get('magic') == 'CFB_OLE' for r in findings),
        'ms_doc_binary_count': sum(r.get('ms_doc_binary') is True for r in findings),
        'successful_partial_or_full': sum(r.get('read_status') in ('ok','partial') for r in findings),
        'independent_story_matches': sum(r.get('independent_story_comparison', {}).get('status') == 'matched' for r in findings),
        'frozen_baseline_total': len(PINNED_CFB_BASELINES),
        'frozen_baseline_verified': sum(r['frozen_baseline_status'] == 'verified_subset_regression' for r in findings),
        'frozen_baseline_unavailable': sum(r['frozen_baseline_status'] == 'not_checked_download_unavailable' for r in findings),
        'frozen_baseline_mismatches': sum(r['frozen_baseline_status'].startswith('mismatch_') for r in findings),
        'full_format_verified': False,
        'native_wps_producer_verified': False,
        'real_full_text_semantic_acceptance': False,
        'samples': findings,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    mismatches = (any(r.get('independent_story_comparison', {}).get('status') == 'mismatch'
                      for r in findings) or report['frozen_baseline_mismatches'])
    # A network failure remains explicit/not-checked, never a false baseline pass.
    # A successful job requires at least one real frozen file to have been checked.
    return 0 if report['frozen_baseline_verified'] > 0 and not mismatches else 1


if __name__ == '__main__':
    raise SystemExit(main())
