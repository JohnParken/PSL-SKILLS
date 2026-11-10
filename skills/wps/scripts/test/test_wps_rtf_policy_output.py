"""Synthetic RTF routing, policy and ordered CLI text regressions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from test_task_cli_support import run_task_cli
from read_wps import ParseError, read_bytes


class RtfPolicyOutputTests(unittest.TestCase):
    def test_cli_text_keeps_table_in_block_order(self):
        data = (b'{\\rtf1\\ansi Before\\par '
                b'\\trowd\\cellx1000\\cellx2000\\intbl A\\cell\\intbl B\\cell\\row '
                b'After\\par}')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ordered.rtf'
            path.write_bytes(data)
            process = run_task_cli([sys.executable, '-I', '-S', '-B',
                                    str(Path(__file__).resolve().parents[1] / 'read_wps.py'),
                                    str(path), '--format', 'text'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout, 'Before\nA\tB\nAfter')
        self.assertEqual(process.stderr, '')
        self.assertEqual(read_bytes(data)['text'], 'Before\nAfter')

    def test_explicit_unsupported_policies_fail_closed(self):
        data = b'{\\rtf1 text\\par}'
        for options in ({'include_related': True}, {'binary_paragraph_sources': True},
                        {'text_policy': 'strict'},
                        {'chart_policy': 'opaque'}, {'compatibility': 'strict'}):
            with self.subTest(options=options), self.assertRaises(ParseError) as caught:
                read_bytes(data, **options)
            self.assertEqual(caught.exception.status, 'unsupported')

    def test_explicit_recover_matches_default(self):
        data = b'{\\rtf1 recoverable\\par'
        default = read_bytes(data)
        selected = read_bytes(data, text_policy='recover')
        self.assertEqual((selected['status'], selected['text']), ('partial', 'recoverable'))
        self.assertEqual(selected['issues'], default['issues'])

    def test_object_policy_via_public_reader(self):
        data = b'{\\rtf1 A{\\pict 0102}B}'
        result = read_bytes(data)
        self.assertEqual((result['status'], result['text']), ('partial', 'A\ufffcB'))
        self.assertTrue(result['objects'][0]['placeholder_emitted'])
        with self.assertRaises(ParseError) as caught:
            read_bytes(data, object_policy='reject')
        self.assertEqual(caught.exception.status, 'unsupported')
        self.assertEqual(read_bytes(data, object_policy='placeholder')['text'], 'A\ufffcB')

    def test_explicit_field_policy(self):
        plain = b'{\\rtf1 ordinary\\par}'
        field = b'{\\rtf1 {\\field{\\*\\fldinst DATE}{\\fldrslt cached}}\\par}'
        self.assertEqual(read_bytes(plain, field_policy='reject')['status'], 'ok')
        with self.assertRaises(ParseError) as caught:
            read_bytes(field, field_policy='reject')
        self.assertEqual(caught.exception.status, 'unsupported')
        result = read_bytes(field, field_policy='cached_result')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('cached', result['text'])

    def test_cli_pure_table_and_rejected_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'table.rtf'
            path.write_bytes(b'{\\rtf1\\trowd\\cellx1000\\intbl A\\cell\\row}')
            command = [sys.executable, '-I', '-S', '-B',
                       str(Path(__file__).resolve().parents[1] / 'read_wps.py'), str(path)]
            process = run_task_cli(command + ['--format', 'text'], capture_output=True, text=True)
            self.assertEqual((process.returncode, process.stdout, process.stderr), (0, 'A', ''))
            for option in (['--hidden-text', 'exclude'], ['--revisions', 'accepted']):
                process = run_task_cli(command + option, capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads(process.stdout)['status'], 'ok')
            path.write_bytes(b'{\\rtf1 {\\field{\\*\\fldinst DATE}{\\fldrslt cached}}}')
            process = run_task_cli(command + ['--field-policy', 'reject'], capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(json.loads(process.stdout)['status'], 'unsupported')

    def test_cli_paragraph_limit_reports_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'many.rtf'
            path.write_bytes(b'{\\rtf1 ' + b'x\\par ' * 100000 + b'last}')
            process = run_task_cli([sys.executable, '-I', '-S', '-B',
                                    str(Path(__file__).resolve().parents[1] / 'read_wps.py'),
                                    str(path), '--format', 'text'], capture_output=True, text=True, timeout=60)
        self.assertEqual(process.returncode, 3)
        self.assertEqual(len(process.stdout.splitlines()), 100000)
        diagnostic = json.loads(process.stderr)
        issue = next(item for item in diagnostic['issues'] if item['code'] == 'rtf_resource_limit_exceeded')
        self.assertEqual((issue['resource'], issue['omitted']), ('paragraphs', 1))

    def test_text_partial_preserves_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partial.rtf'
            path.write_bytes(b'{\\rtf1 text\\par')
            process = run_task_cli([sys.executable, '-I', '-S', '-B',
                                    str(Path(__file__).resolve().parents[1] / 'read_wps.py'),
                                    str(path), '--format', 'text'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 3)
        self.assertEqual(process.stdout, 'text')
        self.assertEqual(json.loads(process.stderr)['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
