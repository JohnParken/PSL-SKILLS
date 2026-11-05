#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unit tests for wps_selective heading detection, paragraph range slicing, and section filtering."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]


import unittest

from wps_selective import (
    detect_heading,
    project_headings,
    project_paragraph_range,
    project_section,
)


class TestWpsSelectiveReading(unittest.TestCase):

    def test_detect_heading(self):
        cases = [
            ("一、总体原则与要求", 1),
            ("第一章 总则", 1),
            ("# Markdown 一级标题", 1),
            ("（一）第一阶段目标", 2),
            ("(二) 第二阶段目标", 2),
            ("第二节 实施细节", 2),
            ("## Markdown 二级标题", 2),
            ("1. 强化组织领导", 3),
            ("2、落实主体责任", 3),
            ("### Markdown 三级标题", 3),
            ("（1）明确时间节点", 4),
            ("(2) 加强人员配备", 4),
            ("普通的正文内容，没有标题特征。", None),
        ]
        for text, expected_level in cases:
            lvl, _ = detect_heading(text)
            self.assertEqual(lvl, expected_level, f"Failed for text: {text}")

    def test_project_headings(self):
        result = {
            "status": "ok",
            "paragraphs": [
                {"text": "公文主标题"},
                {"text": "一、总体要求"},
                {"text": "正文内容第一段。"},
                {"text": "（一）基本原则"},
                {"text": "正文内容第二段。"},
                {"text": "二、重点任务"},
            ],
        }
        res = project_headings(result)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["mode"], "heading_only")
        self.assertEqual(res["total_headings"], 3)
        self.assertEqual(res["headings"][0]["text"], "一、总体要求")
        self.assertEqual(res["headings"][1]["text"], "（一）基本原则")
        self.assertEqual(res["headings"][2]["text"], "二、重点任务")

    def test_project_paragraph_range(self):
        result = {
            "status": "ok",
            "paragraphs": [
                {"text": "段落 1"},
                {"text": "段落 2"},
                {"text": "段落 3"},
                {"text": "段落 4"},
            ],
        }
        res = project_paragraph_range(result, "2:3")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["returned_paragraphs"], 2)
        self.assertEqual(res["paragraphs"][0]["text"], "段落 2")
        self.assertEqual(res["paragraphs"][1]["text"], "段落 3")

        with self.assertRaises(ValueError):
            project_paragraph_range(result, "bad_format")
        with self.assertRaises(ValueError):
            project_paragraph_range(result, "0:2")
        with self.assertRaises(ValueError):
            project_paragraph_range(result, "3:1")

    def test_project_section(self):
        result = {
            "status": "ok",
            "paragraphs": [
                {"text": "一、总体要求"},
                {"text": "正文内容 A。"},
                {"text": "（一）基本原则"},
                {"text": "正文内容 B。"},
                {"text": "二、主要任务"},
                {"text": "正文内容 C。"},
            ],
        }
        res = project_section(result, "总体要求")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["total_paragraphs"], 4)
        self.assertEqual(res["paragraphs"][0]["text"], "一、总体要求")
        self.assertEqual(res["paragraphs"][-1]["text"], "正文内容 B。")

        # Not found
        res_nf = project_section(result, "不存在的章节")
        self.assertEqual(res_nf["status"], "not_found")
        self.assertEqual(len(res_nf["paragraphs"]), 0)


if __name__ == "__main__":
    unittest.main()
