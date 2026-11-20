#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded stateful RTF token/group parser used by wps_rtf_read."""

import codecs
import re
from typing import Any, Dict, List, Optional

from wps_access import ParseError
from wps_rtf_visibility import RtfVisibility


RELATED_DESTINATIONS = {
    'header', 'footer', 'headerl', 'headerr', 'headerf',
    'footerl', 'footerr', 'footerf', 'footnote', 'annotation',
}
PINNED_ISSUE_CODES = {
    'rtf_field_cached_result_unverified', 'rtf_hidden_text_unfiltered',
    'rtf_revisions_unfiltered', 'rtf_font_encoding_unresolved',
    'rtf_encoding_assumed', 'rtf_visibility_unknown',
    'rtf_revision_view_unknown', 'rtf_object_not_decoded',
}
DBCS_CODEPAGES = {'cp936', 'cp950', 'cp932', 'cp949', 'cp1361'}
REVISION_CONTROLS = {
    'deleted', 'revised', 'revauth', 'revauthdel',
    'revdttm', 'revdttmdel', 'insrsid', 'delrsid',
}
CHARSET_CODEPAGES = {
    128: 932, 129: 949, 130: 1361, 134: 936, 136: 950,
    161: 1253, 162: 1254, 163: 1258, 177: 1255, 178: 1256,
    186: 1257, 204: 1251, 222: 874, 238: 1250, 255: 437,
}
IGNORED_DESTINATIONS = {
    'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict',
    'header', 'footer', 'themedata', 'xmlnstbl', 'rsidtbl', 'generator',
}


class RtfStreamParser:
    """Single-pass bounded RTF byte-stream parser with recovery diagnostics."""

    def __init__(self, data: bytes, *, hidden_text: str, revisions,
                 object_policy: str, include_related: bool, limits: Dict[str, int]):
        self.data = data
        self.text = data.decode('latin-1')
        self.length = len(self.text)
        self.idx = 0
        self.hidden_text = hidden_text
        self.revisions = revisions
        self.object_policy = object_policy
        self.include_related = include_related
        self.limits = limits

        self.group_depth = 0
        self.paragraph_format: Dict[str, Any] = {}
        self.page_properties: Dict[str, int] = {}
        self.skip_group_depth: Optional[int] = None
        self.skip_destination: Optional[str] = None
        self.codepage = 'cp936'
        self.codepage_declared = False
        self.uc = 1
        self.skip_bytes = 0

        self.current_para: List[str] = []
        self.paragraphs: List[Dict[str, Any]] = []
        self.in_table = False
        self.current_row: List[Dict[str, str]] = []
        self.current_cell: List[str] = []
        self.current_table: List[List[Dict[str, str]]] = []
        self.tables: List[Dict[str, Any]] = []
        self.merge_markers = 0
        self.captured_merge_markers = 0
        self.row_definitions: List[List[Dict[str, Any]]] = []
        self.current_definitions: List[Dict[str, Any]] = []
        self.current_definition: Dict[str, Any] = {}
        self.row_left = 0

        self.geometry_issues: List[Dict[str, Any]] = []
        self.pinned_issues: Dict[str, Dict[str, Any]] = {}
        self.geometry_issue_count = 0
        self.omitted_paragraphs = 0
        self.omitted_tables = 0
        self.total_cells = 0
        self.total_rows = 0
        self.total_definitions = 0
        self.field_count = 0
        self.objects: List[Dict[str, Any]] = []
        self.hidden_control_count = 0
        self.revision_control_count = 0
        self.limit_issues: List[Dict[str, Any]] = []
        self.font_encoding_conflict = False
        self.font_encoding_unknown = False
        self.encoded_text_seen = False
        self.encoded_high_bytes_seen = False

        self.visibility = RtfVisibility(hidden_text, revisions, self.add_geometry_issue)
        self.blocks: List[Dict[str, Any]] = []
        self.stack: List[tuple] = []
        self.group_frames: List[Dict[str, Any]] = []
        self.related_groups: List[Dict[str, Any]] = []
        self.omitted_related_groups = 0
        self.hex_buffer = bytearray()
        self.byte_decoder = None
        self.hex_start = 0

    def require_budget(self, condition, message):
        if not condition:
            raise ParseError('resource_limit', {'stage': 'RTF stream', 'offset': self.idx}, message)

    def add_geometry_issue(self, item):
        code = item.get('code')
        self.visibility.broken_scope(code, self.idx)
        if code in PINNED_ISSUE_CODES:
            if code in self.pinned_issues:
                return
            self.pinned_issues[code] = item
        self.geometry_issue_count += 1
        if len(self.geometry_issues) < 100:
            self.geometry_issues.append(item)

    def inspect_font_encoding(self, word, value):
        if word == 'fcharset' and value in (0, 1):
            return
        encoding = CHARSET_CODEPAGES.get(value) if word == 'fcharset' else value
        if encoding is None:
            self.font_encoding_unknown = True
        elif self.codepage != f'cp{encoding}':
            self.font_encoding_conflict = True

    def joined(self, parts):
        raw = ''.join(parts).encode('utf-16le', errors='surrogatepass')
        try:
            return raw.decode('utf-16le')
        except UnicodeDecodeError:
            self.add_geometry_issue({
                'code': 'rtf_unicode_incomplete',
                'message': 'Unpaired Unicode surrogate replaced; adjacent text retained',
            })
            return raw.decode('utf-16le', errors='replace')

    def emit_text(self, value, start, end):
        retained = self.visibility.emit(value, start, end, 'table_cell' if self.in_table else 'paragraph')
        if retained:
            (self.current_cell if self.in_table else self.current_para).append(value)
        return retained

    def begin_object(self, kind, start, destination_start):
        if not destination_start or not self.group_frames or self.group_depth < 2:
            raise ParseError('unsupported', {'stage': 'RTF stream', 'offset': start},
                             'Object control outside a verified destination group')
        if self.object_policy == 'reject':
            raise ParseError('unsupported', {'stage': 'RTF stream', 'offset': start},
                             'RTF object found; object-policy reject forbids this destination')
        self.require_budget(len(self.objects) < self.limits['max_objects'], 'RTF object budget exceeded')
        retained = self.object_policy == 'placeholder' and self.emit_text('\ufffc', start, self.idx)
        item = {
            'kind': kind, 'source_byte_offset': start, 'source_byte_end': None,
            'placeholder_emitted': bool(retained), 'complete': False, 'status': 'opaque_not_decoded',
        }
        self.group_frames[-1]['object_index'] = len(self.objects)
        self.objects.append(item)
        self.add_geometry_issue({
            'code': 'rtf_object_not_decoded',
            'message': 'Opaque object destinations were not decoded; markers follow the selected text filters',
        })
        self.skip_group_depth = self.group_depth
        self.skip_destination = kind

    def append_encoded(self, value):
        if not self.hex_buffer:
            self.hex_start = self.idx
        if self.byte_decoder is None and self.codepage in DBCS_CODEPAGES:
            self.byte_decoder = codecs.getincrementaldecoder(self.codepage)(errors='replace')
        if self.byte_decoder is not None:
            self.byte_decoder.decode(value, final=False)
        self.hex_buffer.extend(value)

    def flush_hex(self):
        if not self.hex_buffer:
            return
        self.encoded_text_seen = True
        has_high_bytes = any(value >= 128 for value in self.hex_buffer)
        self.encoded_high_bytes_seen = self.encoded_high_bytes_seen or has_high_bytes
        if has_high_bytes and not self.codepage_declared:
            self.add_geometry_issue({
                'code': 'rtf_encoding_assumed',
                'message': 'No explicit ANSI code page; CP936 fallback is assumed for encoded byte text',
            })
        try:
            decoded = self.hex_buffer.decode(self.codepage, errors='replace')
        except Exception:
            decoded = self.hex_buffer.decode('latin-1', errors='replace')
            self.add_geometry_issue({
                'code': 'rtf_encoding_unavailable',
                'message': 'Declared byte encoding unavailable; raw byte mapping retained',
            })
        if '\ufffd' in decoded:
            self.add_geometry_issue({
                'code': 'rtf_encoding_replacement',
                'message': 'Incomplete or invalid encoded bytes replaced',
            })
        self.emit_text(decoded, self.hex_start, self.idx)
        self.hex_buffer = bytearray()
        self.byte_decoder = None

    def flush_table_if_needed(self):
        if not self.current_table:
            return
        self.captured_merge_markers += sum(
            bool(cell.get('h')) + bool(cell.get('v'))
            for row in self.row_definitions for cell in row
        )
        if len(self.tables) >= self.limits['max_tables']:
            self.omitted_tables += 1
            self.current_table = []
            self.row_definitions = []
            return
        item: Dict[str, Any] = {'rows': self.current_table}
        has_merges = any(cell.get('h') or cell.get('v') for row in self.row_definitions for cell in row)
        if has_merges:
            from wps_rtf_geometry import recover_merges
            try:
                merges, boundaries = recover_merges(self.current_table, self.row_definitions)
                item.update(merges=merges, cell_boundaries_twips=boundaries,
                            merge_geometry_status='explicit_grid_recovered')
                for r0, c0, r1, c1 in merges:
                    if any(self.current_table[r][c]['text']
                           for r in range(r0, r1 + 1) for c in range(c0, c1 + 1) if (r, c) != (r0, c0)):
                        self.add_geometry_issue({
                            'code': 'rtf_merge_covered_content_retained',
                            'table': len(self.tables),
                            'message': 'All physical text retained; covered content must not be discarded during reconstruction',
                        })
            except ValueError as exc:
                item['merge_geometry_status'] = 'unknown'
                self.add_geometry_issue({
                    'code': 'rtf_merge_geometry_not_reconstructed',
                    'table': len(self.tables), 'message': str(exc),
                })
        self.tables.append(item)
        self.row_definitions = []
        self.blocks.append({'type': 'table', 'index': len(self.tables) - 1})
        self.current_table = []

    def append_paragraph(self):
        value = self.joined(self.current_para).strip()
        if value:
            if len(self.paragraphs) < self.limits['max_paragraphs']:
                self.paragraphs.append({
                    'text': value, 'format': dict(self.paragraph_format),
                    'outline_level': self.paragraph_format.get('outlinelevel'),
                })
                self.blocks.append({'type': 'paragraph', 'index': len(self.paragraphs) - 1})
            else:
                self.omitted_paragraphs += 1
        self.current_para = []

    def append_cell(self):
        self.require_budget(self.total_cells < self.limits['max_table_cells'], 'RTF table cell budget exceeded')
        self.current_row.append({'text': self.joined(self.current_cell).strip()})
        self.current_cell = []
        self.total_cells += 1

    def append_row(self):
        self.require_budget(self.total_rows < self.limits['max_table_rows'], 'RTF table row budget exceeded')
        self.current_table.append(self.current_row)
        self.row_definitions.append(self.current_definitions)
        self.current_row = []
        self.current_definitions = []
        self.total_rows += 1

    def recover_pending_row(self):
        incomplete = bool(self.current_cell or self.current_row or self.in_table)
        if self.current_cell or self.current_row:
            if self.current_cell:
                self.append_cell()
            self.append_row()
        if incomplete:
            self.add_geometry_issue({
                'code': 'rtf_unterminated_table_row',
                'message': 'Available physical cell text retained; row boundary incomplete',
            })
        self.in_table = False

    def _consume_skipped_escape(self):
        text, length, idx = self.text, self.length, self.idx
        if self.skip_destination in ('pict', 'object') and text.startswith('\\bin', idx) and (
                idx + 4 == length or not text[idx + 4].isascii() or not text[idx + 4].isalpha()):
            stop = idx + 4
            if stop < length and text[stop] == '-':
                stop += 1
            digit_start = stop
            while stop < length and '0' <= text[stop] <= '9' and stop - digit_start < 11:
                stop += 1
            operand = text[idx + 4:stop]
            if (stop == digit_start or len(operand) > 10 or operand.startswith('-')
                    or (stop < length and '0' <= text[stop] <= '9')):
                self.add_geometry_issue({
                    'code': 'rtf_truncated_binary',
                    'message': 'Object binary length is unavailable or invalid; remaining bytes not interpreted',
                })
                self.idx = length
                return
            count = int(operand)
            if stop < length and text[stop] == ' ':
                stop += 1
            idx = stop
            if count > length - idx:
                self.add_geometry_issue({
                    'code': 'rtf_truncated_binary',
                    'message': 'Object binary payload is physically truncated',
                })
            self.idx = idx + min(count, length - idx)
            return
        if idx + 1 < length and text[idx + 1] in '\\{}':
            self.idx += 2
            return
        skipped_control = re.match(r'\\([A-Za-z]{1,64})(-?[0-9]{1,10})?(?![A-Za-z0-9])', text[idx:idx + 80])
        if skipped_control:
            skipped_word, skipped_value = skipped_control.groups()
            self.visibility.skipped_control(
                self.skip_destination, skipped_word,
                int(skipped_value) if skipped_value is not None else None, idx)
        font_encoding = re.match(r'\\(fcharset|cpg)(-?[0-9]{1,10})(?![0-9A-Za-z])', text[idx:idx + 32])
        if font_encoding:
            self.inspect_font_encoding(font_encoding.group(1), int(font_encoding.group(2)))
        binary = re.match(r'\\bin([0-9]{1,10}) ?', text[idx:idx + 20])
        if binary:
            count = int(binary.group(1))
            idx += len(binary.group(0))
            if count > length - idx:
                self.add_geometry_issue({
                    'code': 'rtf_truncated_binary',
                    'message': 'Skipped binary destination is physically truncated',
                })
            self.idx = idx + min(count, length - idx)
            return
        # Continue normal control dispatch below in the caller.
        self._did_skip_advance = False

    def _consume_skip_fallback(self):
        ch = self.text[self.idx]
        if ch in '{}':
            self.skip_bytes = 0
            self.add_geometry_issue({
                'code': 'rtf_unicode_fallback_missing',
                'message': 'Unicode fallback ended at a group boundary',
            })
            return False
        if self.text.startswith("\\'", self.idx) and self.idx + 4 <= self.length:
            self.idx += 4
            self.skip_bytes -= 1
            return True
        if ch != '\\' or (self.idx + 1 < self.length and self.text[self.idx + 1] in '\\{}'):
            self.idx += 2 if ch == '\\' else 1
            self.skip_bytes -= 1
            return True
        self.skip_bytes = 0
        self.add_geometry_issue({
            'code': 'rtf_unicode_fallback_missing',
            'message': 'Unicode fallback ended at a control word',
        })
        return False

    def _handle_group_open(self):
        self.flush_hex()
        self.require_budget(self.group_depth < self.limits['max_group_depth'], 'RTF group depth budget exceeded')
        self.group_depth += 1
        if self.group_frames:
            self.group_frames[-1]['first_control'] = False
        self.group_frames.append({
            'first_control': True, 'object_index': None,
            'group_start': self.idx, 'related': None,
        })
        self.stack.append((
            self.skip_group_depth, self.skip_destination, self.uc,
            self.codepage, self.codepage_declared, dict(self.paragraph_format),
        ))
        self.visibility.push()
        self.idx += 1

    def _handle_group_close(self):
        self.flush_hex()
        if self.stack:
            frame = self.group_frames.pop()
            if frame['object_index'] is not None:
                item = self.objects[frame['object_index']]
                item['complete'] = True
                item['source_byte_end'] = self.idx + 1
            if frame['related'] is not None:
                related = dict(frame['related'])
                related.update(group_start=frame['group_start'], content_end=self.idx, group_end=self.idx + 1)
                if len(self.related_groups) < self.limits['max_related_stories']:
                    self.related_groups.append(related)
                else:
                    self.omitted_related_groups += 1
            pending_format = self.paragraph_format if self.group_depth == 1 and self.current_para else None
            (self.skip_group_depth, self.skip_destination, self.uc,
             self.codepage, self.codepage_declared, self.paragraph_format) = self.stack.pop()
            self.visibility.pop()
            if pending_format is not None:
                self.paragraph_format = pending_format
            self.group_depth -= 1
        else:
            self.add_geometry_issue({'code': 'rtf_unmatched_group_end', 'message': 'Extra closing group delimiter'})
        self.idx += 1

    def _handle_escape(self):
        control_start = self.idx
        destination_start = bool(self.group_frames and self.group_frames[-1]['first_control'])
        if self.group_frames:
            self.group_frames[-1]['first_control'] = False
        if self.idx + 1 == self.length or self.text[self.idx + 1] not in "'\\{}":
            self.flush_hex()
        self.idx += 1
        if self.idx >= self.length:
            self.add_geometry_issue({'code': 'rtf_truncated_control', 'message': 'Trailing control escape has no operand'})
            return False
        next_ch = self.text[self.idx]
        if next_ch in '\\{}':
            self.append_encoded(next_ch.encode('latin-1'))
            self.idx += 1
            return True
        if next_ch == '*':
            self._handle_star_destination(control_start, destination_start)
            return True
        if next_ch == "'":
            self._handle_hex_escapes()
            return True
        if next_ch in '\r\n':
            self.idx += 1
            return True
        if next_ch == '~':
            self.emit_text('\u00A0', control_start, self.idx + 1)
            self.idx += 1
            return True
        if next_ch == '_':
            self.emit_text('-', control_start, self.idx + 1)
            self.idx += 1
            return True
        self._handle_control_word(control_start, destination_start)
        return True

    def _handle_star_destination(self, control_start, destination_start):
        match = re.match(r'\\([A-Za-z]+)', self.text[self.idx + 1:self.idx + 65])
        destination = match.group(1) if match else 'unknown'
        self.visibility.destination(destination, control_start)
        if destination in ('object', 'pict'):
            if not destination_start:
                raise ParseError('unsupported', {'stage': 'RTF stream', 'offset': control_start},
                                 'Object destination flag appears after group content')
            self.group_frames[-1]['first_control'] = True
            self.idx += 1
            return
        if self.include_related and destination_start and destination in RELATED_DESTINATIONS and match:
            content_start = self.idx + 1 + match.end()
            if content_start < self.length and self.text[content_start] == ' ':
                content_start += 1
            self.group_frames[-1]['related'] = {
                'destination': destination, 'content_start': content_start,
                'codepage': self.codepage, 'codepage_declared': self.codepage_declared, 'uc': self.uc,
            }
        elif destination not in {'fldinst', 'generator', 'fonttbl', 'colortbl', 'stylesheet',
                                 'info', 'themedata', 'xmlnstbl', 'rsidtbl'}:
            self.add_geometry_issue({
                'code': 'rtf_destination_skipped',
                'message': 'Optional destination omitted from main content',
            })
        self.skip_destination = destination
        self.skip_group_depth = self.group_depth
        self.idx += 1

    def _handle_hex_escapes(self):
        while self.idx < self.length and self.text[self.idx] == "'":
            hex_str = self.text[self.idx + 1:self.idx + 3]
            if len(hex_str) == 2 and all(c in '0123456789abcdefABCDEF' for c in hex_str):
                self.append_encoded(bytes((int(hex_str, 16),)))
                self.idx += 3
            else:
                self.add_geometry_issue({
                    'code': 'rtf_truncated_hex',
                    'message': 'Invalid or incomplete hex escape omitted',
                })
                self.idx += 1
                for _ in range(2):
                    if self.idx < self.length and self.text[self.idx] not in '{}\\':
                        self.idx += 1
                break
            if self.idx + 1 < self.length and self.text[self.idx] == '\\' and self.text[self.idx + 1] == "'":
                self.idx += 1
            else:
                break

    def _handle_control_word(self, control_start, destination_start):
        word_start = self.idx
        while self.idx < self.length and ('a' <= self.text[self.idx] <= 'z' or 'A' <= self.text[self.idx] <= 'Z'):
            self.idx += 1
        word = self.text[word_start:self.idx]
        num_str = ''
        if self.idx < self.length and (self.text[self.idx] == '-' or '0' <= self.text[self.idx] <= '9'):
            num_start = self.idx
            if self.text[self.idx] == '-':
                self.idx += 1
            while self.idx < self.length and '0' <= self.text[self.idx] <= '9':
                self.idx += 1
            num_str = self.text[num_start:self.idx]
        num_val = int(num_str) if num_str and num_str != '-' and len(num_str) < 12 else None
        unicode_operand_truncated = word == 'u' and self.idx == self.length
        if num_str and num_val is None:
            self.add_geometry_issue({
                'code': 'rtf_invalid_control_operand',
                'message': 'Invalid or oversized numeric control operand',
            })
        if self.idx < self.length and self.text[self.idx] == ' ':
            self.idx += 1
        self.visibility.control(word, num_val, control_start)
        self._dispatch_control_word(word, num_val, control_start, destination_start, unicode_operand_truncated)

    def _dispatch_control_word(self, word, num_val, control_start, destination_start, unicode_operand_truncated):
        if word in ('object', 'pict'):
            self.begin_object(word, control_start, destination_start)
        elif word in {'paperw', 'paperh', 'margl', 'margr', 'margt', 'margb'} and num_val is not None:
            self.page_properties[word] = num_val
        elif word == 'pard':
            self.paragraph_format = {}
        elif word in {'ql', 'qr', 'qc', 'qj'}:
            self.paragraph_format['alignment'] = word
        elif word in {'sl', 'slmult', 'fi', 'li', 'ri', 'sb', 'sa', 'outlinelevel', 'keepn', 'keep', 'pagebb'}:
            self.paragraph_format[word] = num_val if num_val is not None else 1
        elif word in RELATED_DESTINATIONS:
            if self.include_related and destination_start:
                self.group_frames[-1]['related'] = {
                    'destination': word, 'content_start': self.idx,
                    'codepage': self.codepage, 'codepage_declared': self.codepage_declared, 'uc': self.uc,
                }
            else:
                self.add_geometry_issue({
                    'code': 'rtf_destination_skipped', 'destination': word,
                    'message': 'Related content omitted from main content',
                })
            self.skip_group_depth = self.group_depth
            self.skip_destination = word
        elif word in IGNORED_DESTINATIONS:
            if word == 'pict':
                self.add_geometry_issue({
                    'code': 'rtf_destination_skipped', 'destination': word,
                    'message': 'Object omitted from main content',
                })
            self.skip_group_depth = self.group_depth
            self.skip_destination = word
        elif word in {'object', 'objdata', 'fldinst'}:
            self.add_geometry_issue({
                'code': 'rtf_destination_skipped', 'destination': word,
                'message': 'Object or field instruction omitted',
            })
            self.skip_group_depth = self.group_depth
            self.skip_destination = word
        elif word in {'fcharset', 'cpg'}:
            self.inspect_font_encoding(word, num_val)
        elif word == 'field':
            self.field_count += 1
            self.add_geometry_issue({
                'code': 'rtf_field_cached_result_unverified',
                'message': 'Stored field result retained without execution or freshness validation',
            })
        elif word in {'v', 'webhidden'} and num_val != 0:
            self.hidden_control_count += 1
            if self.hidden_control_count == 1 and self.hidden_text == 'include':
                self.add_geometry_issue({
                    'code': 'rtf_hidden_text_unfiltered',
                    'message': 'Stored hidden text retained; effective visibility not reconstructed',
                })
        elif word in REVISION_CONTROLS and (word not in {'deleted', 'revised'} or num_val != 0):
            self.revision_control_count += 1
            if self.revision_control_count == 1 and self.revisions is None:
                self.add_geometry_issue({
                    'code': 'rtf_revisions_unfiltered',
                    'message': 'Stored revision text retained; accepted/original revision views not reconstructed',
                })
        elif word == 'bin' and num_val is not None and num_val >= 0:
            self.add_geometry_issue({
                'code': 'rtf_destination_skipped', 'destination': 'binary',
                'message': 'Binary payload not interpreted as text',
            })
            if num_val > self.length - self.idx:
                self.add_geometry_issue({
                    'code': 'rtf_truncated_binary',
                    'message': 'Binary payload is physically truncated',
                })
            self.idx += min(num_val, self.length - self.idx)
        elif word == 'bin':
            self.add_geometry_issue({
                'code': 'rtf_truncated_binary',
                'message': 'Binary length unavailable; remaining bytes not interpreted as text',
            })
            self.idx = self.length
        elif word == 'ansicpg' and num_val:
            self.codepage = f'cp{num_val}'
            self.codepage_declared = True
        elif word == 'uc' and num_val is not None:
            self.uc = max(0, min(num_val, 32767))
            if self.uc != num_val:
                self.add_geometry_issue({
                    'code': 'rtf_invalid_control_operand',
                    'message': 'Unicode fallback count outside supported bounds',
                })
        elif word == 'u' and unicode_operand_truncated:
            self.add_geometry_issue({
                'code': 'rtf_unicode_incomplete',
                'message': 'Input ended inside a Unicode numeric operand; ambiguous character omitted',
            })
        elif word == 'u' and num_val is not None:
            char_val = num_val if num_val >= 0 else num_val + 65536
            try:
                u_char = chr(char_val)
            except ValueError:
                u_char = '\uFFFD'
            self.emit_text(u_char, control_start, self.idx)
            self.skip_bytes = self.uc
        elif word == 'u':
            self.add_geometry_issue({
                'code': 'rtf_unicode_incomplete',
                'message': 'Unicode control has no complete numeric operand',
            })
        elif word == 'tab':
            self.emit_text('\t', control_start, self.idx)
        elif word == 'line':
            self.emit_text('\n', control_start, self.idx)
        else:
            self._dispatch_table_or_par_word(word, num_val, control_start)

    def _dispatch_table_or_par_word(self, word, num_val, control_start):
        if word == 'par':
            self.flush_hex()
            if self.in_table:
                self.emit_text('\n', control_start, self.idx)
                return
            self.flush_table_if_needed()
            self.append_paragraph()
        elif word == 'trowd':
            self.recover_pending_row()
            self.row_left = 0
            self.current_definitions = []
            self.current_definition = {}
        elif word in ('clmgf', 'clmrg', 'clvmgf', 'clvmrg'):
            self.merge_markers += 1
            axis = 'h' if word in ('clmgf', 'clmrg') else 'v'
            state = 'first' if word in ('clmgf', 'clvmgf') else 'continue'
            if axis in self.current_definition:
                self.current_definition['conflicting'] = True
                self.add_geometry_issue({
                    'code': 'rtf_merge_conflicting_flags',
                    'message': 'Repeated merge flag; geometry not certified',
                })
            self.current_definition[axis] = state
        elif word == 'trleft':
            self.row_left = num_val
        elif word == 'cellx':
            self.require_budget(self.total_definitions < self.limits['max_cell_definitions'],
                                'RTF cell definition budget exceeded')
            self.total_definitions += 1
            self.current_definition['row_left'] = self.row_left
            self.current_definition['right'] = num_val
            self.current_definitions.append(self.current_definition)
            self.current_definition = {}
        elif word == 'intbl':
            self.in_table = True
        elif word == 'cell':
            self.flush_hex()
            self.append_cell()
        elif word == 'row':
            self.flush_hex()
            if self.current_cell:
                self.recover_pending_row()
            if self.current_row:
                for definition in self.current_definitions:
                    definition['row_left'] = self.row_left
                self.append_row()
            self.in_table = False

    def _handle_plain_char(self, ch):
        if self.group_frames:
            self.group_frames[-1]['first_control'] = False
        pending_lead = self.byte_decoder is not None and bool(self.byte_decoder.getstate()[0])
        if (not pending_lead and ord(ch) >= 128 and self.codepage in DBCS_CODEPAGES
                and self.idx + 1 < self.length and self.text[self.idx + 1] in '\\{}'):
            explicit_hex = self.text.startswith("\\'", self.idx + 1)
            if not explicit_hex:
                try:
                    ambiguous_pair = len(self.text[self.idx:self.idx + 2].encode('latin-1').decode(self.codepage)) == 1
                except UnicodeDecodeError:
                    ambiguous_pair = False
                if ambiguous_pair:
                    raise ParseError('unsupported', {'stage': 'RTF stream', 'offset': self.idx},
                                     'Ambiguous raw DBCS byte and RTF delimiter; explicit hex byte escapes required')
        self.append_encoded(ch.encode('latin-1'))
        self.idx += 1

    def parse(self):
        """Parse the full RTF stream and update all accumulated state in place."""
        while self.idx < self.length:
            ch = self.text[self.idx]
            if self.skip_group_depth is not None and ch == '\\':
                self._did_skip_advance = True
                self._consume_skipped_escape()
                if self._did_skip_advance:
                    continue
            if self.skip_bytes > 0 and self._consume_skip_fallback():
                continue
            if ch == '{':
                self._handle_group_open()
                continue
            if ch == '}':
                self._handle_group_close()
                continue
            if self.skip_group_depth is not None and self.group_depth >= self.skip_group_depth:
                self.idx += 1
                continue
            if ch == '\\':
                if not self._handle_escape():
                    break
                continue
            if ch in '\r\n':
                self.idx += 1
            else:
                self._handle_plain_char(ch)
        return self
