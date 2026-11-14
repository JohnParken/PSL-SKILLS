#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unit tests for bounded RTF stream reading in WPS skill."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]


from test_task_cli_support import run_task_cli
import json
import os
import subprocess
import tempfile
import unittest

import wps_rtf_read
import read_wps


class TestWpsRtfReading(unittest.TestCase):

    def test_parse_simple_rtf(self):
        data = (
            b"{\\rtf1\\ansi\\deff0"
            b"{\\fonttbl{\\f0 SimSun;}}"
            b"\\pard First paragraph\\par"
            b"\\pard Second paragraph\\par}"
        )
        res = wps_rtf_read.parse_rtf_bytes(data)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["format"], "rtf")
        self.assertEqual(len(res["paragraphs"]), 2)
        self.assertEqual(res["paragraphs"][0]["text"], "First paragraph")
        self.assertEqual(res["paragraphs"][1]["text"], "Second paragraph")
        self.assertIn("First paragraph\nSecond paragraph", res["text"])

    def test_parse_unicode_signed_and_unsigned(self):
        # \u20013? -> 中, \u-26503? (-26503 + 65536 = 39033 = 项)
        data = (
            b"{\\rtf1\\ansi\\uc1"
            b"\\pard \\u20013?\\u-26503? 01\\par}"
        )
        res = wps_rtf_read.parse_rtf_bytes(data)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["paragraphs"][0]["text"], "中项 01")

    def test_parse_cp936_hex_escapes(self):
        # 'd6'd0'ce'c4 in CP936 is 中文
        data = (
            b"{\\rtf1\\ansi\\ansicpg936"
            b"\\pard \\'d6\\'d0\\'ce\\'c4\\par}"
        )
        res = wps_rtf_read.parse_rtf_bytes(data)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["paragraphs"][0]["text"], "中文")

    def test_parse_escaped_characters(self):
        data = (
            b"{\\rtf1\\ansi"
            b"\\pard Open \\{brace\\} and \\\\ backslash\\par}"
        )
        res = wps_rtf_read.parse_rtf_bytes(data)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["paragraphs"][0]["text"], "Open {brace} and \\ backslash")

    def test_parse_ignored_destinations(self):
        data = (
            b"{\\rtf1\\ansi"
            b"{\\fonttbl{\\f0 Times New Roman;}}"
            b"{\\colortbl ;\\red0\\green0\\blue0;}"
            b"{\\*\\generator CustomGen 1.0;}"
            b"\\pard Visible text\\par}"
        )
        res = wps_rtf_read.parse_rtf_bytes(data)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(res["paragraphs"]), 1)
        self.assertEqual(res["paragraphs"][0]["text"], "Visible text")

    def test_parse_table_structure(self):
        data = (
            b"{\\rtf1\\ansi"
            b"\\trowd\\cellx1000\\cellx2000"
            b"\\intbl Cell A1\\cell\\intbl Cell B1\\cell\\row"
            b"\\trowd\\cellx1000\\cellx2000"
            b"\\intbl Cell A2\\cell\\intbl Cell B2\\cell\\row}"
        )
        res = wps_rtf_read.parse_rtf_bytes(data)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(res["tables"]), 1)
        table = res["tables"][0]
        self.assertEqual(len(table["rows"]), 2)
        self.assertEqual(table["rows"][0][0]["text"], "Cell A1")
        self.assertEqual(table["rows"][0][1]["text"], "Cell B1")
        self.assertEqual(table["rows"][1][0]["text"], "Cell A2")
        self.assertEqual(table["rows"][1][1]["text"], "Cell B2")

    def test_invalid_signature_raises(self):
        with self.assertRaises(ValueError):
            wps_rtf_read.parse_rtf_bytes(b"not an rtf document")

    def test_size_limit_raises(self):
        with self.assertRaises(ValueError):
            wps_rtf_read.parse_rtf_bytes(b"{\\rtf1 text}", max_bytes=5)

    def test_read_wps_cli_rtf(self):
        with tempfile.NamedTemporaryFile(suffix=".rtf", delete=False) as tf:
            tf.write(b"{\\rtf1\\ansi\\pard Sample text for CLI\\par}")
            tmp_name = tf.name

        try:
            cmd = [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(Path(read_wps.__file__).resolve()),
                tmp_name,
             '--reading-mode', 'standard']
            proc = run_task_cli(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            self.assertEqual(proc.returncode, 0)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["format"], "rtf")
            self.assertEqual(payload["status"], "ok")
            self.assertIn("Sample text for CLI", payload["text"])

            # Test --format text
            cmd_text = cmd + ["--format", "text"]
            proc_text = run_task_cli(cmd_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            self.assertEqual(proc_text.returncode, 0)
            self.assertEqual(proc_text.stdout.strip(), "Sample text for CLI")
        finally:
            os.remove(tmp_name)


if __name__ == "__main__":
    unittest.main()
