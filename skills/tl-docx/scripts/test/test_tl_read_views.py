"""Regression tests for report revision views and raw XML part reading."""

from pathlib import Path
import sys
import unittest

from docx.oxml import parse_xml


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from tl_read import report
from tl_read_structure import structure


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class MemoryPackage:
    """Small in-memory package exposing the tl-docx package reader contract."""

    def __init__(self, parts, relationships=None, types=None):
        self.main = "word/document.xml"
        self.names = set(parts)
        self.types = {name: "application/xml" for name in self.names}
        self.types.update(types or {})
        self._parts = {
            name: value.encode("utf-8") if isinstance(value, str) else value
            for name, value in parts.items()
        }
        self._relationships = relationships or {}

    def xml(self, name):
        return parse_xml(self.read(name))

    def read(self, name):
        return self._parts[name]

    def relations(self, source):
        return {
            relation_id: relation
            for (part, relation_id), relation in self._relationships.items()
            if part == source
        }


def _revision_package():
    return MemoryPackage({
        "word/document.xml": (
            '<w:document xmlns:w="' + WORD_NS + '"><w:body>'
            '<w:ins><w:p><w:r><w:t>new block</w:t></w:r></w:p></w:ins>'
            '<w:del><w:p><w:r><w:delText>old block</w:delText></w:r></w:p></w:del>'
            '<w:p><w:r><w:t>run </w:t><w:ins><w:r><w:t>insert</w:t></w:r></w:ins>'
            '<w:del><w:r><w:delText>delete</w:delText></w:r></w:del></w:r></w:p>'
            '<w:tbl><w:ins><w:tr><w:tc><w:p><w:r><w:t>new row</w:t></w:r>'
            '</w:p></w:tc></w:tr></w:ins><w:del><w:tr><w:tc><w:p><w:r>'
            '<w:delText>old row</w:delText></w:r></w:p></w:tc></w:tr></w:del></w:tbl>'
            '<w:p><w:ins><w:r><w:t>inserted run</w:t></w:r></w:ins>'
            '<w:del><w:r><w:delText>deleted run</w:delText></w:r></w:del></w:p>'
            '</w:body></w:document>'
        ),
        "word/header1.xml": (
            '<w:hdr xmlns:w="' + WORD_NS + '"><w:p><w:r><w:t>header</w:t>'
            '</w:r></w:p></w:hdr>'
        ),
        "word/styles.xml": '<w:styles xmlns:w="' + WORD_NS + '"/>',
        "word/numbering.xml": '<w:numbering xmlns:w="' + WORD_NS + '"/>',
        "word/charts/chart1.xml": '<chart xmlns="urn:chart"/>',
        "docProps/core.xml": '<core xmlns="urn:core"><title>meta</title></core>',
    })


class ReadViewTests(unittest.TestCase):
    def test_legacy_default_is_identical_to_explicit_none(self):
        package = _revision_package()
        self.assertEqual(report(package), report(
            package, revisions=None, include_related=True
        ))

    def test_report_selects_block_run_and_table_revisions(self):
        package = _revision_package()
        expected = {
            "accepted": (["new block", "run insert", "inserted run"], ["new row"]),
            "original": (["old block", "run delete", "deleted run"], ["old row"]),
            "all": (
                ["new block", "old block", "run insertdelete",
                 "inserted rundeleted run"],
                ["new row", "old row"],
            ),
        }
        for view, (paragraph_text, row_text) in expected.items():
            with self.subTest(view=view):
                records, _, coverage = report(
                    package, revisions=view, include_related=False
                )
                paragraphs = [
                    item["text"] for item in records
                    if item["type"] == "paragraph" and "table_index" not in item
                ]
                rows = [
                    cell["text"]
                    for item in records if item["type"] == "table_row"
                    for cell in item["cells"]
                ]
                self.assertEqual(paragraphs, paragraph_text)
                self.assertEqual(rows, row_text)
                self.assertEqual(coverage["revisions"], view)
        records, _, _ = report(package, revisions="all", include_related=False)
        paragraph = next(
            item for item in records
            if item["type"] == "paragraph" and item["text"].startswith("run ")
        )
        self.assertEqual(paragraph["revisions"][0]["path"].split("/")[-1], "w:ins[1]")

    def test_report_flags_unresolved_revision_metadata_and_nested_conflict(self):
        document = (
            '<w:document xmlns:w="' + WORD_NS + '"><w:body><w:tbl><w:tr>'
            '<w:trPr><w:del w:id="1"/></w:trPr><w:tc><w:p><w:r><w:ins>'
            '<w:del><w:r><w:delText>conflict</w:delText></w:r></w:del>'
            '</w:ins></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:pPr>'
            '<w:pPrChange w:id="2"><w:pPr/></w:pPrChange></w:pPr></w:p>'
            '</w:body></w:document>'
        )
        _, issues, coverage = report(
            MemoryPackage({"word/document.xml": document}),
            revisions="accepted", include_related=False,
        )
        codes = {issue["code"] for issue in issues}
        self.assertIn("revision_property_change_unresolved", codes)
        self.assertIn("revision_row_flag_unresolved", codes)
        self.assertIn("revision_nested_view_conflict", codes)
        self.assertEqual(coverage["status"], "partial")

    def test_related_filter_skips_stories_and_metadata_records(self):
        package = _revision_package()
        records, _, coverage = report(package, include_related=False)
        self.assertFalse(any(item["part"] == "word/header1.xml" for item in records))
        self.assertFalse(any(item["type"] == "metadata" for item in records))
        self.assertFalse(any(item["type"] == "numbering_definition" for item in records))
        self.assertEqual(coverage["availability"]["headers"], "excluded")
        self.assertEqual(coverage["availability"]["core_metadata"], "excluded")

    def test_structure_preserves_unknown_mixed_content_and_namespaces(self):
        xml = (
            '<q:root xmlns:q="urn:local" xmlns:a="urn:attrs" a:flag="yes">'
            'before<a:item a:key="value"/>after<!--note-->tail<?step go?>'
            '<![CDATA[<raw>]]>end</q:root>'
        )
        package = MemoryPackage({
            "word/document.xml": '<w:document xmlns:w="' + WORD_NS + '"><w:body/></w:document>',
            "word/charts/chart1.xml": xml,
        })
        records, issues, coverage = structure(
            package, parts=("word/charts/chart1.xml",)
        )
        self.assertEqual(issues, [])
        self.assertEqual(coverage["parts"], 1)
        record = records[0]
        self.assertEqual(record["part"], "word/charts/chart1.xml")
        self.assertEqual(record["path"], "/")
        root = record["root"]
        self.assertEqual(root["tag"], "{urn:local}root")
        self.assertEqual(root["prefix"], "q")
        self.assertEqual(root["attributes"]["{urn:attrs}flag"], "yes")
        self.assertEqual(root["namespace_declarations"][0], {
            "prefix": "q", "namespace": "urn:local",
        })
        self.assertEqual(root["text"], "before")
        self.assertEqual(root["children"][0]["tail"], "after")
        self.assertEqual(root["children"][1]["type"], "comment")
        self.assertEqual(root["children"][1]["tail"], "tail")
        self.assertEqual(root["children"][2]["target"], "step")
        self.assertEqual(root["children"][3]["type"], "cdata")
        self.assertEqual(root["children"][3]["text"], "<raw>")
        self.assertEqual(root["children"][3]["tail"], "end")

    def test_structure_marks_excluded_revision_containers(self):
        package = _revision_package()
        accepted, _, _ = structure(
            package, revisions="accepted", parts=("word/document.xml",)
        )
        original, _, _ = structure(
            package, revisions="original", parts=("word/document.xml",)
        )
        body = accepted[0]["root"]["children"][0]
        self.assertTrue(any(
            node.get("name") == "del" and node.get("included") is False
            and node["children"] == [] for node in body["children"]
        ))
        body = original[0]["root"]["children"][0]
        self.assertTrue(any(
            node.get("name") == "ins" and node.get("included") is False
            and node["children"] == [] for node in body["children"]
        ))

    def test_structure_related_parts_resolve_and_explicit_parts_select(self):
        relationships = {
            ("word/document.xml", "header"): {
                "type": REL_NS + "/header", "target": "word/header1.xml",
                "external": False,
            },
            ("word/document.xml", "styles"): {
                "type": REL_NS + "/styles", "target": "word/styles.xml",
                "external": False,
            },
            ("word/document.xml", "numbering"): {
                "type": REL_NS + "/numbering", "target": "word/numbering.xml",
                "external": False,
            },
            ("word/document.xml", "external"): {
                "type": REL_NS + "/header", "target": "https://example.test/header.xml",
                "external": True,
            },
        }
        package = _revision_package()
        package._relationships = relationships
        base, _, _ = structure(package)
        self.assertEqual(
            {item["part"] for item in base},
            {"word/document.xml", "word/styles.xml", "word/numbering.xml"},
        )
        related, _, _ = structure(package, include_related=True)
        self.assertIn("word/header1.xml", {item["part"] for item in related})
        self.assertNotIn("https://example.test/header.xml", {
            item["part"] for item in related
        })
        explicit, _, _ = structure(
            package, parts=("word/charts/chart1.xml",)
        )
        self.assertEqual([item["part"] for item in explicit], ["word/charts/chart1.xml"])

    def test_structure_rejects_missing_non_xml_and_dtd_parts(self):
        package = _revision_package()
        package.names.add("word/media/image1.png")
        package.types["word/media/image1.png"] = "image/png"
        package._parts["word/media/image1.png"] = b"not xml"
        for part in ("missing.xml", "word/media/image1.png"):
            with self.subTest(part=part), self.assertRaises(ValueError):
                structure(package, parts=(part,))
        package.names.add("word/custom.xml")
        package.types["word/custom.xml"] = "application/xml"
        package._parts["word/custom.xml"] = b"<!DOCTYPE x [<!ENTITY e 'x'>]><x>&e;</x>"
        with self.assertRaises(ValueError):
            structure(package, parts=("word/custom.xml",))


if __name__ == "__main__":
    unittest.main()
