#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unit tests for run_wps_task execution and document creation."""

import json
from pathlib import Path
import tempfile
import unittest

from run_wps_task import check_task_environment, validate_spec, create_document, main
from read_wps import read_bytes


class TestWpsTaskRunner(unittest.TestCase):

    def test_environment_check(self):
        code = check_task_environment()
        self.assertEqual(code, 0)

    def test_spec_validation(self):
        valid_spec = {
            "theme": "official",
            "blocks": [
                {"type": "title", "text": "测试标题"},
                {"type": "paragraph", "text": "测试正文。"},
            ],
        }
        validate_spec(valid_spec)

        # Invalid spec checks
        with self.assertRaises(ValueError):
            validate_spec({})
        with self.assertRaises(ValueError):
            validate_spec({"blocks": []})
        with self.assertRaises(ValueError):
            validate_spec({"blocks": [{"type": "unknown_type"}]})

    def test_create_document_official(self):
        with tempfile.TemporaryDirectory() as td:
            out_file = Path(td) / "official.wps"
            spec = {
                "theme": "official",
                "header": "内部公文",
                "footer": "第 1 页",
                "blocks": [
                    {"type": "title", "text": "关于专项检查的通报"},
                    {"type": "heading", "level": 1, "text": "一、总体情况"},
                    {"type": "paragraph", "text": "工作有序推进。"},
                    {"type": "list", "ordered": True, "items": ["项目A", "项目B"]},
                    {"type": "table", "rows": [["部门", "结果"], ["综合处", "优秀"]]},
                ],
            }
            res = create_document(spec, out_file, compact_receipt=True)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["readback_status"], "ok")
            self.assertTrue(out_file.exists())
            self.assertGreater(res["file_size"], 0)

            raw = out_file.read_bytes()
            self.assertTrue(raw.startswith(b"{\\rtf"))
            parsed = read_bytes(raw)
            self.assertEqual(parsed["status"], "ok")
            self.assertIn("关于专项检查的通报", parsed["text"])
            self.assertIn("一、总体情况", parsed["text"])

    def test_create_document_business_and_general(self):
        with tempfile.TemporaryDirectory() as td:
            for theme in ("business", "general"):
                out_file = Path(td) / f"{theme}.wps"
                spec = {
                    "theme": theme,
                    "blocks": [
                        {"type": "title", "text": f"{theme} 标题"},
                        {"type": "paragraph", "text": f"{theme} 正文说明。"},
                    ],
                }
                res = create_document(spec, out_file)
                self.assertEqual(res["status"], "success")
                self.assertTrue(out_file.exists())

    def test_main_cli_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            out_file = Path(td) / "cli_test.wps"
            spec_json = json.dumps({
                "theme": "general",
                "blocks": [{"type": "paragraph", "text": "CLI 生成测试。"}],
            })
            code = main(["create", str(out_file), "--spec", spec_json, "--compact-receipt"])
            self.assertEqual(code, 0)
            self.assertTrue(out_file.exists())

            # Wrong extension
            bad_file = Path(td) / "cli_test.doc"
            code_bad = main(["create", str(bad_file), "--spec", spec_json])
            self.assertEqual(code_bad, 2)


if __name__ == "__main__":
    unittest.main()
