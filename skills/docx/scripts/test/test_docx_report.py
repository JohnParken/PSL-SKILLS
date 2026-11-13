"""Integration checks for bounded report reading with synthetic DOCX parts."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def paragraph(text, properties=""):
    return f"<w:p>{properties}<w:r><w:t>{text}</w:t></w:r></w:p>"


def heading(text, level=1, content=""):
    return f"<w:p><w:pPr><w:outlineLvl w:val='{level - 1}'/></w:pPr><w:r><w:t>{text}</w:t>{content}</w:r></w:p>"


def textbox(text):
    return f"<w:txbxContent>{paragraph(text)}</w:txbxContent>"


def shape(content):
    return f"<w:drawing><wp:anchor><wp:positionV relativeFrom='paragraph'><wp:posOffset>100</wp:posOffset></wp:positionV>{content}</wp:anchor></w:drawing>"


def cell(text, properties=""):
    return f"<w:tc><w:tcPr>{properties}</w:tcPr>{paragraph(text)}</w:tc>"


def row(cells, properties=""):
    return f"<w:tr><w:trPr>{properties}</w:trPr>{''.join(cells)}</w:tr>"


def relations(*items):
    entries = "".join(
        f"<Relationship Id='{identifier}' Type='{R}/{kind}' Target='{target}' TargetMode='{mode}'/>"
        for identifier, kind, target, mode in items
    )
    return f"<Relationships xmlns='{PR}'>{entries}</Relationships>"


def report_table():
    repeat = "<w:tblHeader/>"
    data = (
        row([cell("类别", "<w:vMerge w:val='restart'/>"), cell("使用", "<w:gridSpan w:val='2'/>")], repeat)
        + row([cell("", "<w:vMerge/>"), cell("本期"), cell("上期")], repeat)
        + row([cell("意图识别", "<w:vMerge w:val='restart'/>"), cell("12"), cell("0")])
        + row([cell("", "<w:vMerge/>"), cell("7")], "<w:gridAfter w:val='1'/>")
    )
    return "<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>" + data + "</w:tbl>"


def chart_xml():
    return f"""<c:chartSpace xmlns:c="{C}" xmlns:a="{A}" xmlns:r="{R}">
      <c:chart><c:title><c:tx><c:rich><a:p><a:r><a:t>使用占比</a:t></a:r></a:p></c:rich></c:tx></c:title>
      <c:plotArea><c:pieChart><c:ser><c:cat><c:strLit><c:ptCount val="3"/>
        <c:pt idx="0"><c:v>一月</c:v></c:pt><c:pt idx="1"><c:v>二月</c:v></c:pt><c:pt idx="2"><c:v>三月</c:v></c:pt>
      </c:strLit></c:cat><c:val><c:numRef><c:f>ExternalWorkbook!B1:B3</c:f>
        <c:numCache><c:formatCode>0.00%</c:formatCode><c:ptCount val="3"/>
          <c:pt idx="2"><c:v>0.00</c:v></c:pt><c:pt idx="0"><c:v>0.449700</c:v></c:pt>
        </c:numCache>
      </c:numRef></c:val></c:ser></c:pieChart></c:plotArea></c:chart>
    <c:externalData r:id="ext"/>
    </c:chartSpace>"""


class DocxReportTests(unittest.TestCase):
    """Exercise the public entry point with package data and a real cursor."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="docx-report-test-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.path = self.folder / "input.docx"

    def write_docx(self, body, parts=None):
        document = (
            f'<w:document xmlns:w="{W}" xmlns:mc="{MC}" xmlns:x="{WPS}" xmlns:wp="{WP}" xmlns:a="{A}" xmlns:c="{C}" xmlns:r="{R}">'
            f"<w:body>{body}<w:sectPr/></w:body></w:document>"
        )
        with ZipFile(self.path, "w") as archive:
            archive.writestr("word/document.xml", document)
            for name, data in (parts or {}).items():
                archive.writestr(name, data)

    def run_cli(self, *args, code=0, source=None):
        """Capture all output channels from a real CLI invocation."""
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SCRIPTS / "read_docx.py"), source or self.path.name, *args],
            cwd=self.folder, capture_output=True, check=False, timeout=30,
        )
        self.assertEqual(result.returncode, code, result.stderr.decode("utf-8"))
        return result

    def read_report(self, *args, budget=12000):
        """Decode only a complete report page within the requested byte budget."""
        result = self.run_cli("--report", "--max-output-bytes", str(budget), *args)
        self.assertEqual(result.stderr, b"")
        self.assertLessEqual(len(result.stdout), budget)
        return json.loads(result.stdout)

    def test_shape_branches_anchors_and_related_story(self):
        choice = f"<mc:Choice Requires='x'>{shape(textbox('月活79,519'))}</mc:Choice>"
        fallback = f"<mc:Fallback>{shape(textbox('重复副本'))}</mc:Fallback>"
        unknown = f"<mc:Choice xmlns:x='urn:unknown' Requires='x'>{shape(textbox('未选分支'))}</mc:Choice>"
        use_fallback = f"<mc:Fallback>{shape(textbox('兼容文字'))}</mc:Fallback>"
        drawings = f"<mc:AlternateContent>{choice}{fallback}</mc:AlternateContent>"
        drawings += f"<mc:AlternateContent>{unknown}{use_fallback}</mc:AlternateContent>"
        drawings += shape("<a:blip r:embed='img'/>") * 2
        body = f"<w:p><w:r><w:t>正文</w:t>{drawings}<w:footnoteReference w:id='2'/></w:r></w:p>"
        self.write_docx(body, {
            "word/_rels/document.xml.rels": relations(("img", "image", "media/logo.png", "Internal")),
            "word/media/logo.png": b"resource is only referenced",
            "word/footnotes.xml": f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="2">{paragraph("说明")}</w:footnote></w:footnotes>',
        })
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = self.read_report()
        blocks = result["blocks"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["related_story_parts"], {"footnote": 1})
        self.assertEqual([b["paragraphs"] for b in blocks if b["type"] == "textbox"], [["月活79,519"], ["兼容文字"]])
        self.assertEqual([b["anchor"]["paragraph_index"] for b in blocks if b["type"] == "textbox"], [1, 1])
        self.assertEqual(blocks[1]["anchor"]["vertical"]["offset_emu"], "100")
        self.assertEqual([b["id"] for b in blocks if b["type"] == "reference"], ["2"])
        self.assertEqual(result["stats"]["image_resources"], 1)
        self.assertEqual(result["stats"]["image"], 2)
        self.assertEqual(blocks[-1]["story_id"], "2")
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_table_geometry_header_override_and_row_text(self):
        self.write_docx(report_table())
        result = self.read_report()
        table, first, second, third, fourth = result["blocks"]
        self.assertEqual(table["column_count"], 3)
        self.assertEqual(table["header"]["source"], "repeat_header")
        self.assertEqual(table["header"]["row_count"], 2)
        self.assertEqual(table["header"]["columns"], [
            {"column": 1, "labels": ["类别"]}, {"column": 2, "labels": ["使用", "本期"]},
            {"column": 3, "labels": ["使用", "上期"]},
        ])
        self.assertEqual([label["text"] for label in table["header"]["spans"] if label["column"] <= 2 < label["column"] + label["column_span"]],
                         ["使用", "本期"])
        self.assertEqual(first["cells"][0]["row_span"], 2)
        self.assertEqual(second["cells"][0]["merge_anchor"], {"row": 1, "column": 1, "label": "类别"})
        self.assertEqual(third["cells"][0]["row_span"], 2)
        self.assertEqual(third["merged_groups"], fourth["merged_groups"])
        self.assertEqual(fourth["merged_groups"], [{"row": 3, "column": 1, "column_span": 1, "label": "意图识别"}])
        self.assertNotIn("merged_groups", second)
        self.assertEqual(fourth["cells"][0]["merge_anchor"], {"row": 3, "column": 1, "label": "意图识别"})
        self.assertEqual(fourth["cells"][0]["text"], "")
        self.assertEqual(fourth["grid_after"], 1)
        self.assertEqual(len(fourth["cells"]), 2)
        self.assertEqual(self.read_report("--table-header", "1:0")["blocks"][0]["header"],
                         {"source": "explicit", "row_count": 0, "spans": []})
        self.run_cli("--report", "--table-header", "2:1", code=2)
        self.run_cli("--report", "--table-header", "1:5", code=2)

    def test_every_row_marked_repeat_is_only_a_header_candidate(self):
        repeated = row([cell("名称"), cell("计数")], "<w:tblHeader/>")
        repeated += row([cell("部门"), cell("7")], "<w:tblHeader/>")
        self.write_docx("<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>" + repeated + "</w:tbl>")
        result = self.read_report()
        table = result["blocks"][0]
        self.assertEqual(table["header"]["source"], "first_row_candidate")
        self.assertEqual(table["repeat_header_count"], 2)
        self.assertEqual(table["header"]["columns"], [{"column": 1, "labels": ["名称"]}, {"column": 2, "labels": ["计数"]}])
        self.assertIn("all_rows_marked_repeat_header", result["issues"])
        self.assertEqual(result["blocks"][2]["cells"][1]["text"], "7")

    def test_candidate_first_row_does_not_hide_merged_group(self):
        body = row([cell("来源类别", "<w:vMerge w:val='restart'/>"), cell("甲")])
        body += row([cell("", "<w:vMerge/>"), cell("乙")])
        self.write_docx(f"<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>{body}</w:tbl>")
        table, first, second = json.loads(self.run_cli().stdout)["blocks"]
        self.assertEqual(table["header"]["source"], "first_row_candidate")
        self.assertEqual(first["merged_groups"], second["merged_groups"])
        self.assertEqual(second["merged_groups"][0]["label"], "来源类别")

    def test_chart_relationships_sparse_values_and_external_workbook(self):
        drawing = shape("<c:chart r:id='chart'/>")
        self.write_docx(f"<w:p><w:r>{drawing}</w:r></w:p>", {
            "word/_rels/document.xml.rels": relations(("chart", "chart", "charts/chart1.xml", "Internal")),
            "word/charts/chart1.xml": chart_xml(),
            "word/charts/_rels/chart1.xml.rels": relations(("ext", "oleObject", "https://example.invalid/input.xlsx", "External")),
        })
        result = self.read_report()
        chart, series = result["blocks"][1:3]
        self.assertEqual(chart["title"], "使用占比")
        self.assertEqual(chart["data_link"], "external_workbook_not_followed")
        self.assertEqual(series["format_code"], "0.00%")
        self.assertEqual(series["value_source"], "cache")
        self.assertEqual(series["value_reference"], {"status": "a1_range", "sheet": "ExternalWorkbook", "range": "B1:B3"})
        self.assertEqual(result["status"], "partial")
        points = [(b["index"], b["category"], b["value"]) for b in result["blocks"] if b["type"] == "chart_point"]
        self.assertEqual([b["value_cell"] for b in result["blocks"] if "value_cell" in b], ["B2"])
        self.assertEqual(points, [(0, "一月", "0.449700"), (1, "二月", None), (2, "三月", "0.00")])
        self.assertNotIn("example.invalid", repr(result))
        self.assertNotIn("ExternalWorkbook!B1:B3", repr(result))

    def test_filename_only_returns_every_supported_element_without_pagination(self):
        """Read a larger mixed document in one call with no view flags."""
        drawing = shape(textbox("月活79,519")) + shape("<c:chart r:id='chart'/>")
        drawing += shape("<a:blip r:embed='image'/>")
        body = f"<w:p><w:r><w:t>正文</w:t>{drawing}</w:r></w:p>" + report_table() + paragraph("补充" * 1500) * 3
        self.write_docx(body, {
            "word/_rels/document.xml.rels": relations(("chart", "chart", "charts/chart1.xml", "Internal"),
                                                       ("image", "image", "media/image.png", "Internal")),
            "word/charts/chart1.xml": chart_xml(),
            "word/charts/_rels/chart1.xml.rels": relations(("ext", "oleObject", "https://example.invalid/book.xlsx", "External")),
            "word/media/image.png": b"image resource reference",
            "word/header1.xml": f'<w:hdr xmlns:w="{W}">{paragraph("完整页眉")}</w:hdr>',
        })
        before = self.path.read_bytes()
        output = self.run_cli()
        self.assertEqual(output.stderr, b"")
        self.assertGreater(len(output.stdout), 12000)
        result = json.loads(output.stdout)
        self.assertEqual(result["format"], "docx-report")
        self.assertTrue(result["coverage"]["selected_view_complete"])
        self.assertFalse(result["has_more"])
        self.assertIsNone(result["next_cursor"])
        blocks = result["blocks"]
        self.assertEqual([b["paragraphs"] for b in blocks if b["type"] == "textbox"], [["月活79,519"]])
        self.assertEqual([b["resource_part"] for b in blocks if b["type"] == "image"], ["word/media/image.png"])
        self.assertEqual([b["value"] for b in blocks if b["type"] == "chart_point"], ["0.449700", None, "0.00"])
        self.assertEqual([b["value_cell"] for b in blocks if "value_cell" in b], ["B2"])
        rows = [b for b in blocks if b["type"] == "table_row"]
        table = next(b for b in blocks if b["type"] == "table")
        self.assertEqual(table["header"]["columns"][1], {"column": 2, "labels": ["使用", "本期"]})
        self.assertEqual([row["row_index"] for row in rows], [1, 2, 3, 4])
        self.assertEqual(rows[-1]["cells"][0]["merge_anchor"]["label"], "意图识别")
        self.assertEqual(rows[-1]["merged_groups"][0]["label"], "意图识别")
        self.assertEqual(blocks[-1]["text"], "完整页眉")
        self.assertEqual(self.path.read_bytes(), before)

    def test_section_contains_complete_elements_with_original_anchors(self):
        chart = shape("<c:chart r:id='chart'/>")
        image = shape("<a:blip r:embed='image'/>")
        section = heading("本节", content=shape(textbox("标题附注")))
        section += "<w:sdt><w:sdtContent>" + heading("子节", 2) + paragraph("子节正文") + "</w:sdtContent></w:sdt>"
        section += report_table() + f"<w:p><w:r>{chart}{image}<w:footnoteReference w:id='2'/></w:r></w:p>"
        body = f"<w:p><w:r>{chart}</w:r></w:p>" + report_table() + section
        body += heading("下一节", content=shape(textbox("其他章节附注"))) + "<w:altChunk/>"
        self.write_docx(body, {
            "word/_rels/document.xml.rels": relations(("chart", "chart", "charts/chart1.xml", "Internal"),
                                                       ("image", "image", "media/image.png", "Internal")),
            "word/charts/chart1.xml": chart_xml(),
            "word/charts/_rels/chart1.xml.rels": relations(("ext", "oleObject", "https://example.invalid/book.xlsx", "External")),
            "word/media/image.png": b"image resource",
            "word/header1.xml": f'<w:hdr xmlns:w="{W}">{paragraph("全局页眉")}</w:hdr>',
            "word/footnotes.xml": f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="2">{paragraph("脚注文字")}</w:footnote></w:footnotes>',
        })
        full = json.loads(self.run_cli().stdout)
        result = json.loads(self.run_cli("--section", " 本节 ").stdout)
        self.assertEqual(result["format"], "docx-report")
        self.assertEqual(result["scope"], {
            "section": "本节", "section_occurrence": 1, "heading_level": 1, "heading_paragraph_index": 2,
        })
        blocks = result["blocks"]
        start = next(index for index, block in enumerate(full["blocks"]) if block.get("text") == "本节")
        stop = next(index for index, block in enumerate(full["blocks"]) if block.get("text") == "下一节")
        self.assertEqual(blocks, full["blocks"][start:stop])
        self.assertEqual([block["text"] for block in blocks if block["type"] == "heading"], ["本节", "子节"])
        self.assertEqual([block["table_index"] for block in blocks if block["type"] == "table"], [2])
        self.assertEqual([block["chart_index"] for block in blocks if block["type"] == "chart"], [2])
        self.assertEqual([block["value"] for block in blocks if block["type"] == "chart_point"], ["0.449700", None, "0.00"])
        self.assertEqual([block["resource_part"] for block in blocks if block["type"] == "image"], ["word/media/image.png"])
        self.assertEqual([block["paragraphs"] for block in blocks if block["type"] == "textbox"], [["标题附注"]])
        self.assertEqual([block["id"] for block in blocks if block["type"] == "reference"], ["2"])
        self.assertEqual(result["stats"]["chart_parts_read"], 1)
        self.assertEqual(result["stats"]["table_cells"], 10)
        self.assertEqual(result["stats"]["image_resources"], 1)
        self.assertEqual(result["issues"]["chart_external_workbook_not_followed"], 1)
        self.assertNotIn("alternate_document_content_not_read", result["issues"])
        self.assertIn("alternate_document_content_not_read", full["issues"])
        self.assertEqual(result["related_story_parts"], {})
        self.assertTrue(result["coverage"]["selected_view_complete"])
        override = json.loads(self.run_cli("--section", "本节", "--table-header", "2:1").stdout)
        table = next(block for block in override["blocks"] if block["type"] == "table")
        self.assertEqual(table["header"]["source"], "explicit")

    def test_section_occurrence_bounds_last_section_and_legacy_modes(self):
        self.write_docx(heading("同名") + paragraph("第一段") + heading("同名") + paragraph("最后一段"), {
            "word/header1.xml": f'<w:hdr xmlns:w="{W}">{paragraph("全局页眉")}</w:hdr>',
        })
        for options, message in [
            (("--section", "同名"), "ambiguous"),
            (("--section", "同名", "--section-occurrence", "3"), "out of range"),
            (("--section", "同"), "not found"),
            (("--section", ""), "nonempty"),
        ]:
            with self.subTest(options=options):
                failed = self.run_cli(*options, code=2)
                self.assertEqual(failed.stdout, b"")
                self.assertIn(message, failed.stderr.decode())
        options = ("--section", "同名", "--section-occurrence", "2")
        selected = json.loads(self.run_cli(*options).stdout)
        self.assertEqual(selected["scope"]["section_occurrence"], 2)
        self.assertEqual([block["text"] for block in selected["blocks"]], ["同名", "最后一段"])
        self.assertEqual(json.loads(self.run_cli(*options, "--report").stdout), selected)
        self.assertEqual(self.run_cli(*options, "--format", "text").stdout.decode(), "同名\n最后一段")
        compact = json.loads(self.run_cli(*options, "--compact-json").stdout)
        self.assertEqual(compact["format"], "docx-compact")
        self.assertEqual([block["text"] for block in compact["blocks"]], ["同名", "最后一段"])

    def test_section_keeps_the_existing_whole_report_safety_checks(self):
        chart = shape("<c:chart r:id='bad'/>")
        self.write_docx(heading("安全段") + paragraph("内容") + heading("后续") + f"<w:p><w:r>{chart}</w:r></w:p>", {
            "word/_rels/document.xml.rels": relations(("bad", "chart", "../../outside.xml", "Internal")),
        })
        result = self.run_cli("--section", "安全段", code=2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"escapes the package", result.stderr)

    def test_pagination_is_bound_to_options_and_source_and_matches_full_read(self):
        self.write_docx(report_table() + paragraph("正文 " * 26) * 16)
        full = self.read_report(budget=10000)
        page = self.read_report(budget=2400)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["page_cache"], "stored")
        original_cursor = page["next_cursor"]
        combined = list(page["blocks"])
        while page["has_more"]:
            result = self.run_cli("--cursor", page["next_cursor"], "--max-output-bytes", "2400")
            self.assertLessEqual(len(result.stdout), 2400)
            page = json.loads(result.stdout)
            self.assertEqual(page["page_cache"], "reused")
            self.assertEqual(page["coverage"]["block_start"], len(combined))
            self.assertEqual(page["source_sha256"], full["source_sha256"])
            combined.extend(page["blocks"])
        self.assertEqual(combined, full["blocks"])
        self.assertIsNone(page["next_cursor"])
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))
        self.assertFalse(page["coverage"]["document_complete"])
        self.run_cli("--report", "--table-header", "1:1", "--cursor", original_cursor, code=2)
        self.run_cli("--compact-json", "--cursor", original_cursor, code=2)
        self.write_docx(paragraph("新内容"))
        self.run_cli("--report", "--cursor", original_cursor, code=2)

    def test_numbering_label_and_legacy_omission_hint(self):
        props = "<w:pPr><w:numPr><w:ilvl w:val='0'/><w:numId w:val='1'/></w:numPr></w:pPr>"
        number = (f"<w:numbering xmlns:w='{W}'><w:abstractNum w:abstractNumId='0'><w:lvl w:ilvl='0'>"
                  "<w:start w:val='2'/><w:numFmt w:val='decimal'/><w:lvlText w:val='%1.'/></w:lvl></w:abstractNum>"
                  "<w:num w:numId='1'><w:abstractNumId w:val='0'/></w:num></w:numbering>")
        self.write_docx(paragraph("甲", props) + paragraph("乙", props) + f"<w:p><w:r>{shape(textbox('图中文字'))}</w:r></w:p>",
                        {"word/numbering.xml": number})
        blocks = self.read_report()["blocks"]
        self.assertEqual([block["numbering_label"] for block in blocks if "numbering_label" in block], ["2.", "3."])
        self.assertEqual(self.run_cli("--format", "text").stdout.decode("utf-8"), "甲\n乙\n")
        compact = json.loads(self.run_cli("--compact-json", "--max-output-bytes", "3000").stdout)
        self.assertEqual(compact["omitted_object_nodes"], {"drawing_nodes": 1, "textbox_nodes": 1})
        self.assertIn("stored-drawing-elements-omitted", compact["coverage"]["omissions"])

    def test_report_rejects_dtd_and_oversize_single_block(self):
        self.write_docx(paragraph("长" * 3000))
        result = self.run_cli("--report", "--max-output-bytes", "2000", code=2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"single block exceeds", result.stderr)
        with ZipFile(self.path, "w") as archive:
            archive.writestr("word/document.xml", '<!DOCTYPE w:document><w:document xmlns:w="' + W + '"><w:body/></w:document>')
        result = self.run_cli("--report", code=2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"DTD", result.stderr)

    def test_release_abandoned_cursors_for_stdout_and_saved_page(self):
        self.write_docx(paragraph("长" * 300) * 8)
        first = self.read_report(budget=2400)
        self.assertTrue(first["has_more"])
        with_output = self.run_cli("--max-output-bytes", "2400", "-o", "page.json")
        self.assertEqual(with_output.stdout, b"")
        saved = json.loads((self.folder / "page.json").read_bytes())
        self.assertEqual(saved["page_cache"], "stored")
        self.assertEqual(len(list(self.folder.glob(".docx-report-*.cache"))), 2)
        for cursor in (first["next_cursor"], saved["next_cursor"]):
            released = self.run_cli_release(cursor)
            self.assertEqual(json.loads(released.stdout), {"status": "released"})
        self.assertFalse(list(self.folder.glob(".docx-report-*.cache")))

    def run_cli_release(self, cursor):
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SCRIPTS / "read_docx.py"), "--release-cursor", cursor],
            cwd=self.folder, capture_output=True, check=False, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_report_refuses_links_and_chart_relationships_outside_the_package(self):
        """Keep report input and every followed part inside its allowed source."""
        drawing = shape("<c:chart r:id='chart'/>")
        self.write_docx(f"<w:p><w:r>{drawing}</w:r></w:p>", {
            "word/_rels/document.xml.rels": relations(("chart", "chart", "../../outside.xml", "Internal")),
        })
        bad_chart = self.run_cli("--report", code=2)
        self.assertEqual(bad_chart.stdout, b"")
        self.assertIn(b"escapes the package", bad_chart.stderr)
        alias = self.folder / "alias.docx"
        alias.symlink_to(self.path)
        self.assertEqual(self.run_cli("--report", source=alias.name, code=2).stdout, b"")
