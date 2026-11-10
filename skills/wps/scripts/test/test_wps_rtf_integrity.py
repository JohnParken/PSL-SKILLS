"""Independent bytes for RTF budgets, encoded streams, and bounded diagnostics."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from read_wps import read_bytes
from wps_access import ParseError
import wps_rtf_read as reader


class RtfIntegrityTests(unittest.TestCase):
    """Keep omissions explicit and structural/encoding ambiguity fail-closed."""

    @staticmethod
    def codes(result):
        """Return diagnostic codes without assuming their list order."""
        return {item['code'] for item in result['issues']}

    def test_actual_paragraph_cap_and_final_paragraph_are_disclosed(self):
        data = b'{\\rtf1 ' + b'kept\\par ' * reader.MAX_PARAGRAPHS + b'overflow\\par final}'
        result = reader.parse_rtf_bytes(data)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['paragraphs']), reader.MAX_PARAGRAPHS)
        self.assertEqual(result['omitted_paragraph_count'], 2)
        self.assertEqual(len(result['blocks']), reader.MAX_PARAGRAPHS)
        notice = next(item for item in result['issues'] if item['code'] == 'rtf_resource_limit_exceeded')
        self.assertEqual((notice['resource'], notice['retained'], notice['omitted']), ('paragraphs', reader.MAX_PARAGRAPHS, 2))
        self.assertEqual(result['reading_assessment']['status'], 'partial')

    def test_exact_paragraph_cap_remains_complete(self):
        with patch.object(reader, 'MAX_PARAGRAPHS', 2):
            result = reader.parse_rtf_bytes(b'{\\rtf1 one\\par two}')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['omitted_paragraph_count'], 0)
        self.assertEqual(result['text'], 'one\ntwo')

    def test_actual_table_cap_retains_prefix_and_discloses_omissions(self):
        row = b'\\trowd\\cellx1000\\intbl cell\\cell\\row\\par '
        result = reader.parse_rtf_bytes(b'{\\rtf1 ' + row * (reader.MAX_TABLES + 1) + b'}')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['tables']), reader.MAX_TABLES)
        self.assertEqual(result['omitted_table_count'], 1)
        self.assertEqual(len(result['blocks']), reader.MAX_TABLES)
        self.assertEqual(result['tables'][-1]['rows'][0][0]['text'], 'cell')
        self.assertEqual(result['text'], '')

    def test_both_omission_notices_survive_diagnostic_saturation(self):
        row = b'\\trowd\\cellx1000\\intbl cell\\cell\\row\\par '
        data = b'{\\rtf1 ' + b'{\\header skipped}' * 105 + b'one\\par two\\par ' + row * 2 + b'}'
        with patch.object(reader, 'MAX_PARAGRAPHS', 1), patch.object(reader, 'MAX_TABLES', 1):
            result = reader.parse_rtf_bytes(data)
        notices = [item for item in result['issues'] if item['code'] == 'rtf_resource_limit_exceeded']
        self.assertEqual({item['resource'] for item in notices}, {'paragraphs', 'tables'})
        self.assertEqual(len(result['issues']), 100)
        self.assertGreater(result['geometry_issues_truncated'], 0)
        with self.assertRaises(RuntimeError):
            reader.require_publishable_candidate(result)

    def test_direct_cp936_paragraph_and_table_bytes(self):
        data = b'{\\rtf1\\ansi\\ansicpg936 ' + '中文'.encode('cp936') + b'\\par\\trowd\\cellx1000\\intbl ' + '表格'.encode('cp936') + b'\\cell\\row}'
        result = reader.parse_rtf_bytes(data)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], '中文')
        self.assertEqual(result['tables'][0]['rows'][0][0]['text'], '表格')

    def test_literal_and_hex_bytes_form_single_characters(self):
        for body in (b"\xd6\\'d0\xce\\'c4", b"\\'d6\xd0\\'ce\xc4", b"\\'d6\\'d0\xce\xc4"):
            with self.subTest(body=body):
                result = reader.parse_rtf_bytes(b'{\\rtf1\\ansicpg936 ' + body + b'}')
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['text'], '中文')

    def test_completed_dbcs_pair_does_not_swallow_following_controls(self):
        for body in (b'\x81\x81', b"\\'81\x81", b"\x81\\'81"):
            with self.subTest(body=body):
                prefix = b'{\\rtf1\\ansicpg936 ' + body
                result = reader.parse_rtf_bytes(prefix + b'\\par next}')
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['text'], b'\x81\x81'.decode('cp936') + '\nnext')
                result = reader.parse_rtf_bytes(prefix + b'{nested}\\par}')
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['text'], b'\x81\x81'.decode('cp936') + 'nested')
                result = reader.parse_rtf_bytes(prefix + b'\\bin3 XYZafter}')
                self.assertNotIn('XYZ', result['text'])
                self.assertTrue(result['text'].endswith('after'))
                with self.assertRaises(ParseError) as caught:
                    read_bytes(prefix + b'\\field}', field_policy='reject')
                self.assertEqual(caught.exception.status, 'unsupported')

    def test_ambiguous_raw_dbcs_delimiters_fail_closed(self):
        # These bytes could be a raw DBCS pair or a truncated lead followed by
        # an RTF delimiter. Neither field nor binary controls may be guessed away.
        for suffix in (b'\\par next}', b'\\field}', b'\\bin3 XYZ}', b'{next}}', b'}', b'\\\\par next}', b'\\\\field}'):
            with self.subTest(suffix=suffix), self.assertRaises(ParseError) as caught:
                read_bytes(b'{\\rtf1\\ansicpg936 \x81' + suffix)
            self.assertEqual(caught.exception.status, 'unsupported')

    def test_explicit_hex_dbcs_delimiter_is_safe(self):
        result = reader.parse_rtf_bytes(b"{\\rtf1\\ansicpg936 \\'81\\'5c\\par next}")
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], b'\x81\x5c'.decode('cp936') + '\nnext')

    def test_nonascii_control_suffix_is_not_an_ascii_control_letter(self):
        result = reader.parse_rtf_bytes(b'{\\rtf1\\ansicpg936 \\b' + '中文'.encode('cp936') + b'}')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], '中文')

    def test_codepage_group_scope_is_restored(self):
        result = reader.parse_rtf_bytes(b'{\\rtf1\\ansicpg936 \xd6\xd0{\\ansicpg1252 \xe9}\xce\xc4}')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], '中é文')

    def test_missing_codepage_is_disclosed_only_for_nonascii_bytes(self):
        for body in (b'\xd6\xd0', b"\\'d6\\'d0"):
            result = reader.parse_rtf_bytes(b'{\\rtf1 ' + body + b'}')
            self.assertEqual(result['text'], '中')
            self.assertIn('rtf_encoding_assumed', self.codes(result))
            self.assertEqual(result['status'], 'partial')
        self.assertEqual(reader.parse_rtf_bytes(b'{\\rtf1 ASCII}')['status'], 'ok')

    def test_invalid_encoded_byte_is_not_silent(self):
        result = reader.parse_rtf_bytes(b'{\\rtf1\\ansicpg936 \xff}')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('rtf_encoding_replacement', self.codes(result))

    def test_unavailable_codepage_is_not_silent(self):
        result = reader.parse_rtf_bytes(b'{\\rtf1\\ansicpg99999 \xe9}')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('rtf_encoding_unavailable', self.codes(result))

    def test_font_codepage_conflicts_in_ignored_font_table_are_diagnosed(self):
        for declaration in (b'\\fcharset128', b'\\cpg932', b'\\fcharset250'):
            data = b'{\\rtf1\\ansicpg936{\\fonttbl{\\f0' + declaration + b' Font;}}\\f0 \xd6\xd0}'
            result = reader.parse_rtf_bytes(data)
            self.assertEqual(result['status'], 'partial')
            self.assertIn('rtf_font_encoding_unresolved', self.codes(result))
        result = reader.parse_rtf_bytes(b'{\\rtf1\\ansicpg936{\\fonttbl{\\f0\\fcharset134 Font;}}\xd6\xd0}')
        self.assertEqual(result['status'], 'ok')

    def test_unicode_only_font_declarations_do_not_guess_byte_encoding(self):
        result = reader.parse_rtf_bytes(b'{\\rtf1\\ansicpg936{\\fonttbl{\\f0\\fcharset128 Font;}}\\u20013?}')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['text'], '中')

    def test_hidden_revision_and_fields_survive_diagnostic_saturation(self):
        data = b'{\\rtf1 ' + b'{\\header skipped}' * 105 + b'{\\v hidden}{\\deleted deleted}{\\revised added}{\\field{\\fldrslt cached}}}'
        result = reader.parse_rtf_bytes(data)
        self.assertEqual(result['text'], 'hiddendeletedaddedcached')
        self.assertEqual(result['hidden_control_count'], 1)
        self.assertEqual(result['revision_control_count'], 2)
        self.assertEqual(result['field_count'], 1)
        self.assertEqual(len(result['issues']), 100)
        for code in ('rtf_hidden_text_unfiltered', 'rtf_revisions_unfiltered', 'rtf_field_cached_result_unverified'):
            self.assertIn(code, self.codes(result))
        with self.assertRaises(ParseError):
            read_bytes(data, field_policy='reject')

    def test_field_count_is_independent_of_diagnostic_cap(self):
        result = reader.parse_rtf_bytes(b'{\\rtf1 ' + b'{\\field{\\fldrslt x}}' * 105 + b'}')
        self.assertEqual(result['field_count'], 105)
        self.assertEqual(result['text'], 'x' * 105)
        reader.require_publishable_candidate(result)

    def test_group_depth_budget_rejects_before_excess_allocation(self):
        with patch.object(reader, 'MAX_GROUP_DEPTH', 3):
            self.assertEqual(reader.parse_rtf_bytes(b'{\\rtf1{{x}}}')['status'], 'ok')
            for data in (b'{\\rtf1{{{x}}}}', b'{\\rtf1{\\pict{{x}}}}'):
                with self.subTest(data=data), self.assertRaises(ParseError) as caught:
                    reader.parse_rtf_bytes(data)
                self.assertEqual(caught.exception.status, 'resource_limit')

    def test_cell_and_pending_cell_budgets_fail_closed(self):
        with patch.object(reader, 'MAX_TABLE_CELLS', 1):
            self.assertEqual(reader.parse_rtf_bytes(b'{\\rtf1\\intbl A\\cell\\row}')['status'], 'ok')
            for suffix in (b'B\\cell\\row}', b'B}'):
                with self.subTest(suffix=suffix), self.assertRaises(ParseError) as caught:
                    reader.parse_rtf_bytes(b'{\\rtf1\\intbl A\\cell ' + suffix)
                self.assertEqual(caught.exception.status, 'resource_limit')

    def test_row_budget_is_aggregate(self):
        data = b'{\\rtf1\\intbl A\\cell\\row\\par\\intbl B\\cell\\row}'
        with patch.object(reader, 'MAX_TABLE_ROWS', 1), self.assertRaises(ParseError) as caught:
            reader.parse_rtf_bytes(data)
        self.assertEqual(caught.exception.status, 'resource_limit')

    def test_cell_definition_budget_includes_unused_definitions(self):
        with patch.object(reader, 'MAX_CELL_DEFINITIONS', 1), self.assertRaises(ParseError) as caught:
            reader.parse_rtf_bytes(b'{\\rtf1\\trowd\\cellx1000\\cellx2000}')
        self.assertEqual(caught.exception.status, 'resource_limit')


if __name__ == '__main__':
    unittest.main()
