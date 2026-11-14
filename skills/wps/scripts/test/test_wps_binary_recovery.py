"""Synthetic bounded-story recovery, not real WPS producer acceptance."""
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from read_wps import ParseError, parse_streams, read_bytes
from test_wps import document, synthetic_cfb
from test_wps_binary_headers import fixture as headers
from test_wps_binary_refs import fixture as notes
from test_wps_binary_comments import fixture as comments


def maximum(streams):
    return parse_streams(streams, include_related=True, maximum=True,
                         text_policy="recover", field_policy="cached_result",
                         object_policy="placeholder")


def bad_header():
    streams = headers(False)
    start = struct.unpack_from("<I", streams[("WordDocument",)], 242)[0]
    table = bytearray(streams[("0Table",)])
    struct.pack_into("<I", table, start, 1)
    streams[("0Table",)] = bytes(table)
    return streams, start


class BinaryRecoveryTests(unittest.TestCase):
    def test_header_slots_retain_body_text_and_sepx_ownership(self):
        streams, start = bad_header()
        result = maximum(streams)
        self.assertEqual(result["text"], "A\n")
        self.assertEqual(result["status"], "partial")
        region = result["related_stories"][1]
        self.assertIn("H", region["text"])
        self.assertEqual(region["anchor_status"], "header_slots_unresolved")
        self.assertTrue(region["section_mapping"]["word_ranges"])
        issue = next(x for x in region["issues"] if x["code"] == "binary_story_mapping_unresolved")
        self.assertEqual((issue["stream"], issue["offset"]), ("0Table", start))
        self.assertFalse(region["references"])

    def test_unified_byte_entry_recovers_synthetic_cfb(self):
        streams, _ = bad_header()
        raw = synthetic_cfb({path[0]: value for path, value in streams.items()})
        result = read_bytes(raw, reading_mode="maximum")
        self.assertEqual(result["text"], "A\n")
        self.assertEqual(result["status"], "partial")

    def test_strict_entry_remains_strict(self):
        streams, _ = bad_header()
        with self.assertRaises(ParseError):
            parse_streams(streams, include_related=True)

    def test_invalid_note_mapping_keeps_unmapped_stored_text(self):
        for kind, index in (("footnotes", 0), ("endnotes", 3)):
            with self.subTest(kind=kind):
                result = maximum(notes(kind, text_cps=(1, 2, 0xFFFFFFFF)))
                self.assertEqual(result["text"], "A\n")
                region = result["related_stories"][index]
                self.assertIn("甲", region["text"])
                self.assertEqual(region["anchor_status"], "unknown")
                self.assertFalse(region["references"])
                self.assertTrue(any(x["code"] == "binary_story_mapping_unresolved" for x in region["issues"]))

    def test_invalid_comment_author_not_reported_as_verified(self):
        result = maximum(comments(author=10))
        self.assertIn("A", result["text"])
        region = result["related_stories"][2]
        self.assertIn("注", region["text"])
        self.assertFalse(region["references"])
        self.assertEqual(region["anchor_status"], "unknown")
        self.assertEqual(result["status"], "partial")

    def test_unreadable_optional_text_does_not_hide_other_stories(self):
        for raw in (b"\x00\xd8\r\x00", "\x13BROKEN\r".encode("utf-16le")):
            streams = document([(b"A\r", True)], {80: raw, 96: "尾注\r".encode("utf-16le")})
            result = maximum(streams)
            self.assertEqual(result["text"], "A\n")
            self.assertIsNone(result["related_stories"][0]["text"])
            self.assertIn("尾注", result["related_stories"][3]["text"])
            self.assertTrue(any(x["code"] == "binary_related_story_unreadable" for x in result["issues"]))

    def test_invalid_main_utf16_stays_fatal(self):
        with self.assertRaises(ParseError):
            maximum(document([(b"\x00\xd8\r\x00", False)]))

    def test_resource_limits_stay_fatal(self):
        with patch("wps_binary_refs.MAX_REFERENCES", 0):
            with self.assertRaises(ParseError) as caught:
                maximum(notes())
            self.assertEqual(caught.exception.status, "resource_limit")
        with patch("wps_binary_headers.MAX_SECTIONS", 0):
            with self.assertRaises(ParseError) as caught:
                maximum(headers(False))
            self.assertEqual(caught.exception.status, "resource_limit")

    def test_table_alias_stays_fatal(self):
        streams = notes()
        word = bytearray(streams[("WordDocument",)])
        word[154 + 46 * 8:154 + 48 * 8] = word[154 + 2 * 8:154 + 4 * 8]
        streams[("WordDocument",)] = bytes(word)
        with self.assertRaises(ParseError) as caught:
            maximum(streams)
        self.assertIn("Overlapping", str(caught.exception))

    def test_sepx_overlap_stays_fatal(self):
        streams, _ = bad_header()
        sed = struct.unpack_from("<I", streams[("WordDocument",)], 202)[0]
        table = bytearray(streams[("0Table",)])
        struct.pack_into("<I", table, sed + 10, 4098)
        streams[("0Table",)] = bytes(table)
        with self.assertRaises(ParseError) as caught:
            maximum(streams)
        self.assertIn("SEPX", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
