"""Versioned, lossless JSON-text continuation inside a selected slide.

Offsets count Unicode codepoints in compact JSON, not bytes, graphemes or tokens.
A fragment is data, not an independently parseable JSON document. Concatenate
contiguous text for each path, check the total, then JSON-decode exactly once.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

from .inspection import encoded


FRAGMENT_SCHEMA = "psl.pptx.fragments.v1"


def fragment_result(base: dict, chosen: list[dict], source: bytes, binding: bytes,
                    budget: int, cursor: str | None) -> dict:
    """Return one bounded fragment, preserving mandatory metadata and diagnostics."""
    from .runtime import RESULT_SCHEMA, TaskError

    # Like the legacy token, this is deterministic and requires no installation
    # writes. Source holders can forge tokens; tokens confer no authority.
    key = hashlib.sha256(b"psl.pptx.cursor.v2\0" + source).digest()
    binding += FRAGMENT_SCHEMA.encode("ascii")

    def token(index: int, offset: int) -> str:
        position = f"v2.{index}.{offset}"
        signature = hmac.new(key, binding + position.encode("ascii"), hashlib.sha256).hexdigest()
        return position + "." + signature

    index, start = 0, 0
    if cursor is not None:
        if not re.fullmatch(r"v2\.[0-9]{1,8}\.[0-9]{1,12}\.[0-9a-f]{64}", cursor):
            raise TaskError("Malformed within-slide cursor", "invalid_cursor")
        _, raw_index, raw_offset, _ = cursor.split(".")
        index, start = int(raw_index), int(raw_offset)
        if not 0 <= index < len(chosen) or (index == 0 and start == 0) or not hmac.compare_digest(cursor, token(index, start)):
            raise TaskError("Cursor does not match this source, parameters or position; restart inspection", "invalid_cursor")
    # Valid PPTX decks have at least one slide, including a nonempty selection.
    if not chosen:
        raise TaskError("Within-slide continuation requires a nonempty selection", "invalid_input")
    slide = chosen[index]
    serialized = json.dumps(slide, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    total = len(serialized)
    if start >= total:
        raise TaskError("Cursor offset is outside the selected slide; restart inspection", "invalid_cursor")
    assessments = {name: item for name, item in slide.items() if name.endswith("_assessment")}

    def page(end: int) -> dict:
        slide_complete = end == total
        complete = slide_complete and index + 1 == len(chosen)
        next_index, next_offset = (index + 1, 0) if slide_complete else (index, end)
        return {**base, "fragment_schema": FRAGMENT_SCHEMA,
                "slide_assessments": assessments,
                "fragments": [{"path": ["slides", index], "source_slide": slide["number"],
                               "encoding": "json-text", "offset_unit": "unicode_codepoints",
                               "start": start, "end": end, "total": total, "text": serialized[start:end]}],
                "pagination": {"mode": "within_slide", "selected_index": index, "total_slides": len(chosen),
                               "slide_complete": slide_complete, "complete": complete,
                               "next_cursor": None if complete else token(next_index, next_offset)}}

    def size(result: dict) -> int:
        return len(encoded({"schema": RESULT_SCHEMA, "ok": True, "command": "inspect", **result}).encode("utf-8"))

    # A terminal cursor is shorter, so independently check the complete slide.
    final = page(total)
    if size(final) <= budget:
        return final
    low, high, best = start + 1, total - 1, None
    while low <= high:
        middle = (low + high) // 2
        candidate = page(middle)
        if size(candidate) <= budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        required = min(size(page(start + 1)), size(final))
        raise TaskError(f"Metadata, complete diagnostics and the next fragment need at least {required} bytes; "
                        "restart without cursor using at least this budget. No content or diagnostics were truncated.",
                        "output_budget_too_small")
    return best
