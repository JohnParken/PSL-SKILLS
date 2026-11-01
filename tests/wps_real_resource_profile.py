"""Maintenance-only real public WPS/MS-DOC resource observations.

No document text is printed or vendored, and no runtime network is added.
Bounded official HTTPS URLs only; downloaded bytes are never written to disk.
This is not an independent WPS producer/visual acceptance or memory SLA.
"""
import hashlib
import json
from pathlib import Path
from time import perf_counter
import sys
import tracemalloc
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / 'wps' / 'scripts'))
from read_wps import read_bytes

MAX_BYTES = 32 * 1024 * 1024
SAMPLES = (
    {
        'id': 'samr_recommendation_plan_2026',
        'page': 'https://www.samr.gov.cn/bzjss/tzgg/art/2026/art_cb801d915d77425e8b522f77a7f9d8da.html',
        'url': 'https://www.samr.gov.cn/cms_files/filemanager/1647978232/attach/20261/760b5a7619124d4c971893b9d6e4fe22.wps',
        'sha256': 'd07786795608fb919849e6e67a25073ce88f1bd0b264915b83576192ef35d493',
    },
    {
        'id': 'samr_compulsory_standards_method_2026',
        'page': 'https://www.samr.gov.cn/bzjss/tzgg/art/2026/art_6a4a689ef68a45afa5b93f638ef1a7ae.html',
        'url': 'https://www.samr.gov.cn/cms_files/filemanager/1647978232/attach/20267/e23ded4e14854c34811ea7aee1d8875f.wps',
        'sha256': 'c8b8e6fe40b1d3cb1f77d1289980eddf50f071e3042d77d9283f8be5e4959d8f',
    },
    {
        'id': 'samr_compulsory_standards_report_2026',
        'page': 'https://www.samr.gov.cn/bzjss/tzgg/art/2026/art_6a4a689ef68a45afa5b93f638ef1a7ae.html',
        'url': 'https://www.samr.gov.cn/cms_files/filemanager/1647978232/attach/20267/3a63f651b33e4c16b31eb46eb6a9cb63.wps',
        'sha256': 'ca3e4b14939e3c4e4c025fee3939256680dfe4da13164532ad4a6aaf79a0d081',
    },
    {
        'id': 'abc_guangxi_smart_park_doc',
        'page': 'https://www.abchina.com/cn/branch/gx/cg/bm/202402/t20240218_2396189.htm',
        'url': 'https://www.abchina.com/cn/branch/gx/cg/bm/202402/W020240218572546885319.doc',
        'sha256': '3277c3ad20464f5197c3b86836ee9f8feeeaa2ff1f5c5728c37fd8c98528a58d',
    },
    {
        'id': 'zhongshan_government_company_list',
        'page': 'https://zsepb.zs.gov.cn/gkmlpt/content/2/2629/post_2629998.html',
        'url': 'https://zsepb.zs.gov.cn/attachment/0/571/571978/2629998.wps',
        'sha256': '28b509c9032dc45cf2c14f55075f53abaff5989ad9c5fe740dc06771098b2be6',
    },
)


# Immutable observed *subset* result, not a WPS Office independent oracle.
PINNED_SUBSET = {
    'samr_recommendation_plan_2026': (
        11, '9f7e07a5360981d2f23a46577cc7fba067c5269f706e04544be42356ea8db67c'),
    'samr_compulsory_standards_method_2026': (
        58, '41d85ce778b21c011e5bea40a24c489187ac8b8e95cf0596daf1aafcc7d0b130'),
    'samr_compulsory_standards_report_2026': (
        80, 'f98d91a15e25765040730d31c5c7e3bd2e32e82913d5b3d6995f18a5e3eef4b2'),
    'abc_guangxi_smart_park_doc': (
        76, 'b240d6e6c881b1f03199fa23899603b0489e756360aceb3b4a1c04d6e3504cb2'),
    'zhongshan_government_company_list': (
        4, '3b9015bbfe914ae02910145a0bee770953877fb8208ce478e5f1da572851f468'),
}


def _approved(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https'
            and parsed.hostname in ('www.samr.gov.cn', 'www.abchina.com', 'zsepb.zs.gov.cn')
            and parsed.username is None and parsed.password is None)


def _fetch(source):
    url = source['url']
    if not _approved(url):
        raise ValueError('Unapproved official source URL')
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0 (read-only format QA)'}),
                 timeout=22) as response:
        if not _approved(response.geturl()):
            raise ValueError('Redirect outside official domains')
        header_size = response.headers.get('Content-Length')
        if header_size and int(header_size) > MAX_BYTES:
            raise ValueError('Attachment exceeds size budget')
        contents = response.read(MAX_BYTES + 1)
    if len(contents) > MAX_BYTES:
        raise ValueError('Attachment exceeds size budget')
    digest = hashlib.sha256(contents).hexdigest()
    if source['sha256'] and source['sha256'] != digest:
        raise ValueError('Pinned official attachment has changed identity')
    return contents, digest


def _measure(data, **options):
    tracemalloc.start()
    started = perf_counter()
    try:
        parsed = read_bytes(data, **options)
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        'status': parsed.get('status'), 'format': parsed.get('format'),
        'paragraphs': len(parsed.get('paragraphs', [])),
        'cp_status': parsed.get('paragraph_source_status', 'not_requested'),
        'cp_count': len(parsed.get('paragraph_sources', [])),
        'utf8_text_bytes': len(parsed.get('text', '').encode('utf-8')),
        'issue_codes': [issue.get('code') for issue in parsed.get('issues', [])[:15]
                        if isinstance(issue, dict)],
        'result_text_sha256': hashlib.sha256(parsed.get('text', '').encode('utf-8')).hexdigest(),
        'result_paragraphs_sha256': hashlib.sha256(json.dumps(
            parsed.get('paragraphs', []), ensure_ascii=False,
            separators=(',', ':')).encode('utf-8')).hexdigest(),
        'elapsed_ms': elapsed_ms,
        'python_tracemalloc_peak_bytes': peak_bytes,
    }


def _inspect(item):
    result = {'id': item['id'], 'official_page': item['page'],
              'original_wps_producer_unverified': True}
    try:
        data, digest = _fetch(item)
        result.update({'download': 'success', 'bytes': len(data), 'sha256': digest})
        if not data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
            result['inspection'] = 'not_CFB'
            return result
        result['inspection'] = 'CFB_OLE'
        for mode, options in (
                ('default', {'object_policy': 'placeholder'}),
                ('with_cp', {'object_policy': 'placeholder',
                             'binary_paragraph_sources': True})):
            try:
                result[mode] = _measure(data, **options)
            except Exception as exc:
                result[mode] = {
                    'status': 'rejected', 'error_type': type(exc).__name__,
                    'error_status': getattr(exc, 'status', 'unknown'),
                    'error_message': str(exc)[:120],
                }
        expected = PINNED_SUBSET.get(item['id'])
        if expected and (result['default'].get('paragraphs') != expected[0] or
                         result['default'].get('result_text_sha256') != expected[1] or
                         result['default'].get('status') != 'partial'):
            raise ValueError('Pinned known supported-subset result changed')
        if result['default'].get('result_text_sha256') != result['with_cp'].get('result_text_sha256'):
            raise ValueError('Text changed between default and opt-in modes')
        if result['default'].get('result_paragraphs_sha256') != result['with_cp'].get('result_paragraphs_sha256'):
            raise ValueError('Paragraphs changed between default and opt-in modes')
    except Exception as exc:
        result['probe_failure'] = type(exc).__name__
        result['probe_failure_detail'] = str(exc)[:130]
    return result


def main():
    results = [_inspect(item) for item in SAMPLES]
    output = {'schema_version': 'wps_official_real_resource_v1',
              'platform': sys.platform,
              'unmodified_reader': True, 'true_producer_verified': False,
              'independent_full_text_acceptance': False,
              'samples': results}
    # Print after computing both success and any pinned regression failures.
    # A transport failure is never counted as a format failure, but the
    # maintenance job should succeed only if pinned source data was measured.
    success = any(x.get('default', {}).get('status') in ('ok', 'partial')
                  for x in results)
    broken_baselines = [
        x['id'] for x in results
        if x.get('probe_failure_detail', '').startswith((
            'Pinned official attachment has changed identity',
            'Pinned known supported-subset result changed',
            'Text changed between default and opt-in modes',
            'Paragraphs changed between default and opt-in modes'))
    ]
    # Unavailable official servers stay "not checked"; changed pinned bytes
    # or changed text must fail, even if another public sample still passes.
    output['pinned_baseline_mismatches'] = broken_baselines
    print(json.dumps(output, ensure_ascii=False))
    return 0 if success and not broken_baselines else 1


if __name__ == '__main__':
    raise SystemExit(main())
