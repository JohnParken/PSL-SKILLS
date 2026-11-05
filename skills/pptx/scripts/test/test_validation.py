"""Independent reader checks using valid packages and deliberately corrupt data."""

from __future__ import annotations
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]


import copy
import io
import stat
import struct
import unittest
import warnings
import zipfile
from unittest.mock import patch
from xml.etree import ElementTree as ET


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from pptx_core.package import ConvertedSlide, build_presentation
from pptx_core.validation import ValidationError, inspect_presentation, safe_xml, validate_presentation
from test_package import NAMESPACES, SHAPE, read_parts, slide_xml


P_NS = NAMESPACES["p"]
R_NS = NAMESPACES["r"]
CT_NS = NAMESPACES["ct"]
REL_NS = NAMESPACES["rel"]
REL_BASE = R_NS + "/"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def zipped(parts: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return output.getvalue()


def replace_xml(parts: dict[str, bytes], name: str, root: ET.Element) -> None:
    parts[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


def add_default(parts: dict[str, bytes], extension: str, content_type: str) -> None:
    root = ET.fromstring(parts["[Content_Types].xml"])
    ET.SubElement(root, "{" + CT_NS + "}Default", {"Extension": extension, "ContentType": content_type})
    replace_xml(parts, "[Content_Types].xml", root)


def workbook_parts() -> dict[str, bytes]:
    definitions = {
        "[Content_Types].xml": (
            f'<Types xmlns="{CT_NS}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
        ),
        "_rels/.rels": f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" Type="{REL_BASE}officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": (
            f'<workbook xmlns="{SHEET_NS}" xmlns:r="{R_NS}"><sheets>'
            '<sheet sheetId="1" name="Series" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" '
            f'Type="{REL_BASE}worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": (
            f'<worksheet xmlns="{SHEET_NS}"><sheetData><row r="1"><c r="A1" t="inlineStr">'
            '<is><t>Revenue</t></is></c><c r="B1"><v>42</v></c></row></sheetData></worksheet>'
        ),
    }
    return {name: payload.encode("utf-8") for name, payload in definitions.items()}


class XmlSafetyTests(unittest.TestCase):
    def test_namespace_scopes_are_used_for_mce(self) -> None:
        valid = (
            f'<root xmlns:mc="{MC_NS}" xmlns:p14="urn:office:p14" mc:Ignorable="p14">'
            '<mc:AlternateContent><mc:Choice Requires="p14"><p14:item/></mc:Choice>'
            '<mc:Fallback/></mc:AlternateContent></root>'
        ).encode()
        self.assertEqual(safe_xml(valid).tag, "root")
        invalid_documents = (
            f'<root xmlns:mc="{MC_NS}" mc:Ignorable="p14"/>',
            f'<root xmlns:mc="{MC_NS}"><child xmlns:p14="urn:p14"/><mc:Choice Requires="p14"/></root>',
            f'<root xmlns:mc="{MC_NS}"><mc:Choice/></root>',
            f'<root xmlns:mc="{MC_NS}" xmlns:p14="urn:p14"><mc:Choice Requires="p15"/></root>',
        )
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaises(ValidationError):
                safe_xml(document.encode("utf-8"))

    def test_utf8_entities_and_parse_resource_limits(self) -> None:
        invalid = [
            b'<!DOCTYPE root [<!ENTITY e "expanded">]><root>&e;</root>',
            '<root/>'.encode("utf-16"),
            b'<?xml version="1.0" encoding="iso-8859-1"?><root/>',
            b"<root>\xff</root>",
            b"<root>\x00</root>",
            b"<root>",
            b"<node>" * 129 + b"</node>" * 129,
            b"<root>" + b"<n/>" * 100000 + b"</root>",
        ]
        for data in invalid:
            with self.subTest(length=len(data)), self.assertRaises(ValidationError):
                safe_xml(data)
        self.assertEqual(safe_xml(b"\xef\xbb\xbf<root>valid UTF-8</root>").text, "valid UTF-8")


class PresentationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = build_presentation(
            [ConvertedSlide(slide_xml(), notes="第一行\n\n keep spaces "), ConvertedSlide(slide_xml(SHAPE.replace("Growth", "Forecast")))],
            title="Quarter & Forecast",
        )
        self.parts = read_parts(self.original)

    def test_readback_reports_only_verified_structural_scope(self) -> None:
        original_copy = bytes(self.original)
        with patch("builtins.open", side_effect=AssertionError("Reader attempted filesystem access")):
            report = inspect_presentation(self.original)
            summary = validate_presentation(self.original)
        self.assertEqual(self.original, original_copy)
        self.assertEqual(report["slide_count"], 2)
        self.assertEqual(report["title"], "Quarter & Forecast")
        self.assertEqual(report["slide_size"], {"width_emu": 12192000, "height_emu": 6858000})
        self.assertEqual(report["slides"][0]["text"], ["季度收入 & Growth"])
        self.assertEqual(report["slides"][1]["text"], ["季度收入 & Forecast"])
        self.assertEqual(report["slides"][0]["notes"], ["第一行", "", " keep spaces "])
        self.assertEqual(report["slides"][1]["notes"], [])
        self.assertEqual(report["slides"][0]["objects"], {"shapes": 1, "images": 0, "tables": 0, "charts": 0})
        self.assertEqual(summary["structural_validation"], "passed")
        self.assertEqual(summary["visual_validation"], "not_run")
        self.assertNotIn("slides", summary)
        self.assertNotIn("title", summary)

    def test_zip_unsafe_names_duplicates_links_and_encryption_fail(self) -> None:
        for name in ("../escape.xml", "/absolute.xml", "ppt\\outside.xml", "ppt/./slide.xml", "ppt//slide.xml", "ppt/%2e%2e.xml"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_presentation(zipped({**self.parts, name: b"<x/>"}))
        duplicate = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as archive:
                for name, payload in self.parts.items():
                    archive.writestr(name, payload)
                archive.writestr("ppt/slides/slide1.xml", self.parts["ppt/slides/slide1.xml"])
        with self.assertRaises(ValidationError):
            validate_presentation(duplicate.getvalue())
        symlink = io.BytesIO()
        with zipfile.ZipFile(symlink, "w") as archive:
            for name, payload in self.parts.items():
                archive.writestr(name, payload)
            member = zipfile.ZipInfo("link.xml")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, "target.xml")
        with self.assertRaises(ValidationError):
            validate_presentation(symlink.getvalue())
        encrypted = bytearray(self.original)
        central = encrypted.index(b"PK\x01\x02")
        struct.pack_into("<H", encrypted, central + 8, struct.unpack_from("<H", encrypted, central + 8)[0] | 1)
        with self.assertRaises(ValidationError):
            validate_presentation(bytes(encrypted))

    def test_zip_crc_oversize_and_compression_fail_before_inspection(self) -> None:
        stored = bytearray(zipped(self.parts, compression=zipfile.ZIP_STORED))
        name_length, extra_length = struct.unpack_from("<HH", stored, 26)
        stored[30 + name_length + extra_length] ^= 1
        with self.assertRaises(ValidationError):
            validate_presentation(bytes(stored))
        oversized = bytearray(self.original)
        central = oversized.index(b"PK\x01\x02")
        struct.pack_into("<I", oversized, central + 24, 64 * 1024 * 1024 + 1)
        with self.assertRaises(ValidationError):
            validate_presentation(bytes(oversized))
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(self.parts, compression=zipfile.ZIP_BZIP2))
        for payload in (b"", b"not a presentation", b"PK\x03\x04broken"):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                validate_presentation(payload)

    def test_content_types_require_coverage_and_unique_targets(self) -> None:
        with self.assertRaises(ValidationError):
            validate_presentation(zipped({**self.parts, "untyped.custom": b"data"}))
        duplicate = dict(self.parts)
        root = ET.fromstring(duplicate["[Content_Types].xml"])
        root.append(copy.deepcopy(root[0]))
        replace_xml(duplicate, "[Content_Types].xml", root)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(duplicate))
        dangling = dict(self.parts)
        root = ET.fromstring(dangling["[Content_Types].xml"])
        ET.SubElement(root, "{" + CT_NS + "}Override", {"PartName": "/missing.xml", "ContentType": "application/xml"})
        replace_xml(dangling, "[Content_Types].xml", root)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(dangling))

    def test_relationship_graph_and_owners_are_checked(self) -> None:
        names = ("ppt/slides/_rels/slide1.xml.rels", "ppt/_rels/presentation.xml.rels")
        for name in names:
            duplicate = dict(self.parts)
            root = ET.fromstring(duplicate[name])
            root.append(copy.deepcopy(root[0]))
            replace_xml(duplicate, name, root)
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_presentation(zipped(duplicate))
        ownerless = dict(self.parts)
        ownerless["ppt/_rels/missing.xml.rels"] = ownerless["ppt/_rels/presentation.xml.rels"]
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(ownerless))
        dangling = dict(self.parts)
        root = ET.fromstring(dangling[names[0]])
        root[0].set("Target", "../slideLayouts/missing.xml")
        replace_xml(dangling, names[0], root)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(dangling))
        unresolved = dict(self.parts)
        unresolved["ppt/slides/slide1.xml"] = slide_xml(SHAPE.replace(
            '<p:cNvPr id="2" name="Text"/>',
            '<p:cNvPr id="2" name="Text"><a:hlinkClick r:id="rIdMissing"/></p:cNvPr>',
        )).encode("utf-8")
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(unresolved))

    def test_slides_layouts_shapes_and_notes_have_semantic_links(self) -> None:
        duplicate = dict(self.parts)
        root = ET.fromstring(duplicate["ppt/presentation.xml"])
        entries = root.findall("p:sldIdLst/p:sldId", NAMESPACES)
        entries[1].set("id", entries[0].get("id"))
        replace_xml(duplicate, "ppt/presentation.xml", root)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(duplicate))
        no_layout = dict(self.parts)
        name = "ppt/slides/_rels/slide1.xml.rels"
        root = ET.fromstring(no_layout[name])
        root.remove(next(item for item in root if item.get("Type") == REL_BASE + "slideLayout"))
        replace_xml(no_layout, name, root)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(no_layout))
        duplicate_shape = dict(self.parts)
        duplicate_shape["ppt/slides/slide1.xml"] = slide_xml(SHAPE.replace('id="2"', 'id="1"')).encode("utf-8")
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(duplicate_shape))
        wrong_notes = dict(self.parts)
        name = "ppt/notesSlides/_rels/notesSlide1.xml.rels"
        root = ET.fromstring(wrong_notes[name])
        next(item for item in root if item.get("Type") == REL_BASE + "slide").set("Target", "../slides/slide2.xml")
        replace_xml(wrong_notes, name, root)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(wrong_notes))

    def test_macros_non_xlsx_and_unsupported_media_are_explicit_failures(self) -> None:
        macro = dict(self.parts)
        root = ET.fromstring(macro["[Content_Types].xml"])
        for item in root:
            if item.get("PartName") == "/ppt/presentation.xml":
                item.set("ContentType", "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml")
        replace_xml(macro, "[Content_Types].xml", root)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(macro))
        embedded = dict(self.parts)
        embedded["ppt/embeddings/object.bin"] = b"not xlsx"
        add_default(embedded, "bin", "application/octet-stream")
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(embedded))
        media = dict(self.parts)
        media["ppt/media/vector.svg"] = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        add_default(media, "svg", "image/svg+xml")
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(media))

    def test_theme_and_master_relationships_require_correct_roots(self) -> None:
        wrong_theme = dict(self.parts)
        wrong_theme["ppt/theme/theme1.xml"] = b"<not_a_theme/>"
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(wrong_theme))
        missing_roster = dict(self.parts)
        root = ET.fromstring(missing_roster["ppt/presentation.xml"])
        root.remove(root.find("p:sldMasterIdLst", NAMESPACES))
        replace_xml(missing_roster, "ppt/presentation.xml", root)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(missing_roster))
        wrong_image = dict(self.parts)
        name = "ppt/slides/_rels/slide1.xml.rels"
        root = ET.fromstring(wrong_image[name])
        ET.SubElement(root, "{" + REL_NS + "}Relationship", {
            "Id": "rId100", "Type": REL_BASE + "image", "Target": "slide2.xml",
        })
        replace_xml(wrong_image, name, root)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(wrong_image))

    def test_external_data_links_are_rejected_and_passive_links_allowed(self) -> None:
        name = "ppt/slides/_rels/slide1.xml.rels"
        for kind, target in (
            ("image", "https://example.org/image.png"),
            ("hyperlink", "file:///private/input"),
            ("hyperlink", "javascript:alert(1)"),
        ):
            parts = dict(self.parts)
            root = ET.fromstring(parts[name])
            ET.SubElement(root, "{" + REL_NS + "}Relationship", {
                "Id": "rId100", "Type": REL_BASE + kind, "Target": target, "TargetMode": "External",
            })
            replace_xml(parts, name, root)
            with self.subTest(kind=kind, target=target), self.assertRaises(ValidationError):
                validate_presentation(zipped(parts))
        parts = dict(self.parts)
        root = ET.fromstring(parts[name])
        ET.SubElement(root, "{" + REL_NS + "}Relationship", {
            "Id": "rId100", "Type": REL_BASE + "hyperlink",
            "Target": "https://example.org/?first=1&second=2", "TargetMode": "External",
        })
        replace_xml(parts, name, root)
        self.assertEqual(validate_presentation(zipped(parts))["slide_count"], 2)

    def test_embedded_workbooks_are_opened_and_checked_with_shared_budget(self) -> None:
        parts = dict(self.parts)
        workbook = workbook_parts()
        parts["ppt/embeddings/data.xlsx"] = zipped(workbook)
        add_default(parts, "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        valid = zipped(parts)
        self.assertEqual(validate_presentation(valid)["slide_count"], 2)
        with patch("pptx_core.validation.MAX_UNCOMPRESSED_BYTES", sum(map(len, parts.values()))):
            with self.assertRaises(ValidationError):
                validate_presentation(valid)
        del workbook["xl/worksheets/sheet1.xml"]
        parts["ppt/embeddings/data.xlsx"] = zipped(workbook)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(parts))
        workbook = workbook_parts()
        workbook["xl/worksheets/sheet1.xml"] = f'<chartsheet xmlns="{SHEET_NS}"/>'.encode()
        parts["ppt/embeddings/data.xlsx"] = zipped(workbook)
        with self.assertRaises(ValidationError):
            validate_presentation(zipped(parts))


if __name__ == "__main__":
    unittest.main()
