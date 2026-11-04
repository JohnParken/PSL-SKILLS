#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Comprehensive unit tests for wps-export script functions, RTF engine, and CLI commands."""

import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import export_wps
import wps_rtf_builder
import docx_to_wps_rtf


def create_mock_cfb_with_word_document(sector_shift=9, w_ident=0xA5EC, n_fib=0x00C1):
    """Helper to synthesize a minimal CFB binary with a WordDocument stream."""
    sector_size = 1 << sector_shift
    buf = bytearray(sector_size * 4)

    # Header
    buf[0:8] = export_wps.CFB_SIGNATURE
    struct.pack_into("<H", buf, 28, 0xFFFE)
    struct.pack_into("<H", buf, 30, sector_shift)
    struct.pack_into("<H", buf, 32, 6)
    struct.pack_into("<I", buf, 44, 1)
    struct.pack_into("<I", buf, 48, 1)
    struct.pack_into("<I", buf, 56, 4096)

    # FAT sector at sector 0
    struct.pack_into("<109I", buf, 76, 0, *([0xFFFFFFFF] * 108))

    fat_offset = sector_size
    struct.pack_into("<I", buf, fat_offset, 0xFFFFFFFD)
    struct.pack_into("<I", buf, fat_offset + 4, 0xFFFFFFFE)
    struct.pack_into("<I", buf, fat_offset + 8, 0xFFFFFFFE)

    # Directory sector at sector 1
    dir_offset = 2 * sector_size

    # Root Entry (entry 0)
    root_name = "Root Entry\x00".encode("utf-16le")
    buf[dir_offset : dir_offset + len(root_name)] = root_name
    struct.pack_into("<H", buf, dir_offset + 64, len(root_name))
    buf[dir_offset + 66] = 5
    struct.pack_into("<I", buf, dir_offset + 116, 0xFFFFFFFE)
    struct.pack_into("<Q", buf, dir_offset + 120, 0)

    # WordDocument Entry (entry 1)
    doc_entry_offset = dir_offset + 128
    doc_name = "WordDocument\x00".encode("utf-16le")
    buf[doc_entry_offset : doc_entry_offset + len(doc_name)] = doc_name
    struct.pack_into("<H", buf, doc_entry_offset + 64, len(doc_name))
    buf[doc_entry_offset + 66] = 2
    struct.pack_into("<I", buf, doc_entry_offset + 116, 2)
    struct.pack_into("<Q", buf, doc_entry_offset + 120, 4096)

    # WordDocument stream at sector 2
    doc_offset = 3 * sector_size
    struct.pack_into("<HH", buf, doc_offset, w_ident, n_fib)

    return bytes(buf)


class TestWpsExport(unittest.TestCase):

    def test_find_soffice_custom(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"#!/bin/sh\necho 1.0\n")
            custom_path = tf.name
        os.chmod(custom_path, 0o755)
        try:
            found = export_wps.find_soffice(custom_path)
            self.assertEqual(found, str(Path(custom_path).resolve()))
        finally:
            os.remove(custom_path)

    def test_find_soffice_env(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"#!/bin/sh\necho 1.0\n")
            env_path = tf.name
        os.chmod(env_path, 0o755)
        try:
            with patch.dict(os.environ, {"SOFFICE_PATH": env_path}):
                found = export_wps.find_soffice()
                self.assertEqual(found, str(Path(env_path).resolve()))
        finally:
            os.remove(env_path)

    @patch('export_wps.find_soffice', return_value=None)
    def test_check_environment(self, _find_soffice):
        res = export_wps.check_environment()
        self.assertIn("status", res)
        self.assertEqual(res["status"], "environment-ok")
        self.assertIn("python_ok", res)
        self.assertTrue(res["python_ok"])
        self.assertIn("backends_available", res)
        self.assertIn("rtf", res["backends_available"])

    def test_inspect_missing_file(self):
        res = export_wps.inspect_wps_structure("/nonexistent/file.wps")
        self.assertFalse(res["valid"])
        self.assertIn("File not found", res["error"])

    def test_inspect_truncated_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            tf.write(b"short")
            tmp_name = tf.name
        try:
            res = export_wps.inspect_wps_structure(tmp_name)
            self.assertFalse(res["valid"])
            self.assertIn("smaller than 16 bytes", res["error"])
        finally:
            os.remove(tmp_name)

    def test_inspect_zip_signature(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            tf.write(b"PK\x03\x04" + b"\x00" * 600)
            tmp_name = tf.name
        try:
            res = export_wps.inspect_wps_structure(tmp_name)
            self.assertFalse(res["valid"])
            self.assertIn("starts with ZIP magic", res["error"])
        finally:
            os.remove(tmp_name)

    def test_inspect_invalid_signature(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            tf.write(b"\x00" * 600)
            tmp_name = tf.name
        try:
            res = export_wps.inspect_wps_structure(tmp_name)
            self.assertFalse(res["valid"])
            self.assertIn("neither CFB/OLE", res["error"])
        finally:
            os.remove(tmp_name)

    def test_inspect_valid_mock_cfb(self):
        mock_data = create_mock_cfb_with_word_document(sector_shift=9, w_ident=0xA5EC, n_fib=0x00C1)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            tf.write(mock_data)
            tmp_name = tf.name
        try:
            res = export_wps.inspect_wps_structure(tmp_name)
            self.assertTrue(res["valid"])
            self.assertEqual(res["format"], "MS-DOC / WPS Binary CFB")
            self.assertEqual(res["word_document"]["fib"]["wIdent"], "0xa5ec")
            self.assertEqual(res["word_document"]["fib"]["nFib"], "0xc1")
            self.assertEqual(res["word_document"]["fib"]["version_name"], "Word 97")
        finally:
            os.remove(tmp_name)

    def test_inspect_invalid_wident(self):
        mock_data = create_mock_cfb_with_word_document(sector_shift=9, w_ident=0x1234, n_fib=0x00C1)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            tf.write(mock_data)
            tmp_name = tf.name
        try:
            res = export_wps.inspect_wps_structure(tmp_name)
            self.assertFalse(res["valid"])
            self.assertIn("Invalid WordDocument stream signature", res["error"])
        finally:
            os.remove(tmp_name)

    def test_inspect_valid_rtf(self):
        doc = wps_rtf_builder.RtfDocument()
        doc.add_title("测试标题")
        doc.add_body_paragraph("这是正文测试段落。")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            doc.save(tf.name)
            tmp_name = tf.name
        try:
            res = export_wps.inspect_wps_structure(tmp_name)
            self.assertTrue(res["valid"])
            self.assertEqual(res["format"], "RTF-stream WPS Compatible Document")
            self.assertTrue(res["has_font_table"])
        finally:
            os.remove(tmp_name)

    @patch("export_wps.find_soffice")
    @patch("subprocess.run")
    def test_convert_docx_to_wps_libreoffice_success(self, mock_run, mock_find):
        mock_find.return_value = "/usr/bin/soffice"

        with tempfile.TemporaryDirectory() as td:
            in_docx = (Path(td) / "sample.docx").resolve()
            in_docx.write_text("dummy docx content", encoding="utf-8")
            out_wps = (Path(td) / "sample.wps").resolve()

            def side_effect(*args, **kwargs):
                mock_data = create_mock_cfb_with_word_document()
                out_wps.write_bytes(mock_data)
                m = MagicMock()
                m.returncode = 0
                m.stdout = "Converted successfully"
                m.stderr = ""
                return m

            mock_run.side_effect = side_effect

            res = export_wps.convert_docx_to_wps(str(in_docx), backend="libreoffice")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["backend"], "libreoffice")
            self.assertTrue(res["structure_check"]["valid"])
            self.assertEqual(res["output_file"], str(out_wps))

    @patch("export_wps.find_soffice")
    def test_convert_docx_missing_soffice(self, mock_find):
        mock_find.return_value = None
        with tempfile.NamedTemporaryFile(suffix=".docx") as tf:
            res = export_wps.convert_docx_to_wps(tf.name, backend="libreoffice")
            self.assertEqual(res["status"], "missing-dependency")

    def test_convert_docx_rtf_backend_real_sample(self):
        sample_path = Path("skills/01_基础打开_中文字体与样式.docx")
        if not sample_path.is_file():
            self.skipTest("Sample docx not found")

        with tempfile.TemporaryDirectory() as td:
            out_wps = (Path(td) / "result.wps").resolve()
            res = export_wps.convert_docx_to_wps(str(sample_path), output_path=str(out_wps), backend="rtf")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["backend"], "rtf")
            self.assertTrue(out_wps.is_file())
            self.assertGreater(out_wps.stat().st_size, 500)

            # Inspect structure
            ins = export_wps.inspect_wps_structure(str(out_wps))
            self.assertTrue(ins["valid"])
            self.assertEqual(ins["format"], "RTF-stream WPS Compatible Document")

            # Verify readback
            rb = export_wps.verify_readback(str(out_wps))
            self.assertEqual(rb["status"], "readback-ok")
            self.assertGreater(rb["paragraph_count"], 5)

    @patch('export_wps.find_soffice', return_value=None)
    def test_convert_docx_complex_table_sample(self, _find_soffice):
        sample_path = Path("skills/03_复杂表格_跨行跨列合并.docx")
        if not sample_path.is_file():
            self.skipTest("Sample table docx not found")

        with tempfile.TemporaryDirectory() as td:
            out_wps = (Path(td) / "table_result.wps").resolve()
            res = export_wps.convert_docx_to_wps(str(sample_path), output_path=str(out_wps), backend="auto")
            self.assertEqual(res["status"], "success")
            self.assertTrue(out_wps.is_file())

            # Read back text
            rb = export_wps.verify_readback(str(out_wps))
            self.assertEqual(rb["status"], "readback-ok")

    def test_rtf_builder_escaping(self):
        text = "Hello {World} \\ 汉字测试 123"
        escaped = wps_rtf_builder.escape_rtf_text(text)
        self.assertIn("\\{World\\}", escaped)
        self.assertIn("\\\\", escaped)
        self.assertIn("\\u27721?", escaped)

    def test_verify_readback_missing_file(self):
        res = export_wps.verify_readback("/nonexistent/test.wps")
        self.assertEqual(res["status"], "error")


if __name__ == "__main__":
    unittest.main()
