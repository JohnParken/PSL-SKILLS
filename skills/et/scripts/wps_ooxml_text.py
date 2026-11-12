# SPDX-License-Identifier: MIT
"""Bounded saved-text reader shared by OOXML workbooks, sheets and comments.

OOXML ``_xHHHH_`` escapes are decoded exactly once while plain XML text remains
unchanged. The reader owns the workbook-wide retained-text budget and returns
the truncated value so callers cannot keep unbudgeted cell text.
"""
from wps_ooxml import split_tag
from wps_ooxml_escape import decode_ooxml_text
from wps_rich_text import MAX_SEGMENTS, ooxml_run_font

TRANS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
STRICT = 'http://purl.oclc.org/ooxml/spreadsheetml/main'
MAIN_NAMESPACES = (TRANS, STRICT)
MAX_TEXT = 8 * 1024 * 1024
MAX_DISPLAY_TEXT = 2 * 1024 * 1024
MAX_CELL_TEXT = 32767


def is_main(element, local):
    namespace, name = split_tag(element.tag)
    return namespace in MAIN_NAMESPACES and name == local


class TextReader:
    """Tolerant saved-string reader shared by the workbook, sheets and comments."""

    def __init__(self, namespace, report):
        self.namespace = namespace
        self.report = report
        self.segments_remaining = MAX_SEGMENTS
        self.rich_formatting_seen = False
        self.total_text = 0
        self.limit = MAX_TEXT
        self.text_limited = False
        self.uncertain = False
        self.display_text = 0
        self.display_limit = MAX_DISPLAY_TEXT
        self.display_limited = False
        self.omitted_display_formats = set()
        self._display_decisions = {}

    def reserve_display_text(self, *values, cache_key=None):
        """Charge display-only strings without making them cell-value omissions."""
        if cache_key is not None and cache_key in self._display_decisions:
            return self._display_decisions[cache_key]
        units = sum(len(value) + sum(ord(char) > 0xFFFF for char in value)
                    for value in values if isinstance(value, str))
        accepted = units <= self.display_limit - self.display_text
        if accepted:
            self.display_text += units
            if cache_key is not None:
                self._display_decisions[cache_key] = True
            return True
        self.display_limited = True
        if cache_key is not None:
            self._display_decisions[cache_key] = False
            if cache_key[0] == 'format':
                self.omitted_display_formats.add(cache_key[1])
        event = self.report.add(
            'display_text_budget_truncated',
            'Display/format metadata exceeded its own budget; saved cell '
            'values are retained and affected display fields are omitted.',
            display_limit=self.display_limit,
            cell_value_impact='none_within_retained_cells')
        if event is not None:
            event['count'] += 1
        return False

    def budget_text(self, raw, sheet, ref, *, value_affects_cells=True):
        """Return the retained cell text under the workbook-wide character budget.

        The unit is Unicode code points (Python ``len``), matching the existing
        limit. A truncated value is returned so callers never keep unbudgeted
        text while still charging the same total.
        """
        if not isinstance(raw, str):
            return raw
        if len(raw) > MAX_CELL_TEXT:
            self.report.add('string_length_limit_reached', kept_chars=MAX_CELL_TEXT,
                            declared_chars=len(raw), cell=ref)
            if value_affects_cells:
                self.uncertain = True
            raw = raw[:MAX_CELL_TEXT]
        if self.text_limited:
            self.report.add('text_limit_reached', 'Further cell text is not retained',
                            sheet=sheet, cell=ref, kept_chars=0,
                            kept_total=self.limit)
            return ''
        remaining = self.limit - self.total_text
        if len(raw) > remaining:
            self.text_limited = True
            if value_affects_cells:
                self.uncertain = True
            self.report.add('text_limit_reached', 'Further cell text is not retained',
                            sheet=sheet, cell=ref, kept_chars=max(0, remaining),
                            kept_total=self.limit)
            raw = raw[:max(0, remaining)]
        self.total_text += len(raw)
        return raw

    def text_value(self, node, segments=None, *, rich_issue=True, value_affects_cells=True):
        node_namespace = split_tag(node.tag)[0]
        run_namespace = node_namespace if node_namespace in MAIN_NAMESPACES else self.namespace
        run_tag = lambda name: '{' + run_namespace + '}' + name
        if (node.text or '').strip():
            self.report.add('string_raw_text_not_interpreted', element=str(node.tag)[:120])
        pieces = []
        raw_parts = []
        sequences = 0
        literal_escapes = 0
        invalid_sequences = 0

        def decoded_text(raw_text):
            nonlocal sequences, literal_escapes, invalid_sequences
            decoded, evidence = decode_ooxml_text(raw_text)
            if evidence:
                sequences += evidence.get('escape_sequences', 0)
                literal_escapes += evidence.get('literal_escapes', 0)
                invalid_sequences += evidence.get('invalid_sequences', 0)
                raw_parts.append(raw_text)
                if evidence.get('invalid_sequences'):
                    self.report.add('ooxml_escape_invalid_surrogate',
                                    element=str(node.tag)[:120],
                                    count=evidence['invalid_sequences'])
            return decoded

        for child in node:
            if (child.tail or '').strip():
                self.report.add('string_tail_text_ignored', element=str(node.tag)[:120])
            child_namespace, child_name = split_tag(child.tag)
            if child_namespace not in MAIN_NAMESPACES:
                self.report.add('string_element_not_interpreted',
                                element=str(child.tag)[:120])
                continue
            if child_name == 't':
                if len(child):
                    self.report.add('string_text_container_not_interpreted',
                                    element=str(child.tag)[:120])
                    if value_affects_cells:
                        self.uncertain = True
                text = decoded_text(child.text or '')
                pieces.append(text)
                if segments is not None:
                    segments.append({'text': text})
            elif child_name == 'r':
                if (child.text or '').strip():
                    self.report.add('string_raw_text_not_interpreted', element='rich run')
                run_text = []
                for sub in child:
                    sub_namespace, sub_name = split_tag(sub.tag)
                    if sub_namespace not in MAIN_NAMESPACES:
                        self.report.add('rich_string_element_not_interpreted',
                                        element=str(sub.tag)[:120])
                        continue
                    if sub_name == 't':
                        if len(sub):
                            self.report.add('string_text_container_not_interpreted',
                                            element='rich run text')
                            if value_affects_cells:
                                self.uncertain = True
                        run_text.append(decoded_text(sub.text or ''))
                    elif sub_name != 'rPr':
                        self.report.add('rich_string_element_not_interpreted',
                                        element=str(sub.tag)[:120])
                text = ''.join(run_text)
                pieces.append(text)
                if segments is not None:
                    segments.append({'text': text, 'font': ooxml_run_font(child, run_tag)})
                if rich_issue and not self.rich_formatting_seen:
                    self.report.add('rich_string_formatting_not_applied')
                    self.rich_formatting_seen = True
            else:
                self.report.add('string_element_not_interpreted',
                                element=str(child.tag)[:120])
        text = ''.join(pieces)
        formatting = {}
        if raw_parts:
            raw = ''.join(raw_parts)
            encoding = {
                'scheme': 'ooxml_xhhh', 'decoded': True,
                'escape_sequences': sequences,
                'literal_escapes': literal_escapes,
            }
            if invalid_sequences:
                encoding['invalid_sequences'] = invalid_sequences
            if len(raw) > MAX_CELL_TEXT:
                encoding['raw_text'] = raw[:MAX_CELL_TEXT]
                encoding['raw_text_truncated'] = True
            else:
                encoding['raw_text'] = raw
            formatting['text_encoding'] = encoding
        if segments is not None:
            formatting['segments'] = segments
        return text, formatting

    def cell_text(self, node, *, value_affects_cells=True):
        runs = [child for child in node if is_main(child, 'r')]
        segments = [] if runs and len(node) <= self.segments_remaining else None
        text, formatting = self.text_value(node, segments,
                                           value_affects_cells=value_affects_cells)
        if segments is not None:
            self.segments_remaining -= len(segments)
            return text, formatting
        if runs:
            formatting.pop('segments', None)
            formatting['segments_status'] = 'segment_budget_exceeded'
            return text, formatting
        return text, formatting

    def raw_text(self, raw, sheet, ref):
        """Budget a raw saved scalar string as a cell value."""
        return self.budget_text(raw, sheet, ref)


