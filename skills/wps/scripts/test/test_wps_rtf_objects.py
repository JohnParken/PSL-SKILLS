"""Synthetic RTF object-policy contracts; no native producer/rendering claims."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parents[1])]

from wps_access import ParseError
from wps_rtf_read import parse_rtf_bytes


class RtfObjectPolicyTests(unittest.TestCase):
    def parse(self, data, policy=None, **options):
        if policy is None:
            return parse_rtf_bytes(data, **options)
        return parse_rtf_bytes(data, object_policy=policy, **options)

    def codes(self, result):
        return {item['code'] for item in result['issues']}

    def test_default_marks_opaque_object_and_remains_partial(self):
        data = b'{\\rtf1 Before{\\object{\\*\\objdata 0102}}After}'
        result = self.parse(data)
        self.assertEqual(result['text'], 'Before\ufffcAfter')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['object_count'], 1)
        self.assertFalse(result['objects_decoded'])
        self.assertTrue(result['objects'][0]['placeholder_emitted'])
        self.assertNotIn('0102', result['text'])
        self.assertEqual(data, b'{\\rtf1 Before{\\object{\\*\\objdata 0102}}After}')

    def test_placeholder_keeps_stored_sequence_and_byte_source(self):
        data = b'{\\rtf1 Before{\\pict 010203}After\\par}'
        result = self.parse(data, 'placeholder')
        self.assertEqual(result['text'], 'Before\ufffcAfter')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['objects_decoded'])
        self.assertEqual(result['object_count'], 1)
        obj = result['objects'][0]
        self.assertEqual(obj['kind'], 'pict')
        self.assertEqual(obj['source_byte_offset'], data.index(b'\\pict'))
        self.assertTrue(obj['placeholder_emitted'])
        self.assertTrue(obj['complete'])
        self.assertEqual(obj['status'], 'opaque_not_decoded')

    def test_placeholder_in_cell_does_not_escape_into_main_text(self):
        data = (b'{\\rtf1\\trowd\\cellx1000\\intbl A'
                b'{\\pict 0102}B\\cell\\row}')
        result = self.parse(data, 'placeholder')
        self.assertEqual(result['text'], '')
        self.assertEqual(result['tables'][0]['rows'][0][0]['text'], 'A\ufffcB')
        self.assertEqual(result['object_count'], 1)

    def test_reject_is_content_dependent_and_does_not_decode(self):
        self.assertEqual(self.parse(b'{\\rtf1 Plain}', 'reject')['text'], 'Plain')
        for destination in (b'object', b'pict'):
            with self.subTest(destination=destination):
                with self.assertRaises(ParseError) as caught:
                    self.parse(b'{\\rtf1 A{\\' + destination + b' 0102}B}', 'reject')
                self.assertEqual(caught.exception.status, 'unsupported')
                self.assertEqual(caught.exception.location['stage'], 'RTF stream')

    def test_nested_preview_is_one_outer_object_not_duplicate_markers(self):
        data = (b'{\\rtf1 A{\\object\\objemb'
                b'{\\*\\objclass Package}{\\*\\objdata 0102}'
                b'{\\result{\\pict 0304}}}B}')
        result = self.parse(data, 'placeholder')
        self.assertEqual(result['text'], 'A\ufffcB')
        self.assertEqual(result['object_count'], 1)
        self.assertEqual(len(result['objects']), 1)
        self.assertEqual(result['objects'][0]['kind'], 'object')
        self.assertNotIn('Package', result['text'])

    def test_escaped_destination_spelling_is_only_text(self):
        data = b'{\\rtf1 A\\{\\\\pict\\}B}'
        result = self.parse(data, 'placeholder')
        self.assertEqual(result['text'], 'A{\\pict}B')
        self.assertEqual(result['object_count'], 0)
        self.assertEqual(result['objects'], [])

    def test_non_destination_object_control_is_not_guessed(self):
        # A destination must start its own group, not consume the whole root.
        for policy in (None, 'placeholder', 'reject'):
            with self.subTest(policy=policy):
                with self.assertRaises(ParseError) as caught:
                    self.parse(b'{\\rtf1 Before\\pict After}', policy)
                self.assertEqual(caught.exception.status, 'unsupported')

    def test_binary_payload_delimiters_and_controls_are_never_parsed(self):
        payload = b'}{\\pict\\field\\par'
        data = (b'{\\rtf1 A{\\object\\bin' + str(len(payload)).encode()
                + b' ' + payload + b'}B}')
        result = self.parse(data, 'placeholder')
        self.assertEqual(result['text'], 'A\ufffcB')
        self.assertEqual(result['object_count'], 1)
        self.assertEqual(result['field_count'], 0)
        self.assertTrue(result['objects'][0]['complete'])
        self.assertNotIn('rtf_unclosed_groups', self.codes(result))

    def test_truncated_binary_preserves_only_proven_object_marker(self):
        data = b'{\\rtf1 A{\\pict\\bin20 abc'
        result = self.parse(data, 'placeholder')
        self.assertEqual(result['text'], 'A\ufffc')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['objects'][0]['complete'])
        self.assertIn('rtf_truncated_binary', self.codes(result))
        self.assertIn('rtf_unclosed_groups', self.codes(result))

    def test_unclosed_object_records_incomplete_source(self):
        result = self.parse(b'{\\rtf1 A{\\object{\\*\\objdata 01}', 'placeholder')
        self.assertEqual(result['text'], 'A\ufffc')
        self.assertFalse(result['objects'][0]['complete'])
        self.assertIn('rtf_unclosed_groups', self.codes(result))

    def test_object_budget_covers_default_and_placeholder(self):
        data = b'{\\rtf1 {\\pict 01}{\\object{\\*\\objdata 02}}}'
        for policy in (None, 'placeholder'):
            with self.subTest(policy=policy):
                with patch('wps_rtf_read.MAX_OBJECTS', 1, create=True):
                    with self.assertRaises(ParseError) as caught:
                        self.parse(data, policy)
                self.assertEqual(caught.exception.status, 'resource_limit')

    def test_exact_object_budget_is_inclusive(self):
        data = b'{\\rtf1 A{\\pict 01}B}'
        with patch('wps_rtf_read.MAX_OBJECTS', 1, create=True):
            result = self.parse(data, 'placeholder')
        self.assertEqual(result['text'], 'A\ufffcB')
        self.assertEqual(result['object_count'], 1)

    def test_unknown_object_policy_rejected_before_content(self):
        with self.assertRaises(ParseError) as caught:
            self.parse(b'{\\rtf1 Plain}', 'decode')
        self.assertEqual(caught.exception.status, 'unsupported')

    def test_objects_inside_omitted_related_group_do_not_enter_main(self):
        data = b'{\\rtf1 A{\\header{\\pict 01}}B}'
        result = self.parse(data, 'placeholder')
        self.assertEqual(result['text'], 'AB')
        self.assertEqual(result['object_count'], 0)
        self.assertEqual(result['objects'], [])
        self.assertEqual(result['status'], 'partial')


    def test_placeholder_obeys_hidden_policy(self):
        data = b'{\\rtf1 A{\\v{\\pict 01}}B}'
        for hidden, expected in (('include', 'A\ufffcB'), ('annotate', 'A\ufffcB'),
                                 ('exclude', 'AB')):
            with self.subTest(hidden=hidden):
                result = self.parse(data, 'placeholder', hidden_text=hidden)
                self.assertEqual(result['text'], expected)
                self.assertEqual(result['object_count'], 1)
                self.assertEqual(result['objects'][0]['placeholder_emitted'],
                                 hidden != 'exclude')

    def test_placeholder_obeys_stored_revision_view(self):
        data = b'{\\rtf1 A{\\deleted{\\pict 01}}B{\\revised{\\object 02}}C}'
        for view, expected, emitted in (
                ('all', 'A\ufffcB\ufffcC', [True, True]),
                ('accepted', 'AB\ufffcC', [False, True]),
                ('original', 'A\ufffcBC', [True, False])):
            with self.subTest(view=view):
                result = self.parse(data, 'placeholder', revisions=view)
                self.assertEqual(result['text'], expected)
                self.assertEqual(result['object_count'], 2)
                self.assertEqual([obj['placeholder_emitted'] for obj in result['objects']],
                                 emitted)

    def test_hidden_and_revision_filters_compose_without_marker_leak(self):
        data = b'{\\rtf1 A{\\v\\deleted{\\pict 01}}B}'
        for hidden, view in (('exclude', 'all'), ('include', 'accepted'),
                             ('exclude', 'original')):
            with self.subTest(hidden=hidden, view=view):
                result = self.parse(data, 'placeholder', hidden_text=hidden, revisions=view)
                self.assertEqual(result['text'], 'AB')
                self.assertEqual(result['object_count'], 1)
                self.assertFalse(result['objects'][0]['placeholder_emitted'])

    def test_reject_cannot_be_bypassed_by_hidden_or_revision_filter(self):
        for controls, options in (
                (b'\\v', {'hidden_text': 'exclude'}),
                (b'\\deleted', {'revisions': 'accepted'}),
                (b'\\revised', {'revisions': 'original'}),
                (b'\\v\\deleted', {'hidden_text': 'exclude', 'revisions': 'accepted'})):
            with self.subTest(controls=controls, options=options):
                data = b'{\\rtf1 A{' + controls + b'{\\pict 01}}B}'
                with self.assertRaises(ParseError) as caught:
                    self.parse(data, 'reject', **options)
                self.assertEqual(caught.exception.status, 'unsupported')

    def test_excluded_objects_still_consume_shared_object_budget(self):
        data = b'{\\rtf1 A{\\v{\\pict 01}{\\pict 02}}B}'
        with patch('wps_rtf_read.MAX_OBJECTS', 1, create=True):
            with self.assertRaises(ParseError) as caught:
                self.parse(data, 'placeholder', hidden_text='exclude')
        self.assertEqual(caught.exception.status, 'resource_limit')

    def test_filter_restores_before_following_object(self):
        data = b'{\\rtf1 A{\\v{\\pict 01}}B{\\pict 02}C}'
        result = self.parse(data, 'placeholder', hidden_text='exclude')
        self.assertEqual(result['text'], 'AB\ufffcC')
        self.assertEqual([obj['placeholder_emitted'] for obj in result['objects']],
                         [False, True])

    def test_reject_exception_is_only_unrequested_related_scope(self):
        data = b'{\\rtf1 A{\\header{\\v\\deleted{\\pict 01}}}B}'
        result = self.parse(data, 'reject', hidden_text='exclude', revisions='accepted')
        self.assertEqual(result['text'], 'AB')
        self.assertEqual(result['object_count'], 0)
        self.assertEqual(result['objects'], [])
        self.assertEqual(result['status'], 'partial')



    def test_optional_object_destination_uses_same_bounded_path(self):
        data = b'{\\rtf1 A{\\*\\pict 01}B}'
        result = self.parse(data)
        self.assertEqual(result['text'], 'A\ufffcB')
        self.assertEqual(result['object_count'], 1)
        with self.assertRaises(ParseError) as caught:
            self.parse(data, 'reject')
        self.assertEqual(caught.exception.status, 'unsupported')

    def test_invalid_binary_lengths_stop_without_interpreting_trailing_groups(self):
        for operand in (b'', b'-1', b'10000000000', b'999999999999999999'):
            with self.subTest(operand=operand):
                data = b'{\\rtf1 A{\\pict\\bin' + operand + b' }{\\pict Forged}B}'
                result = self.parse(data)
                self.assertEqual(result['text'], 'A\ufffc')
                self.assertEqual(result['object_count'], 1)
                self.assertFalse(result['objects'][0]['complete'])
                self.assertIsNone(result['objects'][0]['source_byte_end'])
                self.assertIn('rtf_truncated_binary', self.codes(result))
                self.assertEqual(result['status'], 'partial')

    def test_true_nested_groups_keep_depth_budget_inside_object(self):
        # A later invalid binary operand must not hide an earlier depth breach.
        data = b'{\\rtf1 A{\\object{{\\bin-1 X}}}B}'
        with patch('wps_rtf_read.MAX_GROUP_DEPTH', 2):
            with self.assertRaises(ParseError) as caught:
                self.parse(data)
        self.assertEqual(caught.exception.status, 'resource_limit')

    def test_binary_delimiters_do_not_falsely_consume_depth_budget(self):
        payload = b'{' * 20 + b'}' * 20
        data = (b'{\\rtf1 A{\\pict\\bin' + str(len(payload)).encode()
                + b' ' + payload + b'}B}')
        with patch('wps_rtf_read.MAX_GROUP_DEPTH', 2):
            result = self.parse(data)
        self.assertEqual(result['text'], 'A\ufffcB')
        self.assertTrue(result['objects'][0]['complete'])

    def test_default_opaque_object_cannot_pass_either_publication_gate(self):
        from wps_rtf_read import require_publishable_candidate
        from wps_output_contract import inspect_output_bytes
        data = b'{\\rtf1 A{\\pict 01}B}'
        result = self.parse(data)
        self.assertEqual(result['object_policy'], 'placeholder')
        with self.assertRaises(RuntimeError):
            require_publishable_candidate(result)
        self.assertFalse(inspect_output_bytes(data, 'candidate.rtf')['valid'])



if __name__ == '__main__':
    unittest.main()
