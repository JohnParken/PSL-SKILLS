"""Contract tests for the memory-only PresentationML package writer."""

from __future__ import annotations
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]


import io
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from xml.etree import ElementTree as ET


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from pptx_core.package import ConvertedSlide, PackageError, build_presentation


NAMESPACES = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}
REL_BASE = NAMESPACES["r"] + "/"
SHAPE = (
    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Text"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
    '<p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="1828800" cy="914400"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
    '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="2400"/>'
    '<a:t>季度收入 &amp; Growth</a:t></a:r></a:p></p:txBody></p:sp>'
)


def slide_xml(shapes: str = SHAPE) -> str:
    return (
        f'<p:sld xmlns:p="{NAMESPACES["p"]}" xmlns:a="{NAMESPACES["a"]}" xmlns:r="{NAMESPACES["r"]}">'
        '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/>'
        '<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
        f'{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )


def read_parts(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if archive.testzip() is not None:
            raise AssertionError("Generated PPTX has a CRC mismatch")
        return {name: archive.read(name) for name in archive.namelist()}


class PresentationPackageTests(unittest.TestCase):
    def test_text_notes_and_package_shell_survive_readback(self) -> None:
        source = slide_xml()
        notes = " leading space & <angle>\n\n中文备忘 📝"
        result = build_presentation(
            [ConvertedSlide(source, notes=notes), ConvertedSlide(source)],
            width=1280,
            height=720,
            title="收入 & <Forecast> 2027",
        )
        parts = read_parts(result)
        self.assertIn("[Content_Types].xml", parts)
        self.assertIn("_rels/.rels", parts)
        self.assertNotIn("content_types.xml", parts)
        self.assertNotIn("ppt/notesSlides/notesSlide2.xml", parts)
        self.assertEqual(parts["ppt/slides/slide1.xml"], source.encode("utf-8"))
        presentation = ET.fromstring(parts["ppt/presentation.xml"])
        size = presentation.find("p:sldSz", NAMESPACES)
        self.assertEqual(size.attrib, {"cx": "12192000", "cy": "6858000", "type": "custom"})
        slide_ids = presentation.findall("p:sldIdLst/p:sldId", NAMESPACES)
        self.assertEqual([item.get("id") for item in slide_ids], ["256", "257"])
        self.assertEqual([item.get("{" + NAMESPACES["r"] + "}id") for item in slide_ids], ["rId2", "rId3"])
        slide = ET.fromstring(parts["ppt/slides/slide1.xml"])
        self.assertEqual([item.text for item in slide.findall(".//a:t", NAMESPACES)], ["季度收入 & Growth"])
        notes_root = ET.fromstring(parts["ppt/notesSlides/notesSlide1.xml"])
        note_shapes = notes_root.findall(".//p:sp", NAMESPACES)
        notes_body = next(item for item in note_shapes if item.find("p:nvSpPr/p:nvPr/p:ph[@type='body']", NAMESPACES) is not None)
        paragraphs = notes_body.findall("p:txBody/a:p", NAMESPACES)
        recovered = "\n".join("".join(item.itertext()) for item in paragraphs)
        self.assertEqual(recovered, notes)
        core = ET.fromstring(parts["docProps/core.xml"])
        self.assertEqual(core.find("{http://purl.org/dc/elements/1.1/}title").text, "收入 & <Forecast> 2027")
        application = ET.fromstring(parts["docProps/app.xml"])
        app_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
        self.assertEqual(application.tag, app_ns + "Properties")
        self.assertEqual(application.find(app_ns + "Slides").text, "2")
        self.assertEqual(application.find(app_ns + "Notes").text, "1")
        types = ET.fromstring(parts["[Content_Types].xml"])
        declared = {item.get("PartName"): item.get("ContentType") for item in types.findall("ct:Override", NAMESPACES)}
        self.assertEqual(
            declared["/ppt/notesMasters/notesMaster1.xml"],
            "application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml",
        )

    def test_memory_only_repeatable_and_concurrent(self) -> None:
        slide = ConvertedSlide(slide_xml(), notes="Same task, isolated calls")
        with patch("builtins.open", side_effect=AssertionError("Unexpected filesystem access")):
            first = build_presentation([slide])
            second = build_presentation([slide])
            with ThreadPoolExecutor(max_workers=4) as executor:
                concurrent = list(executor.map(lambda _: build_presentation([slide]), range(8)))
        self.assertEqual(first, second)
        self.assertTrue(all(item == first for item in concurrent))

    def test_native_chart_parts_and_workbook_are_preserved(self) -> None:
        workbook_stream = io.BytesIO()
        with zipfile.ZipFile(workbook_stream, "w") as workbook:
            workbook.writestr("xl/worksheets/sheet1.xml", '<worksheet><sheetData><row r="1"><c r="A1"><v>42</v></c></row></sheetData></worksheet>')
        workbook_bytes = workbook_stream.getvalue()
        chart_xml = (
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
            f'xmlns:r="{NAMESPACES["r"]}"><c:chart><c:plotArea/></c:chart><c:externalData r:id="rId1"/></c:chartSpace>'
        ).encode("utf-8")
        chart_relationships = (
            f'<Relationships xmlns="{NAMESPACES["rel"]}"><Relationship Id="rId1" '
            f'Type="{REL_BASE}package" Target="../embeddings/data.xlsx"/></Relationships>'
        ).encode("utf-8")
        graphic = (
            '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="2" name="Chart"/>'
            '<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
            '<p:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rId2"/>'
            '</a:graphicData></a:graphic></p:graphicFrame>'
        )
        converted = (
            slide_xml(graphic),
            {},
            [{"id": "rId2", "type": REL_BASE + "chart", "target": "../charts/chart101.xml"}],
            [],
            {
                "ppt/charts/chart101.xml": chart_xml,
                "ppt/charts/_rels/chart101.xml.rels": chart_relationships,
                "ppt/embeddings/data.xlsx": workbook_bytes,
            },
            {"ppt/charts/chart101.xml": "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"},
        )
        parts = read_parts(build_presentation([ConvertedSlide.from_conversion(converted, notes="Editable data")]))
        self.assertEqual(parts["ppt/charts/chart101.xml"], chart_xml)
        self.assertEqual(parts["ppt/embeddings/data.xlsx"], workbook_bytes)
        with zipfile.ZipFile(io.BytesIO(parts["ppt/embeddings/data.xlsx"])) as workbook:
            self.assertIn(b"<v>42</v>", workbook.read("xl/worksheets/sheet1.xml"))
        relationships = ET.fromstring(parts["ppt/slides/_rels/slide1.xml.rels"])
        identifiers = [item.get("Id") for item in relationships]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(set(identifiers), {"rId1", "rId2", "rId3"})

    def test_hyperlinks_remain_passive_relationships(self) -> None:
        shape = SHAPE.replace('<p:cNvPr id="2" name="Text"/>', '<p:cNvPr id="2" name="Text"><a:hlinkClick r:id="rId2"/></p:cNvPr>')
        relation = {"id": "rId2", "type": REL_BASE + "hyperlink", "target": "https://example.org/?a=1&b=2", "target_mode": "External"}
        parts = read_parts(build_presentation([ConvertedSlide(slide_xml(shape), relationships=[relation])]))
        relationships = ET.fromstring(parts["ppt/slides/_rels/slide1.xml.rels"])
        hyperlink = next(item for item in relationships if item.get("Id") == "rId2")
        self.assertEqual(hyperlink.get("Target"), relation["target"])
        self.assertEqual(hyperlink.get("TargetMode"), "External")

    def test_missing_and_duplicate_relationships_fail(self) -> None:
        examples = [
            [{"id": "rId1", "type": REL_BASE + "image", "target": "../media/missing.png"}],
            [{"id": "rId2", "type": REL_BASE + "image", "target": "../media/missing.png"}],
            [{"id": "rId2", "type": REL_BASE + "slide", "target": "slide2.xml"}],
            [{"id": "rId2", "type": REL_BASE + "image", "target": "https://example.org/a.png", "target_mode": "External"}],
            [{"id": "rId2", "type": REL_BASE + "hyperlink", "target": "file:///etc/passwd", "target_mode": "External"}],
        ]
        for relationships in examples:
            with self.subTest(relationships=relationships), self.assertRaises(PackageError):
                build_presentation([ConvertedSlide(slide_xml(), relationships=relationships)])
        shape = SHAPE.replace('<p:cNvPr id="2" name="Text"/>', '<p:cNvPr id="2" name="Text"><a:hlinkClick r:id="rId99"/></p:cNvPr>')
        with self.assertRaises(PackageError):
            build_presentation([ConvertedSlide(slide_xml(shape))])

    def test_collisions_and_unsafe_part_names_fail(self) -> None:
        for parts in (
            {"ppt/charts/../../outside.xml": b"<root/>"},
            {"ppt/presentation.xml": b"<root/>"},
            {"ppt/embeddings/macro.bin": b"payload"},
        ):
            with self.subTest(parts=parts), self.assertRaises(PackageError):
                build_presentation([ConvertedSlide(slide_xml(), package_parts=parts)])
        with self.assertRaises(PackageError):
            build_presentation([
                ConvertedSlide(slide_xml(), media={"shared.png": b"first"}),
                ConvertedSlide(slide_xml(), media={"shared.png": b"second"}),
            ])
        with self.assertRaises(PackageError):
            build_presentation([
                ConvertedSlide(slide_xml(), media={"Shared.png": b"same"}),
                ConvertedSlide(slide_xml(), media={"shared.png": b"same"}),
            ])

    def test_invalid_slides_dimensions_and_xml_fail(self) -> None:
        for width in (True, float("inf"), float("nan"), 0, 95, 5377, "1280"):
            with self.subTest(width=width), self.assertRaises(PackageError):
                build_presentation([ConvertedSlide(slide_xml())], width=width)
        with self.assertRaises(PackageError):
            build_presentation([])
        with self.assertRaises(PackageError):
            build_presentation([ConvertedSlide(slide_xml(SHAPE + SHAPE))])
        with self.assertRaises(PackageError):
            build_presentation([ConvertedSlide(slide_xml(), notes="bad\x01text")])
        with self.assertRaises(PackageError):
            build_presentation([ConvertedSlide('<!DOCTYPE x [<!ENTITY x "bad">]>' + slide_xml())])
        with self.assertRaises(PackageError):
            build_presentation([ConvertedSlide("<not_a_slide/>")])

    def test_no_notes_has_no_notes_parts(self) -> None:
        parts = read_parts(build_presentation([ConvertedSlide(slide_xml())]))
        self.assertFalse(any(name.startswith(("ppt/notesSlides/", "ppt/notesMasters/")) for name in parts))
        presentation = ET.fromstring(parts["ppt/presentation.xml"])
        self.assertIsNone(presentation.find("p:notesMasterIdLst", NAMESPACES))


if __name__ == "__main__":
    unittest.main()
