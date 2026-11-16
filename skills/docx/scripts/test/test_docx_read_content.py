"""Default DOCX CLI content, exact budgets, and explicit diagnostic compatibility."""

from contextlib import contextmanager, redirect_stderr
import io
import json
import os
import re
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import test_docx_report as fixtures
import read_docx
import docx_read_content
from test_docx_report_content import M, table, numbered, numbering


@contextmanager
def task_directory(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class ReadableContentTests(unittest.TestCase):
    setUp = fixtures.DocxReportTests.setUp
    write_docx = fixtures.DocxReportTests.write_docx
    run_cli = fixtures.DocxReportTests.run_cli
    read_report = fixtures.DocxReportTests.read_report
    run_cli_release = fixtures.DocxReportTests.run_cli_release

    def test_default_keeps_values_objects_and_related_content_without_structure_noise(self):
        drawing = fixtures.shape(fixtures.textbox("文本框正文")) + fixtures.shape("<c:chart r:id='chart'/>")
        formula = f"<m:oMath xmlns:m='{M}'><m:r><m:t>x</m:t></m:r></m:oMath>"
        body = fixtures.heading("标题") + fixtures.paragraph("正文") + table(numbered("外层") + table(fixtures.paragraph("嵌套值")))
        body += "<w:p><w:r>" + drawing + "</w:r>" + formula + "</w:p>"
        self.write_docx(body, {
            "word/numbering.xml": numbering(),
            "word/_rels/document.xml.rels": fixtures.relations(("chart", "chart", "charts/chart1.xml", "Internal")),
            "word/charts/chart1.xml": fixtures.chart_xml(),
            "word/charts/_rels/chart1.xml.rels": fixtures.relations(("ext", "oleObject", "https://example.invalid/private.xlsx", "External")),
            "word/header1.xml": f"<w:hdr xmlns:w='{fixtures.W}'>{fixtures.paragraph('页眉内容')}</w:hdr>",
        })
        original = self.path.read_bytes()
        result = self.run_cli()
        text = result.stdout.decode()
        for expected in ("# 标题", "正文", "外层", "嵌套值", "位于表1第1行第1列", "第1段 1.", "文本框正文",
                         "使用占比", "一月 = 0.449700", "二月 = （值缺失）", "三月 = 0.00", "数字格式：0.00%",
                         "页眉内容", '公式文字片段', '"x"', "数学结构尚未解释", "图表数值为文档保存值"):
            self.assertIn(expected, text)
        for unwanted in ("schema_version", "source_sha256", "word/", "resource_part", "relationship", "offset_emu",
                         "ExternalWorkbook", "B1:B3", "example.invalid", "next_cursor", "point_counts"):
            self.assertNotIn(unwanted, text)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(self.path.read_bytes(), original)
        report = self.read_report(budget=20000)
        self.assertEqual(report["format"], "docx-report")
        self.assertTrue(any(block["type"] == "chart_point" for block in report["blocks"]))

    def test_default_revision_selection_and_cell_revision_caveat(self):
        paragraph = "<w:p><w:del><w:r><w:delText>OLD_ONLY</w:delText></w:r></w:del><w:ins><w:r><w:t>NEW_ONLY</w:t></w:r></w:ins></w:p>"
        self.write_docx(paragraph + table(fixtures.paragraph("保留项"), "<w:cellDel w:id='1'/>"))
        accepted = self.run_cli().stdout.decode()
        self.assertIn("NEW_ONLY", accepted)
        self.assertNotIn("OLD_ONLY", accepted)
        self.assertIn("接受修订", accepted)
        self.assertIn("单元格修订未解释", accepted)
        original = self.run_cli("--revisions", "original").stdout.decode()
        self.assertIn("OLD_ONLY", original)
        self.assertNotIn("NEW_ONLY", original)
        all_text = self.run_cli("--revisions", "all").stdout.decode()
        self.assertIn("OLD_ONLYNEW_ONLY", all_text)
        self.assertIn("新旧内容同时保留", all_text)

    def test_actual_cli_content_pages_cover_every_chunk_with_exact_utf8_budget(self):
        values = [f"item-{index:02d} " + "汉字🙂" * 28 for index in range(20)]
        self.write_docx("".join(fixtures.paragraph(value) for value in values))
        cursor, end, combined = None, 0, []
        for _ in range(100):
            args = ["--max-output-bytes", "1600"] + (["--cursor", cursor] if cursor else [])
            result = self.run_cli(*args)
            self.assertLessEqual(len(result.stdout), 1600)
            text = result.stdout.decode()
            match = re.search(r"\[内容块 ([0-9]+)–([0-9]+) / ([0-9]+)；[^\n]+\]\nnext_cursor: ([^\n]+)\n\Z", text)
            self.assertIsNotNone(match)
            self.assertEqual(int(match[1]), end + 1)
            end = int(match[2])
            self.assertEqual(int(match[3]), 20)
            combined.extend(line for line in text.splitlines() if line.startswith("item-"))
            cursor = match[4]
            if cursor == "none":
                break
        else:
            self.fail("content pagination never terminated")
        self.assertEqual(combined, values)
        self.assertEqual(end, 20)
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))

    def test_content_cache_reused_and_bound_to_mode_options_and_source(self):
        self.write_docx(fixtures.paragraph("内容" * 100) * 20)
        with task_directory(self.folder):
            first = read_docx.read_docx_report(self.path.name, max_output_bytes=2000, _content=True)
            self.assertEqual(first["page_cache"], "stored")
            cursor = first["next_cursor"]
            with self.assertRaisesRegex(ValueError, "options changed"):
                read_docx.read_docx_report(self.path.name, cursor=cursor)
            with patch.object(read_docx, "_read_snapshot", side_effect=AssertionError("must reuse content cache")):
                last = read_docx.read_docx_report(self.path.name, max_output_bytes=2000000, cursor=cursor, _content=True)
            self.assertEqual(last["page_cache"], "reused")
            self.assertFalse(last["has_more"])
            self.assertEqual(len(first["blocks"]) + len(last["blocks"]), 20)
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))
        first = self.run_cli("--max-output-bytes", "2000").stdout.decode()
        cursor = first.splitlines()[-1].removeprefix("next_cursor: ")
        self.write_docx(fixtures.paragraph("changed"))
        failed = self.run_cli("--cursor", cursor, code=2)
        self.assertEqual(failed.stdout, b"")
        self.assertIn(b"source bytes changed", failed.stderr)
        self.run_cli_release(cursor)

    def test_large_row_fails_explicitly_and_can_resume_with_larger_budget(self):
        value = "字" * 5000
        self.write_docx(table(fixtures.paragraph(value)))
        self.assertIn(value, self.run_cli().stdout.decode())
        first = self.run_cli("--max-output-bytes", "2000").stdout.decode()
        cursor = first.splitlines()[-1].removeprefix("next_cursor: ")
        failed = self.run_cli("--max-output-bytes", "2000", "--cursor", cursor, code=2)
        self.assertEqual(failed.stdout, b"")
        self.assertIn(b"single block exceeds", failed.stderr)
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))
        last = self.run_cli("--max-output-bytes", "20000", "--cursor", cursor).stdout.decode()
        self.assertIn(value, last)
        self.assertTrue(last.endswith("next_cursor: none\n"))

    def test_unicode_budget_is_exact_and_warnings_are_not_clipped(self):
        self.write_docx(fixtures.paragraph("汉字🙂é" * 20))
        first = self.run_cli("--max-output-bytes", "20000")
        exact = self.run_cli("--max-output-bytes", str(len(first.stdout)))
        self.assertEqual(exact.stdout, first.stdout)
        failed = self.run_cli("--max-output-bytes", str(len(first.stdout) - 1), code=2)
        self.assertEqual(failed.stdout, b"")
        self.write_docx(table("<w:altChunk/>" + fixtures.paragraph("kept")))
        failed = self.run_cli("--max-output-bytes", "128", code=2)
        self.assertEqual(failed.stdout, b"")
        self.assertIn(b"complete content caveats", failed.stderr)

    def test_default_section_is_readable_and_advanced_section_stays_available(self):
        self.write_docx(fixtures.heading("甲") + fixtures.paragraph("INSIDE") + fixtures.heading("乙") + fixtures.paragraph("OUTSIDE"))
        text = self.run_cli("--section", "甲").stdout.decode()
        self.assertIn("INSIDE", text)
        self.assertNotIn("OUTSIDE", text)
        self.assertIn("仅包含所选章节", text)
        self.assertEqual(self.read_report("--section", "甲")["scope"]["section"], "甲")
        self.assertEqual(self.run_cli("--section", "甲", "--format", "text").stdout.decode(), "甲\nINSIDE")

    def test_content_serialization_or_export_failure_releases_its_cache(self):
        self.write_docx(fixtures.paragraph("内容" * 100) * 20)
        original_serializer = docx_read_content.serialize_content_page
        failed_after_save = []

        def fail_saved_page(page):
            if list(self.folder.glob(".docx-report-*.cache")):
                failed_after_save.append(True)
                raise RuntimeError("serialization stopped")
            return original_serializer(page)

        with task_directory(self.folder):
            with patch("docx_read_content.serialize_content_page", side_effect=fail_saved_page):
                with self.assertRaisesRegex(RuntimeError, "serialization stopped"):
                    read_docx.main([self.path.name, "--max-output-bytes", "2000"])
            self.assertEqual(failed_after_save, [True])
            self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))
            with patch.object(read_docx, "atomic_write_bytes", side_effect=OSError("export stopped")), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    read_docx.main([self.path.name, "--max-output-bytes", "2000", "-o", "page.txt"])
            self.assertEqual(failure.exception.code, 2)
            self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))

    def test_image_descriptions_and_missing_pixels_are_explicit(self):
        drawing = fixtures.shape("<wp:docPr id='1' name='internal-object-name' descr='示意图说明'/><a:blip r:embed='image'/>")
        self.write_docx("<w:p><w:r>" + drawing + "</w:r></w:p>", {
            "word/_rels/document.xml.rels": fixtures.relations(("image", "image", "media/image.png", "Internal")),
            "word/media/image.png": b"synthetic resource",
        })
        text = self.run_cli().stdout.decode()
        self.assertIn("示意图说明", text)
        self.assertIn("未识别图片像素", text)
        self.assertIn("读取状态：部分", text)
        self.assertNotIn("internal-object-name", text)
        self.assertNotIn("word/media", text)

    def test_default_input_safety_is_not_bypassed_by_presentation(self):
        with ZipFile(self.path, "w") as package:
            package.writestr("word/document.xml", "<!DOCTYPE w:document><w:document xmlns:w='" + fixtures.W + "'><w:body/></w:document>")
        result = self.run_cli(code=2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"DTD", result.stderr)


if __name__ == "__main__":
    unittest.main()
