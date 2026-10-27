"""Fixed semantic expectations for controlled DOCX edits; standard library only.

Inputs are small synthetic packages, not Office rendering or producer acceptance
evidence. Every test operates in its own task directory and checks source bytes.
"""
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from xml.parsers import expat
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def png_pixel(red, green, blue):
    """An actual one-pixel RGB PNG, with independently framed chunks and CRCs."""
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes((0, red, green, blue))))
            + chunk(b"IEND", b""))


@unittest.skipUnless(sys.version_info >= (3, 10) and expat.version_info >= (2, 6, 0),
                     "native DOCX editing requires Python >=3.10 and Expat >=2.6")
class EditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from create_docx import create_document
        from edit_docx import replace_text
        from edit_structure import edit_structure
        from edit_tables import edit_tables
        from manage_comments import manage_comments
        from read_docx import read_docx
        from replace_image import replace_image
        cls.create_document = staticmethod(create_document)
        cls.replace_text = staticmethod(replace_text)
        cls.edit_structure = staticmethod(edit_structure)
        cls.edit_tables = staticmethod(edit_tables)
        cls.manage_comments = staticmethod(manage_comments)
        cls.read_docx = staticmethod(read_docx)
        cls.replace_image = staticmethod(replace_image)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="docx-editing-test-", dir=".")
        self.root = Path(self.temporary.name).resolve()
        self.previous = Path.cwd()
        os.chdir(self.root)
        self.sources = {}

    def tearDown(self):
        try:
            for path, digest in self.sources.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest,
                                 f"Input changed: {path.name}")
        finally:
            os.chdir(self.previous)
            self.temporary.cleanup()

    def source(self, blocks=None, name="source.docx"):
        path = self.root / name
        self.create_document({"blocks": blocks or [
            {"type": "paragraph", "text": "合同 ABC"}]}, path, base_dir=self.root, verify=True)
        self.remember(path)
        return path

    def remember(self, path):
        self.sources[path] = hashlib.sha256(path.read_bytes()).hexdigest()

    def members(self, path):
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def document(self, path):
        return ET.fromstring(self.members(path)["word/document.xml"])

    def non_target_members_unchanged(self, before, after, changed):
        left, right = self.members(before), self.members(after)
        self.assertEqual(set(left), set(right))
        for name in set(left) - set(changed):
            self.assertEqual(left[name], right[name], name)

    def rejection_preserves_output(self, call):
        target = self.root / "rejected.docx"
        original = b"existing output must survive a rejected edit"
        target.write_bytes(original)
        with self.assertRaises(ValueError):
            call(target)
        self.assertEqual(target.read_bytes(), original)

    def test_replace_exact_counts_and_untouched_package_members(self):
        source = self.source([{"type": "paragraph", "text": "甲甲 甲甲"}])
        target = self.root / "edited.docx"
        self.replace_text(source, target, {"replacements": [
            {"old": "甲甲", "new": "乙", "count": 2}]})
        self.assertEqual(self.read_docx(target), "乙 乙")
        self.non_target_members_unchanged(source, target, {"word/document.xml"})

    def test_replacements_are_simultaneous_not_cascading(self):
        source = self.source([{"type": "paragraph", "text": "A B"}])
        target = self.root / "edited.docx"
        self.replace_text(source, target, {"replacements": [
            {"old": "A", "new": "B", "count": 1},
            {"old": "B", "new": "C", "count": 1}]})
        self.assertEqual(self.read_docx(target), "B C")

    def test_wrong_count_preserves_existing_output(self):
        source = self.source()
        self.rejection_preserves_output(lambda target: self.replace_text(
            source, target, {"replacements": [{"old": "ABC", "new": "XYZ", "count": 2}]}))

    def test_overlapping_occurrences_are_rejected(self):
        source = self.source([{"type": "paragraph", "text": "aaaa"}])
        self.rejection_preserves_output(lambda target: self.replace_text(
            source, target, {"replacements": [{"old": "aa", "new": "X", "count": 3}]}))

    def test_cross_run_replacement_is_rejected(self):
        source = self.source([{"type": "paragraph", "runs": [
            {"text": "合"}, {"text": "同", "bold": True}]}])
        self.assertEqual(self.read_docx(source), "合同")
        self.rejection_preserves_output(lambda target: self.replace_text(
            source, target, {"replacements": [{"old": "合同", "new": "协议", "count": 1}]}))

    def test_same_source_destination_is_rejected(self):
        source = self.source()
        with self.assertRaises(ValueError):
            self.replace_text(source, source, {"replacements": [
                {"old": "ABC", "new": "XYZ", "count": 1}]})

    def test_field_result_text_is_protected_from_replacement(self):
        base = self.source()
        parts = self.members(base)
        document = ET.fromstring(parts["word/document.xml"])
        paragraph = document.find(f"{{{W}}}body/{{{W}}}p")
        run = paragraph.find(f"{{{W}}}r")
        paragraph.remove(run)
        field = ET.SubElement(paragraph, f"{{{W}}}fldSimple", {f"{{{W}}}instr": "REF field"})
        field.append(run)
        parts["word/document.xml"] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
        source = self.root / "field.docx"
        with zipfile.ZipFile(source, "w") as archive:
            for name, content in parts.items():
                archive.writestr(name, content)
        self.remember(source)
        self.assertEqual(self.read_docx(source), "合同 ABC")
        self.rejection_preserves_output(lambda target: self.replace_text(source, target, {
            "replacements": [{"old": "ABC", "new": "XYZ", "count": 1}]}))

    def test_structure_insert_and_format_original_anchors(self):
        source = self.source()
        target = self.root / "structured.docx"
        self.edit_structure(source, target, {"operations": [
            {"type": "insert_paragraph", "paragraph_index": 0,
             "expected_text": "合同 ABC", "position": "after", "text": "新增段落"},
            {"type": "format_run", "paragraph_index": 0, "expected_text": "合同 ABC",
             "run_index": 0, "expected_run_text": "合同 ABC",
             "format": {"bold": True, "color": "123abc", "size_pt": 12}}]})
        self.assertEqual(self.read_docx(target), "合同 ABC\n新增段落")
        properties = self.document(target).find(f".//{{{W}}}rPr")
        self.assertIsNotNone(properties.find(f"{{{W}}}b"))
        self.assertEqual(properties.find(f"{{{W}}}color").get(f"{{{W}}}val"), "123ABC")
        self.assertEqual(properties.find(f"{{{W}}}sz").get(f"{{{W}}}val"), "24")
        self.non_target_members_unchanged(source, target, {"word/document.xml"})

    def test_structure_anchor_mismatch_is_rejected(self):
        source = self.source()
        self.rejection_preserves_output(lambda target: self.edit_structure(source, target, {
            "operations": [{"type": "insert_paragraph", "paragraph_index": 0,
                            "expected_text": "not the anchor", "position": "before", "text": "X"}]}))

    def test_table_insert_then_delete_matches_fixed_rows(self):
        source = self.source([{"type": "table", "rows": [
            ["项目", "金额"], ["甲", "10"], ["乙", "20"]], "header": True}])
        inserted = self.root / "inserted.docx"
        self.edit_tables(source, inserted, {"operation": {
            "type": "insert_row", "table_index": 0, "row_index": 1,
            "expected_rows": 3, "expected_text": ["甲", "10"],
            "position": "after", "cells": ["丙", "30"]}})
        self.assertEqual(self.read_docx(inserted), "项目\t金额\n甲\t10\n丙\t30\n乙\t20")
        self.remember(inserted)
        deleted = self.root / "deleted.docx"
        self.edit_tables(inserted, deleted, {"operation": {
            "type": "delete_row", "table_index": 0, "row_index": 2,
            "expected_rows": 4, "expected_text": ["丙", "30"]}})
        self.assertEqual(self.read_docx(deleted), "项目\t金额\n甲\t10\n乙\t20")
        self.non_target_members_unchanged(source, inserted, {"word/document.xml"})

    def test_table_header_and_last_data_row_are_protected(self):
        source = self.source([{"type": "table", "rows": [
            ["项目", "金额"], ["甲", "10"]], "header": True}])
        for index, expected in ((0, ["项目", "金额"]), (1, ["甲", "10"])):
            with self.subTest(row=index):
                self.rejection_preserves_output(lambda target: self.edit_tables(source, target, {
                    "operation": {"type": "delete_row", "table_index": 0,
                                  "row_index": index, "expected_rows": 2,
                                  "expected_text": expected}}))

    def test_comment_lifecycle_and_no_body_text_changes(self):
        source = self.source()
        added = self.root / "added.docx"
        result = self.manage_comments(source, {"action": "add", "paragraph_index": 0,
            "expected_text": "合同 ABC", "anchor_text": "合同 ABC", "text": "核对金额",
            "author": "审阅者", "initials": "SY"}, added)
        self.assertEqual(result, {"action": "add", "comment_id": 0})
        self.remember(added)
        listing = self.manage_comments(added, {"action": "list"})
        self.assertTrue(listing["mutable"], listing["diagnostics"])
        self.assertEqual(listing["comments"], [{"id": 0, "text": "核对金额", "author": "审阅者",
                                              "parent_id": None, "paragraph_index": 0}])
        self.assertEqual(self.read_docx(added), "合同 ABC")
        updated = self.root / "updated.docx"
        self.manage_comments(added, {"action": "update", "id": 0,
            "expected_text": "核对金额", "text": "核对通过"}, updated)
        self.assertEqual(self.manage_comments(updated, {"action": "list"})["comments"][0]["text"], "核对通过")
        self.remember(updated)
        deleted = self.root / "deleted.docx"
        self.manage_comments(updated, {"action": "delete", "id": 0,
            "expected_text": "核对通过"}, deleted)
        self.assertEqual(self.manage_comments(deleted, {"action": "list"})["comments"], [])
        self.assertEqual(self.read_docx(deleted), "合同 ABC")

    def test_comment_guards_and_reply_prevent_root_deletion(self):
        source = self.source()
        added = self.root / "added.docx"
        self.manage_comments(source, {"action": "add", "paragraph_index": 0,
            "expected_text": "合同 ABC", "anchor_text": "合同 ABC", "text": "问题", "author": "甲"}, added)
        self.remember(added)
        self.rejection_preserves_output(lambda target: self.manage_comments(added, {
            "action": "update", "id": 0, "expected_text": "错误预期", "text": "替换"}, target))
        replied = self.root / "replied.docx"
        result = self.manage_comments(added, {"action": "reply", "id": 0,
            "expected_text": "问题", "text": "答复", "author": "乙"}, replied)
        self.assertEqual(result["comment_id"], 1)
        comments = self.manage_comments(replied, {"action": "list"})["comments"]
        self.assertEqual([(c["id"], c["text"], c["parent_id"]) for c in comments],
                         [(0, "问题", None), (1, "答复", 0)])
        self.remember(replied)
        self.rejection_preserves_output(lambda target: self.manage_comments(replied, {
            "action": "delete", "id": 0, "expected_text": "问题"}, target))

    def test_comment_partial_run_anchor_and_list_output_are_rejected(self):
        source = self.source()
        self.rejection_preserves_output(lambda target: self.manage_comments(source, {
            "action": "add", "paragraph_index": 0, "expected_text": "合同 ABC",
            "anchor_text": "ABC", "text": "不拆分原 run", "author": "甲"}, target))
        self.rejection_preserves_output(lambda target: self.manage_comments(
            source, {"action": "list"}, target))

    def image_source(self):
        original = self.root / "red.png"
        original.write_bytes(png_pixel(255, 0, 0))
        source = self.source([{"type": "image", "path": "red.png",
                               "width_mm": 5, "height_mm": 5, "alt": "red pixel"}])
        parts = self.members(source)
        media = next(name for name in parts if name.startswith("word/media/"))
        selection = {"media_part": media, "expected_sha256": hashlib.sha256(parts[media]).hexdigest(),
                     "expected_relationships": 1, "expected_references": 1}
        replacement = self.root / "blue.png"
        replacement.write_bytes(png_pixel(0, 0, 255))
        return source, replacement, selection

    def test_image_replacement_preserves_all_other_members(self):
        source, replacement, selection = self.image_source()
        target = self.root / "image.docx"
        self.replace_image(source, replacement, target, selection)
        self.assertEqual(self.members(target)[selection["media_part"]], png_pixel(0, 0, 255))
        self.non_target_members_unchanged(source, target, {selection["media_part"]})

    def test_image_hash_and_reference_count_guards(self):
        source, replacement, selection = self.image_source()
        for change in ({"expected_sha256": "0" * 64}, {"expected_references": 2}):
            with self.subTest(change=change):
                self.rejection_preserves_output(lambda target: self.replace_image(
                    source, replacement, target, {**selection, **change}))

    def test_successful_edit_uses_and_cleans_task_local_staging(self):
        source = self.source()
        original = tempfile.TemporaryDirectory
        created = []
        def recorded_directory(*args, **kwargs):
            directory = original(*args, **kwargs)
            created.append(Path(directory.name).resolve())
            return directory
        with patch("edit_docx.tempfile.TemporaryDirectory", recorded_directory):
            self.replace_text(source, self.root / "edited.docx", {"replacements": [
                {"old": "ABC", "new": "XYZ", "count": 1}]})
        self.assertTrue(created)
        self.assertTrue(all(path.is_relative_to(self.root) for path in created), created)
        self.assertTrue(all(not path.exists() for path in created))
        self.assertEqual(self.read_docx(self.root / "edited.docx"), "合同 XYZ")


if __name__ == "__main__":
    unittest.main()
