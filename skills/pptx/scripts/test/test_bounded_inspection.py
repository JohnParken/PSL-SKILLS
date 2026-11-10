"""CLI regressions for bounded content versus whole-package security coverage."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import json
import contextlib
import io
import runpy
from unittest.mock import patch
import shutil
import unittest
from xml.etree import ElementTree as ET
import test_workflow
from test_workflow import config, parts, SKILL_ROOT, snapshot, image_png
from test_validation import zipped


class BoundedInspectionTests(unittest.TestCase):
    # The shared workflow fixture initializes this task directory in setUp.
    root: Path

    setUp = test_workflow.WorkflowTests.setUp
    tearDown = test_workflow.WorkflowTests.tearDown
    invoke = test_workflow.WorkflowTests.invoke
    assert_failure = test_workflow.WorkflowTests.assert_failure

    def deck(self, count=6, note="讲稿\nSecond line"):
        payload = config()
        payload["slides"] = [{**payload["slides"][0], "notes": note + str(i)} for i in range(count)]
        self.invoke("create", "-", "deck.pptx", payload=payload)

    def test_selection_and_summary(self):
        self.deck()
        _, full = self.invoke("inspect", "deck.pptx")
        self.assertNotIn("coverage", full)
        _, selected = self.invoke("inspect", "deck.pptx", "--slides", "2-3,6", "--notes", "omit")
        self.assertEqual([p["number"] for p in selected["slides"]], [2, 3, 6])
        self.assertTrue(all("notes" not in p for p in selected["slides"]))
        _, summary = self.invoke("inspect", "deck.pptx", "--summary")
        self.assertTrue(all("text" not in p and "notes" not in p for p in summary["slides"]))
        self.assertEqual(summary["coverage"]["validation"], "all_package_parts")

    def test_pages_reassemble_and_cursor_binding(self):
        self.deck()
        _, full = self.invoke("inspect", "deck.pptx")
        options = ["--max-output-bytes", "1300"]
        collected = []
        cursor = None
        first_cursor = None
        for _ in range(10):
            args = ["inspect", "deck.pptx", *options]
            if cursor:
                args += ["--cursor", cursor]
            process, result = self.invoke(*args)
            self.assertEqual(process.returncode, 0, result)
            self.assertLessEqual(len(process.stdout.encode("utf-8")), 1300)
            collected.extend(result["slides"])
            cursor = result["pagination"]["next_cursor"]
            first_cursor = first_cursor or cursor
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(collected, full["slides"])
        self.assertIsNotNone(first_cursor)
        for extra in (["--notes", "omit"], ["--summary"], ["--allow-png-zero-padding"], ["--slides", "1-5"]):
            result = self.assert_failure("inspect", "deck.pptx", *options, "--cursor", first_cursor, *extra)
            self.assertEqual(result["error"]["code"], "invalid_cursor")
        self.assert_failure("inspect", "deck.pptx", "--max-output-bytes", "1400", "--cursor", first_cursor)
        self.assert_failure("inspect", "deck.pptx", *options, "--cursor", first_cursor[:-1] + ("0" if first_cursor[-1] != "0" else "1"))
        data = (self.root / "deck.pptx").read_bytes()
        (self.root / "deck.pptx").write_bytes(zipped(parts(data)))
        # ZIP recompression can be identical; change a valid core title instead.
        members = parts(data)
        members["docProps/core.xml"] = members["docProps/core.xml"].replace(b"English", b"Changed")
        (self.root / "deck.pptx").write_bytes(zipped(members))
        self.assertEqual(self.assert_failure("inspect", "deck.pptx", *options, "--cursor", first_cursor)["error"]["code"], "invalid_cursor")

    def test_invalid_ranges_cursors_and_budgets(self):
        self.deck()
        for selection in ("", "0", "7", "3-2", "1,1", "3,2", "1-3,3", "1-", "١"):
            self.assert_failure("inspect", "deck.pptx", "--slides", selection)
        for cursor in ("", "bad", "0." + "0" * 64, "99999999." + "0" * 64):
            self.assert_failure("inspect", "deck.pptx", "--max-output-bytes", "1300", "--cursor", cursor)
        for budget in ("0", "1", "511", "67108865"):
            self.assertEqual(self.assert_failure("inspect", "deck.pptx", "--max-output-bytes", budget)["error"]["code"], "invalid_budget")

    def test_notes_table_in_summary_omission_and_pages(self):
        self.deck(2, "Speaker note")
        members = parts((self.root / "deck.pptx").read_bytes())
        name = "ppt/notesSlides/notesSlide1.xml"
        frame = (
            '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Table"/>'
            '<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
            '<p:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            '<a:tbl><a:tblPr/><a:tblGrid><a:gridCol w="914400"/></a:tblGrid><a:tr h="914400">'
            '<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>备注表格</a:t></a:r></a:p>'
            '</a:txBody><a:tcPr/></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
        )
        xml = members[name].decode("utf-8")
        members[name] = xml.replace('</p:spTree>', frame + '</p:spTree>').encode("utf-8")
        (self.root / "deck.pptx").write_bytes(zipped(members))
        _, full = self.invoke("inspect", "deck.pptx")
        self.assertEqual(full["slides"][0]["notes"], ["Speaker note0", "备注表格"])
        _, summary = self.invoke("inspect", "deck.pptx", "--summary")
        self.assertEqual(summary["slides"][0]["notes_paragraphs"], 2)
        _, omitted = self.invoke("inspect", "deck.pptx", "--notes", "omit")
        self.assertNotIn("notes", omitted["slides"][0])
        collected, cursor = [], None
        for _ in range(2):
            args = ["inspect", "deck.pptx", "--max-output-bytes", "1200"]
            if cursor:
                args += ["--cursor", cursor]
            process, page = self.invoke(*args)
            self.assertEqual(process.returncode, 0, page)
            self.assertLessEqual(len(process.stdout.encode("utf-8")), 1200)
            collected.extend(page["slides"])
            cursor = page["pagination"]["next_cursor"]
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(collected, full["slides"])

    def test_long_notes_recoverable_and_not_truncated(self):
        note = "注释和换行\n" * 1000
        self.deck(1, note)
        failure = self.assert_failure("inspect", "deck.pptx", "--max-output-bytes", "1300")
        self.assertEqual(failure["error"]["code"], "output_budget_too_small")
        self.assertIn("No content was truncated", failure["error"]["message"])
        process, small = self.invoke("inspect", "deck.pptx", "--max-output-bytes", "512")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(small["error"]["code"], "output_budget_too_small")
        self.assertLessEqual(len(process.stdout.encode("utf-8")), 512)
        self.assertIn("No content was truncated", process.stderr)
        _, full = self.invoke("inspect", "deck.pptx")
        process, recovered = self.invoke("inspect", "deck.pptx", "--max-output-bytes", "60000")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(recovered["slides"], full["slides"])
        process, _ = self.invoke("inspect", "deck.pptx", "--notes", "omit", "--max-output-bytes", "1300")
        self.assertEqual(process.returncode, 0)

    def test_multibyte_error_json_budget_retains_full_stderr(self):
        # Inject a long lower-layer diagnostic to exercise the actual CLI serializer.
        namespace = runpy.run_path(str(SKILL_ROOT / "scripts" / "pptx.py"))
        main = namespace["main"]
        message = "诊断😀\n" * 1500

        def fail(*args, **kwargs):
            raise ValueError(message)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(main.__globals__, {"environment": lambda: None, "read_presentation": fail}):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["inspect", "deck.pptx", "--max-output-bytes", "512"])
        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertLessEqual(len(stdout.getvalue().encode("utf-8")), 512)
        self.assertTrue(result["error"]["message_truncated"])
        self.assertEqual(result["error"]["full_diagnostic"], "stderr")
        self.assertIn(message, stderr.getvalue())

    def test_unselected_slide_relationship_still_rejected(self):
        self.deck(2)
        members = parts((self.root / "deck.pptx").read_bytes())
        name = "ppt/slides/_rels/slide2.xml.rels"
        members[name] = members[name].replace(b'../slideLayouts/slideLayout1.xml', b'../slideLayouts/missing.xml')
        (self.root / "deck.pptx").write_bytes(zipped(members))
        self.assert_failure("inspect", "deck.pptx", "--slides", "1", "--summary")

    def test_unselected_media_partial_diagnostics_and_budget(self):
        self.deck(2)
        members = parts((self.root / "deck.pptx").read_bytes())
        root = ET.fromstring(members["[Content_Types].xml"])
        ET.SubElement(root, "{http://schemas.openxmlformats.org/package/2006/content-types}Default",
                      {"Extension": "png", "ContentType": "image/png"})
        members["[Content_Types].xml"] = ET.tostring(root)
        members["ppt/media/填充.png"] = image_png() + bytes(46)
        (self.root / "deck.pptx").write_bytes(zipped(members))
        self.assert_failure("inspect", "deck.pptx", "--slides", "1", "--summary")
        process, result = self.invoke("inspect", "deck.pptx", "--slides", "1", "--summary",
                                      "--allow-png-zero-padding", "--max-output-bytes", "1300")
        self.assertEqual(process.returncode, 3)
        self.assertEqual(result["structural_validation"], "partial")
        self.assertEqual(result["diagnostics"][0]["bytes"], 46)
        self.assertEqual(result["diagnostics"][0]["part"], "ppt/media/填充.png")
        again, repeated = self.invoke("inspect", "deck.pptx", "--slides", "1", "--summary",
                                       "--allow-png-zero-padding", "--max-output-bytes", "1300")
        self.assertEqual(again.returncode, 3)
        self.assertEqual(result, repeated)
        small, failure = self.invoke("inspect", "deck.pptx", "--slides", "1", "--summary",
                                     "--allow-png-zero-padding", "--max-output-bytes", "512")
        self.assertEqual(small.returncode, 2)
        self.assertEqual(failure["error"]["code"], "output_budget_too_small")
        self.assertLessEqual(len(small.stdout.encode("utf-8")), 512)
        self.assertLessEqual(len(process.stdout.encode("utf-8")), 1300)
        members["ppt/media/填充.png"] = image_png() + b"broken"
        (self.root / "deck.pptx").write_bytes(zipped(members))
        self.assert_failure("inspect", "deck.pptx", "--slides", "1", "--summary", "--allow-png-zero-padding")

    def test_independent_readonly_without_cache(self):
        self.deck()
        install = self.root / "standalone"
        shutil.copytree(SKILL_ROOT, install, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "test"))
        self.assertFalse((install / "scripts" / "test").exists())
        before = snapshot(install)
        process, result = self.invoke("inspect", "deck.pptx", "--summary", "--max-output-bytes", "1300",
                                      script=install / "scripts" / "pptx.py", guard_installation=True)
        self.assertEqual(process.returncode, 0, result)
        self.assertEqual(before, snapshot(install))
        self.assertFalse(list(install.rglob("__pycache__")))


if __name__ == "__main__":
    unittest.main()
