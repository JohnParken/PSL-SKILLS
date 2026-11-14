"""Direct-format RTF filters must either honor the selected view or refuse it."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from wps_access import ParseError
from wps_rtf_read import parse_rtf_bytes
import wps_rtf_visibility


class RtfVisibilityTests(unittest.TestCase):
    """Independent literal bytes, including delayed decoding and group scopes."""

    @staticmethod
    def parse(body, **options):
        """Wrap an ASCII/body-byte fixture with an explicit ANSI code page."""
        return parse_rtf_bytes(b'{\\rtf1\\ansi\\ansicpg936 ' + body + b'}', **options)

    def test_default_retains_hidden_and_revision_text(self):
        result = self.parse(b'A{\\v hidden}{\\deleted old}{\\revised new}B')
        self.assertEqual(result['text'], 'AhiddenoldnewB')
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('visibility', result)

    def test_hidden_exclude_respects_nested_group_scope_and_v0(self):
        result = self.parse(b'A{\\v H{\\v0 V}H}B\\par C', hidden_text='exclude')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], 'AVB\nC')
        self.assertEqual(result['visibility']['hidden_excluded_utf16_units'], 2)

    def test_hidden_byte_buffers_flush_before_flag_changes(self):
        result = self.parse(b"A\\v \xd6\xd0\\v0 B{\\v \\'d6\\'d0}C", hidden_text='exclude')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], 'ABC')
        self.assertEqual(result['visibility']['excluded_utf16_units'], 2)

    def test_unicode_escaped_symbols_and_soft_breaks_do_not_leak(self):
        body = b'A{\\v \\u20013?\\u-10179?\\u-8704?\\tab\\line\\~\\_\\{\\}\\\\}B'
        result = self.parse(body, hidden_text='exclude')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], 'AB')
        self.assertEqual(result['visibility']['excluded_utf16_units'], 10)

    def test_plain_clears_direct_hidden_but_pard_does_not(self):
        result = self.parse(b'A{\\v hidden\\pard stillhidden\\plain visible}B', hidden_text='exclude')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], 'AvisibleB')

    def test_hidden_text_in_table_cells_is_filtered_without_dropping_cells(self):
        body = b'Before\\par\\trowd\\cellx1000\\cellx2000\\intbl A{\\v secret}B\\cell {\\v allhidden}\\cell\\row After\\par'
        result = self.parse(body, hidden_text='exclude')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([cell['text'] for cell in result['tables'][0]['rows'][0]], ['AB', ''])
        self.assertEqual(result['text'], 'Before\nAfter')

    def test_three_revision_views_and_nested_resets(self):
        body = b'A{\\deleted old}{\\revised new}{\\deleted D{\\deleted0 V}D}B'
        expected = {'accepted': 'AnewVB', 'original': 'AoldDVDB', 'all': 'AoldnewDVDB'}
        for policy, text in expected.items():
            with self.subTest(policy=policy):
                result = self.parse(body, revisions=policy)
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['text'], text)
                self.assertEqual(result['visibility']['revisions'], policy)

    def test_revision_views_apply_to_table_cells(self):
        body = b'\\trowd\\cellx1000\\intbl A{\\deleted old}{\\revised new}B\\cell\\row'
        for policy, expected in (('accepted', 'AnewB'), ('original', 'AoldB')):
            result = self.parse(body, revisions=policy)
            self.assertEqual(result['tables'][0]['rows'][0][0]['text'], expected)

    def test_hidden_and_revision_filters_combine_without_double_counting(self):
        result = self.parse(b'A{\\deleted\\v both}{\\revised new}B', hidden_text='exclude', revisions='accepted')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], 'AnewB')
        self.assertEqual(result['visibility']['excluded_utf16_units'], 4)
        self.assertEqual(result['visibility']['hidden_excluded_utf16_units'], 4)
        self.assertEqual(result['visibility']['revision_excluded_utf16_units'], 4)

    def test_annotate_keeps_text_and_records_no_raw_text(self):
        result = self.parse(b'A{\\v secret}B', hidden_text='annotate')
        self.assertEqual(result['text'], 'AsecretB')
        self.assertEqual(result['status'], 'ok')
        records = result['visibility']['runs']
        self.assertEqual([item['state'] for item in records], ['visible', 'hidden', 'visible'])
        self.assertEqual([item['utf16_units'] for item in records], [1, 6, 1])
        self.assertNotIn('secret', str(records))
        for item in records:
            self.assertLess(item['source']['byte_start'], item['source']['byte_end'])

    def test_annotate_and_revision_filter_records_excluded_units(self):
        result = self.parse(b'A{\\deleted secret}{\\revised new}B', hidden_text='annotate', revisions='accepted')
        self.assertEqual(result['text'], 'AnewB')
        records = result['visibility']['runs']
        self.assertEqual(records[1]['action'], 'revision_excluded')
        self.assertEqual(records[1]['revision'], 'deleted')
        self.assertNotIn('secret', str(records))

    def test_annotate_unknown_styles_keeps_text_and_marks_unknown(self):
        result = self.parse(b'{\\stylesheet{\\s0\\v Hidden;}}\\s0 secret', hidden_text='annotate')
        self.assertEqual(result['text'], 'secret')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['visibility']['runs'][0]['state'], 'unknown')

    def test_style_without_visibility_flags_is_supported(self):
        body = b'{\\stylesheet{\\s0\\b Normal;}{\\cs1\\i Emphasis;}}\\s0 A{\\cs1 B}{\\v secret}C'
        result = self.parse(body, hidden_text='exclude', revisions='accepted')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], 'ABC')

    def test_style_visibility_or_unknown_inheritance_fails_closed(self):
        for body in (b'{\\stylesheet{\\s0\\v Hidden;}}\\s0 secret',
                     b'{\\stylesheet{\\s0\\webhidden Web;}}secret',
                     b'\\s42 secret', b'{\\stylesheet{\\s0\\newvisibility Hidden;}}secret'):
            with self.subTest(body=body), self.assertRaises(ParseError):
                self.parse(body, hidden_text='exclude')

    def test_style_revision_flags_fail_closed(self):
        for body in (b'{\\stylesheet{\\s0\\deleted Deleted;}}\\s0 text',
                     b'{\\stylesheet{\\s0\\revised Added;}}\\s0 text'):
            with self.subTest(body=body), self.assertRaises(ParseError):
                self.parse(body, revisions='accepted')

    def test_fields_fail_closed_for_explicit_filters(self):
        for options in ({'hidden_text': 'exclude'}, {'revisions': 'accepted'}, {'revisions': 'original'}):
            with self.subTest(options=options), self.assertRaises(ParseError):
                self.parse(b'A{\\field{\\*\\fldinst DATE}{\\fldrslt cached}}B', **options)

    def test_ambiguous_web_view_and_revision_flags_fail_closed(self):
        with self.assertRaises(ParseError):
            self.parse(b'{\\webhidden text}', hidden_text='exclude')
        with self.assertRaises(ParseError):
            self.parse(b'{\\deleted\\revised text}', revisions='accepted')
        # General hidden is conclusive even when the web-view flag is set.
        result = self.parse(b'A{\\v\\webhidden secret}B', hidden_text='exclude')
        self.assertEqual(result['text'], 'AB')

    def test_structural_markers_under_filtered_flags_fail_closed(self):
        for body in (b'A{\\v hidden\\par}B', b'\\trowd\\cellx1000\\intbl{\\v hidden\\cell}\\row',
                     b'\\trowd\\cellx1000\\intbl A\\cell{\\v\\row}'):
            with self.subTest(body=body), self.assertRaises(ParseError):
                self.parse(body, hidden_text='exclude')
        for control in (b'\\par', b'\\cell', b'\\row', b'\\sect'):
            with self.subTest(control=control), self.assertRaises(ParseError):
                self.parse(b'{\\deleted old' + control + b'}', revisions='accepted')

    def test_unknown_and_historical_controls_fail_closed(self):
        for body in (b'\\newvisibility secret', b'{\\*\\oldcprops\\v secret}visible',
                     b'\\movefrom deleted', b'\\revmoved moved', b'\\defchp hidden'):
            for options in ({'hidden_text': 'exclude'}, {'revisions': 'accepted'}):
                with self.subTest(body=body, options=options), self.assertRaises(ParseError):
                    self.parse(body, **options)

    def test_invalid_flags_scope_and_surrogate_selection_fail_closed(self):
        for body in (b'\\v2 secret', b'\\v999999999999999 secret', b'{\\v secret'):
            with self.subTest(body=body), self.assertRaises(ParseError):
                self.parse(body, hidden_text='exclude')
        with self.assertRaises(ParseError):
            self.parse(b'\\u-10179?{\\v\\u-8704?}', hidden_text='exclude')

    def test_revision_plain_reset_is_explicitly_unsupported(self):
        with self.assertRaises(ParseError):
            self.parse(b'{\\deleted old\\plain ambiguous}', revisions='accepted')

    def test_annotation_budget_fails_instead_of_truncating(self):
        with patch.object(wps_rtf_visibility, 'MAX_VISIBILITY_RUNS', 2), self.assertRaises(ParseError) as caught:
            self.parse(b'A{\\v secret}B', hidden_text='annotate')
        self.assertEqual(caught.exception.status, 'resource_limit')

    def test_unicode_runs_coalesce_without_splitting_surrogate_pairs(self):
        with patch.object(wps_rtf_visibility, 'MAX_VISIBILITY_RUNS', 1):
            result = self.parse(b'\\u-10179?\\u-8704?\\u20013?', hidden_text='annotate')
        self.assertEqual(result['text'], '😀中')
        self.assertEqual(result['visibility']['runs'][0]['utf16_units'], 3)

    def test_related_notes_never_mix_into_main_body(self):
        for destination in (b'footnote', b'annotation'):
            for options in ({}, {'hidden_text': 'exclude'}, {'revisions': 'accepted'}):
                with self.subTest(destination=destination, options=options):
                    result = self.parse(b'A{\\' + destination + b' Note}B', **options)
                    self.assertEqual(result['text'], 'AB')
                    self.assertEqual(result['status'], 'partial')
                    self.assertTrue(any(item.get('destination') == destination.decode() for item in result['issues']))

    def test_generated_document_supports_harmless_formatting_controls(self):
        from wps_rtf_builder import build_document_from_spec
        blocks = [{'type': 'title', 'text': '标题'}, {'type': 'paragraph', 'text': '正文'},
                  {'type': 'table', 'rows': [['甲', '乙']]}]
        for page_numbering in (False, True):
            source = build_document_from_spec({'page_numbering': page_numbering, 'blocks': blocks}).render().encode('ascii')
            result = parse_rtf_bytes(source, hidden_text='exclude', revisions='accepted')
            self.assertEqual(result['text'], '标题\n正文')
            self.assertEqual([cell['text'] for cell in result['tables'][0]['rows'][0]], ['甲', '乙'])
            self.assertEqual(result['status'], 'partial' if page_numbering else 'ok')

    def test_unified_entry_and_summary_describe_rtf_visibility(self):
        from read_wps import read_bytes
        result = read_bytes(b'{\\rtf1 A{\\v hidden}B}', hidden_text='annotate', reading_mode='standard')
        self.assertEqual(result['text'], 'AhiddenB')
        summary = result['reading_assessment']['visibility']
        self.assertEqual(summary['policy'], 'annotate')
        self.assertEqual(summary['recorded_run_decisions'], {'retained:visible': 2, 'retained:hidden': 1})
        self.assertEqual(summary['by_part']['RTF main content']['recorded_runs'], 3)
        result = read_bytes(b'{\\rtf1 A{\\v hidden}B}', hidden_text='exclude', reading_mode='standard')
        self.assertEqual(result['text'], 'AB')
        self.assertEqual(result['reading_assessment']['visibility']['policy'], 'exclude')
        self.assertEqual(read_bytes(b'{\\rtf1 ordinary}', reading_mode='standard')['text'], 'ordinary')

    def test_unknown_control_annotation_never_claims_resolved_visibility(self):
        result = self.parse(b'\\newvisibility text', hidden_text='annotate')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['visibility']['runs'][0]['state'], 'unknown')

    def test_missing_style_base_fails_but_defined_harmless_base_is_supported(self):
        with self.assertRaises(ParseError):
            self.parse(b'{\\stylesheet{\\s1\\sbasedon99 Derived;}}\\s1 text', hidden_text='exclude')
        body = b'{\\stylesheet{\\s0 Normal;}{\\s1\\sbasedon0\\b Derived;}}\\s1 text'
        self.assertEqual(self.parse(body, hidden_text='exclude')['text'], 'text')

    def test_unknown_policies_fail_explicitly(self):
        for options in ({'hidden_text': 'bad'}, {'revisions': 'bad'}):
            with self.subTest(options=options), self.assertRaises(ParseError):
                self.parse(b'text', **options)


if __name__ == '__main__':
    unittest.main()
