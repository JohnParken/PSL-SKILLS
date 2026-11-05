"""Independent RTF bytes exercising partial recovery, not builder round trips."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from test_task_cli_support import run_task_cli
import unittest
import tempfile
from unittest.mock import patch

from read_wps import read_bytes
from wps_rtf_read import require_publishable_candidate


class RecoveryTests(unittest.TestCase):
    def codes(self, result):
        return {item['code'] for item in result['issues']}

    def test_unterminated_cell_and_row_retain_text(self):
        for ending in (b'Lost', b'Lost\\cell', b'Lost\\cell}'):
            result = read_bytes(b'{\\rtf1\\ansi\\trowd\\cellx1000\\intbl ' + ending)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['tables'][0]['rows'][0][0]['text'], 'Lost')
            self.assertIn('rtf_unterminated_table_row', self.codes(result))

    def test_missing_group_and_trailing_escape_keep_prefix(self):
        for suffix in (b'', b'\\'):
            result = read_bytes(b'{\\rtf1\\ansi Reliable' + suffix)
            self.assertEqual(result['text'], 'Reliable')
            self.assertEqual(result['status'], 'partial')
            self.assertIn('rtf_unclosed_groups', self.codes(result))

    def test_unicode_truncations_do_not_raise_or_invent_numeric_character(self):
        for suffix in (b'\\u', b'\\u-', b'\\u123', b'\\u-10179?', b"\\'a"):
            result = read_bytes(b'{\\rtf1\\ansi Prefix' + suffix)
            self.assertTrue(result['text'].startswith('Prefix'))
            self.assertEqual(result['status'], 'partial')
        result = read_bytes(b'{\\rtf1\\ansi Prefix\\u123')
        self.assertEqual(result['text'], 'Prefix')
        result = read_bytes(b"{\\rtf1\\ansi Prefix\\'a")
        self.assertEqual(result['text'], 'Prefix')

    def test_unicode_pair_and_hex_fallback(self):
        result = read_bytes(b"{\\rtf1\\ansi\\uc1 \\u-10179\\'3f\\u-8704?}")
        self.assertEqual(result['text'], '\U0001f600')
        self.assertEqual(result['status'], 'ok')

    def test_related_destinations_not_silently_complete(self):
        for destination in ('header', 'footer', 'headerf', 'object', 'pict'):
            result = read_bytes(('{\\rtf1{\\' + destination + ' hidden}Body\\par}').encode())
            self.assertEqual(result['text'], 'Body')
            self.assertIn('rtf_destination_skipped', self.codes(result))
            self.assertEqual(result['status'], 'partial')

    def test_field_cache_preserved_without_instruction_execution(self):
        result = read_bytes(b'{\\rtf1 Before{\\field{\\*\\fldinst DATE}{\\fldrslt 2026}}After\\par}')
        self.assertEqual(result['text'], 'Before2026After')
        self.assertIn('rtf_field_cached_result_unverified', self.codes(result))

    def test_binary_braces_not_interpreted_as_text_or_groups(self):
        result = read_bytes(b'{\\rtf1 Before{\\pict\\bin3 }{} }After\\par}')
        self.assertEqual(result['text'], 'BeforeAfter')
        self.assertNotIn('rtf_unclosed_groups', self.codes(result))

    def test_all_prefix_cuts_preserve_available_text_without_exception(self):
        original = b'{\\rtf1\\ansi ABC\\par\\trowd\\cellx1000\\intbl XYZ\\cell\\row}'
        for cut in range(7, len(original)):
            result = read_bytes(original[:cut])
            self.assertIn(result['status'], ('ok', 'partial'))
            if cut >= 15:
                self.assertIn('ABC', result['text'])

    def test_candidate_recovery_never_replaces_old_output(self):
        from run_wps_task import create_document
        damaged = read_bytes(b'{\\rtf1 Broken')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out.wps'
            output.write_bytes(b'old')
            with patch('run_wps_task.read_bytes', return_value=damaged):
                with self.assertRaises(RuntimeError):
                    create_document({'blocks': [{'type': 'paragraph', 'text': 'new'}]}, output)
            self.assertEqual(output.read_bytes(), b'old')
            self.assertEqual(list(Path(directory).iterdir()), [output])
            from run_wps_edit import execute_edit_task
            source = Path(directory) / 'source.rtf'
            source.write_bytes(b'{\\rtf1 Original\\par}')
            original = source.read_bytes()
            with patch('run_wps_edit.read_bytes', side_effect=[read_bytes(original), damaged]):
                with self.assertRaises(RuntimeError):
                    execute_edit_task(source, output, [])
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(output.read_bytes(), b'old')

    def test_partial_cli_keeps_stdout_data_stderr_diagnostic_and_exit3(self):
        import subprocess
        import sys
        import json
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'cut.rtf'
            source.write_bytes(b'{\\rtf1 Reliable')
            script = (Path(__file__).parents[1] / 'read_wps.py')
            for output_format in ('json', 'text'):
                result = run_task_cli([sys.executable, '-I', '-S', '-B', str(script), str(source), '--format', output_format],
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 3)
                if output_format == 'json':
                    self.assertEqual(json.loads(result.stdout)['text'], 'Reliable')
                else:
                    self.assertEqual(result.stdout, 'Reliable')
                    self.assertIn('rtf_unclosed_groups', result.stderr)

    def test_known_content_omissions_can_publish_with_partial_receipt(self):
        candidate = read_bytes(b'{\\rtf1{\\header Head}Body\\par}')
        require_publishable_candidate(candidate)


if __name__ == '__main__':
    unittest.main()
