"""OOXML-level synthetic regressions for advanced tl-docx creation features."""

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

from tl_create import build_document


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
W = f"{{{WORD_NS}}}"
R = f"{{{REL_NS}}}"
WP = f"{{{DRAWING_NS}}}"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "hZkAAAAASUVORK5CYII="
)


def package_document(spec, read_image=lambda _path: PNG_BYTES) -> dict[str, bytes]:
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


def _section_part_text(parts: dict[str, bytes], section, kind: str, variant: str = "default") -> str | None:
    reference = next(
        (
            node
            for node in section.findall(W + f"{kind}Reference")
            if node.get(W + "type") == variant
        ),
        None,
    )
    if reference is None:
        return None
    relationships = xml_part(parts, "word/_rels/document.xml.rels")
    relationship_id = reference.get(R + "id")
    relationship = next(
        item
        for item in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        if item.get("Id") == relationship_id
    )
    target = relationship.get("Target")
    root = xml_part(parts, f"word/{target}")
    return "".join(node.text or "" for node in elements(root, "t"))


class HeadingAndListTests(unittest.TestCase):
    def test_chinese_heading_numbering_has_one_native_four_level_instance(self):
        parts = package_document(
            {
                "heading_numbering": "chinese",
                "blocks": [
                    {"type": "heading", "level": level, "text": f"标题{level}"}
                    for level in range(1, 6)
                ]
                + [{"type": "list", "ordered": True, "items": ["独立列表"]}],
            }
        )
        document_xml = xml_part(parts, "word/document.xml")
        paragraphs = elements(document_xml, "p")
        heading_ids = []
        for level, paragraph in enumerate(paragraphs[:4]):
            number_id = paragraph.find(f"{W}pPr/{W}numPr/{W}numId")
            level_id = paragraph.find(f"{W}pPr/{W}numPr/{W}ilvl")
            self.assertIsNotNone(number_id)
            self.assertEqual(level_id.get(W + "val"), str(level))
            heading_ids.append(number_id.get(W + "val"))
        self.assertEqual(len(set(heading_ids)), 1)
        self.assertIsNone(paragraphs[4].find(f"{W}pPr/{W}numPr"))

        list_id = paragraphs[5].find(f"{W}pPr/{W}numPr/{W}numId").get(W + "val")
        self.assertNotEqual(list_id, heading_ids[0])
        numbering = xml_part(parts, "word/numbering.xml")
        heading_number = next(
            item for item in elements(numbering, "num") if item.get(W + "numId") == heading_ids[0]
        )
        abstract_id = heading_number.find(W + "abstractNumId").get(W + "val")
        heading_abstract = next(
            item
            for item in elements(numbering, "abstractNum")
            if item.get(W + "abstractNumId") == abstract_id
        )
        heading_levels = heading_abstract.findall(W + "lvl")
        self.assertEqual(len(heading_levels), 4)
        self.assertEqual(
            [level.find(W + "numFmt").get(W + "val") for level in heading_levels],
            ["chineseCounting", "chineseCounting", "decimal", "decimal"],
        )
        self.assertEqual(
            [level.find(W + "lvlText").get(W + "val") for level in heading_levels],
            ["%1、", "（%2）", "%3.", "（%4）"],
        )
        self.assertIsNone(heading_levels[1].find(W + "lvlRestart"))
        numbering_children = [child.tag for child in list(numbering)]
        self.assertLess(numbering_children.index(W + "abstractNum"), numbering_children.index(W + "num"))

    def test_decimal_heading_format_and_named_list_continuation(self):
        parts = package_document(
            {
                "heading_numbering": "decimal",
                "blocks": [
                    {"type": "heading", "level": level, "text": f"标题{level}"}
                    for level in range(1, 5)
                ]
                + [
                    {"type": "list", "list_id": "工作清单", "level": 0, "ordered": True, "items": ["根项"]},
                    {"type": "list", "list_id": "工作清单", "level": 1, "ordered": True, "items": ["子项甲"]},
                    {"type": "paragraph", "text": "说明段"},
                    {"type": "list", "list_id": "工作清单", "level": 1, "ordered": True, "items": ["子项乙"]},
                    {"type": "list", "list_id": "另一个列表", "level": 0, "ordered": True, "items": ["另起"]},
                ],
            }
        )
        document_xml = xml_part(parts, "word/document.xml")
        paragraphs = elements(document_xml, "p")
        heading_numbering = xml_part(parts, "word/numbering.xml")
        heading_num_id = paragraphs[0].find(f"{W}pPr/{W}numPr/{W}numId").get(W + "val")
        heading_num = next(
            item for item in elements(heading_numbering, "num") if item.get(W + "numId") == heading_num_id
        )
        abstract_id = heading_num.find(W + "abstractNumId").get(W + "val")
        abstract = next(
            item
            for item in elements(heading_numbering, "abstractNum")
            if item.get(W + "abstractNumId") == abstract_id
        )
        levels = abstract.findall(W + "lvl")
        self.assertEqual([level.find(W + "lvlText").get(W + "val") for level in levels], ["%1 ", "%1.%2 ", "%1.%2.%3 ", "%1.%2.%3.%4 "])
        self.assertTrue(all(level.find(W + "suff").get(W + "val") == "nothing" for level in levels))

        named_paragraphs = [paragraphs[index] for index in (4, 5, 7)]
        named_ids = [
            paragraph.find(f"{W}pPr/{W}numPr/{W}numId").get(W + "val")
            for paragraph in named_paragraphs
        ]
        self.assertEqual(named_ids, [named_ids[0]] * 3)
        self.assertEqual(
            [paragraph.find(f"{W}pPr/{W}numPr/{W}ilvl").get(W + "val") for paragraph in named_paragraphs],
            ["0", "1", "1"],
        )
        another_id = paragraphs[8].find(f"{W}pPr/{W}numPr/{W}numId").get(W + "val")
        self.assertNotEqual(another_id, named_ids[0])
        named_num = next(item for item in elements(heading_numbering, "num") if item.get(W + "numId") == named_ids[0])
        named_abstract_id = named_num.find(W + "abstractNumId").get(W + "val")
        named_abstract = next(
            item
            for item in elements(heading_numbering, "abstractNum")
            if item.get(W + "abstractNumId") == named_abstract_id
        )
        self.assertEqual(len(named_abstract.findall(W + "lvl")), 9)
        for level in named_abstract.findall(W + "lvl")[1:]:
            child_tags = [child.tag for child in level]
            self.assertLess(child_tags.index(W + "lvlRestart"), child_tags.index(W + "lvlText"))
            self.assertLess(child_tags.index(W + "lvlRestart"), child_tags.index(W + "lvlJc"))

    def test_named_list_rejects_skipped_parent_and_changed_ordering(self):
        invalid = [
            {
                "blocks": [
                    {"type": "list", "list_id": "items", "level": 1, "items": ["child"]}
                ]
            },
            {
                "blocks": [
                    {"type": "list", "list_id": "items", "level": 0, "ordered": True, "items": ["root"]},
                    {"type": "list", "list_id": "items", "level": 2, "ordered": True, "items": ["skip"]},
                ]
            },
            {
                "blocks": [
                    {"type": "list", "list_id": "items", "level": 0, "ordered": True, "items": ["root"]},
                    {"type": "list", "list_id": "items", "level": 0, "ordered": False, "items": ["changed"]},
                ]
            },
            {"blocks": [{"type": "list", "level": 1, "items": ["not named"]}]},
        ]
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                build_document(spec, lambda _path: PNG_BYTES)


class AdvancedTableTests(unittest.TestCase):
    def test_header_rows_widths_caption_three_line_and_row_layout(self):
        parts = package_document(
            {
                "table_format": {"preset": "grid", "border_color": "334455"},
                "blocks": [
                    {
                        "type": "table",
                        "header_rows": 2,
                        "widths_mm": [40, 50],
                        "caption": "表 1：经营指标",
                        "format": {
                            "preset": "three_line",
                            "align": "center",
                            "borders": {"top": {"width_pt": 2, "color": "ABCDEF"}},
                        },
                        "rows": [["汇总", "本期"], ["名称", "结果"], ["指标", "完成"]],
                    }
                ],
            }
        )
        document_xml = xml_part(parts, "word/document.xml")
        caption, table = elements(document_xml, "p")[0], elements(document_xml, "tbl")[0]
        self.assertIsNotNone(caption.find(f"{W}pPr/{W}keepNext"))
        self.assertEqual("".join(node.text or "" for node in elements(caption, "t")), "表 1：经营指标")
        self.assertEqual(caption.find(f"{W}pPr/{W}jc").get(W + "val"), "center")

        rows = elements(table, "tr")
        self.assertEqual(len(rows), 3)
        for row in rows[:2]:
            self.assertIsNotNone(row.find(f"{W}trPr/{W}tblHeader"))
        self.assertIsNone(rows[2].find(f"{W}trPr/{W}tblHeader"))
        self.assertTrue(all(row.find(f"{W}trPr/{W}cantSplit").get(W + "val") == "1" for row in rows))
        grid_widths = [int(node.get(W + "w")) for node in elements(table, "gridCol")]
        self.assertEqual(grid_widths, [2268, 2835])
        borders = table.find(f"{W}tblPr/{W}tblBorders")
        self.assertEqual(borders.find(W + "top").get(W + "sz"), "16")
        self.assertEqual(borders.find(W + "top").get(W + "color"), "ABCDEF")
        self.assertEqual(borders.find(W + "bottom").get(W + "sz"), "12")
        self.assertEqual(borders.find(W + "insideV").get(W + "val"), "nil")
        separator = rows[1].find(f"{W}tc/{W}tcPr/{W}tcBorders/{W}bottom")
        self.assertEqual(separator.get(W + "sz"), "6")

    def test_zebra_fill_and_global_table_format(self):
        parts = package_document(
            {
                "table_format": {
                    "preset": "zebra",
                    "column_sizing": "content",
                    "band_fill": "F2F4F7",
                    "cell_margin_left_mm": 2,
                    "cell_margin_right_mm": 2,
                    "vertical_align": "top",
                    "allow_row_split": True,
                },
                "blocks": [
                    {
                        "type": "table",
                        "header": True,
                        "rows": [["名称", "结果"], ["短", "一"], ["较长名称", "二"], ["再次", "三"]],
                    }
                ],
            }
        )
        table = elements(xml_part(parts, "word/document.xml"), "tbl")[0]
        rows = elements(table, "tr")
        fills = [
            [cell.find(f"{W}tcPr/{W}shd").get(W + "fill") if cell.find(f"{W}tcPr/{W}shd") is not None else None for cell in elements(row, "tc")]
            for row in rows
        ]
        self.assertEqual(fills[0], [None, None])
        self.assertEqual(fills[1], [None, None])
        self.assertEqual(fills[2], ["F2F4F7", "F2F4F7"])
        self.assertEqual(fills[3], [None, None])
        self.assertTrue(all(row.find(f"{W}trPr/{W}cantSplit").get(W + "val") == "0" for row in rows))
        margins = table.find(f"{W}tblPr/{W}tblCellMar")
        self.assertEqual(margins.find(W + "left").get(W + "w"), "113")
        table_edges = table.find(f"{W}tblPr/{W}tblBorders")
        self.assertTrue(all(edge.get(W + "val") == "nil" for edge in list(table_edges)))

    def test_cell_edges_reconcile_once_and_conflicts_fail(self):
        parts = package_document(
            {
                "blocks": [
                    {
                        "type": "table",
                        "rows": [[{"text": "A", "borders": {"right": {"width_pt": 1, "color": "AA0000"}}}, "B"]],
                    }
                ]
            }
        )
        row = elements(xml_part(parts, "word/document.xml"), "tr")[0]
        left, right = elements(row, "tc")
        left_edge = left.find(f"{W}tcPr/{W}tcBorders/{W}right")
        right_edge = right.find(f"{W}tcPr/{W}tcBorders/{W}left")
        self.assertEqual(left_edge.get(W + "sz"), right_edge.get(W + "sz"))
        self.assertEqual(left_edge.get(W + "color"), right_edge.get(W + "color"))

        conflict = {
            "blocks": [
                {
                    "type": "table",
                    "rows": [[
                        {"text": "A", "borders": {"right": {"color": "AA0000"}}},
                        {"text": "B", "borders": {"left": {"color": "00AA00"}}},
                    ]],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "shared edge"):
            build_document(conflict, lambda _path: PNG_BYTES)

    def test_row_span_col_span_and_header_boundary_validation(self):
        parts = package_document(
            {
                "blocks": [
                    {
                        "type": "table",
                        "header_rows": 2,
                        "rows": [
                            [{"text": "机构", "row_span": 2}, {"text": "存款", "col_span": 2}, None],
                            [None, "本期", "上期"],
                            ["甲", "10", "9"],
                        ],
                    }
                ]
            }
        )
        table = elements(xml_part(parts, "word/document.xml"), "tbl")[0]
        self.assertEqual(elements(table, "gridSpan")[0].get(W + "val"), "2")
        self.assertGreaterEqual(len(elements(table, "vMerge")), 2)
        reloaded = Document(io.BytesIO(_make_package(parts)))
        self.assertEqual(reloaded.tables[0].cell(0, 0).text, "机构")
        invalid = [
            {
                "blocks": [
                    {
                        "type": "table",
                        "header_rows": 1,
                        "rows": [[{"text": "crosses", "row_span": 2}], [None]],
                    }
                ]
            },
            {
                "blocks": [
                    {
                        "type": "table",
                        "header_rows": 1,
                        "rows": [[{"text": "anchor", "row_span": 2}], [""]],
                    }
                ]
            },
        ]
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                build_document(spec, lambda _path: PNG_BYTES)

    def test_cell_images_are_callback_only_and_fit_the_table_cell(self):
        requests = []

        def read_image(path):
            requests.append(path)
            return PNG_BYTES

        parts = package_document(
            {
                "blocks": [
                    {
                        "type": "table",
                        "widths_mm": [30],
                        "rows": [[{"image": {"path": "figures/inline.png", "width_mm": 20, "height_mm": 15, "alt": "示意图"}}]],
                    }
                ]
            },
            read_image,
        )
        self.assertEqual(requests, ["figures/inline.png"])
        document_xml = xml_part(parts, "word/document.xml")
        self.assertEqual(len(elements(document_xml, "drawing")), 1)
        self.assertEqual(list(document_xml.iter(WP + "docPr"))[0].get("descr"), "示意图")

        too_wide_requests = []
        too_wide = {
            "blocks": [
                {
                    "type": "table",
                    "widths_mm": [30],
                    "rows": [[{"image": {"path": "no-read.png", "width_mm": 40, "height_mm": 15, "alt": ""}}]],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "width exceeds"):
            build_document(too_wide, lambda path: too_wide_requests.append(path) or PNG_BYTES)
        self.assertEqual(too_wide_requests, [])


class SectionVariantTests(unittest.TestCase):
    def test_header_footer_inheritance_clear_variants_and_page_fields(self):
        document = build_document(
            {
                "odd_even_headers": True,
                "different_first_page": True,
                "page_numbers": True,
                "first_page_numbers": True,
                "page_start": 4,
                "page_number_format": "upperRoman",
                "header": "legacy header",
                "footer": "legacy footer",
                "headers": {
                    "default": "正文页眉",
                    "first": "封面页眉",
                    "even": "偶数页眉",
                },
                "footers": {"default": "正文页脚", "first": None},
                "blocks": [
                    {"type": "paragraph", "text": "第一节"},
                    {"type": "section_break", "orientation": "landscape"},
                    {"type": "paragraph", "text": "继承节"},
                    {
                        "type": "section_break",
                        "headers": {"default": None},
                        "page_numbers": False,
                        "first_page_numbers": False,
                        "page_number_start": 1,
                        "page_number_format": "lowerRoman",
                    },
                    {"type": "paragraph", "text": "清空节"},
                    {"type": "section_break"},
                    {"type": "paragraph", "text": "继续节"},
                ],
            },
            lambda _path: PNG_BYTES,
        )
        parts = _package(document)
        document_xml = xml_part(parts, "word/document.xml")
        sections = elements(document_xml, "sectPr")
        self.assertEqual(len(sections), 4)
        settings = xml_part(parts, "word/settings.xml")
        self.assertIsNotNone(settings.find(W + "evenAndOddHeaders"))
        self.assertIsNotNone(sections[0].find(W + "titlePg"))
        self.assertIsNotNone(sections[1].find(W + "titlePg"))

        self.assertEqual(_section_part_text(parts, sections[0], "header", "default"), "正文页眉")
        self.assertEqual(_section_part_text(parts, sections[0], "header", "first"), "封面页眉")
        self.assertEqual(_section_part_text(parts, sections[0], "header", "even"), "偶数页眉")
        self.assertEqual(_section_part_text(parts, sections[1], "header", "default"), None)
        self.assertEqual(_section_part_text(parts, sections[2], "header", "default"), "")
        self.assertEqual(_section_part_text(parts, sections[2], "footer", "default"), "正文页脚")
        self.assertEqual(_section_part_text(parts, sections[2], "footer", "even"), "正文页脚")
        self.assertEqual(_section_part_text(parts, sections[3], "header", "default"), None)

        page_types = [section.find(W + "pgNumType") for section in sections]
        self.assertEqual(page_types[0].get(W + "start"), "4")
        self.assertEqual(page_types[0].get(W + "fmt"), "upperRoman")
        self.assertIsNone(page_types[1].get(W + "start"))
        self.assertEqual(page_types[1].get(W + "fmt"), "upperRoman")
        self.assertEqual(page_types[2].get(W + "start"), "1")
        self.assertEqual(page_types[2].get(W + "fmt"), "lowerRoman")
        self.assertIsNone(page_types[3].get(W + "start"))
        self.assertEqual(page_types[3].get(W + "fmt"), "lowerRoman")

        footer_parts = [name for name in parts if name.startswith("word/footer") and name.endswith(".xml")]
        page_fields = {
            name: [node.text for node in elements(xml_part(parts, name), "instrText")]
            for name in footer_parts
        }
        self.assertTrue(any(" PAGE " in instructions for instructions in page_fields.values()))
        first_footer = _section_part_text(parts, sections[0], "footer", "first")
        self.assertIsNotNone(first_footer)
        # A new section without explicit content remains linked to the prior section.
        reloaded = Document(io.BytesIO(_make_package(parts)))
        self.assertTrue(reloaded.sections[1].header.is_linked_to_previous)
        self.assertFalse(reloaded.sections[2].header.is_linked_to_previous)

    def test_explicit_page_start_alias_conflict_and_first_page_guard(self):
        invalid_specs = [
            {
                "page_start": 2,
                "page_number_start": 3,
                "blocks": [],
            },
            {
                "different_first_page": False,
                "headers": {"first": "封面"},
                "blocks": [],
            },
            {
                "odd_even_headers": False,
                "footers": {"even": "偶数"},
                "blocks": [],
            },
            {
                "different_first_page": False,
                "blocks": [
                    {"type": "section_break", "first_page_numbers": True},
                ],
            },
            {
                "page_numbers": True,
                "footer": [{"field": "PAGE"}],
                "blocks": [],
            },
        ]
        for spec in invalid_specs:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                build_document(spec, lambda _path: PNG_BYTES)

    def test_section_can_disable_inherited_first_page_numbering(self):
        document = build_document(
            {
                "different_first_page": True,
                "first_page_numbers": True,
                "footer": "正文页脚",
                "blocks": [
                    {"type": "paragraph", "text": "第一节"},
                    {"type": "section_break", "different_first_page": False},
                    {"type": "paragraph", "text": "普通首页"},
                ],
            },
            lambda _path: PNG_BYTES,
        )
        self.assertTrue(document.sections[0].different_first_page_header_footer)
        self.assertFalse(document.sections[1].different_first_page_header_footer)


def _make_package(parts: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, contents in parts.items():
            package.writestr(name, contents)
    return stream.getvalue()


def _package(document) -> dict[str, bytes]:
    stream = io.BytesIO()
    document.save(stream)
    stream.seek(0)
    with zipfile.ZipFile(stream) as package:
        return {name: package.read(name) for name in package.namelist()}


if __name__ == "__main__":
    unittest.main()
