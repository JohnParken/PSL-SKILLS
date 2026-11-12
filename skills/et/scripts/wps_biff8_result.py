# SPDX-License-Identifier: MIT
"""Worksheet positioning and result assembly mixin for the BIFF8 reader.

The tolerant reader has already decoded the stored values; this stage binds
declared worksheet offsets to framed substreams, retains unbound records as
opaque evidence, attaches additive numeric interpretations and assembles the
stable ``biff8_readonly_v1`` result. Nothing is executed or written back.
"""
from wps_biff8_errors import ResourceLimitError
from wps_biff8_globals import COMMON_END
from wps_biff8_sheet import parse_sheet, scan_substream_end, skeleton_sheet
from wps_numeric import interpret_cell_number
from wps_rich_text import attach_biff_segments


class ResultAssemblyMixin:
    """Result assembly methods mixed into ``wps_biff8.Reader``."""

    def _read_sheets(self, bounds, global_end):
        offset_index = {}
        for position, record in enumerate(self.records):
            offset_index.setdefault(record[0], position)
        aliased = len({item['offset'] for item in bounds}) != len(bounds)
        if aliased:
            self.issue({'code': 'worksheet_offset_aliased',
                        'message': 'Multiple BoundSheet8 records share one '
                                   'substream offset; later duplicates are '
                                   'represented as skipped sheets.'})
        physical = sorted(bounds, key=lambda item: item['offset'])
        position = min(global_end, len(self.records))
        sheets = {}
        for bound in physical:
            target = offset_index.get(bound['offset'])
            if target is None:
                self.issue({'code': 'worksheet_offset_unmatched',
                            'sheet': bound['name'],
                            'declared_offset': bound['offset'],
                            'message': 'BoundSheet8 offset does not match a '
                                       'framed record boundary; the sheet is '
                                       'represented without cells.'})
                self.omissions_affect_cell_values = True
                sheets[bound['name']] = skeleton_sheet(bound,
                                                       'offset_unmatched')
                continue
            if target > position:
                unbound = self.records[position:target]
                self._retain_unbound(unbound, 'globals',
                                     'unbound_worksheet_prelude_retained',
                                     'Records between the globals and the '
                                     'declared worksheet offset retained raw.')
                position = target
            if target < position or target in getattr(self, '_used_offsets',
                                                      set()):
                self.issue({'code': 'worksheet_offset_overlap',
                            'sheet': bound['name'],
                            'declared_offset': bound['offset'],
                            'message': 'Worksheet substream offset overlaps an '
                                       'already consumed substream; the sheet '
                                       'is represented without cells.'})
                self.omissions_affect_cell_values = True
                sheets[bound['name']] = skeleton_sheet(bound,
                                                       'offset_overlap')
                continue
            if not hasattr(self, '_used_offsets'):
                self._used_offsets = set()
            self._used_offsets.add(target)
            substream_type = bound.get('substream_type', 'worksheet')
            if substream_type != 'worksheet':
                stop = scan_substream_end(self.records, target + 1)
                self.skipped_substreams.append(
                    {'sheet': bound['name'],
                     'type': substream_type,
                     'declared_offset': bound['offset'],
                     'record_offset': self.records[target][0]
                     if target < len(self.records) else None})
                self.issue({'code': substream_type + '_substream_skipped',
                            'sheet': bound['name'],
                            'sheet_type_raw': bound.get('sheet_type_raw'),
                            'record_offset': bound['offset'],
                            'message': 'Non-worksheet BoundSheet8 substream '
                                       'retained raw and skipped; no content '
                                       'is loaded or executed.'})
                known_non_cell = substream_type in ('macro', 'chart', 'vba')
                if not known_non_cell:
                    self.omissions_affect_cell_values = True
                sheets[bound['name']] = skeleton_sheet(
                    bound, substream_type,
                    read_status='not_applicable' if known_non_cell else 'not_read')
                position = stop
                continue
            try:
                sheet, position = parse_sheet(self, target, bound)
            except ResourceLimitError:
                raise
            sheets[bound['name']] = sheet
        if position < len(self.records):
            trailing = self.records[position:]
            zero_ok = all(kind == 0 and not payload
                          for _offset, kind, payload in trailing)
            eof_only = all(kind == COMMON_END and not payload
                           for _offset, kind, payload in trailing)
            if zero_ok:
                self.issue({'code': 'zero_trailing_bytes_ignored',
                            'scope': 'trailing',
                            'bytes': sum(4 + len(payload)
                                         for _offset, _kind, payload
                                         in trailing),
                            'message': 'All-zero trailing padding after the '
                                       'last validated EOF ignored.'})
            elif eof_only:
                self.issue({'code': 'trailing_eof_ignored',
                            'scope': 'trailing', 'count': len(trailing),
                            'message': 'Extra empty EOF records after the last '
                                       'sheet retained as diagnostics.'})
            else:
                self._retain_unbound(
                    trailing, 'trailing',
                    'unbound_trailing_records_retained',
                    'Records outside every declared globals/worksheet '
                    'substream retained raw; they cannot be attributed to a '
                    'saved cell.')
        tail = getattr(self.records, 'tail', b'')
        if tail:
            tail_offset = getattr(self.records, 'tail_offset', 0)
            if not tail.strip(b'\x00'):
                self.issue({'code': 'zero_trailing_bytes_ignored',
                            'scope': 'trailing', 'bytes': len(tail),
                            'message': 'All-zero unframable trailing bytes '
                                       'ignored after full-stream framing '
                                       'checks.'})
            else:
                self.salvage_bytes(tail, tail_offset, 'trailing',
                                   name='UnboundTailOpaque',
                                   code='biff_unframed_tail_retained',
                                   message='Bytes after the last complete '
                                           'record cannot form a framed '
                                           'record; retained raw.')
        return sheets

    def _retain_unbound(self, records_slice, scope, code, message):
        for record in records_slice:
            self.salvage_record(record, scope, code=code, message=message)

    # -- post processing ---------------------------------------------------
    def _finalize_cells(self, sheets, date_system):
        rich_fonts = {}
        if self.rich_strings:
            referenced = {cell['sst_index']
                          for sheet in sheets.values()
                          for cell in sheet['cells'].values()
                          if 'sst_index' in cell}
            try:
                rich_fonts = attach_biff_segments(self.rich_strings,
                                                  self.strings,
                                                  self.metadata, referenced)
            except ValueError as exc:
                self.issue({'code': 'rich_string_font_validation_failed',
                            'message': 'Rich string font metadata could not be '
                                       'fully validated: ' + str(exc)})
                rich_fonts = {}
        self.rich_fonts = rich_fonts
        for sheet in sheets.values():
            for cell in sheet['cells'].values():
                if cell['type'] != 'number' or date_system == 'unknown':
                    continue
                try:
                    style = self.styles[cell['xf_index']]
                    interpretation = interpret_cell_number(
                        cell['value'], cell['number_format_id_raw'],
                        cell['custom_format_raw'], date_system,
                        style['raw_hex'], styles=self.styles)
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    self.issue({'code': 'numeric_interpretation_failed',
                                'cell_value_impact':
                                    'none_within_stored_values',
                                'message': 'Stored number kept; additive '
                                           'format interpretation skipped: '
                                           + str(exc)})
                    continue
                interpretation['cache_status'] = cell['cache_status']
                cell['numeric_interpretation'] = interpretation

    def _result(self, bounds, sheets, date_system):
        # Coverage is explicit parser state, not a projection of the bounded
        # diagnostics list: issue truncation must not hide missing caches or
        # value omissions from the result contract.
        uncertain = (self.salvage_data_uncertain
                     or self.omissions_affect_cell_values)
        missing_cache = self.missing_formula_cache
        status = ('partial' if (self.issues or uncertain or missing_cache)
                  else 'ok')
        ordered_sheets = [sheets[item['name']] for item in bounds
                          if item['name'] in sheets]
        sheet_coverage = {
            'declared': len(bounds),
            'read': sum(1 for item in ordered_sheets
                        if item.get('read_status') in ('read', 'partial')),
            'partial': [item['name'] for item in ordered_sheets
                        if item.get('read_status') == 'partial'],
            'not_read': [item['name'] for item in ordered_sheets
                         if item.get('read_status') == 'not_read'],
            'not_applicable': [item['name'] for item in ordered_sheets
                               if item.get('read_status') == 'not_applicable'],
        }
        result = {
            'status': status,
            'schema_version': 'biff8_readonly_v1',
            'compatibility_mode': self.effective_compatibility,
            'reading_policy': 'tolerant',
            'support_level': 'biff8_stored_data_subset',
            'cell_value_coverage': (
                'unverified_due_to_best_effort_salvage' if uncertain else
                'missing_formula_caches' if missing_cache else
                'complete_within_declared_sheets'),
            'omissions_affect_cell_values': uncertain or missing_cache,
            'biff_parsed': True,
            'sheet_coverage': sheet_coverage,
            'chart_policy': self.chart_policy,
            'charts_decoded': False,
            'embedded_charts': self.embedded_charts,
            **({'note_drawings': self.note_drawings}
               if self.note_drawings else {}),
            **({'opaque_objects': self.opaque_objects}
               if self.opaque_objects else {}),
            'native_et_ett_parsed': False,
            'wps_compatibility_verified': False,
            'formulas_executed': False,
            'external_links_followed': False,
            'date_system': date_system,
            'code_page_raw': self.code_page,
            'format_string_header_flags': self.format_string_flags,
            'sheets': ordered_sheets,
            'shared_string_formatting': [
                dict((key, value) for key, value in rich.items()
                     if key != 'runs_bytes')
                | {'runs_raw_hex': rich['runs_bytes'].hex()}
                for rich in self.rich_strings],
            **({'rich_string_fonts': self.rich_fonts}
               if getattr(self, 'rich_fonts', None) else {}),
            'shared_string_phonetics': [
                dict((key, value) for key, value in phonetic.items()
                     if key != 'ext_bytes')
                | {'raw_ext_hex': phonetic['ext_bytes'].hex()}
                for phonetic in self.phonetic_strings],
            'styles': self.styles,
            **({'table_style_sources': self.table_style_sources}
               if self.table_style_sources else {}),
            'custom_number_formats': {
                str(key): value for key, value in self.formats.items()
                if key not in self.omitted_display_formats},
            **({'custom_number_formats_omitted':
                sorted(str(key) for key in self.omitted_display_formats)}
               if self.omitted_display_formats else {}),
            'output_text_budget': {
                'value_units': self.output_text_units,
                'value_limit': self.limits['max_output_text_units'],
                'display_units': self.display_text_units,
                'display_limit': self.limits['max_display_text_units'],
                'display_limited': self.display_text_limited,
            },
            'uninterpreted_metadata': self.metadata,
            'issues': self.issues,
            'issues_truncated': self.truncated_issue_count > 0,
            'truncated_issue_count': self.truncated_issue_count,
            'metadata_truncated_count': self.metadata_truncated_count,
            'structured_metadata_truncated_count':
                self.structured_metadata_truncated,
            'structured_metadata_entries': self.structured_row_count,
            'omitted_cell_records': self.omitted_cell_records,
            'salvage_unknown_record_count':
                self.salvage_unknown_record_count,
            'salvage_continue_record_count': self.salvage_continue_records,
            **({'skipped_substreams': self.skipped_substreams}
               if self.skipped_substreams else {}),
            'record_count': len(self.records),
            'cell_count': self.cell_count,
            'policies': {
                'numbers': 'Exact Decimal representation of stored IEEE754; '
                           'raw bytes retained; no text coercion',
                'dates': 'All stored numeric serials remain numbers; raw '
                         'XF/format IDs and custom strings retained; no ISO '
                         'conversion or effective-style inheritance inference',
                'formulas': 'Opaque tokens/ancillary bytes only, never '
                            'decompiled or executed; every present cache is '
                            'unverified and may be stale',
                'unknown_records': 'Bounded opaque records retained raw with '
                                   'unknown coverage diagnostics; unclassified '
                                   'content is never executed or followed',
                'scope': 'Record-subset extraction, not full MS-XLS grammar '
                         'conformance or real ET/ETT acceptance'},
        }
        if self.salvage_requested:
            result.update(salvage_requested=True,
                          saved_value_completeness_certified=False)
            result['policies']['unknown_records'] = (
                'Best-effort unknown records stored raw with uncertain '
                'cell value coverage; not a certified MS-XLS or ET read')
        return result

    def sheet(self, index, bound):
        """Compatibility wrapper around the split worksheet parser."""
        return parse_sheet(self, index, bound)


