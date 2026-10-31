"""Maintenance-only validation of public BOC/CCB/ICBC office attachments.

Sources originate from directly linked official bank pages. HTTPS downloads only
to exact allowlisted bank hostnames; bounded 32 MiB; no output of document body.
No runtime dependency or networking is added to the WPS Skill.
"""
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_BYTES = 32 * 1024 * 1024
BANK_HOSTS = frozenset({
    'www.bankofchina.com', 'www.boc.cn', 'pic.bankofchina.com',
    'www1.ccb.com', 'www.ccb.com', 'job2.ccb.com',
    'media.icbc.com.cn', 'www.icbc.com.cn', 'www.sh.icbc.com.cn',
})
SAMPLES = [
    {
        'id': 'boc_pension_2025_confirm',
        'bank': 'BOC',
        'page': 'https://www.bankofchina.com/cbservice/bi2/202510/t20251009_25521263.html',
        'url': 'https://www.bankofchina.com/cbservice/bi2/202510/P020251009612975299059.docx',
        'expected_suffix': '.docx',
        'anchors': ['参评', '确认'],
    },
    {
        'id': 'boc_career_pension_2025_confirm',
        'bank': 'BOC',
        'page': 'https://www.bankofchina.com/cbservice/bi2/202511/t20251114_25569711.html',
        'url': 'https://www.bankofchina.com/cbservice/bi2/202511/P020251114543200001855.docx',
        'expected_suffix': '.docx',
        'anchors': ['报名', '确认'],
    },
    {
        'id': 'ccb_xiamen_2026_requirement',
        'bank': 'CCB',
        'page': 'https://www1.ccb.com/chn/2026-04/16/article_2026041608483476352.shtml',
        'url': 'https://www1.ccb.com/chn/attachDir/2026/04/2026041608475854919.doc',
        'expected_suffix': '.doc',
        'anchors': ['采购'],
    },
    {
        'id': 'ccb_xiamen_2026_interest',
        'bank': 'CCB',
        'page': 'https://www1.ccb.com/chn/2026-04/16/article_2026041608483476352.shtml',
        'url': 'https://www1.ccb.com/chn/attachDir/2026/04/2026041608480724515.docx',
        'expected_suffix': '.docx',
        'anchors': ['采购', '意向'],
    },
    {
        'id': 'ccb_xiamen_2026_commitment',
        'bank': 'CCB',
        'page': 'https://www1.ccb.com/chn/2026-04/16/article_2026041608483476352.shtml',
        'url': 'https://www1.ccb.com/chn/attachDir/2026/04/2026041608481632298.docx',
        'expected_suffix': '.docx',
        'anchors': ['承诺'],
    },
    {
        'id': 'ccb_xiamen_2026_signage_requirement',
        'bank': 'CCB',
        'page': 'https://www.ccb.com/chn/2026-03/05/article_2026030510470824252.shtml',
        'url': 'https://www.ccb.com/chn/attachDir/2026/03/2026030510460593110.doc',
        'expected_suffix': '.doc',
        'anchors': ['采购'],
    },
    {
        'id': 'ccb_xiamen_2026_signage_interest',
        'bank': 'CCB',
        'page': 'https://www.ccb.com/chn/2026-03/05/article_2026030510470824252.shtml',
        'url': 'https://www.ccb.com/chn/attachDir/2026/03/2026030510463131206.docx',
        'expected_suffix': '.docx',
        'anchors': ['采购', '意向'],
    },
    {
        'id': 'ccb_xiamen_2026_signage_commitment',
        'bank': 'CCB',
        'page': 'https://www.ccb.com/chn/2026-03/05/article_2026030510470824252.shtml',
        'url': 'https://www.ccb.com/chn/attachDir/2026/03/2026030510464514614.docx',
        'expected_suffix': '.docx',
        'anchors': ['承诺'],
    },
    {
        'id': 'icbc_jinan_2024_price',
        'bank': 'ICBC',
        'page': 'https://www.sh.icbc.com.cn/shandong/page/1008706621704302592.html',
        'url': 'https://media.icbc.com.cn/pcms-default/purchase/announcement/PCMS1002396320240914111652?filename=%E4%B8%AD%E5%9B%BD%E5%B7%A5%E5%95%86%E9%93%B6%E8%A1%8C%E6%B5%8E%E5%8D%97%E5%88%86%E8%A1%8C%E7%BD%91%E7%82%B9%E6%AC%BE%E7%AE%B1%E5%AF%84%E5%BA%93%E5%8F%8A%E7%A6%BB%E8%A1%8C%E5%BC%8F%E8%87%AA%E5%8A%A8%E6%9F%9C%E5%91%98%E6%9C%BA%E9%9B%86%E4%B8%AD%E8%A3%85%E5%8D%B8%E9%92%9E%E4%B8%9A%E5%8A%A1%E5%A4%96%E5%8C%85%E9%A1%B9%E7%9B%AE%E6%9C%80%E7%BB%88%E6%8A%A5%E4%BB%B7%E8%A1%A8%EF%BC%88%E6%89%AB%E6%8F%8F%EF%BC%89.docx',
        'expected_suffix': '.docx',
        'anchors': ['报价'],
    },
    {
        'id': 'icbc_dalian_2023_price',
        'bank': 'ICBC',
        'page': 'https://www.sh.icbc.com.cn/dalian/page/887367093941452800.html',
        'url': 'https://media.icbc.com.cn/pcms-default/purchase/announcement/PCMS0012555820231016141404?filename=%E9%87%87%E8%B4%AD%E4%BB%B7%E6%A0%BC.doc',
        'expected_suffix': '.doc',
        'anchors': ['采购', '价格'],
    },
]


def _approved(url):
    info = urlsplit(url)
    return info.scheme == 'https' and info.hostname in BANK_HOSTS and not info.username and not info.password


def _download(url):
    if not _approved(url):
        raise ValueError('unapproved_source')
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0 (read-only compatibility maintenance)'})
    with urlopen(request, timeout=17) as response:
        if not _approved(response.geturl()):
            raise ValueError('redirect_outside_allowlist')
        length = response.headers.get('Content-Length')
        if length and int(length) > MAX_BYTES:
            raise ValueError('declared_size_exceeds_budget')
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('download_exceeds_budget')
        return data


def _magic(data):
    if data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        return 'CFB_OLE'
    if data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        return 'ZIP_OOXML_CANDIDATE'
    if data.startswith(b'%PDF-'):
        return 'PDF'
    if data.lstrip()[:32].lower().startswith((b'<html', b'<!doctype html', b'<?xml')):
        return 'WEB_HTML_OR_XML'
    return 'UNKNOWN'


def _inspect(item, read_bytes, story_decoder):
    finding = {
        'id': item['id'], 'bank': item['bank'], 'official_page': item['page'],
        'official_attachment_url': item['url'],
        'declared_extension': item['expected_suffix'],
        'document_producer_verified': False, 'native_wps_verified': False,
        'full_format_verified': False,
    }
    try:
        data = _download(item['url'])
        kind = _magic(data)
        finding.update({'download': 'ok', 'size': len(data),
                        'sha256': hashlib.sha256(data).hexdigest(), 'magic': kind})
        if kind not in ('CFB_OLE', 'ZIP_OOXML_CANDIDATE'):
            finding['read_status'] = 'not_supported_container'
            return finding
        try:
            options = ({'compatibility': 'data', 'object_policy': 'placeholder'}
                       if kind == 'CFB_OLE' else {})
            doc = read_bytes(data, **options)
            body = doc.get('text')
            finding.update({
                'read_status': doc.get('status'), 'reader_format': doc.get('format'),
                'paragraph_count': len(doc.get('paragraphs', [])),
                'anchor_checks': {term: term in body if isinstance(body, str) else False
                                  for term in item['anchors']},
                'issue_codes': [x.get('code') for x in doc.get('issues', [])[:12]
                                if isinstance(x, dict)],
            })
            if kind == 'CFB_OLE' and doc.get('format') == 'ms_doc_subset':
                try:
                    expected = story_decoder(data, Path(__file__).resolve().parents[1] / 'skills' / 'wps' / 'scripts')
                    actual_paragraph_digest = hashlib.sha256(json.dumps(
                        doc.get('paragraphs', []), ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
                    finding['independent_story'] = {
                        'text_equal': hashlib.sha256(body.encode('utf-8')).hexdigest() == expected['text_sha256'],
                        'paragraphs_equal': actual_paragraph_digest == expected['paragraphs_sha256'],
                        'paragraph_count': len(expected['paragraphs']),
                    }
                except (ValueError, KeyError, TypeError, IndexError, UnicodeError, OSError) as error:
                    finding['independent_story'] = {
                        'status': 'out_of_independent_subset', 'error_type': type(error).__name__}
        except Exception as error:
            finding.update({'read_status': 'rejected',
                            'error_type': type(error).__name__,
                            'error_status': getattr(error, 'status', 'unknown'),
                            'error_message': str(error)[:160],
                            'error_location': {
                                key: value for key, value in getattr(error, 'location', {}).items()
                                if key in ('stream', 'offset', 'stage') and type(value) in (str, int)
                            }})
    except Exception as error:
        finding.update({'download': 'failed',
                        'download_error_type': type(error).__name__,
                        'download_error': str(error)[:130]})
    return finding


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'skills' / 'wps' / 'scripts'))
    sys.path.insert(0, str(root / 'tests'))
    from read_wps import read_bytes
    from wps_public_story_oracle import independently_decode_main
    samples = [_inspect(item, read_bytes, independently_decode_main) for item in SAMPLES]
    result = {
        'schema_version': 'wps_boc_ccb_icbc_official_probe_v1',
        'bank_scope': ['BOC', 'CCB', 'ICBC'],
        'total': len(samples),
        'downloaded': sum(x.get('download') == 'ok' for x in samples),
        'cfb_count': sum(x.get('magic') == 'CFB_OLE' for x in samples),
        'ooxml_candidate_count': sum(x.get('magic') == 'ZIP_OOXML_CANDIDATE' for x in samples),
        'read_ok': sum(x.get('read_status') == 'ok' for x in samples),
        'read_partial': sum(x.get('read_status') == 'partial' for x in samples),
        'read_rejected': sum(x.get('read_status') == 'rejected' for x in samples),
        'independent_story_matches': sum(x.get('independent_story', {}).get('text_equal') is True
                                        and x.get('independent_story', {}).get('paragraphs_equal') is True
                                        for x in samples),
        'native_wps_samples': 0,
        'wps_producer_verified': False,
        'complete_format_compatibility_verified': False,
        'samples': samples,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['downloaded'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
