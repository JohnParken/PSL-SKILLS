"""Development-only bounded MS-DOC quality and memory profiler.

No change to runtime reader and no production WPS performance guarantees.
Synthetic strings are never real documents, and metrics have no hard latency
threshold because GitHub runner load varies between jobs.
"""
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / 'wps' / 'scripts'))
from read_wps import parse_streams
from test_wps import document


def profile(paragraphs, *, sources=False):
    if paragraphs not in (1024, 4096, 16384):
        raise ValueError('Unapproved bounded synthetic profile size')
    stored = '甲乙经营分析\r' * paragraphs
    raw = stored.encode('utf-16le')
    streams = document([(raw, False)])
    tracemalloc.start()
    before = perf_counter()
    try:
        result = parse_streams(streams, binary_paragraph_sources=sources)
        elapsed_ms = (perf_counter() - before) * 1000
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    expected = stored.replace('\r', '\n')
    if (result['status'] != 'ok' or result['text'] != expected
            or len(result['paragraphs']) != paragraphs
            or result['paragraphs'][0] != '甲乙经营分析'
            or result['paragraphs'][-1] != '甲乙经营分析'):
        raise ValueError('Synthetic data coverage differs from independent literals')
    if sources and (len(result.get('paragraph_sources', ())) != paragraphs
                    or result.get('paragraph_source_status') != 'exact_stored_cp'):
        raise ValueError('Stored CP provenance coverage differs')
    if not sources and 'paragraph_sources' in result:
        raise ValueError('Default scope unexpectedly changed')
    return {
        'paragraphs': paragraphs, 'binary_paragraph_sources': sources,
        'utf16_input_bytes': len(raw), 'msdoc_stream_bytes': len(streams[('WordDocument',)]),
        'elapsed_ms_observed': round(elapsed_ms, 3), 'peak_tracemalloc_bytes_observed': peak,
        'text_sha256': hashlib.sha256(expected.encode('utf-8')).hexdigest(),
        'status': result['status'],
        'full_wps_producer_validation': False,
    }


def main():
    samples = [profile(1024), profile(4096), profile(16384),
               profile(16384, sources=True)]
    print(json.dumps({
        'schema_version': 'wps_synthetic_resource_profile_v1',
        'platform': sys.platform, 'stdlib_only': True,
        'interpretation': 'measured_on_current_runner_not_performance_SLA',
        'samples': samples,
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
