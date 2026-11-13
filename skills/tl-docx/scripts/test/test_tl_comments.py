"""Exercise the thread comment API against bounded in-memory OPC packages."""

from copy import deepcopy
from io import BytesIO
import sys
from pathlib import Path
import unittest
from zipfile import ZipFile

from docx import Document
from docx.oxml import OxmlElement
from docx.opc.oxml import serialize_part_xml
from docx.oxml.ns import qn

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from tl_comments import manage_comments
from tl_package import TYPES, Package


def members(data):
    """Read package member payloads without extracting them to disk."""
    with ZipFile(BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def with_extra_member(data):
    """Add a stable opaque member used to check package preservation."""
    output = BytesIO()
    with ZipFile(BytesIO(data)) as source, ZipFile(output, "w") as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr("custom/preserve.bin", b"\x00opaque\xff")
    return output.getvalue()


def base_package(*, classic=False):
    """Build a two-paragraph document with a useful run boundary."""
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("budget ")
    paragraph.add_run("review")
    document.add_paragraph("second paragraph")
    if classic:
        document.add_comment(paragraph.runs[0], "classic note", "Reviewer", "RV")
    stream = BytesIO()
    document.save(stream)
    return Package(with_extra_member(stream.getvalue()))


def add_spec(index=0, expected="budget review", anchor="budget review", text="Check the cap."):
    return {
        "action": "add",
        "paragraph_index": index,
        "expected_text": expected,
        "anchor_text": anchor,
        "text": text,
        "author": "Reviewer",
        "initials": "RV",
    }


class ThreadCommentTests(unittest.TestCase):
    """Verify modern threads, classic bounds and target-part preservation."""

    def test_modern_add_reply_update_delete_roundtrip(self):
        package = base_package()
        root_bytes, root_result = manage_comments(package, add_spec())
        self.assertEqual("modern", root_result["mode"])
        self.assertEqual([], root_result["diagnostics"])
        self.assertEqual(0, root_result["comment_id"])
        self.assertEqual({
            "word/document.xml", "[Content_Types].xml", "word/_rels/document.xml.rels",
            "word/comments.xml", "word/commentsExtended.xml", "word/commentsIds.xml",
            "word/commentsExtensible.xml",
        }, set(root_result["changed_parts"]))

        root = Package(root_bytes)
        reply_bytes, reply_result = manage_comments(root, {
            "action": "reply", "id": 0, "expected_text": "Check the cap.",
            "text": "Please attach the source.", "author": "Owner",
        })
        self.assertEqual(1, reply_result["comment_id"])
        self.assertEqual(0, next(item for item in reply_result["comments"] if item["id"] == 1)["parent_id"])
        sibling_bytes, sibling_result = manage_comments(Package(reply_bytes), {
            "action": "reply", "id": 0, "expected_text": "Check the cap.",
            "text": "I will attach it.", "author": "Owner 2",
        })
        self.assertEqual(2, sibling_result["comment_id"])
        self.assertTrue(all(item["parent_id"] == 0 for item in sibling_result["comments"][1:]))
        reply = Package(sibling_bytes)

        updated_bytes, updated = manage_comments(reply, {
            "action": "update", "id": 1, "expected_text": "Please attach the source.",
            "text": "Source attached.",
        })
        self.assertEqual("Source attached.", next(item for item in updated["comments"] if item["id"] == 1)["text"])
        updated_package = Package(updated_bytes)

        without_reply, deleted_reply = manage_comments(updated_package, {
            "action": "delete", "id": 1, "expected_text": "Source attached.",
        })
        self.assertEqual([0, 2], [item["id"] for item in deleted_reply["comments"]])
        without_sibling, deleted_sibling = manage_comments(Package(without_reply), {
            "action": "delete", "id": 2, "expected_text": "I will attach it.",
        })
        self.assertEqual([0], [item["id"] for item in deleted_sibling["comments"]])
        without_root, deleted_root = manage_comments(Package(without_sibling), {
            "action": "delete", "id": 0, "expected_text": "Check the cap.",
        })
        self.assertEqual("modern", deleted_root["mode"])
        self.assertEqual([], deleted_root["comments"])
        self.assertEqual([], deleted_root["diagnostics"])
        self.assertEqual([], manage_comments(Package(without_root), {"action": "list"})[1]["comments"])

    def test_add_root_to_existing_modern_thread_and_preserve_other_parts(self):
        original = base_package()
        first, _ = manage_comments(original, add_spec())
        modern = Package(first)
        before = members(first)
        second, result = manage_comments(modern, add_spec(
            index=1, expected="second paragraph", anchor="second paragraph", text="Second note."))
        after = members(second)
        self.assertEqual([0, 1], [item["id"] for item in result["comments"]])
        self.assertEqual({
            "word/document.xml", "word/comments.xml", "word/commentsExtended.xml",
            "word/commentsIds.xml", "word/commentsExtensible.xml",
        }, set(result["changed_parts"]))
        for name, payload in before.items():
            if name not in result["changed_parts"]:
                self.assertEqual(payload, after[name], name)
        self.assertEqual(b"\x00opaque\xff", after["custom/preserve.bin"])

    def test_classic_simple_update_delete_and_no_auto_upgrade(self):
        package = base_package(classic=True)
        listed = manage_comments(package, {"action": "list"})[1]
        self.assertEqual("classic", listed["mode"])
        self.assertTrue(listed["mutable"], listed["diagnostics"])
        self.assertEqual("classic note", listed["comments"][0]["text"])
        with self.assertRaisesRegex(ValueError, "classic comments"):
            manage_comments(package, add_spec())
        with self.assertRaisesRegex(ValueError, "complete modern"):
            manage_comments(package, {
                "action": "reply", "id": 0, "expected_text": "classic note",
                "text": "Reply", "author": "Owner",
            })
        updated, result = manage_comments(package, {
            "action": "update", "id": 0, "expected_text": "classic note", "text": "revised",
        })
        self.assertEqual("classic", result["mode"])
        deleted, result = manage_comments(Package(updated), {
            "action": "delete", "id": 0, "expected_text": "revised",
        })
        self.assertEqual([], result["comments"])
        self.assertEqual("classic", result["mode"])
        self.assertEqual(".docx", Package(deleted).extension)

    def test_incomplete_modern_metadata_is_listed_with_diagnostic_and_rejected(self):
        package = base_package()
        modern, _ = manage_comments(package, add_spec())
        source = Package(modern)
        member_map = members(modern)
        member_map.pop("word/commentsIds.xml")
        broken = BytesIO()
        with ZipFile(broken, "w") as archive:
            for name, payload in member_map.items():
                archive.writestr(name, payload)
        incomplete = Package(broken.getvalue())
        listed = manage_comments(incomplete, {"action": "list"})[1]
        self.assertEqual("modern", listed["mode"])
        self.assertFalse(listed["mutable"])
        self.assertTrue(any("four parts" in item for item in listed["diagnostics"]))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            manage_comments(incomplete, {
                "action": "update", "id": 0, "expected_text": "Check the cap.", "text": "No",
            })
        self.assertEqual(".docx", source.extension)

    def test_incomplete_anchor_is_queryable_but_not_mutable(self):
        package = base_package()
        modern, _ = manage_comments(package, add_spec())
        source = Package(modern)
        main = deepcopy(source.xml(source.main))
        end = next(node for node in main.iter() if node.tag.endswith("commentRangeEnd"))
        end.getparent().remove(end)
        broken = Package(source.overlay({source.main: serialize_part_xml(main)}))
        listed = manage_comments(broken, {"action": "list"})[1]
        self.assertFalse(listed["mutable"])
        self.assertIsNone(listed["comments"][0]["paragraph_index"])
        self.assertTrue(any("incomplete anchors" in item for item in listed["diagnostics"]))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            manage_comments(broken, {
                "action": "update", "id": 0, "expected_text": "Check the cap.", "text": "No",
            })

    def test_anchor_must_cover_complete_runs_without_splitting(self):
        package = base_package()
        with self.assertRaisesRegex(ValueError, "complete consecutive runs"):
            manage_comments(package, add_spec(anchor="budget"))
        self.assertEqual("none", manage_comments(package, {"action": "list"})[1]["mode"])

    def test_cross_paragraph_bookmark_blocks_add(self):
        document = Document()
        first = document.add_paragraph("before")
        target = document.add_paragraph("target text")
        last = document.add_paragraph("after")
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), "7")
        start.set(qn("w:name"), "active")
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), "7")
        first._p.insert(1, start)
        last._p.append(end)
        stream = BytesIO()
        document.save(stream)
        package = Package(with_extra_member(stream.getvalue()))
        with self.assertRaisesRegex(ValueError, "Active ranges or fields"):
            manage_comments(package, add_spec(index=1, expected="target text", anchor="target text"))

    def test_completed_bookmark_before_target_does_not_block_add(self):
        document = Document()
        earlier = document.add_paragraph("bookmarked")
        target = document.add_paragraph("target text")
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), "8")
        start.set(qn("w:name"), "complete")
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), "8")
        earlier._p.insert(1, start)
        earlier._p.insert(3, end)
        stream = BytesIO()
        document.save(stream)
        package = Package(with_extra_member(stream.getvalue()))
        _, result = manage_comments(package, add_spec(index=1, expected="target text", anchor="target text"))
        self.assertEqual("modern", result["mode"])

    def test_dotx_uses_the_same_in_memory_comment_path(self):
        package = base_package()
        content_types = deepcopy(package.xml(TYPES))
        main_type = next(node for node in content_types if node.get("PartName") == "/" + package.main)
        main_type.set("ContentType", "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml")
        dotx = Package(package.overlay({TYPES: serialize_part_xml(content_types)}))
        self.assertEqual(".dotx", dotx.extension)
        edited, result = manage_comments(dotx, add_spec())
        self.assertEqual("modern", result["mode"])
        self.assertEqual(".dotx", Package(edited).extension)


if __name__ == "__main__":
    unittest.main()
