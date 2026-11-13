"""Check cache reuse, source validation and cleanup through the read entry."""

from contextlib import contextmanager, redirect_stderr
from hashlib import sha256
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

TEST_ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(TEST_ROOT), str(TEST_ROOT.parent)]

import docx_read_pages
import read_docx
from test_docx_report import W, heading, paragraph


@contextmanager
def _task_directory(directory):
    previous = Path.cwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)


class ReportPageCacheTests(unittest.TestCase):
    """Exercise reuse with the same working directory as separate CLI calls."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="docx-report-pages-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / "report.docx"
        self.write_source()

    def write_source(self, text="数据", *, body=None):
        if body is None:
            body = "".join(paragraph(f"{index} " + text * 100) for index in range(20))
        with ZipFile(self.source, "w") as package:
            package.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')

    def read(self, **kwargs):
        options = {"max_output_bytes": 2200, **kwargs}
        with _task_directory(self.folder):
            return read_docx.read_docx_report(self.source.name, **options)

    def cache(self):
        matches = list(self.folder.glob(".docx-report-*.cache"))
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_continue_skips_zip_xml_and_cleans_up_on_terminal_page(self):
        raw = self.source.read_bytes()
        expected = self.read(max_output_bytes=None)
        self.assertNotIn("page_cache", expected)
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))
        first = self.read()
        self.assertEqual(first["page_cache"], "stored")
        self.assertEqual(stat.S_IMODE(self.cache().stat().st_mode), 0o600)
        with patch.object(read_docx, "_read_snapshot", side_effect=AssertionError("must reuse parsed data")):
            last = self.read(cursor=first["next_cursor"], max_output_bytes=2000000)
        self.assertFalse(last["has_more"])
        self.assertEqual(last["page_cache"], "reused")
        self.assertEqual(first["blocks"] + last["blocks"], expected["blocks"])
        self.assertEqual(last["source_sha256"], sha256(raw).hexdigest())
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))

    def test_source_and_options_are_checked_before_using_cache(self):
        first = self.read()
        with patch.object(docx_read_pages, "_open_cache", side_effect=AssertionError("cache is not eligible")):
            with self.assertRaisesRegex(ValueError, "options changed"):
                self.read(cursor=first["next_cursor"], revisions="all")
            self.write_source("改动")
            with self.assertRaisesRegex(ValueError, "source bytes changed"):
                self.read(cursor=first["next_cursor"])
        with _task_directory(self.folder):
            self.assertEqual(docx_read_pages.release_cursor(first["next_cursor"]), {"status": "released"})
            self.assertEqual(docx_read_pages.release_cursor(first["next_cursor"]), {"status": "already_absent"})
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))

    def test_section_cursor_reuses_the_selected_view_and_binds_occurrence(self):
        content = [f"{index} " + "数据" * 100 for index in range(10)]
        self.write_source(body=heading("同名") + paragraph("别处") + heading("同名") + "".join(map(paragraph, content))
                          + heading("其他章节") + paragraph("节外文字"))
        scope = {"section": "同名", "section_occurrence": 2}
        expected = self.read(**scope, max_output_bytes=None)
        first = self.read(**scope)
        self.assertEqual(first["page_cache"], "stored")
        self.assertTrue(first["has_more"])
        cursor = first["next_cursor"]
        for options in ({}, {"section": "同名", "section_occurrence": 1}, {"section": "其他章节"}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "options changed"):
                self.read(**options, cursor=cursor)
        with patch.object(read_docx, "_read_snapshot", side_effect=AssertionError("must reuse the selected section")):
            last = self.read(section=" 同名 ", section_occurrence=2, cursor=cursor, max_output_bytes=2000000)
        self.assertEqual(last["scope"], expected["scope"])
        self.assertEqual(last["stats"], expected["stats"])
        self.assertEqual(last["page_cache"], "reused")
        self.assertFalse(last["has_more"])
        blocks = first["blocks"] + last["blocks"]
        self.assertEqual(blocks, expected["blocks"])
        self.assertEqual([block["text"] for block in blocks], ["同名", *content])
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))

    def test_missing_cache_recovers_without_skipping_blocks(self):
        first = self.read()
        self.cache().unlink()
        expected = self.read(max_output_bytes=None)
        last = self.read(cursor=first["next_cursor"], max_output_bytes=2000000)
        self.assertEqual(first["blocks"] + last["blocks"], expected["blocks"])
        self.assertNotIn("page_cache", last)

    def test_changed_or_replaced_cache_is_preserved_and_rejected(self):
        first = self.read()
        cache = self.cache()
        original = cache.read_bytes()
        cache.write_bytes(original[:-1] + b" ")
        with self.assertRaisesRegex(ValueError, "cache content changed"):
            self.read(cursor=first["next_cursor"])
        with _task_directory(self.folder), self.assertRaisesRegex(ValueError, "cache content changed"):
            docx_read_pages.release_cursor(first["next_cursor"])
        self.assertEqual(cache.read_bytes(), original[:-1] + b" ")
        saved = cache.with_suffix(".saved")
        cache.rename(saved)
        cache.touch(mode=0o600, exist_ok=False)
        cache.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "registered task or file"):
            self.read(cursor=first["next_cursor"])
        self.assertTrue(saved.exists())
        self.assertEqual(cache.read_bytes(), original)

    def test_valid_same_task_cursor_cannot_follow_cache_links(self):
        first = self.read()
        cache = self.cache()
        original = self.source.read_bytes()
        cache.unlink()
        cache.symlink_to(self.source.name)
        with self.assertRaises(OSError):
            self.read(cursor=first["next_cursor"])
        with _task_directory(self.folder), self.assertRaises(OSError):
            docx_read_pages.release_cursor(first["next_cursor"])
        self.assertTrue(cache.is_symlink())
        self.assertEqual(self.source.read_bytes(), original)

    def test_unwritable_cache_uses_existing_stateless_paging(self):
        with patch.object(docx_read_pages, "_open_task", side_effect=PermissionError("read-only task")):
            first = self.read()
        self.assertTrue(first["has_more"])
        self.assertNotIn("page_cache", first)
        self.assertTrue(first["next_cursor"].startswith("1."))
        self.assertLessEqual(len(read_docx.serialize_docx_page(first).encode("utf-8")), 2200)
        last = self.read(cursor=first["next_cursor"], max_output_bytes=2000000)
        self.assertEqual(first["blocks"] + last["blocks"], self.read(max_output_bytes=None)["blocks"])

    def test_smaller_budget_can_continue_with_a_stateless_cursor(self):
        first = self.read()
        cached_token = first["next_cursor"]
        _, source, options, *_ = cached_token.split(".")
        stateless_token = docx_read_pages.page_cursor(source, options, first["next_block"])
        with patch.object(docx_read_pages, "_cache_supported", return_value=False):
            expected = self.read(cursor=stateless_token, max_output_bytes=2200)
        self.assertEqual(len(expected["blocks"]), 1)
        exact_budget = len(read_docx.serialize_docx_page(expected).encode("utf-8"))
        with patch.object(read_docx, "_read_snapshot", side_effect=AssertionError("must reuse parsed data")):
            actual = self.read(cursor=cached_token, max_output_bytes=exact_budget)
        self.assertEqual(actual, expected)
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))

    def test_interleaved_pagination_keeps_each_cache_independent(self):
        first = self.read()
        second = self.read()
        self.assertNotEqual(first["next_cursor"], second["next_cursor"])
        with _task_directory(self.folder):
            self.assertEqual(docx_read_pages.release_cursor(first["next_cursor"]), {"status": "released"})
        self.assertEqual(len(list(self.folder.glob(".docx-report-*.cache"))), 1)
        last = self.read(cursor=second["next_cursor"], max_output_bytes=2000000)
        self.assertEqual(last["page_cache"], "reused")
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))

    def test_legacy_cursor_can_be_released_without_writes(self):
        token = docx_read_pages.page_cursor("a" * 64, "b" * 64, 1)
        with patch.object(docx_read_pages, "_open_task", side_effect=AssertionError("no cache access")):
            self.assertEqual(docx_read_pages.release_cursor(token), {"status": "not_required"})

    def test_partial_write_failure_removes_only_the_owned_cache(self):
        other = self.folder / ".docx-report-other.cache"
        other.write_bytes(b"leave untouched")
        with patch.object(docx_read_pages.os, "fsync", side_effect=OSError("write failed")):
            result = self.read()
        self.assertTrue(result["has_more"])
        self.assertNotIn("page_cache", result)
        self.assertEqual(list(self.folder.glob(".docx-report-*.cache")), [other])
        self.assertEqual(other.read_bytes(), b"leave untouched")

    def test_failed_page_export_releases_unpublished_cursor(self):
        diagnostics = io.StringIO()
        with (
            _task_directory(self.folder),
            patch.object(read_docx, "atomic_write_bytes", side_effect=OSError("cannot publish output")),
            redirect_stderr(diagnostics),
            self.assertRaises(SystemExit) as raised,
        ):
            read_docx.main([self.source.name, "--max-output-bytes", "2200", "-o", "page.json"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("cannot publish output", diagnostics.getvalue())
        self.assertFalse((self.folder / "page.json").exists())
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))
