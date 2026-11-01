"""Bounded logical MS-DOC paragraph CP ranges, independent of rendering.

Ranges use UTF-16 code units within the stored story, NOT physical WordDocument
byte offsets, effective visual layout, or an offset into rendered/filtered text.
"""

MAX_PARAGRAPHS = 100000
MAX_UNITS = 8 * 1024 * 1024


def paragraph_cp_ranges(text, cp_start, *, max_paragraphs=MAX_PARAGRAPHS):
    if not isinstance(text, str) or type(cp_start) is not int or cp_start < 0:
        raise ValueError('Expected stored story text and nonnegative CP start')
    if type(max_paragraphs) is not int or not 0 <= max_paragraphs <= MAX_PARAGRAPHS:
        raise ValueError('Invalid paragraph range budget')
    results = []
    start = cursor = cp_start
    for char in text:
        cursor += 2 if ord(char) > 0xFFFF else 1
        if cursor - cp_start > MAX_UNITS:
            raise ValueError('Stored story CP budget exceeded')
        if char == '\r':
            if len(results) >= max_paragraphs:
                raise ValueError('Paragraph CP range budget exceeded')
            results.append({'index': len(results), 'cp_start': start,
                            'cp_end': cursor, 'text_cp_end': cursor - 1})
            start = cursor
    if start < cursor:
        if len(results) >= max_paragraphs:
            raise ValueError('Paragraph CP range budget exceeded')
        results.append({'index': len(results), 'cp_start': start,
                        'cp_end': cursor, 'text_cp_end': cursor})
    return results
