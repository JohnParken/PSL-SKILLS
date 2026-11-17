"""Synthetic CLI cache ownership and stdout handoff failure regressions."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document
import tl_docx
from tl_package import Package
from tl_read_cache import cached_report, release


class OutputSink:
    """Capture bytes or inject a failure at the stdout handoff boundary."""

    def __init__(self, mode=None, callback=None):
        self.buffer = self
        self.mode = mode
        self.callback = callback
        self.data = bytearray()
        self.flushes = 0

    def write(self, data):
        """Model normal, partial and failed binary stream writes."""
        if self.callback:
            self.callback()
        if self.mode == 'write':
            raise BrokenPipeError('injected write failure')
        if self.mode == 'interrupt':
            raise KeyboardInterrupt
        if self.mode == 'short':
            self.data.extend(data[:10])
            return 10
        self.data.extend(data)
        return len(data)

    def flush(self):
        """Fail only once bytes have reached the stream buffer."""
        self.flushes += 1
        if self.mode == 'flush':
            raise OSError('injected flush failure')


class CacheDeliveryTests(unittest.TestCase):
    """Failed delivery cleans only a newly created, still-verifiable cache."""

    def setUp(self):
        self.previous = Path.cwd()
        self.temp = tempfile.TemporaryDirectory(prefix='tl-cache-delivery-')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(os.chdir, self.previous)
        os.chdir(self.temp.name)
        self.folder = Path(self.temp.name)
        self.root = os.open('.', os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.root)
        document = Document()
        for index in range(80):
            document.add_paragraph(f'Row {index} ' + 'x' * 80)
        raw = io.BytesIO()
        document.save(raw)
        self.package = Package(raw.getvalue())
        self.source = self.folder / 'synthetic.docx'
        self.source.write_bytes(self.package.data)
        self.argv = ['read', 'synthetic.docx', '--cache', '--max-output-bytes', '2500']

    def cache_files(self):
        """Inspect only this test's isolated synthetic task directory."""
        return set(self.folder.glob('.tl-docx-read-*.cache'))

    def invoke(self, sink, argv=None):
        """Run the real parser, executor and main, capturing both channels."""
        errors = io.StringIO()
        with patch.object(tl_docx.sys, 'stdout', sink), patch.object(tl_docx.sys, 'stderr', errors):
            result = tl_docx.main(self.argv if argv is None else argv)
        return result, errors.getvalue()

    def first_page(self):
        """Obtain a genuine continuation token and cursor through stdout."""
        sink = OutputSink()
        result, errors = self.invoke(sink)
        self.assertEqual((result, errors, sink.flushes), (0, '', 1))
        page = json.loads(sink.data)
        self.assertTrue(page['has_more'])
        self.assertEqual(page['cache']['status'], 'stored')
        self.assertLessEqual(len(sink.data), 2500)
        return page

    def test_new_cache_write_flush_and_short_write_failures(self):
        """Each output failure is nonzero and leaves no new cache behind."""
        for mode in ('write', 'flush', 'short'):
            with self.subTest(mode=mode):
                sink = OutputSink(mode)
                result, errors = self.invoke(sink)
                self.assertEqual(result, 1)
                self.assertIn('output delivery failed', errors)
                self.assertEqual(self.cache_files(), set())
                self.assertNotIn(b'"status":"error"', sink.data)
                self.assertEqual(self.source.read_bytes(), self.package.data)

    def test_successful_page_keeps_cache_and_terminal_page_releases(self):
        """Public continuation and terminal release receipts remain unchanged."""
        page = self.first_page()
        self.assertEqual(len(self.cache_files()), 1)
        sink = OutputSink()
        result, errors = self.invoke(sink, [
            'read', 'synthetic.docx', '--cache-token', page['cache']['token'],
            '--cursor', page['next_cursor'], '--max-output-bytes', '1000000',
        ])
        terminal = json.loads(sink.data)
        self.assertEqual((result, errors), (0, ''))
        self.assertFalse(terminal['has_more'])
        self.assertEqual(terminal['cache'], {'status': 'released', 'archive_status': 'not_required'})
        self.assertEqual(self.cache_files(), set())

    def test_failed_reused_continuation_is_not_adopted(self):
        """An existing shared continuation survives write and flush failures."""
        page = self.first_page()
        before = {path: path.read_bytes() for path in self.cache_files()}
        argv = ['read', 'synthetic.docx', '--cache-token', page['cache']['token'],
                '--cursor', page['next_cursor'], '--max-output-bytes', '2500']
        for mode in ('write', 'flush'):
            result, errors = self.invoke(OutputSink(mode), argv)
            self.assertEqual(result, 1)
            self.assertIn('output delivery failed', errors)
            self.assertEqual({path: path.read_bytes() for path in self.cache_files()}, before)
        sink = OutputSink()
        self.assertEqual(self.invoke(sink, argv), (0, ''))
        self.assertEqual(json.loads(sink.data)['cache']['status'], 'reused')

    def test_missing_continuation_rebuild_is_owned(self):
        """A new replacement cache is removed if its new token was not handed off."""
        page = self.first_page()
        release(self.root, page['cache']['token'])
        result, _ = self.invoke(OutputSink('flush'), [
            'read', 'synthetic.docx', '--cache-token', page['cache']['token'],
            '--cursor', page['next_cursor'], '--max-output-bytes', '2500',
        ])
        self.assertEqual(result, 1)
        self.assertEqual(self.cache_files(), set())

    def test_failed_new_cache_does_not_touch_sibling_or_unknown_files(self):
        """Parallel invocation cache and unknown files are not enumerated for deletion."""
        _, sibling = cached_report(self.root, self.package)
        unknown = self.folder / '.tl-docx-read-unknown.cache'
        unknown.write_bytes(b'not ours')
        before = {path: path.read_bytes() for path in self.cache_files()}
        self.assertEqual(self.invoke(OutputSink('flush'))[0], 1)
        self.assertEqual({path: path.read_bytes() for path in self.cache_files()}, before)
        self.assertEqual(release(self.root, sibling['token'])['status'], 'released')

    def test_changed_cache_is_retained_and_cleanup_failure_reported(self):
        """Never delete an unverified replacement even after an output failure."""
        def corrupt():
            for path in self.cache_files():
                path.write_bytes(b'changed by another actor')
        result, errors = self.invoke(OutputSink('write', corrupt))
        self.assertEqual(result, 1)
        self.assertIn('cache cleanup incomplete', errors)
        self.assertEqual([path.read_bytes() for path in self.cache_files()], [b'changed by another actor'])

    def test_symlink_replacement_is_retained_and_target_protected(self):
        """Failure cleanup does not follow a changed cache path."""
        def replace():
            for path in self.cache_files():
                path.unlink()
                path.symlink_to(self.source)
        result, errors = self.invoke(OutputSink('flush', replace))
        self.assertEqual(result, 1)
        self.assertIn('cache cleanup incomplete', errors)
        self.assertTrue(next(iter(self.cache_files())).is_symlink())
        self.assertEqual(self.source.read_bytes(), self.package.data)

    def test_root_remains_pinned_during_output_failure(self):
        """Cleanup does not reopen an unrelated current working directory."""
        other = self.folder / 'other'
        other.mkdir()
        result, errors = self.invoke(OutputSink('write', lambda: os.chdir(other)))
        self.assertEqual(result, 1)
        self.assertNotIn('cleanup incomplete', errors)
        self.assertEqual(self.cache_files(), set())
        self.assertEqual(list(other.iterdir()), [])

    def test_interrupt_still_cleans_owned_cache(self):
        """Interrupted output propagates while running ownership-safe cleanup."""
        with self.assertRaises(KeyboardInterrupt):
            self.invoke(OutputSink('interrupt'))
        self.assertEqual(self.cache_files(), set())

    def test_pagination_error_remains_single_json_and_cleans(self):
        """Failure before output retains the existing error JSON/exit contract."""
        sink = OutputSink()
        result, errors = self.invoke(sink, self.argv[:-1] + ['512'])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(sink.data)['status'], 'error')
        self.assertIn('Byte budget', errors)
        self.assertEqual(self.cache_files(), set())

    def test_concurrent_success_and_broken_pipe_are_isolated(self):
        """Two real processes own different caches despite sharing the task root."""
        command = [sys.executable, '-I', '-B', str(Path(tl_docx.__file__)), *self.argv]
        reader, writer = os.pipe()
        os.close(reader)
        try:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as successful:
                with subprocess.Popen(command, stdout=writer, stderr=subprocess.PIPE) as failing:
                    output, success_errors = successful.communicate(timeout=30)
                    _, failure_errors = failing.communicate(timeout=30)
                    self.assertEqual(successful.returncode, 0, success_errors)
                    self.assertNotEqual(failing.returncode, 0)
        finally:
            os.close(writer)
        self.assertIn(b'output delivery failed', failure_errors)
        token = json.loads(output)['cache']['token']
        expected = self.folder / ('.tl-docx-read-' + token.split('.')[1] + '.cache')
        self.assertEqual(self.cache_files(), {expected})
        self.assertEqual(release(self.root, token)['status'], 'released')
        self.assertEqual(self.source.read_bytes(), self.package.data)

    def test_actual_broken_pipe_does_not_leave_cache(self):
        """Exercise a real process and a pipe with no reader, not just a fake stream."""
        reader, writer = os.pipe()
        os.close(reader)
        try:
            completed = subprocess.run(
                [sys.executable, '-I', '-B', str(Path(tl_docx.__file__)), *self.argv],
                stdout=writer, stderr=subprocess.PIPE, check=False,
            )
        finally:
            os.close(writer)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b'output delivery failed', completed.stderr)
        self.assertEqual(self.cache_files(), set())


if __name__ == '__main__':
    unittest.main()
