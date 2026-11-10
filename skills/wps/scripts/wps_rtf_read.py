#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded, content-identified RTF stream reader for WPS-family and RTF documents."""

from typing import Dict, Any, List, Optional
import codecs
import re

from wps_access import ParseError
from wps_rtf_visibility import RtfVisibility

MAX_RTF_BYTES = 32 * 1024 * 1024
MAX_PARAGRAPHS = 100000
MAX_TABLES = 10000
MAX_GROUP_DEPTH = 512
MAX_TABLE_CELLS = 100000
MAX_TABLE_ROWS = 100000
MAX_CELL_DEFINITIONS = 100000
MAX_OBJECTS = 1000
PINNED_ISSUE_CODES = {'rtf_field_cached_result_unverified', 'rtf_hidden_text_unfiltered', 'rtf_revisions_unfiltered',
                      'rtf_font_encoding_unresolved', 'rtf_encoding_assumed', 'rtf_visibility_unknown', 'rtf_revision_view_unknown',
                      'rtf_object_not_decoded'}
DBCS_CODEPAGES = {'cp936', 'cp950', 'cp932', 'cp949', 'cp1361'}
REVISION_CONTROLS = {'deleted', 'revised', 'revauth', 'revauthdel', 'revdttm', 'revdttmdel', 'insrsid', 'delrsid'}
CHARSET_CODEPAGES = {128: 932, 129: 949, 130: 1361, 134: 936, 136: 950,
                     161: 1253, 162: 1254, 163: 1258, 177: 1255, 178: 1256,
                     186: 1257, 204: 1251, 222: 874, 238: 1250, 255: 437}

IGNORED_DESTINATIONS = {
    "fonttbl",
    "colortbl",
    "stylesheet",
    "info",
    "pict",
    "header",
    "footer",
    "themedata",
    "xmlnstbl",
    "rsidtbl",
    "generator",
}


def parse_rtf_bytes(data: bytes, max_bytes: int = MAX_RTF_BYTES, *, hidden_text='include', revisions=None,
                    object_policy='placeholder') -> Dict[str, Any]:
    """Parse raw bytes of an RTF document into structured paragraphs and tables."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("RTF input must be bytes or bytearray")

    if len(data) > max_bytes:
        raise ValueError(f"RTF file exceeds size limit of {max_bytes} bytes")

    # Default reading retains an opaque marker rather than silently closing the gap.
    object_policy = 'placeholder' if object_policy is None else object_policy
    if object_policy not in ('reject', 'placeholder'):
        raise ParseError('unsupported', {'stage': 'options'}, 'Unknown RTF object policy')

    # Fast signature check
    stripped_data = data.lstrip()
    if not stripped_data.startswith(b"{\\rtf"):
        raise ValueError("Invalid RTF signature: missing {\\rtf header")

    # Decode as latin-1 to preserve exact single-byte positions
    text = data.decode("latin-1")
    length = len(text)
    idx = 0

    group_depth = 0
    paragraph_format = {}
    page_properties = {}
    skip_group_depth: Optional[int] = None
    skip_destination = None
    codepage = "cp936"
    codepage_declared = False
    uc = 1
    skip_bytes = 0

    current_para: List[str] = []
    paragraphs: List[Dict[str, str]] = []
    in_table = False
    current_row: List[Dict[str, str]] = []
    current_cell: List[str] = []
    current_table: List[List[Dict[str, str]]] = []
    tables: List[Dict[str, Any]] = []
    merge_markers = 0
    row_definitions = []
    current_definitions = []
    current_definition = {}
    row_left = 0
    geometry_issues = []
    pinned_issues = {}
    geometry_issue_count = 0
    omitted_paragraphs = 0
    omitted_tables = 0
    total_cells = 0
    total_rows = 0
    total_definitions = 0
    field_count = 0
    objects = []
    hidden_control_count = 0
    revision_control_count = 0
    limit_issues = []
    font_encoding_conflict = False
    font_encoding_unknown = False
    encoded_text_seen = False
    encoded_high_bytes_seen = False

    def inspect_font_encoding(word, value):
        nonlocal font_encoding_conflict, font_encoding_unknown
        if word == 'fcharset' and value in (0, 1):
            return
        encoding = CHARSET_CODEPAGES.get(value) if word == 'fcharset' else value
        if encoding is None:
            font_encoding_unknown = True
        elif codepage != f'cp{encoding}':
            font_encoding_conflict = True

    def require_budget(condition, message):
        if not condition:
            raise ParseError('resource_limit', {'stage': 'RTF stream', 'offset': idx}, message)

    def add_geometry_issue(item):
        nonlocal geometry_issue_count
        code = item.get('code')
        visibility.broken_scope(code, idx)
        if code in PINNED_ISSUE_CODES:
            if code in pinned_issues:
                return
            pinned_issues[code] = item
        geometry_issue_count += 1
        if len(geometry_issues) < 100:
            geometry_issues.append(item)

    visibility = RtfVisibility(hidden_text, revisions, add_geometry_issue)
    captured_merge_markers = 0
    blocks = []

    def joined(parts):
        raw = "".join(parts).encode("utf-16le", errors="surrogatepass")
        try:
            return raw.decode('utf-16le')
        except UnicodeDecodeError:
            add_geometry_issue({'code': 'rtf_unicode_incomplete', 'message': 'Unpaired Unicode surrogate replaced; adjacent text retained'})
            return raw.decode('utf-16le', errors='replace')

    stack: List[tuple] = []
    # First-control evidence identifies destinations, not arbitrary occurrences
    # of a similarly named control inside a content group.
    group_frames = []
    hex_buffer = bytearray()
    byte_decoder = None
    hex_start = 0

    def emit_text(value, start, end):
        retained = visibility.emit(value, start, end, 'table_cell' if in_table else 'paragraph')
        if retained:
            (current_cell if in_table else current_para).append(value)
        return retained

    def begin_object(kind, start, destination_start):
        nonlocal skip_group_depth, skip_destination
        if not destination_start or not group_frames or group_depth < 2:
            raise ParseError('unsupported', {'stage': 'RTF stream', 'offset': start},
                             'Object control outside a verified destination group')
        if object_policy == 'reject':
            # A visibility filter cannot authorize objects that were forbidden.
            raise ParseError('unsupported', {'stage': 'RTF stream', 'offset': start},
                             'RTF object found; object-policy reject forbids this destination')
        require_budget(len(objects) < MAX_OBJECTS, 'RTF object budget exceeded')
        retained = object_policy == 'placeholder' and emit_text('\ufffc', start, idx)
        item = {'kind': kind, 'source_byte_offset': start, 'source_byte_end': None,
                'placeholder_emitted': bool(retained), 'complete': False,
                'status': 'opaque_not_decoded'}
        group_frames[-1]['object_index'] = len(objects)
        objects.append(item)
        add_geometry_issue({'code': 'rtf_object_not_decoded',
                            'message': 'Opaque object destinations were not decoded; markers follow the selected text filters'})
        skip_group_depth = group_depth
        skip_destination = kind

    def append_encoded(value):
        nonlocal byte_decoder, hex_start
        if not hex_buffer:
            hex_start = idx
        if byte_decoder is None and codepage in DBCS_CODEPAGES:
            byte_decoder = codecs.getincrementaldecoder(codepage)(errors='replace')
        if byte_decoder is not None:
            byte_decoder.decode(value, final=False)
        hex_buffer.extend(value)

    def flush_hex():
        nonlocal hex_buffer, byte_decoder, encoded_text_seen, encoded_high_bytes_seen
        if hex_buffer:
            encoded_text_seen = True
            has_high_bytes = any(value >= 128 for value in hex_buffer)
            encoded_high_bytes_seen = encoded_high_bytes_seen or has_high_bytes
            if has_high_bytes and not codepage_declared:
                add_geometry_issue({'code': 'rtf_encoding_assumed',
                                    'message': 'No explicit ANSI code page; CP936 fallback is assumed for encoded byte text'})
            try:
                decoded = hex_buffer.decode(codepage, errors="replace")
            except Exception:
                decoded = hex_buffer.decode("latin-1", errors="replace")
                add_geometry_issue({'code': 'rtf_encoding_unavailable', 'message': 'Declared byte encoding unavailable; raw byte mapping retained'})
            if '\ufffd' in decoded:
                add_geometry_issue({'code': 'rtf_encoding_replacement', 'message': 'Incomplete or invalid encoded bytes replaced'})
            emit_text(decoded, hex_start, idx)
            hex_buffer = bytearray()
            byte_decoder = None

    def flush_table_if_needed():
        nonlocal current_table, tables, row_definitions, captured_merge_markers, omitted_tables
        if current_table:
            captured_merge_markers += sum(bool(cell.get("h")) + bool(cell.get("v")) for row in row_definitions for cell in row)
            if len(tables) >= MAX_TABLES:
                omitted_tables += 1
                current_table = []
                row_definitions = []
                return
            item = {"rows": current_table}
            has_merges = any(cell.get('h') or cell.get('v') for row in row_definitions for cell in row)
            if has_merges:
                from wps_rtf_geometry import recover_merges
                try:
                    merges, boundaries = recover_merges(current_table, row_definitions)
                    item.update(merges=merges, cell_boundaries_twips=boundaries, merge_geometry_status='explicit_grid_recovered')
                    for r0, c0, r1, c1 in merges:
                        if any(current_table[r][c]['text'] for r in range(r0, r1 + 1) for c in range(c0, c1 + 1) if (r, c) != (r0, c0)):
                            add_geometry_issue({'code': 'rtf_merge_covered_content_retained', 'table': len(tables),
                                                    'message': 'All physical text retained; covered content must not be discarded during reconstruction'})
                except ValueError as exc:
                    item['merge_geometry_status'] = 'unknown'
                    add_geometry_issue({'code': 'rtf_merge_geometry_not_reconstructed', 'table': len(tables), 'message': str(exc)})
            tables.append(item)
            row_definitions = []
            blocks.append({"type": "table", "index": len(tables) - 1})
            current_table = []

    def append_paragraph():
        nonlocal current_para, omitted_paragraphs
        value = joined(current_para).strip()
        if value:
            if len(paragraphs) < MAX_PARAGRAPHS:
                paragraphs.append({'text': value, 'format': dict(paragraph_format),
                                   'outline_level': paragraph_format.get('outlinelevel')})
                blocks.append({'type': 'paragraph', 'index': len(paragraphs) - 1})
            else:
                omitted_paragraphs += 1
        current_para = []

    def append_cell():
        nonlocal current_cell, total_cells
        require_budget(total_cells < MAX_TABLE_CELLS, 'RTF table cell budget exceeded')
        current_row.append({'text': joined(current_cell).strip()})
        current_cell = []
        total_cells += 1

    def append_row():
        nonlocal current_row, current_definitions, total_rows
        require_budget(total_rows < MAX_TABLE_ROWS, 'RTF table row budget exceeded')
        current_table.append(current_row)
        row_definitions.append(current_definitions)
        current_row = []
        current_definitions = []
        total_rows += 1

    def recover_pending_row():
        nonlocal current_cell, current_row, current_definitions, in_table
        incomplete = bool(current_cell or current_row or in_table)
        if current_cell or current_row:
            if current_cell:
                append_cell()
            append_row()
        if incomplete:
            add_geometry_issue({'code': 'rtf_unterminated_table_row', 'message': 'Available physical cell text retained; row boundary incomplete'})
        in_table = False

    while idx < length:
        ch = text[idx]

        if skip_group_depth is not None and ch == '\\':
            if skip_destination in ('pict', 'object') and text.startswith('\\bin', idx) and (
                    idx + 4 == length or not text[idx + 4].isascii() or not text[idx + 4].isalpha()):
                # Never parse ambiguous binary bytes as braces or destinations.
                stop = idx + 4
                if stop < length and text[stop] == '-':
                    stop += 1
                digit_start = stop
                while stop < length and '0' <= text[stop] <= '9' and stop - digit_start < 11:
                    stop += 1
                operand = text[idx + 4:stop]
                if (stop == digit_start or len(operand) > 10 or operand.startswith('-')
                        or stop < length and '0' <= text[stop] <= '9'):
                    add_geometry_issue({'code': 'rtf_truncated_binary',
                                        'message': 'Object binary length is unavailable or invalid; remaining bytes not interpreted'})
                    idx = length
                    continue
                count = int(operand)
                if stop < length and text[stop] == ' ':
                    stop += 1
                idx = stop
                if count > length - idx:
                    add_geometry_issue({'code': 'rtf_truncated_binary',
                                        'message': 'Object binary payload is physically truncated'})
                idx += min(count, length - idx)
                continue
            if idx + 1 < length and text[idx + 1] in '\\{}':
                idx += 2
                continue
            skipped_control = re.match(r'\\([A-Za-z]{1,64})(-?[0-9]{1,10})?(?![A-Za-z0-9])', text[idx:idx + 80])
            if skipped_control:
                skipped_word, skipped_value = skipped_control.groups()
                visibility.skipped_control(skip_destination, skipped_word, int(skipped_value) if skipped_value is not None else None, idx)
            font_encoding = re.match(r'\\(fcharset|cpg)(-?[0-9]{1,10})(?![0-9A-Za-z])', text[idx:idx + 32])
            if font_encoding:
                inspect_font_encoding(font_encoding.group(1), int(font_encoding.group(2)))
            binary = re.match(r'\\bin([0-9]{1,10}) ?', text[idx:idx + 20])
            if binary:
                count = int(binary.group(1))
                idx += len(binary.group(0))
                if count > length - idx:
                    add_geometry_issue({'code': 'rtf_truncated_binary', 'message': 'Skipped binary destination is physically truncated'})
                idx += min(count, length - idx)
                continue

        if skip_bytes > 0:
            if ch in '{}':
                skip_bytes = 0
                add_geometry_issue({'code': 'rtf_unicode_fallback_missing', 'message': 'Unicode fallback ended at a group boundary'})
            elif text.startswith("\\'", idx) and idx + 4 <= length:
                idx += 4
                skip_bytes -= 1
                continue
            elif ch != '\\' or idx + 1 < length and text[idx + 1] in '\\{}':
                idx += 2 if ch == '\\' else 1
                skip_bytes -= 1
                continue
            else:
                skip_bytes = 0
                add_geometry_issue({'code': 'rtf_unicode_fallback_missing', 'message': 'Unicode fallback ended at a control word'})

        if ch == "{":
            flush_hex()
            require_budget(group_depth < MAX_GROUP_DEPTH, 'RTF group depth budget exceeded')
            group_depth += 1
            if group_frames:
                group_frames[-1]['first_control'] = False
            group_frames.append({'first_control': True, 'object_index': None})
            stack.append((skip_group_depth, skip_destination, uc, codepage, codepage_declared, dict(paragraph_format)))
            visibility.push()
            idx += 1
            continue
        elif ch == "}":
            flush_hex()
            if stack:
                frame = group_frames.pop()
                if frame['object_index'] is not None:
                    item = objects[frame['object_index']]
                    item['complete'] = True
                    item['source_byte_end'] = idx + 1
                pending_format = paragraph_format if group_depth == 1 and current_para else None
                skip_group_depth, skip_destination, uc, codepage, codepage_declared, paragraph_format = stack.pop()
                visibility.pop()
                if pending_format is not None:
                    paragraph_format = pending_format
                group_depth -= 1
            else:
                add_geometry_issue({'code': 'rtf_unmatched_group_end', 'message': 'Extra closing group delimiter'})
            idx += 1
            continue

        if skip_group_depth is not None and group_depth >= skip_group_depth:
            idx += 1
            continue

        if ch == "\\":
            control_start = idx
            destination_start = bool(group_frames and group_frames[-1]['first_control'])
            if group_frames:
                group_frames[-1]['first_control'] = False
            # Literal and hex bytes may form one multibyte character. Only a
            # structural/control boundary finishes the current encoded run.
            if idx + 1 == length or text[idx + 1] not in "'\\{}":
                flush_hex()
            idx += 1
            if idx >= length:
                add_geometry_issue({'code': 'rtf_truncated_control', 'message': 'Trailing control escape has no operand'})
                break
            next_ch = text[idx]

            # Literal escaped control symbols
            if next_ch in "\\{}":
                append_encoded(next_ch.encode('latin-1'))
                idx += 1
                continue
            elif next_ch == "*":
                # Destination flag
                match = re.match(r'\\([A-Za-z]+)', text[idx + 1:idx + 65])
                destination = match.group(1) if match else 'unknown'
                visibility.destination(destination, control_start)
                if destination in ('object', 'pict'):
                    if not destination_start:
                        raise ParseError('unsupported', {'stage': 'RTF stream', 'offset': control_start},
                                         'Object destination flag appears after group content')
                    # Recognized optional object destinations use the same
                    # bounded object path when their control is read next.
                    group_frames[-1]['first_control'] = True
                    idx += 1
                    continue
                skip_destination = destination
                if destination not in {'fldinst', 'generator', 'fonttbl', 'colortbl', 'stylesheet', 'info', 'themedata', 'xmlnstbl', 'rsidtbl'}:
                    add_geometry_issue({'code': 'rtf_destination_skipped', 'message': 'Optional destination omitted from main content'})
                skip_group_depth = group_depth
                idx += 1
                continue
            elif next_ch == "'":
                while idx < length and text[idx] == "'":
                    hex_str = text[idx + 1 : idx + 3]
                    if len(hex_str) == 2 and all(c in "0123456789abcdefABCDEF" for c in hex_str):
                        append_encoded(bytes((int(hex_str, 16),)))
                        idx += 3
                    else:
                        add_geometry_issue({'code': 'rtf_truncated_hex', 'message': 'Invalid or incomplete hex escape omitted'})
                        idx += 1
                        for _ in range(2):
                            if idx < length and text[idx] not in '{}\\':
                                idx += 1
                        break
                    if idx + 1 < length and text[idx] == "\\" and text[idx + 1] == "'":
                        idx += 1
                    else:
                        break
                continue
            elif next_ch in "\r\n":
                idx += 1
                continue
            elif next_ch == "~":
                emit_text("\u00A0", control_start, idx + 1)
                idx += 1
                continue
            elif next_ch == "_":
                emit_text("-", control_start, idx + 1)
                idx += 1
                continue

            # Parse control word
            word_start = idx
            while idx < length and ('a' <= text[idx] <= 'z' or 'A' <= text[idx] <= 'Z'):
                idx += 1
            word = text[word_start:idx]

            num_str = ""
            if idx < length and (text[idx] == "-" or "0" <= text[idx] <= "9"):
                num_start = idx
                if text[idx] == "-":
                    idx += 1
                while idx < length and "0" <= text[idx] <= "9":
                    idx += 1
                num_str = text[num_start:idx]
            num_val = int(num_str) if num_str and num_str != '-' and len(num_str) < 12 else None
            unicode_operand_truncated = word == 'u' and idx == length
            if num_str and num_val is None:
                add_geometry_issue({'code': 'rtf_invalid_control_operand', 'message': 'Invalid or oversized numeric control operand'})

            # Optional trailing whitespace delimiter
            if idx < length and text[idx] == " ":
                idx += 1

            visibility.control(word, num_val, control_start)
            # Dispatch control word
            if word in ('object', 'pict'):
                begin_object(word, control_start, destination_start)
            elif word in {'paperw', 'paperh', 'margl', 'margr', 'margt', 'margb'} and num_val is not None:
                page_properties[word] = num_val
            elif word == 'pard':
                paragraph_format = {}
            elif word in {'ql', 'qr', 'qc', 'qj'}:
                paragraph_format['alignment'] = word
            elif word in {'sl', 'slmult', 'fi', 'li', 'ri', 'sb', 'sa', 'outlinelevel', 'keepn', 'keep', 'pagebb'}:
                paragraph_format[word] = num_val if num_val is not None else 1
            elif word in IGNORED_DESTINATIONS:
                if word in {'header', 'footer', 'pict'}:
                    add_geometry_issue({'code': 'rtf_destination_skipped', 'destination': word,
                                        'message': 'Related content or object omitted from main content'})
                skip_group_depth = group_depth
                skip_destination = word
            elif word in {'headerl', 'headerr', 'headerf', 'footerl', 'footerr', 'footerf', 'object', 'objdata', 'fldinst', 'footnote', 'annotation'}:
                add_geometry_issue({'code': 'rtf_destination_skipped', 'destination': word, 'message': 'Related content, object or field instruction omitted'})
                skip_group_depth = group_depth
                skip_destination = word
            elif word in {'fcharset', 'cpg'}:
                inspect_font_encoding(word, num_val)
            elif word == 'field':
                field_count += 1
                add_geometry_issue({'code': 'rtf_field_cached_result_unverified', 'message': 'Stored field result retained without execution or freshness validation'})
            elif word in {'v', 'webhidden'} and num_val != 0:
                hidden_control_count += 1
                if hidden_control_count == 1 and hidden_text == 'include':
                    add_geometry_issue({'code': 'rtf_hidden_text_unfiltered',
                                        'message': 'Stored hidden text retained; effective visibility not reconstructed'})
            elif word in REVISION_CONTROLS and (word not in {'deleted', 'revised'} or num_val != 0):
                revision_control_count += 1
                if revision_control_count == 1 and revisions is None:
                    add_geometry_issue({'code': 'rtf_revisions_unfiltered',
                                        'message': 'Stored revision text retained; accepted/original revision views not reconstructed'})
            elif word == 'bin' and num_val is not None and num_val >= 0:
                add_geometry_issue({'code': 'rtf_destination_skipped', 'destination': 'binary', 'message': 'Binary payload not interpreted as text'})
                if num_val > length - idx:
                    add_geometry_issue({'code': 'rtf_truncated_binary', 'message': 'Binary payload is physically truncated'})
                idx += min(num_val, length - idx)
            elif word == 'bin':
                add_geometry_issue({'code': 'rtf_truncated_binary', 'message': 'Binary length unavailable; remaining bytes not interpreted as text'})
                idx = length
            elif word == "ansicpg" and num_val:
                codepage = f"cp{num_val}"
                codepage_declared = True
            elif word == "uc" and num_val is not None:
                uc = max(0, min(num_val, 32767))
                if uc != num_val:
                    add_geometry_issue({'code': 'rtf_invalid_control_operand', 'message': 'Unicode fallback count outside supported bounds'})
            elif word == 'u' and unicode_operand_truncated:
                add_geometry_issue({'code': 'rtf_unicode_incomplete', 'message': 'Input ended inside a Unicode numeric operand; ambiguous character omitted'})
            elif word == "u" and num_val is not None:
                char_val = num_val if num_val >= 0 else num_val + 65536
                try:
                    u_char = chr(char_val)
                except ValueError:
                    u_char = "\uFFFD"
                emit_text(u_char, control_start, idx)
                skip_bytes = uc
            elif word == 'u':
                add_geometry_issue({'code': 'rtf_unicode_incomplete', 'message': 'Unicode control has no complete numeric operand'})
            elif word == "par":
                flush_hex()
                if in_table:
                    emit_text('\n', control_start, idx)
                    continue
                if not in_table:
                    flush_table_if_needed()
                append_paragraph()
            elif word == "trowd":
                recover_pending_row()
                row_left = 0
                current_definitions = []
                current_definition = {}
            elif word in ("clmgf", "clmrg", "clvmgf", "clvmrg"):
                merge_markers += 1
                axis = 'h' if word in ('clmgf', 'clmrg') else 'v'
                state = 'first' if word in ('clmgf', 'clvmgf') else 'continue'
                if axis in current_definition:
                    current_definition['conflicting'] = True
                    add_geometry_issue({'code': 'rtf_merge_conflicting_flags', 'message': 'Repeated merge flag; geometry not certified'})
                current_definition[axis] = state
            elif word == "trleft":
                row_left = num_val
            elif word == "cellx":
                require_budget(total_definitions < MAX_CELL_DEFINITIONS, 'RTF cell definition budget exceeded')
                total_definitions += 1
                current_definition["row_left"] = row_left
                current_definition['right'] = num_val
                current_definitions.append(current_definition)
                current_definition = {}
            elif word == "intbl":
                in_table = True
            elif word == "cell":
                flush_hex()
                append_cell()
            elif word == "row":
                flush_hex()
                if current_cell:
                    recover_pending_row()
                if current_row:
                    for definition in current_definitions:
                        definition["row_left"] = row_left
                    append_row()
                in_table = False
            elif word == "tab":
                emit_text("\t", control_start, idx)
            elif word == "line":
                emit_text("\n", control_start, idx)
        elif ch in "\r\n":
            idx += 1
        else:
            if group_frames:
                group_frames[-1]['first_control'] = False
            # A complete byte-stream state prevents a DBCS trail byte from
            # being mistaken for a new lead byte before an RTF control.
            pending_lead = byte_decoder is not None and bool(byte_decoder.getstate()[0])
            if not pending_lead and ord(ch) >= 128 and codepage in DBCS_CODEPAGES and idx + 1 < length and text[idx + 1] in "\\{}":
                explicit_hex = text.startswith("\\'", idx + 1)
                if not explicit_hex:
                    try:
                        ambiguous_pair = len(text[idx:idx + 2].encode('latin-1').decode(codepage)) == 1
                    except UnicodeDecodeError:
                        ambiguous_pair = False
                    if ambiguous_pair:
                        raise ParseError('unsupported', {'stage': 'RTF stream', 'offset': idx},
                                         'Ambiguous raw DBCS byte and RTF delimiter; explicit hex byte escapes required')
            append_encoded(ch.encode('latin-1'))
            idx += 1

    flush_hex()
    if stack:
        add_geometry_issue({'code': 'rtf_unclosed_groups', 'count': len(stack), 'message': 'Input ended before all groups closed'})
    if skip_bytes:
        add_geometry_issue({'code': 'rtf_unicode_fallback_missing', 'message': 'Input ended inside Unicode fallback'})
    recover_pending_row()
    flush_table_if_needed()
    append_paragraph()

    for resource, omitted, retained, limit in (
            ('paragraphs', omitted_paragraphs, len(paragraphs), MAX_PARAGRAPHS),
            ('tables', omitted_tables, len(tables), MAX_TABLES)):
        if omitted:
            limit_issues.append({'code': 'rtf_resource_limit_exceeded', 'resource': resource,
                                 'limit': limit, 'retained': retained, 'omitted': omitted,
                                 'message': 'Only the bounded prefix of this resource is retained'})

    if font_encoding_unknown and encoded_text_seen or font_encoding_conflict and encoded_high_bytes_seen:
        add_geometry_issue({'code': 'rtf_font_encoding_unresolved',
                            'message': 'Byte text uses the document code page; conflicting or unknown font encoding cannot be reliably applied'})
    if merge_markers != captured_merge_markers:
        add_geometry_issue({"code": "rtf_merge_geometry_not_reconstructed", "message": "Merge markers outside a complete cell grid"})
    visibility_result = visibility.finish(idx)
    full_text = "\n".join(p["text"] for p in paragraphs)
    # Reserve diagnostic slots for omission notices even when recovery issues
    # already filled the cap. Exact counters remain outside the bounded list.
    reserved_issues = limit_issues + list(pinned_issues.values())
    other_issues = [item for item in geometry_issues if item.get('code') not in pinned_issues]
    issues = reserved_issues + other_issues[:100 - len(reserved_issues)]
    partial = bool(issues)
    return {
        "schema_version": "rtf_readonly_v1",
        "format": "rtf",
        "page_properties": page_properties,
        "status": "partial" if partial else "ok",
        "partial": partial,
        "issues": issues,
        "geometry_issues_truncated": max(0, geometry_issue_count - (len(issues) - len(limit_issues))),
        **({"visibility": visibility_result} if visibility_result is not None else {}),
        "field_count": field_count,
        "object_policy": object_policy,
        "object_count": len(objects),
        "objects": objects,
        "objects_decoded": False,
        "hidden_control_count": hidden_control_count,
        "revision_control_count": revision_control_count,
        "omitted_paragraph_count": omitted_paragraphs,
        "omitted_table_count": omitted_tables,
        "text": full_text,
        "paragraphs": paragraphs,
        "tables": tables,
        "blocks": blocks,
        "reading_assessment": {
            "status": "partial" if partial else "ok",
            "character_count": len(full_text),
            "paragraph_count": len(paragraphs),
            "table_count": len(tables),
        },
    }


def require_publishable_candidate(candidate):
    """Related-content omissions are disclosed; recovered corruption cannot publish."""
    allowed = {'rtf_destination_skipped', 'rtf_field_cached_result_unverified'}
    if candidate.get('status') not in ('ok', 'partial'):
        raise RuntimeError('Candidate readback rejected')
    issues = candidate.get('issues', [])
    if candidate.get('status') == 'partial' and (candidate.get('geometry_issues_truncated') or not issues
                                                or any(item.get('code') not in allowed for item in issues)):
        raise RuntimeError('Candidate readback requires structural or text recovery; publication stopped')
