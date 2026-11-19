"""Synthetic stable-error and complete-continuation public contract tests."""

import errno
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_docx
from tl_contract import failure, read_continuation
from tl_package import Package


def replay(request):
    """Independent consumer: argument vector only, never shell interpolation."""
    argv = [request['action']]
    for name, value in request['parameters'].items():
        if name == 'include_related':
            argv.append('--include-related' if value else '--body-only')
        elif value is True:
            argv.append('--' + name.replace('_', '-'))
        elif value is not None and value is not False:
            for item in value if isinstance(value, list) else [value]:
                argv.append('--' + name.replace('_', '-') + '=' + str(item))
    return argv + ['--', request['source']]


def package_data(long=False):
    """Create nonprivate DOCX input from an independently known text sequence."""
    document = Document()
    document.add_heading("--Section 'quoted' ; $(not-a-command) 中文", level=1)
    for index in range(60):
        document.add_paragraph(f'Paragraph {index}: ' + '测试 α " \u005c' * (1500 if long and index == 0 else 12))
    raw = io.BytesIO()
    document.save(raw)
    return raw.getvalue()


class ContinuationTests(unittest.TestCase):
    """Exercise the real isolated CLI and reconstruct each next request."""

    def setUp(self):
        task = tempfile.TemporaryDirectory(prefix='tl-contract-')
        self.addCleanup(task.cleanup)
        self.task = Path(task.name)
        self.source = "-report 'quoted' ; $(not-a-command) 中文.docx"
        self.data = package_data()
        (self.task / self.source).write_bytes(self.data)

    def invoke(self, argv, status=0):
        """Capture actual process bytes and check that stdout is one JSON value."""
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(tl_docx.__file__)), *argv],
                                cwd=self.task, capture_output=True, check=False, timeout=30)
        self.assertEqual(status, result.returncode, result.stderr.decode())
        return json.loads(result.stdout), result

    def first(self, options=None, budget=3500):
        return self.invoke(['read', '--max-output-bytes=' + str(budget), *(options or []), '--', self.source])

    def test_recommended_budget_preserves_unbounded_default_and_replays(self):
        """The skill recipe is bounded without changing existing API defaults."""
        expected, process = self.invoke(['read', '--', self.source])
        self.assertGreater(len(process.stdout), 12000)
        self.assertFalse(expected['has_more'])
        page, process = self.first(budget=12000)
        self.assertTrue(page['has_more'])
        collected = []
        next_start = 0
        while True:
            self.assertEqual(page['start'], next_start)
            self.assertLessEqual(len(process.stdout), 12000)
            collected.extend(page['records'])
            next_start = page['end']
            if not page['has_more']:
                break
            self.assertEqual(page['continuation']['parameters']['max_output_bytes'], 12000)
            page, process = self.invoke(replay(page['continuation']))
        self.assertEqual(collected, expected['records'])
        self.assertEqual(next_start, expected['total'])

    def test_all_pages_replay_special_source_and_preserve_content(self):
        expected, _ = self.invoke(['read', '--', self.source])
        page, process = self.first()
        records = []
        while True:
            self.assertLessEqual(len(process.stdout), 3500)
            records.extend(page['records'])
            if not page['has_more']:
                self.assertNotIn('continuation', page)
                self.assertIsNone(page['next_cursor'])
                break
            request = page['continuation']
            self.assertEqual(request['source'], self.source)
            self.assertEqual(request['parameters']['cursor'], page['next_cursor'])
            self.assertNotIn(str(self.task), json.dumps(request))
            page, process = self.invoke(replay(request))
        self.assertEqual(records, expected['records'])
        self.assertEqual((self.task / self.source).read_bytes(), self.data)
        self.assertEqual([self.source], [item.name for item in self.task.iterdir()])

    def test_full_and_selected_section_roundtrip(self):
        for options in (['--full'], ['--body-only', '--revisions=all',
                         "--section=--Section 'quoted' ; $(not-a-command) 中文", '--section-occurrence=1']):
            with self.subTest(options=options):
                expected, _ = self.invoke(['read', *options, '--', self.source])
                page, _ = self.first(options, budget=6500)
                records = list(page['records'])
                self.assertTrue(page['has_more'])
                while page['has_more']:
                    page, process = self.invoke(replay(page['continuation']))
                    self.assertLessEqual(len(process.stdout), 6500)
                    records.extend(page['records'])
                self.assertEqual(expected['records'], records)

    def test_cache_missing_rebuild_current_token_and_terminal_release(self):
        page, _ = self.first(['--cache'])
        first_token = page['cache']['token']
        self.assertEqual(page['continuation']['parameters']['cache_token'], first_token)
        # Remove only the explicitly registered test cache via the public API.
        self.invoke(['release-cache', first_token])
        page, _ = self.invoke(replay(page['continuation']))
        self.assertEqual(page['cache']['status'], 'rebuilt_missing')
        self.assertNotEqual(page['cache']['token'], first_token)
        self.assertEqual(page['continuation']['parameters']['cache_token'], page['cache']['token'])
        request = page['continuation']
        request['parameters']['max_output_bytes'] = 1000000
        terminal, _ = self.invoke(replay(request))
        self.assertFalse(terminal['has_more'])
        self.assertNotIn('continuation', terminal)
        self.assertEqual(terminal['cache']['status'], 'released')
        self.assertEqual([self.source], [item.name for item in self.task.iterdir()])

    def test_long_record_fragments_lossless_with_cache_rebuild(self):
        (self.task / self.source).write_bytes(package_data(long=True))
        expected, _ = self.invoke(['read', '--', self.source])
        page, process = self.first(['--fragment-records', '--cache'], budget=6500)
        self.assertTrue(page['has_more'])
        self.invoke(['release-cache', page['cache']['token']])
        pieces, pages = [], 0
        while True:
            self.assertLessEqual(len(process.stdout), 6500)
            pieces.extend(page['records'])
            if not page['has_more']:
                break
            request = page['continuation']
            # Rebuild missing cache, then switch to stateless parsing and back.
            if pages == 1 and 'cache_token' in request['parameters']:
                self.invoke(['release-cache', request['parameters'].pop('cache_token')])
            if pages == 2:
                request['parameters']['cache'] = True
            page, process = self.invoke(replay(request))
            pages += 1
        self.assertGreater(pages, 3)
        rebuilt, pending = [], ''
        for record in pieces:
            if record['type'] != 'record_fragment':
                self.assertEqual(pending, '')
                rebuilt.append(record)
                continue
            self.assertEqual(len(pending), record['character_start'])
            pending += record['json_fragment']
            if record['final_fragment']:
                self.assertEqual(sha256(pending.encode()).hexdigest(), record['source_sha256'])
                rebuilt.append(json.loads(pending))
                pending = ''
        self.assertEqual(pending, '')
        self.assertEqual(rebuilt, expected['records'])
        self.assertEqual([self.source], [item.name for item in self.task.iterdir()])

    def test_source_and_option_mismatch_remain_rejected(self):
        page, _ = self.first()
        request = page['continuation']
        request['parameters']['details'] = True
        result, _ = self.invoke(replay(request), status=1)
        self.assertEqual(result['error_code'], 'cursor_mismatch')
        self.assertFalse(result['retryable'])
        request['parameters'].pop('details')
        document = Document()
        document.add_paragraph('Different source')
        document.save(self.task / self.source)
        result, _ = self.invoke(replay(request), status=1)
        self.assertEqual(result['error_code'], 'cursor_mismatch')

    def test_tiny_budget_returns_bounded_actionable_error(self):
        result, process = self.invoke(['read', '--max-output-bytes=512', '--cache', '--', self.source], status=1)
        self.assertLessEqual(len(process.stdout), 512)
        self.assertEqual(result['error_code'], 'output_budget_exceeded')
        self.assertTrue(result['retryable'])
        self.assertEqual(result['next_action']['action'], 'increase_budget_or_fragment')
        self.assertEqual([self.source], [item.name for item in self.task.iterdir()])

    def test_huge_parameter_metadata_fails_without_truncated_json(self):
        heading = '界' * 4000
        document = Document()
        document.add_heading(heading, level=1)
        for _ in range(10):
            document.add_paragraph('Body')
        document.save(self.task / self.source)
        result, process = self.invoke(['read', '--section=' + heading, '--max-output-bytes=512', '--', self.source], status=1)
        self.assertLessEqual(len(process.stdout), 512)
        self.assertEqual(result['error_code'], 'output_budget_exceeded')
        self.assertNotIn('continuation', result)

    def test_usage_errors_are_safe_json_exit_two(self):
        for args in ([], ['read', '--unknown=/private/not-public'], ['read', '--max-output-bytes=no', '--', self.source]):
            with self.subTest(args=args):
                result, process = self.invoke(args, status=2)
                self.assertEqual(result['error_code'], 'invalid_arguments')
                self.assertNotIn(b'/private/not-public', process.stdout + process.stderr)
                self.assertFalse(result['retryable'])

    def test_missing_file_does_not_expose_path(self):
        result, process = self.invoke(['read', 'missing/input.docx'], status=1)
        self.assertEqual(result['error_code'], 'input_unavailable')
        self.assertNotIn(b'missing/input', process.stdout + process.stderr)


class ContractUnitTests(unittest.TestCase):
    """Check all selectors and frame boundaries independent of file parsing."""

    def test_options_replay_without_shell_or_loss(self):
        variants = [
            ['--details', '--body-only', '--revisions=all', '--paragraph-range=1:3'],
            ['--report', '--table-header=1:2', '--include-related'],
            ['--compact-json', '--tables=rows', '--table-header=1:2'],
            ['--format=structure', '--xml-part=word/document.xml', '--xml-part=word/header1.xml'],
            ['--summary'], ['--headings'], ['--full'],
            ['--fragment-records', '--max-output-bytes=6500'],
        ]
        for options in variants:
            args = tl_docx.arguments(['read', *options, '--', "-a ' ; $.docx"])
            request = read_continuation(args, args.max_output_bytes or 12000)
            request['parameters']['cursor'] = 'cursor-value'
            restored = tl_docx.arguments(replay(request))
            for key, value in vars(args).items():
                if key not in ('cursor', 'max_output_bytes'):
                    self.assertEqual(value, getattr(restored, key), (key, options))

    def test_every_page_counts_continuation_and_terminal_omits_it(self):
        request = {'action': 'read', 'source': 'a.docx', 'parameters': {'max_output_bytes': 512}}
        records = [{'text': str(i) * 60} for i in range(20)]
        result = tl_docx.page({'status': 'ok'}, records, 512, None, 'bind', request)
        self.assertLessEqual(len(tl_docx.encode(result)), 512)
        self.assertGreater(result['end'], result['start'])
        terminal = tl_docx.page({'status': 'ok'}, records, 100000, result['next_cursor'], 'bind', request)
        self.assertNotIn('continuation', terminal)
        self.assertEqual(result['records'] + terminal['records'], records)
        exact = len(tl_docx.encode(terminal))
        self.assertEqual(terminal, tl_docx.page({'status': 'ok'}, records, exact, result['next_cursor'], 'bind', request))

    def test_partial_is_usable_not_a_failure(self):
        package = Package(package_data())
        args = tl_docx.arguments(['read', 'synthetic.docx', '--full'])
        report = ([{'type': 'paragraph', 'text': 'Known content'}], [{'code': 'unsupported_shape'}], {})
        with patch('tl_read.report', return_value=report):
            result = tl_docx.read_result(package, args)
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('error_code', result)
        self.assertNotIn('retryable', result)
        self.assertEqual(result['records'][0]['text'], 'Known content')

    def test_error_classification_uses_types_not_message_words(self):
        value = failure(ValueError('permission denied cache missing cursor error'), 'read')
        self.assertEqual(value['error_code'], 'invalid_input')
        self.assertFalse(value['retryable'])
        transient = OSError(errno.EAGAIN, 'private details', '/private/path')
        self.assertTrue(failure(transient, 'read')['retryable'])
        self.assertFalse(failure(transient, 'create')['retryable'])
        self.assertNotIn('/private/path', json.dumps(failure(transient, 'read')))
        self.assertEqual(failure(RuntimeError('unknown'), 'read')['error_code'], 'operation_failed')

    def test_oversized_diagnostics_remain_valid_and_explicit(self):
        result = failure(ValueError('界\n\x00' * 10000), 'read')
        self.assertLessEqual(len(tl_docx.encode(result)), 512)
        self.assertTrue(result['error_truncated'])
        self.assertEqual(result['error_code'], 'invalid_input')

    def test_old_fragment_cursor_explicitly_rejected(self):
        package = Package(package_data(long=True))
        args = tl_docx.arguments(['read', 'a.docx', '--fragment-records', '--max-output-bytes=6500'])
        bindings = []
        encoder = tl_docx.encode

        def capture(value):
            if isinstance(value, list) and len(value) > 1 and value[1] == 'read-view-v7':
                bindings.append(value)
            return encoder(value)

        with patch.object(tl_docx, 'encode', capture):
            current = tl_docx.read_result(package, args)
        self.assertTrue(current['has_more'])
        old_fields = bindings[-1][:-2]
        self.assertEqual(old_fields[-2:], ['json-record-v1', 6500])
        old_binding = sha256(encoder(old_fields)).hexdigest()
        args.cursor = tl_docx.base64.urlsafe_b64encode(encoder([old_binding, 1])).decode('ascii')
        with self.assertRaises(ValueError) as rejected:
            tl_docx.read_result(package, args)
        self.assertEqual(failure(rejected.exception, 'read')['error_code'], 'cursor_mismatch')

    def test_unpaired_surrogate_diagnostics_are_safe_utf8(self):
        result = failure(ValueError('Invalid \ud800 request'), 'read')
        self.assertTrue(result['error_encoding_replaced'])
        self.assertEqual(json.loads(tl_docx.encode(result))['error_code'], 'invalid_input')

    def test_usage_and_success_share_flush_boundary(self):
        from test_tl_cache_delivery import OutputSink  # pylint: disable=import-outside-toplevel
        for mode in (None, 'write', 'short', 'flush'):
            sink, errors = OutputSink(mode), io.StringIO()
            with patch.object(tl_docx.sys, 'stdout', sink), patch.object(tl_docx.sys, 'stderr', errors):
                self.assertEqual(tl_docx.main([]), 2)
            if mode is None:
                self.assertEqual(sink.flushes, 1)
                self.assertEqual(json.loads(sink.data)['error_code'], 'invalid_arguments')
            else:
                self.assertIn('output_delivery_failed', errors.getvalue())

    def test_failed_receipt_preserves_existing_facts(self):
        from test_tl_cache_delivery import OutputSink  # pylint: disable=import-outside-toplevel
        original = {'status': 'error', 'error': 'Specific verification failure', 'publication': {'published': False}}
        sink = OutputSink()
        with patch.object(tl_docx, 'execute', return_value=(original, 1)), patch.object(tl_docx.sys, 'stdout', sink):
            self.assertEqual(tl_docx.main(['read', 'a.docx']), 1)
        result = json.loads(sink.data)
        self.assertEqual(result['error'], original['error'])
        self.assertEqual(result['publication'], original['publication'])
        self.assertFalse(result['retryable'])

    def test_bad_and_out_of_range_cursors_are_distinct(self):
        with self.assertRaises(ValueError) as bad:
            tl_docx.page({}, [], 512, 'x' * 513, 'binding')
        self.assertEqual(failure(bad.exception, 'read')['error_code'], 'invalid_cursor')
        first = tl_docx.page({}, [{'a': 'x' * 80}] * 10, 512, None, 'binding')
        with self.assertRaises(ValueError) as outside:
            tl_docx.page({}, [], 512, first['next_cursor'], 'binding')
        self.assertEqual(failure(outside.exception, 'read')['error_code'], 'cursor_out_of_range')


if __name__ == '__main__':
    unittest.main()
