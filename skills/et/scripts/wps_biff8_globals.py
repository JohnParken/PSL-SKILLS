# SPDX-License-Identifier: MIT
"""Tolerant BIFF8 global-substream reading mixin.

Every recognized global record is decoded when it is value/control relevant
and retained raw otherwise. Malformed, duplicate or unsupported global
metadata no longer aborts the workbook: it is reported, and worksheet parsing
continues from the recorded bounds. Encryption remains a framing-level hard
refusal handled before this stage.
"""
import struct

from wps_biff8_charts import drawing_frame
from wps_biff8_errors import ResourceLimitError, require
from wps_biff8_links import (CONTINUE, CRN, LINK_RECORDS, SupportingLinks)
from wps_biff8_noncell import inspect_noncell
from wps_biff8_names import (UnsafeDefinedName,
                             classify_static_builtin_name,
                             classify_static_name,
                             normalize_data_name_header)
from wps_biff8_records import (LEGACY_GLOBAL_METADATA,
                               LEGACY_VALIDATED_GLOBAL_METADATA, bof)
from wps_biff8_tablestyles import table_style_group
from wps_biff8_values import unicode_string
from wps_dxf import validate_dxf

SUPPORTED_BIFF8_CODE_PAGES = {874, 932, 936, 949, 950, 1200, 1250, 1251, 1252}
PALETTE_COLOR_COUNT = 56
COMMON_END = 0x000A
SHEET_TYPE_NAMES = {1: 'macro', 2: 'chart', 6: 'vba', 0: 'worksheet'}


class GlobalSubstreamMixin:
    """Global-substream parsing methods mixed into ``wps_biff8.Reader``.

    The concrete reader calls ``_init_global_counters`` once during its
    initialization, before any parsing method can consume these counters.
    """

    def _init_global_counters(self):
        """Initialize per-reader budgets; never share mutable parse state."""
        self.metadata_bytes = 0
        self.dxf_property_count = 0
        self.dxf_record_count = 0

    def parse(self):
        self.note_options()
        require(self.records and self.records[0][1] == 0x0809,
                'Stream must start with a BIFF8 globals BOF')
        bof(self.records[0], 5)
        bounds = []
        names = set()
        date_system = None
        seen_sst = False
        global_end = None
        template_seen = False
        links = SupportingLinks(allow_opaque=True)
        internal_refs = links.local_refs
        print_areas = set()
        safe_name_keys = set()
        safe_name_sheets = []
        static_builtin_keys = set()
        consumed_until = 0
        index = 1
        while index < len(self.records):
            if index < consumed_until:
                index += 1
                continue
            record = self.records[index]
            offset, kind, payload = record
            self.current_location = {'record_offset': offset,
                                     'record_id': hex(kind),
                                     'scope': 'globals'}
            index += 1
            try:
                if kind == COMMON_END:
                    if payload:
                        self.issue({'code': 'globals_eof_payload_ignored',
                                    'record_offset': offset,
                                    'message': 'Globals EOF payload retained '
                                               'raw.'})
                        self.salvage_record(record, 'globals',
                                            code='globals_eof_payload_ignored',
                                            may_affect_cell_values=False)
                    global_end = index
                    break
                if kind == 0x0085:
                    index = self._read_boundsheet(record, bounds, names,
                                                  index)
                    continue
                if kind == 0x0086:
                    self._read_writeprotect(record, index)
                    continue
                if kind == 0x0022:
                    date_system = self._read_date1904(record, payload,
                                                      date_system)
                    continue
                if kind == 0x00E0:
                    self._read_xf(record, payload)
                    continue
                if kind == 0x041E:
                    self._read_format(record, payload)
                    continue
                if kind == 0x00FC:
                    index, seen_sst = self._read_sst(record, payload, index,
                                                     seen_sst)
                    continue
                if kind == 0x088D:
                    self._read_dxf(record, payload)
                    continue
                if kind == 0x088F:
                    index = self._read_table_style(record, index)
                    continue
                if kind == 0x088E:
                    self.table_styles_collection_seen = True
                    self.metadata_record(record, self.global_metadata,
                                         'globals')
                    continue
                if kind == 0x0060:
                    template_seen = self._read_template(record, template_seen)
                    continue
                if kind in LINK_RECORDS:
                    index, link_detail = self._read_links(record, index, links)
                    continue
                if kind == 0x0018:
                    index = self._read_defined_name(
                        record, payload, index, internal_refs, len(bounds),
                        print_areas, safe_name_keys, safe_name_sheets,
                        static_builtin_keys)
                    continue
                if kind in LEGACY_VALIDATED_GLOBAL_METADATA:
                    self._read_palette(record)
                    continue
                if kind == 0x00EB:
                    self._read_drawing_group(record)
                    continue
                if kind == 0x0042:
                    self._read_codepage(record, payload)
                    continue
                if kind in self.global_metadata:
                    if kind == 0x088E:
                        self.table_styles_collection_seen = True
                    self.metadata_record(record, self.global_metadata,
                                         'globals')
                    continue
                if kind == CONTINUE:
                    self.issue({'code': 'unexpected_continue_record',
                                'scope': 'globals', 'record_offset': offset,
                                'record_id': hex(kind),
                                'message': 'Continue record outside a '
                                           'string/chain context retained '
                                           'raw.'})
                    self.salvage_record(record, 'globals',
                                        code='unexpected_continue_record')
                    continue
                if kind in LEGACY_GLOBAL_METADATA:
                    self.metadata_record(record, self.global_metadata,
                                         'globals')
                    continue
                index = self.unknown_record(self.records, index - 1, 'globals')
            except ResourceLimitError:
                raise
            except ValueError as exc:
                self.issue({'code': 'global_record_validation_failed',
                            'scope': 'globals', 'record_offset': offset,
                            'record_id': hex(kind), 'message': str(exc)})
                self.salvage_record(record, 'globals',
                                    code='global_record_validation_failed')
        if global_end is None:
            self.issue({'code': 'globals_eof_missing', 'scope': 'globals',
                        'message': 'Globals substream ended without EOF; '
                                   'declared worksheet offsets are validated '
                                   'against the framed records.'})
            global_end = len(self.records)
        if date_system is None:
            date_system = 'unknown'
            self.issue({'code': 'date_system_missing',
                        'message': 'Mandatory BIFF8 Date1904 absent; saved '
                                   'numeric serials are retained without date '
                                   'interpretation.'})
        if not bounds:
            self.issue({'code': 'worksheet_list_missing',
                        'message': 'No valid BoundSheet8 record was found; no '
                                   'worksheet cells can be attributed.'})
            self.omissions_affect_cell_values = True
        if not self.styles:
            self.issue({'code': 'xf_table_missing',
                        'message': 'No XF record was parsed; style 0 fallback '
                                   'retains every stored cell value.'})
            self.styles = [{'xf_index': 0, 'number_format_id_raw': 0,
                            'raw_hex': '', 'synthetic_fallback': True}]
        if not all(last < len(bounds) for _first, last in internal_refs):
            self.issue({'code': 'internal_reference_out_of_range',
                        'message': 'An internal sheet reference exceeds the '
                                   'declared worksheet count; references are '
                                   'retained unbound.'})
        if not all(sheet <= len(bounds) for sheet in print_areas):
            self.issue({'code': 'print_area_out_of_range',
                        'message': 'A static Print_Area reference exceeds the '
                                   'declared worksheet count.'})
        if links.external_book_count:
            self.issue({'code': 'external_links_retained_opaque',
                        'scope': 'globals',
                        'count': links.external_book_count,
                        'message': 'Supporting links retain original indices. '
                                   'Formula cells use only saved Workbook '
                                   'caches.'})
        if not all((itab == 0 or itab <= len(bounds)) and sheet < len(bounds)
                   for itab, sheet in safe_name_sheets):
            self.issue({'code': 'name_scope_out_of_range',
                        'message': 'A static name scope or internal sheet '
                                   'reference exceeds the BoundSheet8 count.'})
        if self.rich_strings:
            self.issue({'code': 'rich_string_formatting_not_applied',
                        'message': 'SST text, supported segments and '
                                   'FormatRun bytes retained once by SST '
                                   'index. Font rendering and layout are not '
                                   'applied.'})
        if self.phonetic_strings:
            self.issue({'code': 'phonetic_string_data_not_decoded',
                        'message': 'SST text extracted; ExtRst bytes retained '
                                   'raw once by SST index. Phonetic '
                                   'annotations are not decoded or applied.'})
        sheets = self._read_sheets(bounds, global_end)
        self._finalize_cells(sheets, date_system)
        return self._result(bounds, sheets, date_system)

    def _read_writeprotect(self, record, index):
        offset, kind, payload = record
        if index != 2:
            self.issue({'code': 'writeprotect_metadata_order',
                        'record_offset': offset,
                        'message': 'WriteProtect should be the first global '
                                   'record; position retained in the '
                                   'diagnostic.'})
        try:
            evidence = inspect_noncell(0x0086, payload, 'globals')
        except ValueError as exc:
            self.issue({'code': 'writeprotect_metadata_invalid',
                        'record_offset': offset, 'message': str(exc)})
            self.salvage_record(record, 'globals',
                                name='WriteProtectOpaque',
                                code='writeprotect_metadata_invalid',
                                may_affect_cell_values=False)
            return
        metadata_index = self.metadata_record(
            record, {kind: 'WriteProtectValidated'}, 'globals')
        if metadata_index is not None:
            self.metadata[metadata_index]['structure'] = evidence

    # -- global record handlers --------------------------------------------
    def _read_boundsheet(self, record, bounds, names, index):
        offset, kind, payload = record
        if len(payload) < 8:
            self.issue({'code': 'boundsheet_invalid', 'record_offset': offset,
                        'message': 'Truncated BoundSheet8 record retained '
                                   'raw.'})
            self.salvage_record(record, 'globals',
                                code='boundsheet_invalid',
                                may_affect_cell_values=False)
            return index
        try:
            position = struct.unpack_from('<I', payload)[0]
            hidden = payload[4] & 3
            sheet_type = payload[5]
            name, end = unicode_string(payload, 6, True)
            if end != len(payload) or not 1 <= payload[6] <= 31:
                raise ValueError('BoundSheet8 name length/encoding mismatch')
        except (ValueError, struct.error) as exc:
            self.issue({'code': 'boundsheet_invalid', 'record_offset': offset,
                        'message': 'Malformed BoundSheet8 retained raw: '
                                   + str(exc)})
            self.salvage_record(record, 'globals', code='boundsheet_invalid',
                                may_affect_cell_values=False)
            return index
        if hidden not in (0, 1, 2):
            self.issue({'code': 'boundsheet_hidden_state_invalid',
                        'record_offset': offset, 'hidden_state_raw': hidden,
                        'message': 'Invalid BoundSheet8 hidden state retained; '
                                   'sheet is treated as visible.'})
            hidden = 0
        if any(char in name for char in '\0\x03:\\*?/[]') or \
                name.startswith("'") or name.endswith("'"):
            self.issue({'code': 'worksheet_name_invalid',
                        'record_offset': offset,
                        'message': 'Worksheet name contains characters outside '
                                   'the supported set; the name is retained '
                                   'as saved.'})
        if name.casefold() in names:
            self.issue({'code': 'worksheet_name_duplicate',
                        'record_offset': offset,
                        'message': 'Duplicate worksheet name; a stable suffix '
                                   'is appended for this result.'})
            name = name + '~' + str(len(bounds))
        if len(bounds) >= self.limits['max_sheets']:
            self.issue({'code': 'sheet_count_budget_truncated',
                        'record_offset': offset,
                        'message': 'Worksheet count budget exceeded; later '
                                   'sheets are not attributed to a '
                                   'substream.'})
            return index
        names.add(name.casefold())
        bounds.append({'name': name, 'offset': position,
                       'hidden_state': hidden,
                       'sheet_type_raw': sheet_type,
                       'substream_type': SHEET_TYPE_NAMES.get(sheet_type,
                                                              'unknown')})
        return index

    def _read_date1904(self, record, payload, date_system):
        offset, kind, _ = record
        try:
            if len(payload) != 2:
                raise ValueError('Date1904 payload must be two bytes')
            flag = struct.unpack('<H', payload)[0]
            if flag not in (0, 1):
                raise ValueError('Invalid Date1904 value')
        except ValueError as exc:
            self.issue({'code': 'date_1904_invalid', 'record_offset': offset,
                        'message': 'Malformed Date1904 ignored: ' + str(exc)})
            self.salvage_record(record, 'globals', code='date_1904_invalid',
                                may_affect_cell_values=False)
            return date_system
        resolved = '1904' if flag else '1900'
        if date_system is not None:
            self.issue({'code': 'date_1904_duplicate',
                        'record_offset': offset, 'first': date_system,
                        'later': resolved,
                        'message': 'Duplicate Date1904 declaration; the first '
                                   'value is used.'})
            return date_system
        return resolved

    def _read_xf(self, record, payload):
        offset, kind, _ = record
        if len(payload) != 20:
            self.issue({'code': 'xf_record_invalid', 'record_offset': offset,
                        'message': 'Malformed XF record (payload must be 20 '
                                   'bytes) retained raw; its index slot is '
                                   'kept so later XF references stay aligned.'})
            self.salvage_record(record, 'globals', name='XFOpaque',
                                code='xf_record_invalid',
                                may_affect_cell_values=False)
            if len(self.styles) < self.limits['max_xf_styles']:
                self.styles.append({
                    'xf_index': len(self.styles),
                    'number_format_id_raw': 0,
                    'raw_hex': payload.hex(),
                    'valid': False,
                    'opaque_reason': 'malformed_xf_payload',
                    'record_offset': offset,
                })
            return
        if len(self.styles) >= self.limits['max_xf_styles']:
            event = self.grouped_issue(('xf_budget_truncated',), {
                'code': 'xf_budget_truncated', 'record_offset': offset,
                'count': 0,
                'cell_value_impact': 'cells_referencing_later_xfs_fall_back',
                'message': 'XF table budget exceeded; later XF records are not '
                           'indexed and cells referencing them fall back to '
                           'style 0 with a diagnostic.'})
            if event is not None:
                event['count'] += 1
            self.salvage_record(record, 'globals', name='XFOpaque',
                                code='xf_budget_truncated',
                                may_affect_cell_values=False)
            return
        self.styles.append({'xf_index': len(self.styles),
                            'number_format_id_raw':
                                struct.unpack_from('<H', payload, 2)[0],
                            'raw_hex': payload.hex()})

    def _read_format(self, record, payload):
        offset, kind, _ = record
        try:
            if len(payload) < 5:
                raise ValueError('Truncated Format record')
            identity = struct.unpack_from('<H', payload)[0]
            value, end = unicode_string(payload, 2, ignore_reserved_flags=True)
            if end != len(payload) or not 1 <= len(value) <= 255:
                raise ValueError('Malformed number format')
            if identity in self.formats:
                raise ValueError('Duplicate number format identity')
        except ValueError as exc:
            self.issue({'code': 'format_record_invalid',
                        'record_offset': offset,
                        'message': 'Malformed/duplicate Format record retained '
                                   'raw: ' + str(exc)})
            self.salvage_record(record, 'globals', name='FormatOpaque',
                                code='format_record_invalid',
                                may_affect_cell_values=False)
            return
        self.formats[identity] = value
        if payload[4] & 0xFE:
            self.format_string_flags[str(identity)] = {
                'flags_raw': payload[4], 'record_offset': offset}
            self.plain_string_flags(record, payload[4], 'globals')

    def _read_sst(self, record, payload, index, seen_sst):
        offset, kind, _ = record
        if seen_sst:
            self.issue({'code': 'sst_duplicate_ignored',
                        'record_offset': offset,
                        'message': 'Duplicate SST record ignored; the first '
                                   'shared-string table is used.'})
            self.salvage_record(record, 'globals', name='SSTOpaque',
                                code='sst_duplicate_ignored',
                                may_affect_cell_values=True)
            return index, seen_sst
        seen_sst = True
        if len(payload) < 8:
            self.issue({'code': 'sst_record_invalid', 'record_offset': offset,
                        'message': 'Truncated SST header; shared strings are '
                                   'unavailable.'})
            self.salvage_record(record, 'globals', name='SSTOpaque',
                                code='sst_record_invalid',
                                may_affect_cell_values=True)
            return index, seen_sst
        total, unique = struct.unpack_from('<ii', payload)
        if total < 0 or unique < 0 or unique > total:
            self.issue({'code': 'sst_counts_invalid', 'record_offset': offset,
                        'declared_total': total, 'declared_unique': unique,
                        'message': 'Invalid SST reference/unique counts; the '
                                   'declared unique count is clamped to the '
                                   'declared total.'})
            total = max(0, total)
            unique = min(max(0, unique), total)
        if unique > self.limits['max_shared_strings']:
            raise ResourceLimitError('SST unique string count budget exceeded')
        strings = self.continued_strings(self.records, index - 1, 8)
        parsed = 0
        while parsed < unique:
            metadata_budget = self.limits['max_metadata_bytes'] - \
                self.metadata_bytes
            entries_remaining = self.limits['max_rich_phonetic_entries'] - \
                len(self.rich_strings) - len(self.phonetic_strings)
            saved = (strings.index, strings.position)
            try:
                value = strings.string(
                    allow_rich=True, metadata_budget=metadata_budget,
                    allow_phonetic=True,
                    metadata_entries_remaining=entries_remaining)
                rich = strings.last_rich
                phonetic = strings.last_phonetic
            except ResourceLimitError:
                strings.index, strings.position = saved
                # Rich/phonetic bytes are metadata; keep the readable text and
                # advance past the unretained run/phonetic payloads.
                self.issue({'code': 'rich_phonetic_metadata_budget_truncated',
                            'record_offset': offset,
                            'sst_index': len(self.strings),
                            'message': 'Rich/phonetic string metadata budget '
                                       'exhausted; plain string text is '
                                       'retained without run bytes.'})
                value = strings.string(allow_rich=True, allow_phonetic=True,
                                       retain_metadata=False)
                rich = phonetic = None
            self.strings.append(value)
            parsed += 1
            if rich is not None:
                self.rich_strings.append({**rich,
                                          'sst_index': len(self.strings) - 1})
            if phonetic is not None:
                self.phonetic_strings.append(
                    {**phonetic, 'sst_index': len(self.strings) - 1})
            size = (len(rich['runs_bytes']) if rich is not None else 0) + \
                (len(phonetic['ext_bytes']) if phonetic is not None else 0)
            self.metadata_bytes += size
        try:
            end_index = strings.finish()
        except ValueError as exc:
            self.issue({'code': 'sst_trailing_bytes_retained',
                        'record_offset': offset,
                        'message': 'Unparsed shared-string tail retained raw: '
                                   + str(exc)})
            payload_tail = self.records[strings.index][2][strings.position:]
            self.salvage_bytes(payload_tail, offset, 'globals',
                               name='SSTTrailingBytesOpaque',
                               code='sst_trailing_bytes_retained',
                               message='Unparsed SST bytes retained raw.')
            end_index = strings.index + 1
        # Consume any remaining Continue records belonging to this SST block.
        cursor = end_index
        while cursor < len(self.records) and \
                self.records[cursor][1] == CONTINUE and \
                cursor - end_index < self.limits['max_continue_chain']:
            self.salvage_record(self.records[cursor], 'globals',
                                name='SSTContinuationOpaque',
                                code='sst_extra_continue_retained',
                                may_affect_cell_values=True)
            cursor += 1
        return cursor, seen_sst

    def _read_dxf(self, record, payload):
        offset, kind, _ = record
        try:
            structure = validate_dxf(payload, formatting_policy='preserve')
        except ValueError as exc:
            self.issue({'code': 'dxf_validation_failed',
                        'record_offset': offset,
                        'message': 'Malformed DXF formatting metadata retained '
                                   'raw: ' + str(exc)})
            self.salvage_record(record, 'globals', name='DXFOpaque',
                                code='dxf_validation_failed',
                                may_affect_cell_values=False)
            return
        self.dxf_property_count += structure['property_count']
        if self.dxf_property_count > self.limits['max_dxf_properties']:
            self.issue({'code': 'dxf_budget_truncated',
                        'record_offset': offset,
                        'message': 'DXF aggregate property budget exceeded; '
                                   'later DXF metadata is retained raw only.'})
            self.salvage_record(record, 'globals', name='DXFOpaque',
                                code='dxf_budget_truncated',
                                may_affect_cell_values=False)
            return
        index = self.metadata_record(record, {kind: 'DXF'}, 'globals')
        if index is None:
            return
        self.metadata[index]['structure'] = structure
        if structure['formatting_issues']:
            self.issue({'code': 'dxf_formatting_issues_retained',
                        'record_id': hex(kind), 'record_offset': offset,
                        'formatting_issue_count':
                            len(structure['formatting_issues']),
                        'metadata_index': index,
                        'cell_value_impact': 'none_within_parsed_cells',
                        'message': 'Known DXF metadata retained without '
                                   'applying invalid or unsupported '
                                   'formatting; see '
                                   'structure.formatting_issues'})
        self.dxf_record_count += 1

    def _read_table_style(self, record, index):
        offset, kind, _ = record
        if not self.table_styles_collection_seen:
            self.issue({'code': 'tablestyle_without_collection',
                        'record_offset': offset,
                        'message': 'TableStyle record without a preceding '
                                   'TableStyles collection retained raw.'})
            self.salvage_record(record, 'globals', name='TableStyleOpaque',
                                code='tablestyle_without_collection',
                                may_affect_cell_values=False)
            return index
        if len(self.table_style_sources) >= self.limits['max_templates'] \
                if False else len(self.table_style_sources) >= 128:
            self.issue({'code': 'tablestyle_budget_truncated',
                        'record_offset': offset,
                        'message': 'TableStyle collection budget exceeded; '
                                   'later groups are retained raw only.'})
            self.salvage_record(record, 'globals', name='TableStyleOpaque',
                                code='tablestyle_budget_truncated',
                                may_affect_cell_values=False)
            return index
        try:
            next_index, source, components = table_style_group(
                self.records, index - 1, self.dxf_record_count)
        except ValueError as exc:
            self.issue({'code': 'tablestyle_validation_failed',
                        'record_offset': offset,
                        'message': 'Invalid TableStyle group retained raw: '
                                   + str(exc)})
            self.salvage_record(record, 'globals', name='TableStyleOpaque',
                                code='tablestyle_validation_failed',
                                may_affect_cell_values=False)
            return index
        for component in components:
            name = 'TableStyle' if component[1] == 0x088F \
                else 'TableStyleElement'
            self.metadata_record(component, {component[1]: name}, 'globals')
        self.table_style_sources.append(source)
        return next_index

    def _read_template(self, record, template_seen):
        offset, kind, payload = record
        if template_seen or payload:
            self.issue({'code': 'template_record_invalid',
                        'record_offset': offset,
                        'message': 'Duplicate/malformed Template marker '
                                   'retained raw.'})
            self.salvage_record(record, 'globals', name='TemplateOpaque',
                                code='template_record_invalid',
                                may_affect_cell_values=False)
            return template_seen
        self.metadata_record(record, {kind: 'Template'}, 'globals')
        return True

    def _read_links(self, record, index, links):
        offset, kind, _ = record
        try:
            consumed_until, (link_name, details) = links.read(self.records,
                                                              index - 1)
        except ValueError as exc:
            self.issue({'code': 'link_metadata_invalid',
                        'record_offset': offset, 'record_id': hex(kind),
                        'message': 'Malformed supporting-link metadata retained '
                                   'raw: ' + str(exc)})
            self.salvage_record(record, 'globals', name='LinkOpaque',
                                code='link_metadata_invalid',
                                may_affect_cell_values=False)
            return index, None
        metadata_index = self.metadata_record(record, {kind: link_name},
                                              'globals')
        if metadata_index is not None:
            self.metadata[metadata_index]['structure'] = details
        for component in self.records[index:consumed_until]:
            self.metadata_record(component,
                                 {CRN: 'ExternalCellCacheOpaque',
                                  CONTINUE: 'ExternSheetContinuation'},
                                 'globals')
        return consumed_until, details

    def _read_defined_name(self, record, payload, index, internal_refs,
                           sheet_count, print_areas, safe_name_keys,
                           safe_name_sheets, static_builtin_keys):
        offset, kind, _ = record
        payload, header_issue = normalize_data_name_header(payload)
        if header_issue:
            self.issue({**header_issue, 'record_id': hex(kind),
                        'record_offset': offset, 'scope': 'globals'})
        static_print_area = (
            len(payload) == 27 and
            payload[:8] == b'\x20\x00\x00\x01\x0b\x00\x00\x00' and
            payload[10:16] == b'\x00\x00\x00\x00\x00\x06' and
            payload[16] == 0x3B)
        if static_print_area:
            sheet = struct.unpack_from('<H', payload, 8)[0]
            ref, r1, r2, c1, c2 = struct.unpack_from('<5H', payload, 17)
            if not (1 <= sheet <= 128 and sheet not in print_areas
                    and ref < len(internal_refs)
                    and internal_refs[ref] == (sheet - 1, sheet - 1)
                    and r1 <= r2 and c1 <= c2 < 256):
                self.issue({'code': 'print_area_invalid',
                            'record_offset': offset,
                            'message': 'Invalid/duplicate/nonlocal Print_Area '
                                       'name retained raw.'})
                self.salvage_record(record, 'globals',
                                    name='DefinedNameOpaque',
                                    code='print_area_invalid',
                                    may_affect_cell_values=False)
                return index
            print_areas.add(sheet)
            self.metadata_record(record, {kind: 'PrintAreaStatic'}, 'globals')
            return index
        if len(safe_name_keys) + len(static_builtin_keys) >= \
                self.limits['max_static_names']:
            self.issue({'code': 'static_name_budget_truncated',
                        'record_offset': offset,
                        'message': 'Static defined-name budget exceeded; this '
                                   'name is retained raw only.'})
            self.salvage_record(record, 'globals', name='DefinedNameOpaque',
                                code='static_name_budget_truncated',
                                may_affect_cell_values=False)
            return index
        builtin = len(payload) >= 16 and payload[0] in (0x20, 0x21)
        classifier = classify_static_builtin_name if builtin \
            else classify_static_name
        try:
            structure = classifier(payload, max(sheet_count, 128),
                                   internal_refs)
        except UnsafeDefinedName as exc:
            self.issue({'code': 'defined_name_retained_opaque',
                        'record_offset': offset,
                        'message': 'Defined name outside the verified static '
                                   'subset retained raw: ' + str(exc)})
            self.salvage_record(record, 'globals', name='DefinedNameOpaque',
                                code='defined_name_retained_opaque',
                                may_affect_cell_values=False)
            return index
        if builtin:
            key = (structure['local_sheet_ordinal_raw'],
                   structure['builtin_name_id_raw'])
            if key in static_builtin_keys:
                self.issue({'code': 'defined_name_duplicate',
                            'record_offset': offset,
                            'message': 'Duplicate built-in static Name '
                                       'retained raw.'})
                self.salvage_record(record, 'globals',
                                    name='DefinedNameOpaque',
                                    code='defined_name_duplicate',
                                    may_affect_cell_values=False)
                return index
            static_builtin_keys.add(key)
            source_name = ('PrintTitlesStatic'
                           if structure['builtin_name_id_raw'] == 0x07
                           else 'FilterDatabaseStatic')
        else:
            key = (structure['local_sheet_ordinal_raw'],
                   structure['name_folded_sha256'])
            if key in safe_name_keys:
                self.issue({'code': 'defined_name_duplicate',
                            'record_offset': offset,
                            'message': 'Duplicate ordinary static name '
                                       'retained raw.'})
                self.salvage_record(record, 'globals',
                                    name='DefinedNameOpaque',
                                    code='defined_name_duplicate',
                                    may_affect_cell_values=False)
                return index
            safe_name_keys.add(key)
            source_name = ('OrdinaryStaticInternalCellName'
                           if structure['classification'] ==
                           'ordinary_static_internal_cell_only'
                           else 'OrdinaryStaticInternalAreaName')
        safe_name_sheets.append((structure['local_sheet_ordinal_raw'],
                                 structure['internal_sheet_index_zero_based']))
        metadata_index = self.metadata_record(record, {kind: source_name},
                                              'globals')
        if metadata_index is not None:
            self.metadata[metadata_index]['structure'] = structure
        return index

    def _read_palette(self, record):
        offset, kind, payload = record
        if len(payload) != 2 + 4 * PALETTE_COLOR_COUNT or \
                struct.unpack_from('<H', payload)[0] != PALETTE_COLOR_COUNT:
            self.issue({'code': 'palette_invalid', 'record_offset': offset,
                        'message': 'Invalid Palette record retained raw.'})
            self.salvage_record(record, 'globals', name='PaletteOpaque',
                                code='palette_invalid',
                                may_affect_cell_values=False)
            return
        self.metadata_record(record, {kind: 'Palette'}, 'globals')

    def _read_drawing_group(self, record):
        offset, kind, payload = record
        if self.drawing_group_seen:
            self.issue({'code': 'drawing_group_duplicate',
                        'record_offset': offset,
                        'message': 'Duplicate MsoDrawingGroup retained raw.'})
            self.salvage_record(record, 'globals',
                                name='MsoDrawingGroupOpaque',
                                code='drawing_group_duplicate',
                                may_affect_cell_values=False)
            return
        try:
            unknown = drawing_frame(payload, 0xF000,
                                    allow_unknown_atoms=True)
        except ValueError as exc:
            self.issue({'code': 'drawing_group_invalid',
                        'record_offset': offset,
                        'message': 'Invalid MsoDrawingGroup retained raw: '
                                   + str(exc)})
            self.salvage_record(record, 'globals',
                                name='MsoDrawingGroupOpaque',
                                code='drawing_group_invalid',
                                may_affect_cell_values=False)
            return
        if unknown:
            self.salvage_data_uncertain = True
            self.issue({'code': 'unknown_officeart_atoms', 'scope': 'globals',
                        'record_offset': offset, 'records': unknown[:256],
                        'cell_value_impact': 'unknown',
                        'message': 'Unknown OfficeArt records retained raw; '
                                   'object semantics and value impact are '
                                   'unverified.'})
        self.drawing_group_seen = True
        self.metadata_record(record, {kind: 'MsoDrawingGroupOpaque'},
                             'globals')

    def _read_codepage(self, record, payload):
        offset, kind, _ = record
        if len(payload) != 2:
            self.issue({'code': 'codepage_record_invalid',
                        'record_offset': offset,
                        'message': 'CodePage payload must be two bytes; the '
                                   'declaration is retained raw.'})
            self.salvage_record(record, 'globals', name='CodePageOpaque',
                                code='codepage_record_invalid',
                                may_affect_cell_values=False)
            return
        code_page = struct.unpack('<H', payload)[0]
        if code_page not in SUPPORTED_BIFF8_CODE_PAGES:
            self.issue({'code': 'unknown_codepage_declaration',
                        'record_id': hex(kind), 'record_offset': offset,
                        'code_page_raw': code_page,
                        'message': 'Unknown code-page declaration retained; '
                                   'BIFF8 Unicode fields are decoded by their '
                                   'own flags only.'})
        elif code_page != 1200:
            self.issue({'code': 'codepage_metadata_not_applied',
                        'record_offset': offset, 'record_id': hex(kind),
                        'code_page': code_page,
                        'message': 'Supported BIFF8 text fields use their own '
                                   'Unicode flags; code page is retained, not '
                                   'used to guess byte encodings.'})
        if self.code_page is not None and self.code_page != code_page:
            self.issue({'code': 'conflicting_codepage_declaration',
                        'record_offset': offset,
                        'previous_code_page': self.code_page,
                        'later_code_page': code_page,
                        'message': 'Conflicting CodePage declarations; the '
                                   'first value is retained.'})
        elif self.code_page is None:
            self.code_page = code_page
        self.metadata_record(record, self.global_metadata, 'globals')

    # -- worksheet substreams ----------------------------------------------
