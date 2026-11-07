"""Opt-in content selection and whole-slide, byte-bounded inspection pages."""
from __future__ import annotations

import hashlib
import hmac
import json
import re


def encoded(value: dict) -> str:
    """The CLI wire encoding, including its final newline."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n"


def select_pages(spec: str | None, count: int) -> list[int]:
    if spec is None:
        return list(range(1, count + 1))
    if len(spec) > 10000 or not re.fullmatch(r"[1-9][0-9]*(?:-[1-9][0-9]*)?(?:,[1-9][0-9]*(?:-[1-9][0-9]*)?)*", spec):
        raise ValueError("Invalid slide selection; use ascending, nonoverlapping 1-based ranges such as 1-3,5")
    selected = []
    for item in spec.split(","):
        bounds = item.split("-")
        first, last = int(bounds[0]), int(bounds[-1])
        if first > last or last > count or (selected and first <= selected[-1]):
            raise ValueError("Slide ranges must be ascending, nonoverlapping and inside the deck")
        selected.extend(range(first, last + 1))
    return selected


def bounded_result(value: dict, source: bytes, *, summary: bool = False, slides: str | None = None,
                   notes: str = "include", allow_png_zero_padding: bool = False, max_output_bytes: int | None = None, cursor: str | None = None) -> dict:
    # Validation has already inspected EVERY package part. Selection never alters that boundary.
    from .runtime import RESULT_SCHEMA, TaskError
    selected = select_pages(slides, value["slide_count"])
    if max_output_bytes is not None and not 512 <= max_output_bytes <= 67108864:
        raise TaskError("max-output-bytes must be between 512 and 67108864; rejected budgets do not bound error JSON", "invalid_budget")
    if cursor and max_output_bytes is None:
        raise TaskError("cursor requires max-output-bytes and the original selection parameters", "invalid_cursor")
    chosen = []
    for number in selected:
        slide = dict(value["slides"][number - 1])
        if summary:
            slide = {"number": number, "objects": slide["objects"], "text_paragraphs": len(slide["text"]),
                     "notes_paragraphs": len(slide["notes"])}
        elif notes == "omit":
            slide.pop("notes")
        chosen.append(slide)
    binding = encoded({"version": 1, "slides": selected, "summary": summary, "notes": notes,
                       "budget": max_output_bytes, "input": value["input"],
                       "padding": allow_png_zero_padding}).encode("utf-8")
    # No stored secret or installation writes. This detects modified cursors, not hostile
    # forgery by someone who already possesses the source; it is not an authorization token.
    key = hashlib.sha256(b"psl.pptx.cursor.v1\0" + source).digest()

    def token(position: int) -> str:
        signature = hmac.new(key, binding + str(position).encode("ascii"), hashlib.sha256).hexdigest()
        return str(position) + "." + signature

    start = 0
    if cursor is not None:
        if not re.fullmatch(r"[0-9]{1,8}\.[0-9a-f]{64}", cursor):
            raise TaskError("Malformed inspection cursor", "invalid_cursor")
        start = int(cursor.split(".")[0])
        if not 0 < start < len(chosen) or not hmac.compare_digest(cursor, token(start)):
            raise TaskError("Cursor does not match this source, parameters or position; restart inspection", "invalid_cursor")
    base = {key: item for key, item in value.items() if key != "slides"}
    base["coverage"] = {"validation": "all_package_parts", "content": "summary" if summary else "selected_slides",
                        "selected_slides": selected, "notes": "counts_only" if summary else notes,
                        "not_returned_is_missing": False}

    def page(end: int) -> dict:
        return {**base, "slides": chosen[start:end], "pagination": {
            "start": start, "end": end, "total": len(chosen), "complete": end == len(chosen),
            "next_cursor": token(end) if end < len(chosen) else None}}

    def size(result: dict) -> int:
        return len(encoded({"schema": RESULT_SCHEMA, "ok": True, "command": "inspect", **result}).encode("utf-8"))

    if max_output_bytes is None:
        return page(len(chosen))
    # Binary search avoids repeatedly serializing every prefix of a large deck.
    low, high, best = start + 1, len(chosen), None
    while low <= high:
        middle = (low + high) // 2
        candidate = page(middle)
        if size(candidate) <= max_output_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    # The terminal cursor is shorter than intermediate cursors, so check it separately.
    final = page(len(chosen))
    if size(final) <= max_output_bytes:
        return final
    if best is None:
        required = size(page(start + 1))
        raise TaskError(f"Next whole slide and metadata need {required} bytes; restart without cursor using at least this budget, "
                        "or omit notes/use summary/narrow slides. No content was truncated.", "output_budget_too_small")
    return best
