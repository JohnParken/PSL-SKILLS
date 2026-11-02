#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded, content-identified RTF stream reader for WPS-family and RTF documents."""

from typing import Dict, Any, List, Optional

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

    stack: List[tuple] = []
    hex_buffer = bytearray()

    def flush_hex():
        nonlocal hex_buffer
        if hex_buffer:
            try:
                decoded = hex_buffer.decode(codepage, errors="replace")
            except Exception:
                decoded = hex_buffer.decode("latin-1", errors="replace")
            if in_table:
                current_cell.append(decoded)
            else:
                current_para.append(decoded)
            hex_buffer = bytearray()

    def flush_table_if_needed():
        nonlocal current_table, tables
        if current_table:
            tables.append({"rows": current_table})
            current_table = []

    while idx < length:
        ch = text[idx]

        if skip_bytes > 0:
            skip_bytes -= 1
            idx += 1
            continue

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
            idx += 1
            continue

        if skip_group_depth is not None and group_depth >= skip_group_depth:
            idx += 1
            continue

        if ch == "\\":
            flush_hex()
            idx += 1
            if idx >= length:
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
            num_val = int(num_str) if num_str else None

            # Optional trailing whitespace delimiter
            if idx < length and text[idx] == " ":
                idx += 1

            # Dispatch control word
            if word in IGNORED_DESTINATIONS:
                skip_group_depth = group_depth
            elif word == "ansicpg" and num_val:
                codepage = f"cp{num_val}"
            elif word == "uc" and num_val is not None:
                uc = num_val
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
            elif word == "par":
                flush_hex()
                flush_table_if_needed()
                p_text = "".join(current_para).strip()
                if p_text and len(paragraphs) < MAX_PARAGRAPHS:
                    paragraphs.append({"text": p_text})
                current_para = []
            elif word == "intbl":
                in_table = True
            elif word == "cell":
                flush_hex()
                cell_text = "".join(current_cell).strip()
                current_row.append({"text": cell_text})
                current_cell = []
            elif word == "row":
                flush_hex()
                if current_row:
                    current_table.append(current_row)
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
    flush_table_if_needed()
    final_p = "".join(current_para).strip()
    if final_p and len(paragraphs) < MAX_PARAGRAPHS:
        paragraphs.append({"text": final_p})

    full_text = "\n".join(p["text"] for p in paragraphs)
    return {
        "schema_version": "rtf_readonly_v1",
        "format": "rtf",
        "status": "ok",
        "text": full_text,
        "paragraphs": paragraphs,
        "tables": tables,
        "reading_assessment": {
            "status": "ok",
            "character_count": len(full_text),
            "paragraph_count": len(paragraphs),
            "table_count": len(tables),
        },
    }
