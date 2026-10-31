"""Bounded MS-DOC paragraph ranges in logical UTF-16 code-point units (CP).

A CP range is a logical stored-text position, not a physical byte offset or
an effective/visible layout range. Consumers must retain the status provided
by the reader and never apply these ranges to field-filtered text.
"""

MAX_PARAGRAPHS = 100000


def paragraph_cp_ranges(text, cp_start):
    """Return half-open stored CP ranges, including the terminating CR if present.

    Each entry includes text_cp_end, the exclusive end of the text *before*
    its CR terminator. A paragraph without CR uses cp_end == text_cp_end.
    """
    if not isinstance(text, str) or type(cp_start) is not int or cp_start < 0:
        raise ValueError("Expected stored story text and nonnegative CP start")
    spans = []
    start = cursor = cp_start
    for char in text:
        cursor += 2 if ord(char) > 0xFFFF else 1
        if char == '\r':
            if len(spans) >= MAX_PARAGRAPHS:
                raise ValueError("Paragraph CP range budget exceeded")
            spans.append({"index": len(spans), "cp_start": start,
                          "cp_end": cursor, "text_cp_end": cursor - 1})
            start = cursor
    if start < cursor:
        if len(spans) >= MAX_PARAGRAPHS:
            raise ValueError("Paragraph CP range budget exceeded")
        spans.append({"index": len(spans), "cp_start": start,
                      "cp_end": cursor, "text_cp_end": cursor})
    return spans
