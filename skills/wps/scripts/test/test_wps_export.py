#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Comprehensive unit tests for wps-export script functions, RTF engine, and CLI commands."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from test_task_cli_support import task_api


import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import export_wps
import wps_rtf_builder
import docx_to_wps_rtf


def create_mock_cfb_with_word_document(sector_shift=9, w_ident=0xA5EC, n_fib=0x00C1):
    """Valid synthetic CFB/FIB/piece table, independent of the export inspector."""
    from test_wps import document, replace_word, synthetic_cfb
    streams = replace_word(document(), 0, w_ident, 2)
    streams = replace_word(streams, 2, n_fib, 2)
    return synthetic_cfb({name[0]: data for name, data in streams.items()}, major=3 if sector_shift == 9 else 4)


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
        self.assertIn("No such file", res["error"])

    def test_inspect_truncated_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            tf.write(b"short")
            tmp_name = tf.name
        try:
            res = export_wps.inspect_wps_structure(tmp_name)
            self.assertFalse(res["valid"])
            self.assertEqual(res["container"], "unknown")
        finally:
            os.remove(tmp_name)

    def test_inspect_zip_signature(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            tf.write(b"PK\x03\x04" + b"\x00" * 600)
            tmp_name = tf.name
        try:
            res = export_wps.inspect_wps_structure(tmp_name)
            self.assertFalse(res["valid"])
            self.assertEqual(res["container"], "zip")
        finally:
            os.remove(tmp_name)

    def test_inspect_invalid_signature(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            tf.write(b"\x00" * 600)
            tmp_name = tf.name
        try:
            res = export_wps.inspect_wps_structure(tmp_name)
            self.assertFalse(res["valid"])
            self.assertEqual(res["container"], "unknown")
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
            self.assertIn("signature", res["error"].lower())
        finally:
            os.remove(tmp_name)

    def test_inspect_valid_rtf(self):
        doc = wps_rtf_builder.RtfDocument()
        doc.add_title("测试标题")
        doc.add_body_paragraph("这是正文测试段落。")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wps") as tf:
            os.unlink(tf.name)
            task_api(doc.save, tf.name)
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
            from test_wps_ooxml import package
            in_docx.write_bytes(package("<w:p><w:r><w:t>Hello 中文</w:t></w:r></w:p><w:p><w:r><w:t>第二段</w:t></w:r></w:p>"))
            out_wps = (Path(td) / "sample.wps").resolve()

            def side_effect(*args, **kwargs):
                mock_data = create_mock_cfb_with_word_document()
                command = args[0]
                private = Path(command[command.index("--outdir") + 1])
                (private / "sample.doc").write_bytes(mock_data)
                m = MagicMock()
                m.returncode = 0
                m.stdout = "Converted successfully"
                m.stderr = ""
                return m

            mock_run.side_effect = side_effect

            res = task_api(export_wps.convert_docx_to_wps, str(in_docx), backend="libreoffice")
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["backend"], "libreoffice")
            self.assertTrue(res["structure_check"]["valid"])
            self.assertEqual(res["output_file"], str(out_wps))

    @patch("export_wps.find_soffice")
    def test_convert_docx_missing_soffice(self, mock_find):
        mock_find.return_value = None
        with tempfile.NamedTemporaryFile(suffix=".docx") as tf:
            res = task_api(export_wps.convert_docx_to_wps, tf.name, backend="libreoffice")
            self.assertEqual(res["status"], "missing-dependency")

    def test_convert_docx_rtf_backend_real_sample(self):
        sample_path = Path("tests/fixtures/docx/01_基础打开_中文字体与样式.docx")
        if not sample_path.is_file():
            self.skipTest("Sample docx not found")

        with tempfile.TemporaryDirectory() as td:
            out_wps = (Path(td) / "result.wps").resolve()
            res = task_api(export_wps.convert_docx_to_wps, str(sample_path), output_path=str(out_wps), backend="rtf")
            self.assertEqual(res["status"], "partial")
            unmapped = {issue.get('property') for issue in res['conversion_issues'] if issue['code'] == 'source_format_not_mapped'}
            self.assertTrue({'keepNext', 'numPr', 'szCs'} <= unmapped)
            self.assertEqual(res['source_format_mapping']['status'], 'detected_omissions')
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
        sample_path = Path("tests/fixtures/docx/03_复杂表格_跨行跨列合并.docx")
        if not sample_path.is_file():
            self.skipTest("Sample table docx not found")

        with tempfile.TemporaryDirectory() as td:
            out_wps = (Path(td) / "table_result.wps").resolve()
            res = task_api(export_wps.convert_docx_to_wps, str(sample_path), output_path=str(out_wps), backend="auto")
            self.assertEqual(res["status"], "partial")
            self.assertTrue(res["fallback_used"])
            self.assertFalse(res["binary_doc"])
            self.assertTrue(out_wps.is_file())

            # Read back text
            rb = export_wps.verify_readback(str(out_wps))
            self.assertEqual(rb["status"], "readback-partial")
            self.assertEqual(rb["returncode"], 3)
            self.assertIn("rtf_merge_geometry_not_reconstructed", [item["code"] for item in rb["issues"]])

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
