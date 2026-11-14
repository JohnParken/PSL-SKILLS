"""Synthetic lossless maximum-result transport tests, not visual format acceptance."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from read_wps import read_bytes
from wps_access import ParseError
from wps_maximum_pages import assemble_pages, canonical, project, read_page
from test_wps import document, synthetic_cfb
from test_wps_ooxml_related import package, paragraph


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def altered_token(cursor, **changes):
    token = json.loads(base64.urlsafe_b64decode(cursor))
    token.pop("checksum")
    token.update(changes)
    token["checksum"] = digest(canonical(token))
    return base64.urlsafe_b64encode(canonical(token).encode("utf-8")).decode("ascii")


class MaximumPagesTests(unittest.TestCase):
    def consume(self, result, data=b"source", options=None, **limits):
        options = options or {"reading_mode": "maximum"}
        cursor, chunks, end = None, [], 0
        for _ in range(2000):
            view = project(result, data, options, cursor=cursor, **limits)
            wire = canonical(view) + "\n"
            self.assertEqual(json.loads(wire), view)
            self.assertLessEqual(len(wire), limits.get("max_chars", 12000))
            self.assertLessEqual(len(wire.encode("utf-8")), limits.get("max_bytes", 12000))
            self.assertEqual(view["start"], end)
            fragment = view["payload_fragment"]
            self.assertGreater(len(fragment), 0)
            self.assertLessEqual(len(fragment), limits.get("page_size", 8000))
            end += len(fragment)
            chunks.append(fragment)
            self.assertEqual(view["end"], end)
            self.assertEqual(view["chunk_sha256"], digest(fragment))
            self.assertEqual(view["prefix_sha256"], digest("".join(chunks)))
            self.assertEqual(view["source_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(view["options_sha256"], digest(canonical(options)))
            self.assertEqual(view["source_status"], result.get("status", "invalid_or_unsupported"))
            self.assertEqual(view["cursor_integrity"], "sha256_consistency_not_authenticated")
            cursor = view["next_cursor"]
            if not view["has_more"]:
                self.assertIsNone(cursor)
                self.assertTrue(view["page_is_last"])
                self.assertEqual(view["single_page_complete"], len(chunks) == 1)
                break
            self.assertIsInstance(cursor, str)
        else:
            self.fail("pagination failed to terminate")
        combined = "".join(chunks)
        self.assertEqual(json.loads(combined), result)
        self.assertEqual(combined, canonical(result))
        self.assertEqual(view["result_sha256"], digest(combined))
        self.assertEqual(view["total_chars"], len(combined))
        self.assertEqual(view["total_utf8_bytes"], len(combined.encode("utf-8")))
        return chunks

    def test_all_result_fields_and_unicode_roundtrip(self):
        text = '正文中文😀 "quote" \\ path\n\t' * 110
        result = {"status": "partial", "partial": True, "text": text,
                  "tables": [{"rows": [[{"text": text}]]}],
                  "related_stories": [{"type": "header", "text": text}],
                  "revisions": [{"type": "deleted", "text": "100"}, {"type": "inserted", "text": "200"}],
                  "diagnostics": [{"message": text, "source": {"part": "word/header.xml"}}]}
        self.assertGreater(len(self.consume(result, page_size=801, max_chars=4000, max_bytes=3500)), 1)

    def test_failure_and_partial_diagnostics_are_not_upgraded(self):
        for status in ('corrupt', 'resource_limit', 'partial'):
            result = {'status': status, 'partial': status == 'partial',
                      'text': None, 'diagnostics': [{'message': 'unreadable region ' * 300}]}
            self.consume(result, page_size=1500)

    def test_one_character_pages_and_empty_result(self):
        self.assertEqual(len(self.consume({}, page_size=1)), 2)
        self.consume({"status": "ok", "text": ""}, page_size=1)

    def test_huge_single_entry_still_makes_progress(self):
        result = {"status": "partial", "diagnostics": [{"message": "😀\n\\" * 9000}]}
        self.assertGreater(len(self.consume(result, page_size=262144, max_bytes=3600, max_chars=3600)), 10)

    def test_result_larger_than_full_json_limit_can_start_bounded_transport(self):
        from read_wps import encode_result
        result = {'status': 'ok', 'text': 'X' * (17 * 1024 * 1024)}
        with self.assertRaises(ParseError):
            encode_result(result)
        first = project(result, b'source', {}, page_size=64)
        self.assertGreater(first['total_utf8_bytes'], 16 * 1024 * 1024)
        self.assertLessEqual(len((canonical(first) + '\n').encode()), 12000)
        second = project(result, b'source', {}, page_size=64, cursor=first['next_cursor'])
        self.assertEqual(second['start'], first['end'])
        self.assertEqual(second['result_sha256'], first['result_sha256'])

    def test_terminal_without_cursor_fits_small_budget(self):
        view = project({}, b"s", {}, max_chars=1800, max_bytes=1800)
        self.assertFalse(view["has_more"])
        self.assertEqual(view["payload_fragment"], "{}")

    def test_deterministic_and_options_order_independent(self):
        result = {"status": "ok", "text": "X" * 1500}
        first = project(result, b"s", {"a": 1, "b": 2}, page_size=25)
        self.assertEqual(first, project(result, b"s", {"b": 2, "a": 1}, page_size=25))
        second = project(result, b"s", {"b": 2, "a": 1}, page_size=25,
                         cursor=first["next_cursor"], max_chars=5000, max_bytes=5000)
        self.assertEqual(second["start"], first["end"])

    def assert_view_error(self, operation):
        with self.assertRaises(ParseError) as caught:
            operation()
        self.assertEqual(caught.exception.status, "output_view_error")

    def test_source_options_result_and_page_size_bound(self):
        result = {"status": "ok", "text": "X" * 100}
        options = {"reading_mode": "maximum"}
        cursor = project(result, b"s", options, page_size=10)["next_cursor"]
        for candidate, source, opts, size in ((result, b"t", options, 10),
                (result, b"s", {"reading_mode": "maximum", "revisions": "all"}, 10),
                ({**result, "text": "Y" * 100}, b"s", options, 10),
                (result, b"s", options, 11)):
            self.assert_view_error(lambda: project(candidate, source, opts, page_size=size, cursor=cursor))

    def test_cursor_checksum_and_structure(self):
        result = {"status": "ok", "text": "X" * 100}
        cursor = project(result, b"s", {}, page_size=10)["next_cursor"]
        for bad in ("!", "x" * 2049, 1, [], cursor[:-3],
                    altered_token(cursor, next=True), altered_token(cursor, next=0),
                    altered_token(cursor, next=100000), altered_token(cursor, prefix="0" * 64),
                    altered_token(cursor, revision=2), altered_token(cursor, extra="unexpected")):
            with self.subTest(cursor=str(bad)[:40]):
                self.assert_view_error(lambda: project(result, b"s", {}, page_size=10, cursor=bad))

    def test_budget_validation_and_unrepresentable_payload(self):
        for option, bad in (("page_size", 0), ("page_size", True), ("page_size", 262145),
                            ("max_chars", -1), ("max_chars", True), ("max_bytes", 511),
                            ("max_bytes", 16777217)):
            self.assert_view_error(lambda: project({}, b"s", {}, **{option: bad}))
        self.assert_view_error(lambda: project({"text": "X" * 100}, b"s", {}, max_bytes=512))
        self.assert_view_error(lambda: project({"value": float("nan")}, b"s", {}))
        self.assert_view_error(lambda: project({}, b"s", {"value": float("nan")}))
        self.assert_view_error(lambda: project({}, b"s", {"text": "\ud800"}))
        self.assert_view_error(lambda: project({"text": "\ud800"}, b"s", {}))
        with patch("wps_maximum_pages.MAX_RESULT_BYTES", 8):
            self.assert_view_error(lambda: project({"text": "X" * 100}, b"s", {}))

    def test_public_api_multiple_container_formats(self):
        streams = document([(b"Main\r", True)], {80: "注释\r".encode("utf-16le")})
        binary = synthetic_cfb({path[0]: value for path, value in streams.items()})
        samples = (br"{\rtf1 Main {\deleted old}{\revised new}}", package(paragraph("OOXML 正文")), binary)
        for data in samples:
            with self.subTest(signature=data[:8]):
                expected = read_bytes(data, reading_mode="maximum")
                fragments, cursor = [], None
                for _ in range(500):
                    view = read_page(data, page_size=137, cursor=cursor)
                    fragments.append(view["payload_fragment"])
                    cursor = view["next_cursor"]
                    if cursor is None:
                        break
                self.assertIsNone(cursor)
                self.assertEqual(json.loads("".join(fragments)), expected)
        self.assert_view_error(lambda: read_page(samples[0], reading_mode="standard"))

    def test_assemble_verifies_whole_chain(self):
        result = {"status": "partial", "text": "中文😀" * 100, "diagnostics": [{"code": "missing"}]}
        pages, cursor = [], None
        while True:
            page = project(result, b"source", {}, page_size=100, cursor=cursor)
            pages.append(page)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(assemble_pages(iter(pages)), result)
        invalid = [[], pages[:-1], pages[1:], list(reversed(pages)), pages + [pages[-1]],
                   pages[:1] + pages, [pages[-1]]]
        for bad in invalid:
            self.assert_view_error(lambda: assemble_pages(bad))
        changes = ({"status": "ok"}, {"source_status": "ok"}, {"partial": True}, {"payload_fragment": "X" + pages[0]["payload_fragment"][1:]},
                   {"source_sha256": "0" * 64}, {"options_sha256": "0" * 64},
                   {"result_sha256": "0" * 64}, {"chunk_sha256": "0" * 64},
                   {"prefix_sha256": "0" * 64}, {"total_chars": 1}, {"total_utf8_bytes": 1},
                   {"start": True}, {"end": 50000}, {"has_more": None}, {"next_cursor": None})
        for change in changes:
            with self.subTest(change=change):
                bad = [{**pages[0], **change}] + pages[1:]
                self.assert_view_error(lambda: assemble_pages(bad))
        self.assert_view_error(lambda: assemble_pages(pages[:-1] + [{**pages[-1], "next_cursor": pages[0]["next_cursor"]}]))
        with patch("wps_maximum_pages.MAX_RESULT_BYTES", 8):
            self.assert_view_error(lambda: assemble_pages(pages))
        with patch("wps_maximum_pages.MAX_ASSEMBLY_PAGES", 1):
            self.assert_view_error(lambda: assemble_pages(pages))

    def test_cli_continuation_in_fresh_process(self):
        script = str(Path(__file__).resolve().parents[1] / "read_wps.py")
        data = b"{\\rtf1 " + b"hello " * 40 + b"}"
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sample.wps").write_bytes(data)
            fragments, cursor = [], None
            for _ in range(100):
                command = [sys.executable, "-I", "-S", "-B", script, "sample.wps",
                           "--reading-mode", "maximum", "--page-size", "512"]
                if cursor:
                    command.extend(["--cursor", cursor])
                process = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=False)
                self.assertIn(process.returncode, (0, 3), process.stderr + process.stdout)
                view = json.loads(process.stdout)
                fragments.append(view["payload_fragment"])
                cursor = view["next_cursor"]
                if cursor is None:
                    break
            self.assertIsNone(cursor)
            self.assertEqual(json.loads("".join(fragments)), read_bytes(data, reading_mode="maximum"))
            self.assertEqual(Path(directory, "sample.wps").read_bytes(), data)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["sample.wps"])


if __name__ == "__main__":
    unittest.main()
