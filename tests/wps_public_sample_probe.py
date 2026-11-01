"""Maintenance-only smoke acceptance of externally sourced WPS test material.

This checks a pinned public fixture against assertions established upstream.
It does NOT assert independently certified origin, complete text correctness,
table layout or Word/WPS display. No runtime download; input must exist.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

SOURCE_URL = (
    'https://github.com/listeng/wps2md/blob/'
    'c4ae028156e44ed8b57ef879dc7bd1d14750c0f1/tests/sample.wps'
)
EXPECTED_GIT_BLOB = '140d30603be830b7ec95226e6dd8103a106938cd'
EXPECTED_SIZE = 125952
EXPECTED_SHA256 = '59ea15f76da2ae5a9cf9592a2c7623918c8e385dc9f587233a44ce166b2e5e8d'
KNOWN_ANCHORS = (
    '春季露营活动指南',
    '一、活动筹备清单',
    '1. 必备露营装备',
    '营地位于城郊云栖谷',
    '负氧离子浓度是市区的15倍',
)


def _report(sample, reader):
    before = sample.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size != EXPECTED_SIZE:
        raise ValueError('Public fixture size/type mismatch')
    data = sample.read_bytes()
    if len(data) != EXPECTED_SIZE or not data.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        raise ValueError('Public fixture is not expected CFB payload')
    git_blob = hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()
    sha256 = hashlib.sha256(data).hexdigest()
    if git_blob != EXPECTED_GIT_BLOB:
        raise ValueError('Git blob identity mismatch')
    if not EXPECTED_SHA256 or sha256 != EXPECTED_SHA256:
        raise ValueError('SHA-256 mismatch or not yet frozen')
    command = [sys.executable, '-I', '-S', '-B', str(reader),
               str(sample), '--object-policy', 'placeholder',
               '--binary-paragraph-sources']
    outcome = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if outcome.returncode not in (0, 3):
        raise ValueError('Reader rejected public WPS candidate')
    result = json.loads(outcome.stdout)
    if result.get('status') not in ('ok', 'partial') or result.get('format') != 'ms_doc_subset':
        raise ValueError('Unexpected result contract')
    body = result.get('text')
    if not isinstance(body, str):
        raise ValueError('Missing body')
    anchors = {anchor: anchor in body for anchor in KNOWN_ANCHORS}
    if not all(anchors.values()):
        raise ValueError('Upstream text anchors differ from extracted body')
    if result.get('reading_assessment', {}).get('full_format_verified') is not False:
        raise ValueError('Must not claim complete format certification')
    if sample.stat() != before or hashlib.sha256(sample.read_bytes()).hexdigest() != sha256:
        raise ValueError('Input changed during probe')
    return {
        'status': 'passed_limited_smoke',
        'fixture_type': 'third_party_public_claimed_wps_not_producer_approved',
        'sha256': sha256, 'git_blob': git_blob,
        'bytes': len(data), 'upstream_anchor_count': len(anchors),
        'anchors_matched': all(anchors.values()),
        'main_paragraphs': len(result.get('paragraphs', [])),
        'reading_status': result['status'],
        'paragraph_source_status': result.get('paragraph_source_status'),
        'reader_exit_code': outcome.returncode,
        'full_text_independently_verified': False,
        'real_producer_provenance_verified': False,
        'full_format_verified': False,
        'source_url': SOURCE_URL,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture')
    parser.add_argument('reader')
    args = parser.parse_args(argv)
    try:
        report = _report(Path(args.fixture), Path(args.reader))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'failed',
                          'reason': type(exc).__name__,
                          'full_text_independently_verified': False,
                          'real_producer_provenance_verified': False}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
