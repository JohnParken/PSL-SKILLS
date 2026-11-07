"""Bound page contract tests, independent expected text/row coverage and bytes."""
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import hashlib
import json
import os
import subprocess
import unittest
import zipfile
from unittest.mock import patch

import read_docx as reader
import test_docx_read_fastpath as fixtures
from test_docx_read_fastpath import para, N


class BoundPageTests(unittest.TestCase):
    def setUp(self):
        fixtures.ReadFastpathTests.setUp(self)
        self.folder = self.folder.relative_to(Path.cwd()) if self.folder.is_absolute() else self.folder
        self.path = self.folder / "sample.docx"

    cli = fixtures.ReadFastpathTests.cli

    def page(self, **options):
        return reader.read_docx_page(self.path, **options)

    def replace_body(self, body):
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("word/document.xml", f'<w:document xmlns:w="{N}"><w:body>{body}</w:body></w:document>')

    def test_rows_round_trip_cli_and_api_with_adjustable_budget(self):
        cursor = None
        blocks = []
        for i in range(100):
            budget = 1900 if i % 2 else 1700
            args = ["--compact-json", "--tables", "rows", "--max-output-bytes", str(budget)]
            if cursor:
                args += ["--cursor", cursor]
            result = self.cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLessEqual(len(result.stdout.encode("utf-8")), budget)
            data = json.loads(result.stdout)
            self.assertEqual(data, self.page(tables="rows", max_output_bytes=budget, cursor=cursor))
            self.assertEqual(data["coverage"]["block_start"], len(blocks))
            blocks.extend(data["blocks"])
            self.assertEqual(data["coverage"]["block_end_exclusive"], len(blocks))
            self.assertEqual(data["has_more"], data["truncated"])
            cursor = data["next_cursor"]
            if cursor is None:
                break
        else:
            self.fail("pagination made no terminal progress")
        rows = [b for b in blocks if b["type"] == "table_row"]
        self.assertEqual([b["cells"] for b in rows], [[str(i), "产品" + str(i)] for i in range(42)])
        self.assertEqual([b["row_index"] for b in rows], list(range(1, 43)))
        self.assertEqual(blocks, reader.read_docx_compact(self.path, tables="rows")["blocks"])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.hash)

    def test_source_and_each_read_option_changes_are_rejected(self):
        first = self.page(tables="rows", max_output_bytes=1700)
        cursor = first["next_cursor"]
        self.assertIsNotNone(cursor)
        for update in ({"revisions": "all"}, {"include_related": True}, {"mode": "summary"},
                       {"tables": "summary"}, {"paragraph_range": (1, 8)}, {"section": "第一部分"}):
            options = dict(tables="rows", cursor=cursor, max_output_bytes=1700)
            options.update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "options changed"):
                self.page(**options)
        with zipfile.ZipFile(self.path, "a") as package:
            package.writestr("word/extra.xml", "<extra/>")
        with self.assertRaisesRegex(ValueError, "source bytes changed"):
            self.page(tables="rows", cursor=cursor)

    def test_cursor_shape_checksum_and_positions_rejected(self):
        first = self.page(tables="rows", max_output_bytes=1700)
        cursor = first["next_cursor"]
        fields = cursor.split(".")
        invalid = ["", True, 1, cursor + "x", cursor[:-1] + ("0" if cursor[-1] != "0" else "1")]
        for position in (0, -1, first["total_blocks"], 99999999):
            invalid.append(reader._page_cursor(fields[1], fields[2], position))
        for token in invalid:
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "cursor"):
                self.page(tables="rows", cursor=token)

    def test_unicode_exact_stdout_budget_and_single_block(self):
        self.replace_body(para("汉字🙂𝄞é" * 25))
        result = self.cli("--compact-json", "--max-output-bytes", "5000")
        self.assertEqual(result.returncode, 0, result.stderr)
        size = len(result.stdout.encode("utf-8"))
        self.assertGreater(size, len(result.stdout))
        exact = self.cli("--compact-json", "--max-output-bytes", str(size))
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(exact.stdout, result.stdout)
        short = self.cli("--compact-json", "--max-output-bytes", str(size - 1))
        self.assertNotEqual(short.returncode, 0)
        self.assertEqual(short.stdout, "")
        self.assertIn("--max-output-bytes", short.stderr)
        self.replace_body(para("🙂" * 5000))
        with self.assertRaisesRegex(ValueError, "single block exceeds"):
            self.page(max_output_bytes=2000)

    def test_metadata_and_diagnostics_never_clipped(self):
        result = self.cli("--compact-json", "--max-output-bytes", "128")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("complete page metadata/diagnostics", result.stderr)
        self.assertGreater(len(result.stderr.encode("utf-8")), 128)

    def test_summary_and_related_coverage(self):
        self.replace_body("".join(para(f"标题{i}🙂", style="Heading1") for i in range(110)))
        with zipfile.ZipFile(self.path, "a") as package:
            package.writestr("word/header1.xml", f'<w:hdr xmlns:w="{N}">{para("页眉🙂" * 100)}</w:hdr>')
        cursor = None
        headings = []
        while True:
            result = self.cli("--summary", "--include-related", "--max-output-bytes", "2000",
                              *(["--cursor", cursor] if cursor else []))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLessEqual(len(result.stdout.encode("utf-8")), 2000)
            data = json.loads(result.stdout)
            headings.extend(b["text"] for b in data["blocks"])
            self.assertEqual(data["related"][0]["text_chars"], 300)
            self.assertFalse(data["coverage"]["document_complete"])
            self.assertIn("headings-only-view", data["coverage"]["omissions"])
            cursor = data["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(headings, [f"标题{i}🙂" for i in range(110)])
        full = self.page(include_related=True, max_output_bytes=30000)
        self.assertTrue(full["related"][0]["preview_truncated"])
        self.assertEqual(len(full["related"][0]["text_preview"]), 200)
        legacy = self.cli("--summary")
        self.assertEqual(len(json.loads(legacy.stdout)["blocks"]), 100)

    def test_table_preview_is_not_complete_document(self):
        data = self.page()
        self.assertFalse(data["has_more"])
        self.assertTrue(data["coverage"]["selected_view_complete"])
        self.assertFalse(data["coverage"]["document_complete"])
        self.assertIn("table-rows-after-preview-omitted", data["coverage"]["omissions"])
        table = next(b for b in data["blocks"] if b["type"] == "table")
        self.assertEqual(table["rows"], [["0", "产品0"], ["1", "产品1"]])

    def test_paths_and_nonregular_inputs_rejected(self):
        for path in (self.path.resolve(), Path("../input.docx")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                reader.read_docx_page(path)
        link = self.folder / "link.docx"
        link.symlink_to(self.path.name)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            reader.read_docx_page(link)
        directory_link = self.folder / "nested"
        directory_link.symlink_to(".", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            reader.read_docx_page(directory_link / self.path.name)
        fifo = self.folder / "pipe.docx"
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            reader.read_docx_page(fifo)

    def test_snapshot_is_used_for_both_hash_and_parse(self):
        original = self.path.read_bytes()
        original_read = reader._snapshot_input
        def take_then_replace(path):
            snapshot = original_read(path)
            self.replace_body(para("replacement"))
            return snapshot
        with patch.object(reader, "_snapshot_input", side_effect=take_then_replace):
            data = self.page()
        self.assertEqual(data["source_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(data["blocks"][0]["text"], "封面")

    def test_modification_during_snapshot_fails(self):
        original_read = os.read
        changed = False
        def mutating_read(fd, size):
            nonlocal changed
            result = original_read(fd, size)
            if not changed:
                changed = True
                with self.path.open("ab") as stream:
                    stream.write(b"changed")
            return result
        with patch.object(reader.os, "read", side_effect=mutating_read):
            with self.assertRaisesRegex(ValueError, "changed while reading snapshot"):
                self.page()

    def test_empty_view_and_exact_final_budget(self):
        self.replace_body(para("正文"))
        data = self.page(mode="headings")
        self.assertEqual(data["blocks"], [])
        self.assertIsNone(data["next_cursor"])
        self.assertFalse(data["has_more"])
        self.assertEqual(self.page(mode="headings", max_output_bytes=len(reader.serialize_docx_page(data).encode("utf-8"))), data)

    def test_section_whitespace_normalized_but_occurrence_bound(self):
        first = self.page(section="  第一部分  ", tables="rows", max_output_bytes=1800)
        self.assertIsNotNone(first["next_cursor"])
        second = self.page(section="第一部分", tables="rows", cursor=first["next_cursor"], max_output_bytes=1800)
        self.assertEqual(second["start_block"], first["next_block"])
        with self.assertRaisesRegex(ValueError, "options changed"):
            self.page(section="第一部分", section_occurrence=1, tables="rows", cursor=first["next_cursor"])

    def test_zip_and_xml_safety_remain_enforced(self):
        with zipfile.ZipFile(self.path, "w") as package:
            package.writestr("../word/document.xml", "<x/>")
        with self.assertRaises(ValueError):
            self.page()
        with zipfile.ZipFile(self.path, "w") as package:
            package.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>')
        with self.assertRaises(ValueError):
            self.page()
        self.path.write_bytes(reader.CFB_SIGNATURE + b"data")
        with self.assertRaises(reader.UnsupportedInputError):
            self.page()

    def test_missing_safe_open_capability_fails_closed(self):
        with patch.object(reader.os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(ValueError, "safe dir_fd"):
                self.page()

    def test_saved_output_matches_wire_and_preserves_input(self):
        result = self.cli("--compact-json", "--max-output-bytes", "2000", "--tables", "rows")
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.cli("--compact-json", "--max-output-bytes", "2000", "--tables", "rows", "-o", "page.json")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(saved.stdout, "")
        self.assertEqual((self.folder / "page.json").read_bytes(), result.stdout.encode("utf-8"))
        refused = self.cli("--compact-json", "--max-output-bytes", "2000", "-o", self.path.name)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.hash)

    def test_wire_is_utf8_under_ascii_stdout_encoding(self):
        self.replace_body(para("🙂汉字"))
        # -I ignores PYTHONIOENCODING; reconfigure explicitly within the isolated
        # process to prove the bound-mode writer emits UTF-8 bytes itself.
        script = ("import sys;sys.path.insert(0,sys.argv.pop(1));import read_docx;"
                  "sys.stdout.reconfigure(encoding='ascii');raise SystemExit(read_docx.main(sys.argv[1:]))")
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script, str(fixtures.ROOT),
                                 self.path.name, "--compact-json", "--max-output-bytes", "2000"],
                                cwd=self.folder, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout), 2000)
        self.assertEqual(json.loads(result.stdout)["blocks"][0]["text"], "🙂汉字")

    def test_all_budget_sizes_preserve_complete_contiguous_blocks(self):
        self.replace_body("".join(para("🙂汉字" * i) for i in range(1, 15)))
        expected = ["🙂汉字" * i for i in range(1, 15)]
        for budget in (1600, 1700, 1999, 2000, 2500, 4000):
            with self.subTest(budget=budget):
                cursor = None
                actual = []
                for _ in range(20):
                    page = self.page(max_output_bytes=budget, cursor=cursor)
                    self.assertLessEqual(len(reader.serialize_docx_page(page).encode("utf-8")), budget)
                    actual.extend(block["text"] for block in page["blocks"])
                    cursor = page["next_cursor"]
                    if cursor is None:
                        break
                else:
                    self.fail("page loop did not terminate")
                self.assertEqual(actual, expected)

    def test_invalid_cli_combinations_and_api_types(self):
        for options in (("--max-output-bytes", "2000"),
                        ("--compact-json", "--max-output-bytes", "0"),
                        ("--compact-json", "--max-output-bytes", "2000", "--start-block", "0"),
                        ("--compact-json", "--max-output-bytes", "2000", "--max-output-chars", "2000")):
            with self.subTest(options=options):
                self.assertNotEqual(self.cli(*options).returncode, 0)
        for options in ({"max_output_bytes": True}, {"paragraph_range": (True, 2)},
                        {"include_related": 1}, {"section_occurrence": True},
                        {"include_related": True, "mode": "headings"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.page(**options)


if __name__ == "__main__":
    unittest.main()
