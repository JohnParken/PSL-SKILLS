#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded, tolerant MS-XLS BIFF8 stored-value reader with partial diagnostics.

One tolerant reading policy: stored cell values are always preferred over
aborting for non-value problems. Non-value metadata, unknown records, framing
tails and unsupported substream types degrade to bounded raw retention plus a
stable ``issues[]`` code. Hard boundaries remain: encryption is never
decrypted, macro/VBA content is never loaded or executed, formulas are never
evaluated, external links are never followed, framing stays bounded and no
source byte is written back.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_cfb import MAX_STREAM_BYTES, COMPATIBILITY_MODES
from wps_biff8_charts import CHART_POLICIES
from wps_biff8_globals import (COMMON_END, PALETTE_COLOR_COUNT,
                               SHEET_TYPE_NAMES, SUPPORTED_BIFF8_CODE_PAGES,
                               GlobalSubstreamMixin)
from wps_biff8_headers import reconstruct_biff_headers
from wps_biff8_links import CONTINUE
from wps_biff8_records import (GLOBAL_METADATA, LEGACY_GLOBAL_METADATA,
                               LEGACY_SHEET_METADATA, SHEET_METADATA, bof,
                               frame_records)
from wps_biff8_result import ResultAssemblyMixin

READING_MODES = COMPATIBILITY_MODES + ("data",)
DEFAULT_READING_MODE = 'data'
MAX_RECORDS = 100000
MAX_CELLS = 200000
MAX_MERGES = 65536
MAX_SHARED_STRINGS = 200000
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_METADATA_ENTRIES = 65536
MAX_STRUCTURED_ROWS = 65536
MAX_STRUCTURED_BYTES = 8 * 1024 * 1024
MAX_RICH_PHONETIC_ENTRIES = 4096
MAX_DXF_PROPERTIES = 4096
MAX_OUTPUT_TEXT_UNITS = 8 * 1024 * 1024
MAX_DISPLAY_TEXT_UNITS = 2 * 1024 * 1024
MAX_ISSUES = 1000
MAX_CHARTS = 128
MAX_FORMULA_GROUPS = 1024
MAX_SHEETS = 1024
MAX_XF_STYLES = 4096
MAX_CONTINUE_CHAIN = 256
MAX_TEMPLATE_BLOCKS = 1024
MAX_STATIC_NAMES = 256


def _limits():
    """Snapshot the module limits so a parse is internally consistent."""
    return {
        'max_records': MAX_RECORDS,
        'max_cells': MAX_CELLS,
        'max_merges': MAX_MERGES,
        'max_shared_strings': MAX_SHARED_STRINGS,
        'max_metadata_bytes': MAX_METADATA_BYTES,
        'max_metadata_entries': MAX_METADATA_ENTRIES,
        'max_structured_rows': MAX_STRUCTURED_ROWS,
        'max_structured_bytes': MAX_STRUCTURED_BYTES,
        'max_rich_phonetic_entries': MAX_RICH_PHONETIC_ENTRIES,
        'max_dxf_properties': MAX_DXF_PROPERTIES,
        'max_output_text_units': MAX_OUTPUT_TEXT_UNITS,
        'max_display_text_units': MAX_DISPLAY_TEXT_UNITS,
        'max_issues': MAX_ISSUES,
        'max_charts': MAX_CHARTS,
        'max_formula_groups': MAX_FORMULA_GROUPS,
        'max_sheets': MAX_SHEETS,
        'max_xf_styles': MAX_XF_STYLES,
        'max_continue_chain': MAX_CONTINUE_CHAIN,
        'max_template_blocks': MAX_TEMPLATE_BLOCKS,
        'max_static_names': MAX_STATIC_NAMES,
    }


from wps_biff8_errors import EncryptedError, ResourceLimitError, UnsupportedError, require
from wps_biff8_values import (ContinuedStrings as _ContinuedStrings, unicode_string, number, rk_value)


class ContinuedStrings(_ContinuedStrings):
    """Keep the reader-level configurable metadata ceiling for direct callers."""

    def __init__(self, records, index, position=0):
        super().__init__(records, index, position, metadata_limit=MAX_METADATA_BYTES)


def records(data):
    """Frame the stream using the reader's current limits."""
    return frame_records(data, max_stream_bytes=MAX_STREAM_BYTES,
                         max_records=MAX_RECORDS)


class Reader(GlobalSubstreamMixin, ResultAssemblyMixin):
    """One tolerant BIFF8 parse; never writes its input."""

    def __init__(self, data, *, compatibility=None, chart_policy=None,
                 salvage_unknown_metadata=False, extract_stored_values=False):
        require(compatibility is None or compatibility in READING_MODES,
                'Unknown BIFF compatibility mode')
        require(type(salvage_unknown_metadata) is bool,
                'Salvage option must be boolean')
        require(chart_policy is None or chart_policy in CHART_POLICIES,
                'Unknown chart policy')
        self.requested_compatibility = compatibility
        self.effective_compatibility = (compatibility
                                        or DEFAULT_READING_MODE)
        self.requested_chart_policy = chart_policy
        self.salvage_requested = salvage_unknown_metadata
        self.extract_stored_values = True
        self.limits = _limits()
        self.data = data
        self.records = records(data)
        self.issues = []
        self.grouped_issues = {}
        self.truncated_issue_count = 0
        self.metadata = []
        self._init_global_counters()
        self.metadata_truncated_count = 0
        self.metadata_budget_event = None
        self.passive_issues = {}
        self.salvage_unknown_record_count = 0
        self.salvage_continue_records = 0
        self.salvage_issues = {}
        self.salvage_data_uncertain = False
        self.omissions_affect_cell_values = False
        self.missing_formula_cache = False
        self.omitted_cell_records = 0
        self.structured_row_count = 0
        self.structured_metadata_bytes = 0
        self.structured_metadata_truncated = 0
        self.decoded_metadata_kinds = set(GLOBAL_METADATA) | set(SHEET_METADATA)
        self.global_metadata = {**GLOBAL_METADATA, **LEGACY_GLOBAL_METADATA}
        self.sheet_metadata = {**SHEET_METADATA, **LEGACY_SHEET_METADATA}
        self.code_page = None
        self.format_string_flags = {}
        self.string_flag_issues = {}
        self.current_location = {'record_offset': 0, 'record_id': '0x809',
                                 'scope': 'globals'}
        self.styles = []
        self.formats = {}
        self.strings = []
        self.rich_strings = []
        self.phonetic_strings = []
        self.cell_count = 0
        self.merge_count = 0
        self.output_text_units = 0
        self.display_text_units = 0
        self.display_text_limited = False
        self.omitted_display_formats = set()
        self._display_decisions = {}
        self.chart_policy = 'opaque'
        self.embedded_charts = []
        self.drawing_group_seen = False
        self.note_drawings = []
        self.opaque_objects = []
        self.table_styles_collection_seen = False
        self.table_style_sources = []
        self.skipped_substreams = []
        self.sheet_names = set()

    # -- bounded diagnostics -------------------------------------------------
    def issue(self, event):
        """Append one diagnostic; over the cap, count it instead of raising."""
        if len(self.issues) < self.limits['max_issues']:
            self.issues.append(event)
            return True
        self.truncated_issue_count += 1
        return False

    def grouped_issue(self, key, event):
        """Emit one diagnostic per key and reuse the same item for counting."""
        item = self.grouped_issues.get(key)
        if item is None:
            if not self.issue(event):
                return None
            item = event
            self.grouped_issues[key] = item
        return item

    def metadata_budget_issue(self, scope):
        event = self.metadata_budget_event
        if event is None:
            event = {'code': 'metadata_budget_truncated', 'scope': scope,
                     'count': 0,
                     'cell_value_impact': 'none_within_parsed_cells',
                     'message': 'Raw metadata budget exhausted; later known '
                                'or unknown record bytes are counted but not '
                                'retained. Cell values already parsed are '
                                'unaffected.'}
            if not self.issue(event):
                return
            self.metadata_budget_event = event
        event['count'] += 1

    # -- bounded raw retention ----------------------------------------------
    def _append_metadata(self, scope, offset, kind, name, payload, **extra):
        size = len(payload)
        if (size > self.limits['max_metadata_bytes'] - self.metadata_bytes
                or len(self.metadata) >= self.limits['max_metadata_entries']):
            self.metadata_truncated_count += 1
            self.metadata_budget_issue(scope)
            return None
        self.metadata_bytes += size
        entry = {'scope': scope, 'offset': offset,
                 'record_id': hex(kind) if kind is not None else None,
                 'name': name, 'payload_hex': payload.hex(), **extra}
        self.metadata.append(entry)
        return len(self.metadata) - 1

    def metadata_record(self, record, names, scope):
        """Retain a known metadata record; return its metadata index or None."""
        offset, kind, payload = record
        name = names.get(kind) if isinstance(names, dict) else None
        index = self._append_metadata(scope, offset, kind,
                                      name or 'KnownBIFFMetadataOpaque',
                                      payload)
        if index is None:
            return None
        if kind not in self.decoded_metadata_kinds:
            event = self.passive_issues.get(kind)
            if event is None:
                event = {'code': 'non_cell_metadata_not_decoded',
                         'scope': scope, 'offset': offset,
                         'record_id': hex(kind), 'name': name,
                         'scopes': [], 'records': 0,
                         'message': 'Known bounded metadata retained raw; not '
                                    'applied to stored cell values.'}
                if self.issue(event):
                    self.passive_issues[kind] = event
                else:
                    event = None
            if event is not None:
                if scope not in event['scopes']:
                    event['scopes'].append(scope)
                event['records'] += 1
        return index

    def salvage_record(self, record, scope, *, name=None, code=None,
                       message=None, may_affect_cell_values=True):
        """Retain a record that could not be interpreted; never abort."""
        offset, kind, payload = record
        label = name or 'UnknownBIFFSalvaged'
        index = self._append_metadata(
            scope, offset, kind, label, payload,
            semantics_verified=False,
            may_affect_cell_values=bool(may_affect_cell_values))
        if name is None:
            self.salvage_unknown_record_count += 1
            if may_affect_cell_values:
                self.salvage_data_uncertain = True
                self.omissions_affect_cell_values = True
        issue_code = code or ('unknown_biff_record_salvaged' if name is None
                              else 'biff_metadata_retained_without_validation')
        key = (issue_code, scope, kind)
        event = self.salvage_issues.get(key)
        if event is None:
            event = {'code': issue_code, 'record_id': hex(kind),
                     'offset': offset, 'scope': scope, 'count': 0,
                     'cell_value_impact': ('unknown' if may_affect_cell_values
                                           else 'none_within_parsed_cells'),
                     'message': message or
                     'Record retained raw; its effect on saved-value coverage '
                     'or interpretation is unverified.'}
            if not self.issue(event):
                return index
            self.salvage_issues[key] = event
        event['count'] += 1
        return index

    def salvage_bytes(self, data, offset, scope, *, name, code, message,
                      may_affect_cell_values=True):
        """Retain an unframable tail as one bounded opaque metadata entry."""
        index = self._append_metadata(scope, offset, None, name, data,
                                      semantics_verified=False,
                                      may_affect_cell_values=bool(
                                          may_affect_cell_values))
        if may_affect_cell_values:
            self.salvage_data_uncertain = True
            self.omissions_affect_cell_values = True
        self.issue({'code': code, 'offset': offset, 'name': name,
                    'bytes': len(data),
                    'cell_value_impact': ('unknown'
                                          if may_affect_cell_values
                                          else 'none_within_substreams'),
                    'message': message})
        return index

    def unknown_record(self, record_list, index, scope):
        """Retain one unknown record and its immediate bounded Continue chain."""
        record = record_list[index]
        payload = bytearray(record[2])
        cursor = index + 1
        continues = 0
        while cursor < len(record_list) and \
                record_list[cursor][1] == CONTINUE and \
                continues < self.limits['max_continue_chain'] and \
                len(payload) + len(record_list[cursor][2]) <= \
                self.limits['max_metadata_bytes']:
            payload.extend(record_list[cursor][2])
            continues += 1
            cursor += 1
        self.salvage_record((record[0], record[1], bytes(payload)), scope)
        self.salvage_continue_records += continues
        return cursor

    def continued_strings(self, record_list, index, position=0):
        """Reader-level continued-string parser with the metadata ceiling."""
        return ContinuedStrings(record_list, index, position)

    def reserve_output_text(self, *values):
        """Charge saved cell-value text to the value-unit budget.

        Every serialized cell occurrence is charged, not merely SST unique
        strings. UTF-16 units are counted without constructing an encoded copy.
        """
        units = sum(len(value) + sum(ord(char) > 0xFFFF for char in value)
                    for value in values if isinstance(value, str))
        if units > MAX_OUTPUT_TEXT_UNITS - self.output_text_units:
            raise ResourceLimitError('Repeated cell text exceeds output text '
                                     'budget')
        self.output_text_units += units

    @staticmethod
    def _text_units(values):
        return sum(len(value) + sum(ord(char) > 0xFFFF for char in value)
                   for value in values if isinstance(value, str))

    def reserve_display_text(self, *values, cache_key=None):
        """Charge display-only text without failing the value read.

        Format/comment/hyperlink text uses a separate budget and is omitted
        when exhausted. A cache key makes repeated custom format strings charge
        once while still returning the same decision for every cell.
        """
        if cache_key is not None and cache_key in self._display_decisions:
            return self._display_decisions[cache_key]
        units = self._text_units(values)
        accepted = units <= MAX_DISPLAY_TEXT_UNITS - self.display_text_units
        if accepted:
            self.display_text_units += units
            if cache_key is not None:
                self._display_decisions[cache_key] = True
            return True
        self.display_text_limited = True
        if cache_key is not None:
            self._display_decisions[cache_key] = False
            if cache_key[0] == 'format':
                self.omitted_display_formats.add(cache_key[1])
        event = self.grouped_issue(('display_text_budget_truncated',), {
            'code': 'display_text_budget_truncated', 'count': 0,
            'display_limit': MAX_DISPLAY_TEXT_UNITS,
            'cell_value_impact': 'none_within_retained_cells',
            'message': 'Display/format metadata exceeded its own budget; '
                       'saved cell values are retained, affected display '
                       'fields are omitted.'})
        if event is not None:
            event['count'] += 1
        return False

    def plain_string_flags(self, record, raw_flag, scope, cell=None):
        """Record ignored XLUnicodeString reserved bits, never SST rich bits."""
        if not raw_flag & 0xFE:
            return
        key = record[1]
        event = self.string_flag_issues.get(key)
        if event is None:
            event = {'code': 'plain_string_reserved_flags_ignored',
                     'record_id': hex(record[1]), 'offset': record[0],
                     'scope': scope, 'records': 0,
                     'message': 'Plain XLUnicodeString reserved bits retained '
                                'but ignored; only fHighByte selects Unicode '
                                'storage.'}
            if cell is not None:
                event['cell'] = cell
            if not self.issue(event):
                return
            self.string_flag_issues[key] = event
        event['records'] += 1

    # -- option compatibility notices --------------------------------------
    def note_options(self):
        if self.requested_compatibility not in (None, DEFAULT_READING_MODE):
            self.issue({'code': 'compatibility_option_ignored',
                        'requested': str(self.requested_compatibility),
                        'message': 'Compatibility policies are deprecated; '
                                   'the tolerant content-first reader was '
                                   'used.'})
        if self.requested_chart_policy == 'reject':
            self.issue({'code': 'chart_policy_option_ignored',
                        'requested': 'reject',
                        'message': 'Charts are always retained as bounded '
                                   'opaque bytes; no rejection policy '
                                   'remains.'})
        if self.salvage_requested:
            self.issue({'code': 'salvage_option_ignored',
                        'message': 'Best-effort salvage is the default reading '
                                   'policy; the option no longer changes '
                                   'behavior.'})

def parse_stream(data, *, compatibility=None, chart_policy=None,
                 salvage_unknown_metadata=False,
                 extract_stored_values=False):
    """Parse BIFF8 bytes with the single tolerant policy.

    ``compatibility``/``salvage_unknown_metadata``/``extract_stored_values``
    remain accepted for API compatibility. Their strict semantics are ignored;
    a non-default compatibility or explicit chart rejection is reported as an
    issue while the tolerant reader is still used.
    """
    reader = Reader(data, compatibility=compatibility,
                    chart_policy=chart_policy,
                    salvage_unknown_metadata=salvage_unknown_metadata,
                    extract_stored_values=extract_stored_values)
    try:
        return reader.parse()
    except ValueError as exc:
        # Preserve cursor-specific offsets when present; attach only known
        # context. Hard refusals keep their status class.
        exc.location = {**reader.current_location,
                        'compatibility_mode': reader.requested_compatibility
                        or DEFAULT_READING_MODE,
                        **getattr(exc, 'location', {})}
        raise


from wps_access import (DEFAULT_FILE_COMPATIBILITY, ParseError, load_compound,
                        read_input, resolve_chart_policy)


def normalize_result(result):
    """Unified JSON contract: any issue makes the result partial."""
    result['reader_status'] = result['status']
    result['partial'] = bool(result.get('issues')) or bool(
        result.get('issues_truncated'))
    result['status'] = 'partial' if result['partial'] else 'ok'
    result['format'] = 'biff8_subset'
    result['scope'] = 'stored_cells_and_metadata_subset'
    result['producer_verified'] = False
    return result


def _biff8_candidate(content):
    return (len(content) >= 20
            and content[0:2] == b'\x09\x08'
            and content[2:4] == b'\x10\x00'
            and content[4:8] == b'\x00\x06\x05\x00')


def read_file(path, *, compatibility=DEFAULT_FILE_COMPATIBILITY,
              chart_policy=None, salvage_unknown_metadata=False):
    """Read a CFB workbook file; always returns a tolerant complete result."""
    try:
        container = load_compound(read_input(path),
                                  compatibility or DEFAULT_FILE_COMPATIBILITY)
    except ParseError as exc:
        error_types = {'io_error': OSError, 'encrypted': EncryptedError,
                       'resource_limit': ResourceLimitError,
                       'unsupported': UnsupportedError}
        error = error_types.get(exc.status, ValueError)(str(exc))
        error.status, error.location = exc.status, exc.location
        raise error from exc
    result = read_container(container, path,
                            compatibility=compatibility,
                            chart_policy=chart_policy,
                            salvage_unknown_metadata=salvage_unknown_metadata)
    from wps_diagnostics import summarize
    return summarize(normalize_result(result))


def read_container(container, path=None, *,
                   compatibility=DEFAULT_FILE_COMPATIBILITY,
                   chart_policy=None,
                   salvage_unknown_metadata=False):
    """Read one Workbook/Book stream from a validated CFB container."""
    if container.encrypted:
        raise EncryptedError('Encrypted compound workbook unsupported; no '
                             'decryption attempted')
    candidates = [stream for stream in container.streams
                  if len(stream) == 1
                  and stream[0].casefold() in ('workbook', 'book')]
    if not candidates:
        raise UnsupportedError('No root Workbook/Book stream in the '
                               'compound container')
    chosen = candidates[0]
    if len(candidates) > 1:
        preferred = [stream for stream in candidates
                     if _biff8_candidate(container.read_stream(stream))]
        chosen = preferred[0] if preferred else candidates[0]
        container.issues.append({
            'code': 'multiple_workbook_streams',
            'streams': [list(stream) for stream in candidates],
            'chosen': list(chosen),
            'message': 'More than one root Workbook/Book stream is present; '
                       'the first BIFF8-looking candidate is read and no '
                       'failed parse is retried.'})
    content = container.read_stream(chosen)
    result = parse_stream(
        content, compatibility=compatibility, chart_policy=chart_policy,
        salvage_unknown_metadata=salvage_unknown_metadata,
        extract_stored_values=True)
    macro_streams = [list(stream) for stream in container.streams
                     if any(part.casefold() in ('vba', '_vba_project_cur',
                                                'macros')
                            for part in stream)]
    other_streams = [item for item in container.report()['streams']
                     if item['path'] != list(chosen)]
    issues = list(result['issues']) + list(container.issues)
    if macro_streams:
        issues.append({'code': 'macro_storage_present_unloaded',
                       'count': len(macro_streams),
                       'streams': macro_streams[:16],
                       'message': 'Macro/VBA storage was detected but is never '
                                  'loaded, decoded or executed; only Workbook '
                                  'stored values are read.'})
    if other_streams:
        issues.append({'code': 'other_container_streams_not_decoded',
                       'count': len(other_streams),
                       'message': 'Only the validated BIFF Workbook stream is '
                                  'interpreted; other streams are listed, not '
                                  'applied.'})
    truncated = result.get('truncated_issue_count', 0)
    if len(issues) > MAX_ISSUES:
        truncated += len(issues) - MAX_ISSUES
        issues = issues[:MAX_ISSUES]
    output = {
        **result,
        'source': {
            'file': str(path) if path is not None else None,
            'extension': Path(path).suffix.lower() if path is not None else '',
            'stream_path': list(chosen),
            'stream_sha256': hashlib.sha256(content).hexdigest(),
            'container_version': container.major,
            'container_compatibility': container.compatibility,
            'container_issues': container.issues,
        },
        'other_container_streams_not_decoded': other_streams,
        **({'macro_storages_not_loaded': macro_streams}
           if macro_streams else {}),
        'container_issues': container.issues,
        'issues': issues,
        'issues_truncated': truncated > 0,
        'truncated_issue_count': truncated,
    }
    output['partial'] = bool(issues) or bool(output['issues_truncated'])
    output['status'] = 'partial' if output['partial'] else 'ok'
    return output


def reconstruct_headers(result, sheet, cell_range, header_rows,
                        formulas='reject'):
    """Adapt a BIFF8 result to the shared header reconstruction algorithm."""
    return reconstruct_biff_headers(result, sheet, cell_range, header_rows,
                                    formulas, max_cells=MAX_CELLS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('--sheet')
    parser.add_argument('--range', dest='cell_range')
    parser.add_argument('--header-rows')
    parser.add_argument('--header-formulas', choices=('reject', 'cached'),
                        default='reject')
    parser.add_argument('--compatibility', choices=READING_MODES,
                        default=DEFAULT_FILE_COMPATIBILITY,
                        help='Deprecated no-op; all values use the tolerant '
                             'reader')
    parser.add_argument('--chart-policy', choices=CHART_POLICIES,
                        default=None,
                        help='Deprecated no-op; charts are always retained as '
                             'bounded opaque bytes')
    parser.add_argument('--salvage-unknown-metadata', action='store_true',
                        help='Deprecated no-op; bounded raw retention is the '
                             'default')
    args = parser.parse_args()
    try:
        result = read_file(args.input,
                           compatibility=args.compatibility,
                           chart_policy=args.chart_policy,
                           salvage_unknown_metadata=args.salvage_unknown_metadata)
        if args.header_rows is not None:
            require(args.sheet is not None and args.cell_range is not None,
                    'Header reconstruction requires explicit '
                    'sheet/range/header-rows')
            result['header_schema'] = reconstruct_headers(
                result, args.sheet, args.cell_range, args.header_rows,
                args.header_formulas)
        elif args.sheet is not None or args.cell_range is not None or \
                args.header_formulas != 'reject':
            raise ValueError('Header options require explicit header-rows')
        from wps_result import encode_result
        encoded = encode_result(result)
        print(encoded)
        return 3 if result.get('status') == 'partial' else 0
    except (ValueError, OSError, struct.error, OverflowError) as exc:
        from wps_access import diagnostic_context
        error_status = getattr(exc, 'status', 'io_error'
                               if isinstance(exc, OSError)
                               else 'invalid_or_unsupported')
        print(json.dumps({'status': error_status,
                          'diagnostic_context': diagnostic_context(
                              error_status, getattr(exc, 'location', {})),
                          'location': getattr(exc, 'location', {}),
                          'message': str(exc),
                          'biff_parsed': False,
                          'native_et_ett_parsed': False},
                         ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
