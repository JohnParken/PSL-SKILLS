#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unit tests for run_wps_edit operations and document mutations."""

from pathlib import Path
import tempfile
import unittest

from run_wps_task import create_document
from run_wps_edit import execute_edit_task, main
from read_wps import read_bytes


class TestWpsEditOperations(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = Path(self.temp_dir.name)
        self.base_file = self.td / "base.wps"
        spec = {
            "theme": "official",
            "page_numbering": False,
            "blocks": [
                {"type": "title", "text": "原始标题通知"},
                {"type": "heading", "level": 1, "text": "一、工作方案"},
                {"type": "paragraph", "text": "旧机构名称负责日常协调与推进。"},
                {"type": "table", "rows": [["人员", "岗位"], ["张三", "旧岗位"]]},
            ],
        }
        create_document(spec, self.base_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_text_replacement(self):
        out_file = self.td / "replaced_text.wps"
        ops = [{
            "operation": "replace_text",
            "find": "旧机构名称",
            "replace": "新办公室",
        }]
        res = execute_edit_task(self.base_file, out_file, ops)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["edit_stats"]["text_replacements"], 1)

        parsed = read_bytes(out_file.read_bytes())
        self.assertIn("新办公室负责日常协调与推进。", parsed["text"])
        self.assertNotIn("旧机构名称", parsed["text"])

    def test_paragraph_replace_and_delete(self):
        out_file = self.td / "mod_para.wps"
        ops = [
            {"operation": "replace_paragraph", "index": 3, "text": "全新替换的正文段落。"},
            {"operation": "append_blocks", "blocks": [{"type": "paragraph", "text": "结尾追加段落。"}]},
        ]
        res = execute_edit_task(self.base_file, out_file, ops)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["edit_stats"]["paragraphs_modified"], 1)

        parsed = read_bytes(out_file.read_bytes())
        self.assertIn("全新替换的正文段落。", parsed["text"])
        self.assertIn("结尾追加段落。", parsed["text"])

    def test_table_cell_update(self):
        out_file = self.td / "mod_table.wps"
        ops = [{
            "operation": "update_cell",
            "table_index": 0,
            "row": 1,
            "col": 1,
            "text": "新主任",
        }]
        res = execute_edit_task(self.base_file, out_file, ops)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["edit_stats"]["cells_updated"], 1)

        parsed = read_bytes(out_file.read_bytes())
        tbl = parsed.get("tables", [])[0]
        self.assertEqual(tbl["rows"][1][1]["text"], "新主任")

    def test_cli_subcommands(self):
        out_text = self.td / "cli_text.wps"
        code = main([
            "text", str(self.base_file), str(out_text),
            "--find", "原始标题", "--replace", "修订后标题",
        ])
        self.assertEqual(code, 0)
        self.assertTrue(out_text.exists())


if __name__ == "__main__":
    unittest.main()
