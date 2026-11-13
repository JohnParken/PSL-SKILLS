"""Synthetic in-memory regression tests for the tl-docx creation API."""

from __future__ import annotations

import base64
import io
import sys
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from docx import Document
from docx.shared import Cm

from tl_create import build_document


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
W = f"{{{WORD_NS}}}"
WP = f"{{{DRAWING_NS}}}"


def package_document(spec, read_image=lambda _path: b"") -> dict[str, bytes]:
    document = build_document(spec, read_image)
    stream = io.BytesIO()
    document.save(stream)
    stream.seek(0)
    with zipfile.ZipFile(stream) as package:
        return {name: package.read(name) for name in package.namelist()}


def xml_part(parts: dict[str, bytes], name: str):
    return ElementTree.fromstring(parts[name])


def elements(root, name: str):
    return list(root.iter(W + name))


class BuildDocumentTests(unittest.TestCase):
    def test_official_defaults_and_text_round_trip(self):
        parts = package_document(
            {
                "blocks": [
                    {"type": "title", "text": "年度总结"},
                    {"type": "subtitle", "text": "年度目标"},
                    {"type": "heading", "level": 1, "text": "年度报告"},
                    {"type": "paragraph", "text": "正文 & <内容>"},
                    {"type": "page_break"},
                ]
            }
        )
        document = Document(io.BytesIO(_make_package(parts)))
        self.assertEqual(
            [paragraph.text for paragraph in document.paragraphs[:4]],
            ["年度总结", "年度目标", "年度报告", "正文 & <内容>"],
        )

        styles = xml_part(parts, "word/styles.xml")
        normal_style = next(
            style for style in elements(styles, "style") if style.get(W + "styleId") == "Normal"
        )
        normal_fonts = normal_style.find(f"{W}rPr/{W}rFonts")
        self.assertEqual(normal_fonts.get(W + "eastAsia"), "仿宋")
        self.assertEqual(normal_fonts.get(W + "ascii"), "Times New Roman")

        document_xml = xml_part(parts, "word/document.xml")
        body_paragraphs = elements(document_xml, "p")
        title_fonts = body_paragraphs[0].find(f"{W}r/{W}rPr/{W}rFonts")
        title_size = body_paragraphs[0].find(f"{W}r/{W}rPr/{W}sz")
        subtitle_size = body_paragraphs[1].find(f"{W}r/{W}rPr/{W}sz")
        self.assertEqual(title_fonts.get(W + "eastAsia"), "方正小标宋简体")
        self.assertEqual(title_size.get(W + "val"), "44")
        self.assertEqual(subtitle_size.get(W + "val"), "28")
        body_props = body_paragraphs[3].find(W + "pPr")
        self.assertEqual(body_props.find(W + "spacing").get(W + "line"), "560")
        self.assertEqual(body_props.find(W + "ind").get(W + "firstLine"), "640")
        self.assertIsNotNone(body_paragraphs[2].find(f"{W}pPr/{W}keepNext"))
        self.assertIsNone(body_paragraphs[2].find(f"{W}pPr/{W}pageBreakBefore"))
        page_breaks = [node for node in elements(document_xml, "br") if node.get(W + "type") == "page"]
        self.assertEqual(len(page_breaks), 1)

        section_properties = document_xml.find(f"{W}body/{W}sectPr")
        page_size = section_properties.find(W + "pgSz")
        page_margins = section_properties.find(W + "pgMar")
        self.assertEqual(page_size.get(W + "w"), "11906")
        self.assertEqual(page_size.get(W + "h"), "16838")
        self.assertEqual(page_margins.get(W + "top"), "1440")
        self.assertEqual(page_margins.get(W + "left"), "1803")
        self.assertIn("正文 &amp; &lt;内容&gt;".encode("utf-8"), parts["word/document.xml"])

    def test_runs_fields_bookmarks_toc_and_tabs(self):
        parts = package_document(
            {
                "blocks": [
                    {"type": "heading", "level": 2, "text": "章节 & 内容", "bookmark": "chapter"},
                    {
                        "type": "paragraph",
                        "runs": [
                            {"text": "加粗", "bold": True, "color": "008000", "size_pt": 13},
                            {"tab": True},
                            {"field": "REF chapter", "display": "章节 & 内容"},
                            {"text": " 页 "},
                            {"field": "PAGE", "display": "1"},
                            {"field": "NUMPAGES"},
                            {"field": "PAGEREF chapter", "display": "1"},
                        ],
                        "tab_stops": [{"position_cm": 4, "alignment": "right", "leader": "dots"}],
                    },
                    {"type": "toc", "levels": 2, "title": "目录"},
                ]
            }
        )
        reloaded = Document(io.BytesIO(_make_package(parts)))
        self.assertEqual(reloaded.paragraphs[1].text, "加粗\t章节 & 内容 页 11")
        document_xml = xml_part(parts, "word/document.xml")
        instructions = [node.text for node in elements(document_xml, "instrText")]
        self.assertIn(" REF chapter ", instructions)
        self.assertIn(" PAGE ", instructions)
        self.assertIn(" NUMPAGES ", instructions)
        self.assertIn(" PAGEREF chapter ", instructions)
        self.assertIn(' TOC \\o "1-2" \\h \\z \\u ', instructions)
        self.assertEqual(len(elements(document_xml, "bookmarkStart")), 1)
        self.assertEqual(len(elements(document_xml, "bookmarkEnd")), 1)
        self.assertEqual(len(elements(document_xml, "tab")), 2)
        self.assertEqual(len(elements(document_xml, "tabs")), 1)
        self.assertIn(b"chapter", parts["word/document.xml"])
        self.assertIn("Heading 2", [p.style.name for p in Document(io.BytesIO(_make_package(parts))).paragraphs])

    def test_native_lists_reset_between_blocks_and_table_merges(self):
        parts = package_document(
            {
                "blocks": [
                    {"type": "list", "ordered": True, "items": ["一", "二"]},
                    {"type": "list", "ordered": True, "items": ["另起一组"]},
                    {"type": "list", "ordered": False, "items": ["项目"]},
                    {
                        "type": "table",
                        "header": True,
                        "rows": [["名称", "状态"], ["左侧", None], [None, None]],
                        "col_widths_cm": [3, 4],
                        "merges": [{"row": 1, "col": 0, "rowspan": 2, "colspan": 2}],
                    },
                    {"type": "table", "header": True, "repeat_header": False, "rows": [["不重复"]]},
                ]
            }
        )
        document_xml = xml_part(parts, "word/document.xml")
        list_paragraphs = elements(document_xml, "p")[:4]
        ids = [paragraph.find(f"{W}pPr/{W}numPr/{W}numId").get(W + "val") for paragraph in list_paragraphs]
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[0], ids[2])
        self.assertNotEqual(ids[2], ids[3])

        table_header = elements(document_xml, "tr")[0]
        self.assertIsNotNone(table_header.find(f"{W}trPr/{W}tblHeader"))
        self.assertIsNone(elements(document_xml, "tr")[3].find(f"{W}trPr/{W}tblHeader"))
        merged_cell = elements(document_xml, "tr")[1].find(f"{W}tc/{W}tcPr/{W}gridSpan")
        self.assertEqual(merged_cell.get(W + "val"), "2")
        self.assertGreaterEqual(len(elements(document_xml, "vMerge")), 2)
        first_table = elements(document_xml, "tbl")[0]
        grid_widths = [int(column.get(W + "w")) for column in elements(first_table, "gridCol")]
        self.assertEqual(grid_widths, [1701, 2268])
        reloaded = Document(io.BytesIO(_make_package(parts)))
        self.assertEqual(len(reloaded.tables), 2)
        self.assertEqual(reloaded.tables[0].cell(1, 0).text, "左侧")

    def test_sections_metadata_and_section_footer(self):
        parts = package_document(
            {
                "metadata": {"title": "内存文档", "author": "测试者"},
                "header": "文档页眉",
                "blocks": [
                    {"type": "paragraph", "text": "第一节"},
                    {
                        "type": "section_break",
                        "orientation": "landscape",
                        "start_type": "continuous",
                        "page_start": 3,
                        "margins_cm": {"top": 1.5},
                        "footer": [{"text": "页码 "}, {"field": "PAGE", "display": "3"}],
                    },
                    {"type": "paragraph", "text": "第二节"},
                ],
            }
        )
        reloaded = Document(io.BytesIO(_make_package(parts)))
        self.assertEqual(reloaded.core_properties.title, "内存文档")
        self.assertEqual(reloaded.core_properties.author, "测试者")
        self.assertEqual(len(reloaded.sections), 2)
        self.assertGreater(reloaded.sections[1].page_width, reloaded.sections[1].page_height)
        self.assertAlmostEqual(reloaded.sections[1].top_margin.cm, 1.5, places=2)

        document_xml = xml_part(parts, "word/document.xml")
        page_numbering = elements(document_xml, "pgNumType")
        self.assertEqual(page_numbering[0].get(W + "start"), "3")
        self.assertIn("continuous", [node.get(W + "val") for node in elements(document_xml, "type")])
        header_xml = xml_part(parts, "word/header1.xml")
        self.assertEqual("".join(node.text or "" for node in elements(header_xml, "t")), "文档页眉")
        footer_xml = xml_part(parts, "word/footer1.xml")
        self.assertIn(" PAGE ", [node.text for node in elements(footer_xml, "instrText")])

    def test_image_bytes_only_come_from_callback(self):
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
            "hZkAAAAASUVORK5CYII="
        )
        requests = []

        def read_image(path):
            requests.append(path)
            return image

        parts = package_document(
            {"blocks": [{"type": "image", "path": "figures/chart.png", "width_cm": 2, "alt": "图表"}]},
            read_image,
        )
        self.assertEqual(requests, ["figures/chart.png"])
        document_xml = xml_part(parts, "word/document.xml")
        doc_properties = list(document_xml.iter(WP + "docPr"))
        self.assertEqual(doc_properties[0].get("descr"), "图表")
        self.assertIn("word/media/image1.png", parts)

    def test_invalid_specs_fail_before_reading_images(self):
        image_reads = []

        def read_image(path):
            image_reads.append(path)
            return b"unused"

        invalid_specs = [
            {"blocks": [{"type": "paragraph", "text": "正文", "surprise": True}]},
            {"blocks": [{"type": "heading", "level": 10, "text": "标题"}]},
            {"blocks": [{"type": "paragraph", "runs": [{"field": 'REF bad"/><w:evil'}]}]},
            {"blocks": [{"type": "image", "path": "a.png"}, {"type": "paragraph", "text": "bad\ntext"}]},
            {
                "blocks": [
                    {"type": "table", "rows": [["A", ""], ["", ""]], "merges": [
                        {"row": 0, "col": 0, "rowspan": 2, "colspan": 2},
                        {"row": 1, "col": 0, "rowspan": 1, "colspan": 2},
                    ]}
                ]
            },
        ]
        for spec in invalid_specs:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                build_document(spec, read_image)
        self.assertEqual(image_reads, [])

    def test_ref_must_target_a_declared_bookmark(self):
        with self.assertRaisesRegex(ValueError, "unknown bookmark"):
            build_document(
                {"blocks": [{"type": "paragraph", "runs": [{"field": "PAGEREF absent"}]}]},
                lambda _path: b"",
            )

    def test_named_themes_and_top_level_page_overrides(self):
        expected_fonts = {"business": "微软雅黑", "academic": "宋体"}
        for theme, expected_font in expected_fonts.items():
            with self.subTest(theme=theme):
                parts = package_document(
                    {
                        "theme": theme,
                        "page": {"orientation": "landscape", "margins_cm": {"top": 3}},
                        "blocks": [
                            {"type": "heading", "level": 1, "text": "主题标题"},
                            {"type": "paragraph", "text": "主题正文"},
                        ],
                    }
                )
                styles = xml_part(parts, "word/styles.xml")
                normal_style = next(
                    style for style in elements(styles, "style") if style.get(W + "styleId") == "Normal"
                )
                self.assertEqual(normal_style.find(f"{W}rPr/{W}rFonts").get(W + "eastAsia"), expected_font)
                document_xml = xml_part(parts, "word/document.xml")
                page_size = document_xml.find(f"{W}body/{W}sectPr/{W}pgSz")
                self.assertEqual(page_size.get(W + "orient"), "landscape")
                self.assertGreater(int(page_size.get(W + "w")), int(page_size.get(W + "h")))
                page_margins = document_xml.find(f"{W}body/{W}sectPr/{W}pgMar")
                self.assertEqual(page_margins.get(W + "top"), "1701")
                paragraphs = elements(document_xml, "p")
                heading_run = paragraphs[0].find(f"{W}r/{W}rPr")
                self.assertEqual(heading_run.find(W + "rFonts").get(W + "eastAsia"), "黑体")
                self.assertEqual(heading_run.find(W + "sz").get(W + "val"), "32")
                expected_color = "1A365D" if theme == "business" else "000000"
                self.assertEqual(heading_run.find(W + "color").get(W + "val"), expected_color)
                self.assertIsNotNone(heading_run.find(W + "b"))
                body_paragraph = paragraphs[1]
                body_run = body_paragraph.find(f"{W}r/{W}rPr")
                self.assertEqual(body_run.find(W + "sz").get(W + "val"), "21")
                self.assertEqual(body_paragraph.find(f"{W}pPr/{W}spacing").get(W + "line"), "300")
                self.assertEqual(body_paragraph.find(f"{W}pPr/{W}spacing").get(W + "after"), "80")
                expected_indent = "420" if theme == "academic" else "0"
                self.assertEqual(body_paragraph.find(f"{W}pPr/{W}ind").get(W + "firstLine"), expected_indent)


def _make_package(parts: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, contents in parts.items():
            package.writestr(name, contents)
    return stream.getvalue()


if __name__ == "__main__":
    unittest.main()
