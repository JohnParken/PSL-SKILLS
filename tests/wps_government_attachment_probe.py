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
        'id': 'shenzhen_finance_request',
        'page': 'https://szfb.sz.gov.cn/gkmlpt/mguide',
        'url': 'https://szfb.sz.gov.cn/attachment/1/1661/1661793/12555364.wps',
        'anchors': ['政府信息公开', '申请'],
    },
]


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
                           'error_status': getattr(exc, 'status', 'unknown')})
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
                    'independent_paragraph_count': independent['paragraph_count']}
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
    report = {
        'schema_version': 'wps_gov_attachment_probe_v1',
        'checked_by': 'maintenance_ci_only',
        'sample_count': len(findings),
        'downloaded': sum(r.get('retrieval') == 'downloaded' for r in findings),
        'cfb_count': sum(r.get('magic') == 'CFB_OLE' for r in findings),
        'ms_doc_binary_count': sum(r.get('ms_doc_binary') is True for r in findings),
        'successful_partial_or_full': sum(r.get('read_status') in ('ok','partial') for r in findings),
        'independent_story_matches': sum(r.get('independent_story_comparison', {}).get('status') == 'matched' for r in findings),
        'full_format_verified': False,
        'native_wps_producer_verified': False,
        'real_full_text_semantic_acceptance': False,
        'samples': findings,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    mismatches = any(r.get('independent_story_comparison', {}).get('status') == 'mismatch' for r in findings)
    return 0 if report['downloaded'] and not mismatches else 1


if __name__ == '__main__':
    raise SystemExit(main())
