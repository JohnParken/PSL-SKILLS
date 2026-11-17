"""Lossless within-slide continuation, including real isolated CLI coverage."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from pptx_core.inspection import bounded_result, encoded
from pptx_core.runtime import RESULT_SCHEMA, TaskError
import test_workflow
from test_workflow import config, image_png, parts
from test_validation import zipped


def result_fixture() -> dict:
    """Use independently specified nested data, not the fragment encoder as oracle."""
    text = ('中文😀 "quoted" \\ slash\n\t' * 800) + '尾'
    return {"input": "deck.pptx", "sha256": hashlib.sha256(b"source").hexdigest(),
            "slide_count": 2, "title": "Test", "structural_validation": "passed",
            "slides": [{"number": 1, "objects": {"shapes": 1}, "text": [text], "notes": [text],
                        "tables": [{"rows": [[{"text": text, "span": 2}], []]}],
                        "charts": [{"series": [{"points": [{"index": 0, "value": "001.250"}, {"index": 91, "value": text}]}]}],
                        "notes_tables": [{"rows": [[{"text": "private"}]]}],
                        "notes_charts": [{"series": []}],
                        "content_assessment": {"status": "partial", "diagnostics": ["missing_cache"]},
                        "text_assessment": {"status": "math_structure_not_preserved", "diagnostics": ["math_text_flattened"]}},
                       {"number": 2, "objects": {}, "text": [], "notes": [], "empty": {}}]}


def assemble(pages: list[dict]) -> list[dict]:
    """Consumer checks coverage, source and exact offsets before decoding slides."""
    streams = {}
    totals = {}
    for page in pages:
        for item in page["fragments"]:
            path = tuple(item["path"])
            previous = streams.get(path, "")
            assert item["start"] == len(previous)
            assert item["end"] == item["start"] + len(item["text"])
            assert item["source_slide"] == page["coverage"]["selected_slides"][path[1]]
            streams[path] = previous + item["text"]
            totals[path] = item["total"]
    assert all(len(text) == totals[path] for path, text in streams.items())
    return [json.loads(streams[path]) for path in sorted(streams)]


class FragmentTests(unittest.TestCase):
    """Direct bounded API tests with large paragraphs and arbitrary objects."""

    def pages(self, value=None, *, source=b"source", budget=6000, **options):
        value = value or result_fixture()
        pages, cursor = [], None
        for _ in range(1000):
            page = bounded_result(value, source, max_output_bytes=budget, within_slide=True, cursor=cursor, **options)
            wire = encoded({"schema": RESULT_SCHEMA, "ok": True, "command": "inspect", **page})
            self.assertLessEqual(len(wire.encode("utf-8")), budget)
            self.assertNotIn("slides", page)
            self.assertEqual(page["fragment_schema"], "psl.pptx.fragments.v1")
            self.assertEqual(page["structural_validation"], value["structural_validation"])
            self.assertEqual(page["sha256"], value["sha256"])
            pages.append(page)
            cursor = page["pagination"]["next_cursor"]
            if cursor is None:
                break
        self.assertIsNone(cursor, "Continuation must always advance and eventually complete")
        return pages

    def test_huge_strings_nested_objects_and_exact_reassembly(self):
        value = result_fixture()
        pages = self.pages(value)
        self.assertGreater(len(pages), 10)
        self.assertEqual(assemble(pages), value["slides"])
        for page in pages:
            if page["pagination"]["selected_index"] == 0:
                self.assertEqual(page["slide_assessments"]["content_assessment"], value["slides"][0]["content_assessment"])
                self.assertEqual(page["slide_assessments"]["text_assessment"], value["slides"][0]["text_assessment"])
        self.assertFalse(pages[0]["pagination"]["slide_complete"])
        self.assertTrue(pages[-1]["pagination"]["complete"])

    def test_omission_summary_and_selection_preserve_assessments(self):
        value = result_fixture()
        omitted = assemble(self.pages(value, notes="omit", slides="1"))[0]
        for name in ("notes", "notes_tables", "notes_charts"):
            self.assertNotIn(name, omitted)
        self.assertEqual(omitted["content_assessment"], value["slides"][0]["content_assessment"])
        summary_pages = self.pages(value, summary=True)
        summaries = assemble(summary_pages)
        self.assertEqual(summaries[0]["text_paragraphs"], 1)
        self.assertEqual(summaries[0]["notes_paragraphs"], 1)
        self.assertEqual(summaries[0]["content_assessment"], value["slides"][0]["content_assessment"])
        self.assertFalse(any(name in summaries[0] for name in ("text", "notes", "charts", "tables")))
        self.assertEqual(summary_pages[0]["coverage"]["notes"], "counts_only")
        self.assertEqual(assemble(self.pages(value, slides="2")), [value["slides"][1]])

    def test_cursor_binds_source_path_options_and_schema(self):
        value = result_fixture()
        options = {"max_output_bytes": 3000, "within_slide": True}
        first = bounded_result(value, b"source", **options)
        cursor = first["pagination"]["next_cursor"]
        self.assertTrue(cursor.startswith("v2.0."))
        changes = ({"summary": True}, {"notes": "omit"}, {"slides": "1"},
                   {"allow_png_zero_padding": True}, {"max_output_bytes": 3001}, {"within_slide": False})
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(TaskError) as caught:
                bounded_result(value, b"source", cursor=cursor, **{**options, **changed})
            self.assertEqual(caught.exception.code, "invalid_cursor")
        for source, changed_value in ((b"changed", value), (b"source", {**value, "input": "other.pptx"})):
            with self.assertRaises(TaskError) as caught:
                bounded_result(changed_value, source, cursor=cursor, **options)
            self.assertEqual(caught.exception.code, "invalid_cursor")
        for malformed in ("", "bad", "1." + "0" * 64, cursor + "0", "v2.0.0." + "0" * 64,
                          "v2.99999999.0." + "0" * 64, "v2.0.999999999999." + "0" * 64):
            with self.subTest(cursor=malformed), self.assertRaises(TaskError) as caught:
                bounded_result(value, b"source", cursor=malformed, **options)
            self.assertEqual(caught.exception.code, "invalid_cursor")

    def test_small_budget_and_large_diagnostics_never_truncated(self):
        value = result_fixture()
        with self.assertRaises(TaskError) as caught:
            bounded_result(value, b"source", within_slide=True)
        self.assertEqual(caught.exception.code, "invalid_budget")
        with self.assertRaises(TaskError) as caught:
            bounded_result(value, b"source", within_slide=True, max_output_bytes=512)
        self.assertEqual(caught.exception.code, "output_budget_too_small")
        value["structural_validation"] = "partial"
        value["diagnostics"] = [{"part": "ppt/media/填充.png", "message": "诊断😀" * 1000}]
        with self.assertRaises(TaskError) as caught:
            bounded_result(value, b"source", within_slide=True, max_output_bytes=3000)
        self.assertEqual(caught.exception.code, "output_budget_too_small")
        for page in self.pages(value, budget=40000):
            self.assertEqual(page["diagnostics"], value["diagnostics"])
            self.assertEqual(page["structural_validation"], "partial")


class FragmentCliTests(unittest.TestCase):
    """Real parser, security checks, byte budget, partial status and stdout."""
    root: Path
    setUp = test_workflow.WorkflowTests.setUp
    tearDown = test_workflow.WorkflowTests.tearDown
    invoke = test_workflow.WorkflowTests.invoke
    assert_failure = test_workflow.WorkflowTests.assert_failure

    def test_real_cli_long_text_and_notes_partial_and_security(self):
        payload = config()
        payload["slides"][0]["notes"] = '中文😀 "引号" \\ tab\t' * 150
        payload["slides"].append(payload["slides"][0])
        self.invoke("create", "-", "deck.pptx", payload=payload)
        members = parts((self.root / "deck.pptx").read_bytes())
        root = ET.fromstring(members["[Content_Types].xml"])
        ET.SubElement(root, "{http://schemas.openxmlformats.org/package/2006/content-types}Default",
                      {"Extension": "png", "ContentType": "image/png"})
        members["[Content_Types].xml"] = ET.tostring(root)
        members["ppt/media/填充.png"] = image_png() + bytes(46)
        (self.root / "deck.pptx").write_bytes(zipped(members))
        _, full = self.invoke("inspect", "deck.pptx", "--allow-png-zero-padding")
        self.assert_failure("inspect", "deck.pptx", "--within-slide")
        pages, cursor = [], None
        for _ in range(30):
            args = ["inspect", "deck.pptx", "--within-slide", "--max-output-bytes", "3000", "--allow-png-zero-padding"]
            if cursor is not None:
                args += ["--cursor", cursor]
            process, page = self.invoke(*args)
            self.assertEqual(process.returncode, 3, page)
            self.assertLessEqual(len(process.stdout.encode("utf-8")), 3000)
            self.assertEqual(page["diagnostics"], full["diagnostics"])
            pages.append(page)
            cursor = page["pagination"]["next_cursor"]
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(assemble(pages), full["slides"])
        process, failure = self.invoke("inspect", "deck.pptx", "--within-slide", "--max-output-bytes", "512", "--allow-png-zero-padding")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(failure["error"]["code"], "output_budget_too_small")
        self.assertLessEqual(len(process.stdout.encode("utf-8")), 512)
        members["ppt/slides/_rels/slide2.xml.rels"] = members["ppt/slides/_rels/slide2.xml.rels"].replace(
            b"../slideLayouts/slideLayout1.xml", b"../slideLayouts/missing.xml")
        (self.root / "deck.pptx").write_bytes(zipped(members))
        self.assert_failure("inspect", "deck.pptx", "--within-slide", "--slides", "1", "--summary",
                            "--max-output-bytes", "3000", "--allow-png-zero-padding")


if __name__ == "__main__":
    unittest.main()
