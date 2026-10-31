"""Additive BIFF8 stored-evidence audit; no formula/style/render evaluation.

Only consumes the existing bounded BIFF8 reader result.  No WPS text branch,
format conversion, network, Office software, filesystem write, or formula execution.
"""
import argparse
from collections import Counter
import json
import struct

MAX_AUDIT_SHEETS = 128
MAX_AUDIT_CELLS = 200000
MAX_RICH_ENTRIES = 4096
MAX_RICH_SPANS = 20000
MAX_AUDIT_XFS = 4096


def _cp_boundaries(text):
    """UTF-16 scalar boundaries. Never split a surrogate pair to claim a font."""
    boundaries = {0}
    position = 0
    for char in text:
        position += 2 if ord(char) > 0xffff else 1
        boundaries.add(position)
    return boundaries, position


def _rich_sources(result):
    # Shared strings are not exposed as a separate decoded collection by
    # the existing reader; cell references are the only text witnesses.
    texts = {}
    ambiguous = set()
    for sheet in result['sheets']:
        for cell in sheet['cells'].values():
            index = cell.get('sst_index')
            if index is None or cell.get('type') != 'text':
                continue
            if type(index) is not int or index < 0:
                raise ValueError('Invalid shared-string index')
            value = cell['value']
            if index in texts and texts[index] != value:
                ambiguous.add(index)
            else:
                texts[index] = value
    output = []
    span_total = 0
    for source in result.get('shared_string_formatting', []):
        index, length = source['sst_index'], source['utf16_units']
        runs = bytes.fromhex(source['runs_raw_hex'])
        if len(runs) != source['run_count'] * 4:
            raise ValueError('Rich runs byte count mismatch')
        positions = [struct.unpack_from('<HH', runs, start)
                     for start in range(0, len(runs), 4)]
        if any(not (0 <= cp <= length) for cp, _ in positions):
            raise ValueError('Rich source character index invalid')
        if any(positions[i][0] >= positions[i+1][0]
               for i in range(len(positions)-1)):
            raise ValueError('Rich source runs unsorted')
        value = texts.get(index)
        if index in ambiguous:
            output.append({'sst_index': index, 'status': 'conflicting_cell_text',
                           'spans': []})
            continue
        if value is None:
            output.append({'sst_index': index, 'status': 'unreferenced_string_text_unavailable',
                           'spans': [], 'runs_observed': len(positions)})
            continue
        boundaries, actual = _cp_boundaries(value)
        if actual != length:
            output.append({'sst_index': index, 'status': 'text_units_do_not_match_metadata',
                           'spans': []})
            continue
        if any(cp not in boundaries for cp, _ in positions):
            output.append({'sst_index': index, 'status': 'run_splits_unicode_scalar',
                           'spans': []})
            continue
        spans = []
        for j, (start, font) in enumerate(positions):
            end = positions[j+1][0] if j+1 < len(positions) else length
            # A terminal format-run sentinel does not style the final character.
            if start == length or end == start:
                continue
            spans.append({'cp_start': start, 'cp_end': end,
                          'font_index_raw': font})
        if span_total + len(spans) > MAX_RICH_SPANS:
            output.append({'sst_index': index, 'status': 'audit_span_budget_exceeded',
                           'spans': []})
            continue
        span_total += len(spans)
        output.append({'sst_index': index,
                       'status': 'direct_run_locations_only',
                       'unattributed_prefix_utf16_units': positions[0][0] if positions else length,
                       'spans': spans,
                       'effective_font': 'unknown'})
    return output


def _formula_cache_summary(result):
    output = []
    total = Counter()
    for sheet in result['sheets']:
        cache = Counter()
        recalculation_flag = 0
        stale_possible = 0
        for cell in sheet['cells'].values():
            formula = cell.get('formula')
            if formula is None:
                continue
            state = cell.get('cache_status')
            if state not in ('unverified', 'error_unverified', 'unverified_empty_string', 'missing', 'pending_string'):
                raise ValueError('Unknown formula cache provenance')
            cache[state] += 1
            if formula.get('needs_recalculation_flag'):
                recalculation_flag += 1
            if formula.get('cache_may_be_stale') is True:
                stale_possible += 1
        total.update(cache)
        output.append({'sheet': sheet['name'], 'formula_count': sum(cache.values()),
                       'cache_status_counts': dict(sorted(cache.items())),
                       'recalculation_flagged': recalculation_flag,
                       'possibly_stale': stale_possible,
                       'formula_expression_decoded': False,
                       'formula_executed': False})
    return {'sheets': output, 'total_formulas': sum(total.values()),
            'cache_status_counts': dict(sorted(total.items())),
            'all_caches_unverified': True,
            'saved_cache_is_current': 'unknown',
            'cache_values_recalculated': False}


def _xf_sources(result):
    sources = []
    for index, item in enumerate(result.get('styles', [])):
        raw = bytes.fromhex(item['raw_hex'])
        if len(raw) != 20:
            raise ValueError('BIFF8 XF evidence not 20 bytes')
        sources.append({'xf_index': index,
                        'font_index_raw': struct.unpack_from('<H', raw, 0)[0],
                        'number_format_id_raw': item['number_format_id_raw'],
                        'format_and_alignment_tail_hex': raw[4:].hex(),
                        'effective_formatting': 'unknown',
                        'style_inheritance_resolved': False})
    return sources


def audit_biff8(result):
    """Audit an already parsed BIFF8 subset; do not alter its cells or status."""
    if not isinstance(result, dict) or result.get('schema_version') != 'biff8_readonly_v1':
        raise ValueError('Expected BIFF8 subset result, not a Word/OOXML document')
    if (len(result.get('sheets', ())) > MAX_AUDIT_SHEETS
            or sum(len(x['cells']) for x in result['sheets']) > MAX_AUDIT_CELLS
            or len(result.get('shared_string_formatting', ())) > MAX_RICH_ENTRIES
            or len(result.get('styles', ())) > MAX_AUDIT_XFS):
        raise ValueError('BIFF8 audit source budget exceeded')
    from wps_biff8_audit_sources import (font_sources, rich_font_references,
                                        calculation_metadata, xf_ancestry)
    fonts = font_sources(result)
    rich = _rich_sources(result)
    return {'schema_version': 'biff8_stored_evidence_audit_v1',
            'source_schema_version': 'biff8_readonly_v1',
            'source_read_status': result.get('status'),
            'source_partial_preserved': bool(result.get('partial') or result.get('issues')),
            'rich_format_runs': rich,
            'font_sources': fonts,
            'rich_font_references': rich_font_references(rich, fonts),
            'formula_caches': _formula_cache_summary(result),
            'calculation_metadata': calculation_metadata(result),
            'xf_sources': _xf_sources(result),
            'xf_ancestry': xf_ancestry(result),
            'effective_styles_verified': False,
            'native_et_ett_producer_verified': False,
            'full_format_verified': False,
            'notes': 'Run offsets, cache flags and XF bytes are saved evidence only. '
                     'They never certify effective styling, cache freshness or formula results.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='Task-relative BIFF8 .et/.ett or supported BIFF workbook')
    arguments = parser.parse_args(argv)
    # -I does not include the executable's directory in sys.path.
    # Resolve only this installed Skill script folder, never the task input path.
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from read_wps import read
    from wps_result import encode_result
    original = read(arguments.input)
    if original.get('status') not in ('ok', 'partial'):
        print(encode_result({'status': 'failed',
                             'source_status': original.get('status'),
                             'source_location': original.get('location'),
                             'source_issues': original.get('issues')}))
        return 1
    try:
        report = audit_biff8(original)
        print(encode_result(report))
        return 3 if original['status'] == 'partial' else 0
    except (ValueError, KeyError, TypeError) as error:
        print(encode_result({'status': 'failed', 'error': type(error).__name__,
                             'message': str(error), 'source_status': original.get('status')}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
