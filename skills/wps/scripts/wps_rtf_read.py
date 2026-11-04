#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded, content-identified RTF stream reader for WPS-family and RTF documents."""

from typing import Dict, Any, List, Optional
import re

MAX_RTF_BYTES = 32 * 1024 * 1024
MAX_PARAGRAPHS = 100000
MAX_TABLES = 10000

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


def parse_rtf_bytes(data: bytes, max_bytes: int = MAX_RTF_BYTES) -> Dict[str, Any]:
    """Parse raw bytes of an RTF document into structured paragraphs and tables."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("RTF input must be bytes or bytearray")

    if len(data) > max_bytes:
        raise ValueError(f"RTF file exceeds size limit of {max_bytes} bytes")

    # Fast signature check
    stripped_data = data.lstrip()
    if not stripped_data.startswith(b"{\\rtf"):
        raise ValueError("Invalid RTF signature: missing {\\rtf header")

    # Decode as latin-1 to preserve exact single-byte positions
    text = data.decode("latin-1")
    length = len(text)
    idx = 0

    group_depth = 0
    skip_group_depth: Optional[int] = None
    codepage = "cp936"
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
    geometry_issue_count = 0

    def add_geometry_issue(item):
        nonlocal geometry_issue_count
        geometry_issue_count += 1
        if len(geometry_issues) < 100:
            geometry_issues.append(item)

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
    hex_buffer = bytearray()

    def flush_hex():
        nonlocal hex_buffer
        if hex_buffer:
            try:
                decoded = hex_buffer.decode(codepage, errors="replace")
            except Exception:
                decoded = hex_buffer.decode("latin-1", errors="replace")
                add_geometry_issue({'code': 'rtf_encoding_unavailable', 'message': 'Declared byte encoding unavailable; raw byte mapping retained'})
            if '\ufffd' in decoded:
                add_geometry_issue({'code': 'rtf_encoding_replacement', 'message': 'Incomplete or invalid encoded bytes replaced'})
            if in_table:
                current_cell.append(decoded)
            else:
                current_para.append(decoded)
            hex_buffer = bytearray()

    def flush_table_if_needed():
        nonlocal current_table, tables, row_definitions, captured_merge_markers
        if current_table:
            item = {"rows": current_table}
            captured_merge_markers += sum(bool(cell.get("h")) + bool(cell.get("v")) for row in row_definitions for cell in row)
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

    def recover_pending_row():
        nonlocal current_cell, current_row, current_definitions, in_table
        incomplete = bool(current_cell or current_row or in_table)
        if current_cell or current_row:
            if current_cell:
                current_row.append({'text': joined(current_cell).strip()})
                current_cell = []
            current_table.append(current_row)
            row_definitions.append(current_definitions)
            current_row = []
            current_definitions = []
        if incomplete:
            add_geometry_issue({'code': 'rtf_unterminated_table_row', 'message': 'Available physical cell text retained; row boundary incomplete'})
        in_table = False

    while idx < length:
        ch = text[idx]

        if skip_group_depth is not None and ch == '\\':
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
            group_depth += 1
            stack.append((skip_group_depth, uc))
            idx += 1
            continue
        elif ch == "}":
            flush_hex()
            if stack:
                skip_group_depth, uc = stack.pop()
                group_depth -= 1
            else:
                add_geometry_issue({'code': 'rtf_unmatched_group_end', 'message': 'Extra closing group delimiter'})
            idx += 1
            continue

        if skip_group_depth is not None and group_depth >= skip_group_depth:
            idx += 1
            continue

        if ch == "\\":
            flush_hex()
            idx += 1
            if idx >= length:
                add_geometry_issue({'code': 'rtf_truncated_control', 'message': 'Trailing control escape has no operand'})
                break
            next_ch = text[idx]

            # Literal escaped control symbols
            if next_ch in "\\{}":
                if in_table:
                    current_cell.append(next_ch)
                else:
                    current_para.append(next_ch)
                idx += 1
                continue
            elif next_ch == "*":
                # Destination flag
                match = re.match(r'\\([A-Za-z]+)', text[idx + 1:idx + 65])
                destination = match.group(1) if match else 'unknown'
                if destination not in {'fldinst', 'generator', 'fonttbl', 'colortbl', 'stylesheet', 'info', 'themedata', 'xmlnstbl', 'rsidtbl'}:
                    add_geometry_issue({'code': 'rtf_destination_skipped', 'message': 'Optional destination omitted from main content'})
                skip_group_depth = group_depth
                idx += 1
                continue
            elif next_ch == "'":
                while idx < length and text[idx] == "'":
                    hex_str = text[idx + 1 : idx + 3]
                    if len(hex_str) == 2 and all(c in "0123456789abcdefABCDEF" for c in hex_str):
                        hex_buffer.append(int(hex_str, 16))
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
                flush_hex()
                continue
            elif next_ch in "\r\n":
                idx += 1
                continue
            elif next_ch == "~":
                (current_cell if in_table else current_para).append("\u00A0")
                idx += 1
                continue
            elif next_ch == "_":
                (current_cell if in_table else current_para).append("-")
                idx += 1
                continue

            # Parse control word
            word_start = idx
            while idx < length and text[idx].isalpha():
                idx += 1
            word = text[word_start:idx]

            num_str = ""
            if idx < length and (text[idx] == "-" or text[idx].isdigit()):
                num_start = idx
                if text[idx] == "-":
                    idx += 1
                while idx < length and text[idx].isdigit():
                    idx += 1
                num_str = text[num_start:idx]
            num_val = int(num_str) if num_str and num_str != '-' and len(num_str) < 12 else None
            unicode_operand_truncated = word == 'u' and idx == length
            if num_str and num_val is None:
                add_geometry_issue({'code': 'rtf_invalid_control_operand', 'message': 'Invalid or oversized numeric control operand'})

            # Optional trailing whitespace delimiter
            if idx < length and text[idx] == " ":
                idx += 1

            # Dispatch control word
            if word in IGNORED_DESTINATIONS:
                if word in {'header', 'footer', 'pict'}:
                    add_geometry_issue({'code': 'rtf_destination_skipped', 'destination': word,
                                        'message': 'Related content or object omitted from main content'})
                skip_group_depth = group_depth
            elif word in {'headerl', 'headerr', 'headerf', 'footerl', 'footerr', 'footerf', 'object', 'objdata', 'fldinst'}:
                add_geometry_issue({'code': 'rtf_destination_skipped', 'destination': word, 'message': 'Related content, object or field instruction omitted'})
                skip_group_depth = group_depth
            elif word == 'field':
                add_geometry_issue({'code': 'rtf_field_cached_result_unverified', 'message': 'Stored field result retained without execution or freshness validation'})
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
                if in_table:
                    current_cell.append(u_char)
                else:
                    current_para.append(u_char)
                skip_bytes = uc
            elif word == 'u':
                add_geometry_issue({'code': 'rtf_unicode_incomplete', 'message': 'Unicode control has no complete numeric operand'})
            elif word == "par":
                flush_hex()
                if not in_table:
                    flush_table_if_needed()
                p_text = joined(current_para).strip()
                if p_text and len(paragraphs) < MAX_PARAGRAPHS:
                    paragraphs.append({"text": p_text})
                    blocks.append({"type": "paragraph", "index": len(paragraphs) - 1})
                current_para = []
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
                current_definition["row_left"] = row_left
                current_definition['right'] = num_val
                current_definitions.append(current_definition)
                current_definition = {}
            elif word == "intbl":
                in_table = True
            elif word == "cell":
                flush_hex()
                cell_text = joined(current_cell).strip()
                current_row.append({"text": cell_text})
                current_cell = []
            elif word == "row":
                flush_hex()
                if current_cell:
                    recover_pending_row()
                if current_row:
                    for definition in current_definitions:
                        definition["row_left"] = row_left
                    current_table.append(current_row)
                    row_definitions.append(current_definitions)
                    current_row = []
                in_table = False
            elif word == "tab":
                (current_cell if in_table else current_para).append("\t")
            elif word == "line":
                (current_cell if in_table else current_para).append("\n")
        elif ch in "\r\n":
            idx += 1
        else:
            flush_hex()
            if in_table:
                current_cell.append(ch)
            else:
                current_para.append(ch)
            idx += 1

    flush_hex()
    if stack:
        add_geometry_issue({'code': 'rtf_unclosed_groups', 'count': len(stack), 'message': 'Input ended before all groups closed'})
    if skip_bytes:
        add_geometry_issue({'code': 'rtf_unicode_fallback_missing', 'message': 'Input ended inside Unicode fallback'})
    recover_pending_row()
    flush_table_if_needed()
    final_p = joined(current_para).strip()
    if final_p and len(paragraphs) < MAX_PARAGRAPHS:
        paragraphs.append({"text": final_p})
        blocks.append({"type": "paragraph", "index": len(paragraphs) - 1})

    if merge_markers != captured_merge_markers:
        add_geometry_issue({"code": "rtf_merge_geometry_not_reconstructed", "message": "Merge markers outside a complete cell grid"})
    full_text = "\n".join(p["text"] for p in paragraphs)
    return {
        "schema_version": "rtf_readonly_v1",
        "format": "rtf",
        "status": "partial" if geometry_issues else "ok",
        "partial": bool(geometry_issues),
        "issues": geometry_issues,
        "geometry_issues_truncated": max(0, geometry_issue_count - len(geometry_issues)),
        "text": full_text,
        "paragraphs": paragraphs,
        "tables": tables,
        "blocks": blocks,
        "reading_assessment": {
            "status": "partial" if geometry_issues else "ok",
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
    if candidate.get('status') == 'partial' and (not issues or any(item.get('code') not in allowed for item in issues)):
        raise RuntimeError('Candidate readback requires structural or text recovery; publication stopped')
