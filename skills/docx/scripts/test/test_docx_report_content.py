"""Regression fixtures for visible stored content and explicit semantic gaps."""

import hashlib
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

import test_docx_report as fixtures
import docx_read_elements

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def table(content, properties=""):
    return ("<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc>"
            f"<w:tcPr>{properties}</w:tcPr>{content}</w:tc></w:tr></w:tbl>")


def numbered(text, number_id="1"):
    properties = f"<w:pPr><w:numPr><w:ilvl w:val='0'/><w:numId w:val='{number_id}'/></w:numPr></w:pPr>"
    return fixtures.paragraph(text, properties)


def numbering():
    return (f"<w:numbering xmlns:w='{fixtures.W}'><w:abstractNum w:abstractNumId='0'><w:lvl w:ilvl='0'>"
            "<w:start w:val='1'/><w:numFmt w:val='decimal'/><w:lvlText w:val='%1.'/></w:lvl></w:abstractNum>"
            "<w:num w:numId='1'><w:abstractNumId w:val='0'/></w:num></w:numbering>")


class ReportContentTests(unittest.TestCase):
    """Use the unchanged public CLI, including status and original-byte checks."""

    setUp = fixtures.DocxReportTests.setUp
    write_docx = fixtures.DocxReportTests.write_docx
    run_cli = fixtures.DocxReportTests.run_cli
    read_report = fixtures.DocxReportTests.read_report

    def test_math_runs_recovered_without_claiming_fraction_semantics(self):
        math = (f"<m:oMath xmlns:m='{M}'><m:f><m:num><m:r><m:t>1</m:t></m:r></m:num>"
                "<m:den><m:r><m:t>2</m:t></m:r></m:den></m:f></m:oMath>")
        self.write_docx(table("<w:p><w:r><w:t>ratio=</w:t></w:r>" + math + "</w:p>"))
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        report = self.read_report()
        row = next(block for block in report["blocks"] if block["type"] == "table_row")
        equation = next(block for block in report["blocks"] if block["type"] == "equation")
        self.assertEqual(row["cells"][0]["text"], "ratio=")
        self.assertEqual(equation["text_runs"], ["1", "2"])
        self.assertEqual(equation["representation"], "stored-text-runs")
        self.assertNotIn("text", equation)
        self.assertEqual(equation["anchor"]["row"], 1)
        self.assertEqual(equation["anchor"]["column"], 1)
        self.assertEqual(report["issues"], {"equation_structure_not_interpreted": 1, "table_header_unconfirmed": 1})
        self.assertEqual(report["status"], "partial")
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_math_revision_filter_and_display_container_are_not_duplicated(self):
        formula = (f"<m:oMathPara xmlns:m='{M}'><m:oMath>"
                   "<w:del><m:r><m:t>old</m:t></m:r></w:del>"
                   "<w:ins><m:r><m:t>new</m:t></m:r></w:ins>"
                   "</m:oMath></m:oMathPara>")
        self.write_docx("<w:p>" + formula + "</w:p>")
        for revision, expected in (("accepted", ["new"]), ("original", ["old"]), ("all", ["old", "new"])):
            with self.subTest(revision=revision):
                report = self.read_report("--revisions", revision)
                equations = [block for block in report["blocks"] if block["type"] == "equation"]
                self.assertEqual(len(equations), 1)
                self.assertEqual(equations[0]["text_runs"], expected)
                self.assertEqual(report["issues"], {"equation_structure_not_interpreted": 1})

    def test_block_equations_and_empty_equations_keep_partial_coverage(self):
        formula = f"<m:oMathPara xmlns:m='{M}'><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></m:oMathPara>"
        self.write_docx(formula + table(formula + f"<w:p><m:oMath xmlns:m='{M}'/></w:p>"))
        report = self.read_report("--table-header", "1:0")
        equations = [block for block in report["blocks"] if block["type"] == "equation"]
        self.assertEqual([block["text_runs"] for block in equations], [["x"], ["x"], []])
        self.assertEqual(report["issues"], {"equation_structure_not_interpreted": 3})
        self.assertEqual(equations[1]["anchor"]["table_index"], 1)
        self.assertEqual(report["status"], "partial")

    def test_nested_table_numbering_advances_once_in_main_story_order(self):
        cell = numbered("first") + table(numbered("nested")) + numbered("last")
        self.write_docx(numbered("before") + table(cell) + numbered("after"), {"word/numbering.xml": numbering()})
        report = self.read_report("--table-header", "1:0", "--table-header", "2:0")
        paragraphs = [block for block in report["blocks"] if block["type"] == "paragraph"]
        self.assertEqual([block["numbering_label"] for block in paragraphs], ["1.", "5."])
        rows = [block for block in report["blocks"] if block["type"] == "table_row"]
        self.assertEqual(rows[0]["cells"][0]["text"], "first\nlast")
        self.assertEqual(rows[0]["cells"][0]["paragraph_numbering"],
                         [{"paragraph_index": 1, "numbering_label": "2."}, {"paragraph_index": 2, "numbering_label": "4."}])
        self.assertEqual(rows[1]["cells"][0]["paragraph_numbering"], [{"paragraph_index": 1, "numbering_label": "3."}])
        self.assertEqual(report["stats"]["numbered_paragraphs"], 5)
        self.assertEqual(report["status"], "ok")

    def test_numbering_wrappers_and_revision_scope(self):
        inserted = "<w:ins>" + numbered("new") + "</w:ins>"
        deleted = "<w:del>" + numbered("old") + "</w:del>"
        self.write_docx(table("<w:sdt><w:sdtContent>" + inserted + deleted + "</w:sdtContent></w:sdt>") + numbered("after"),
                        {"word/numbering.xml": numbering()})
        for revision, count in (("accepted", 1), ("original", 1), ("all", 2)):
            with self.subTest(revision=revision):
                report = self.read_report("--revisions", revision, "--table-header", "1:0")
                row = next(block for block in report["blocks"] if block["type"] == "table_row")
                self.assertEqual(len(row["cells"][0]["paragraph_numbering"]), count)
                self.assertEqual(report["blocks"][-1]["numbering_label"], f"{count + 1}.")
                self.assertEqual(report["status"], "ok")

    def test_numbering_prepass_obeys_text_budget_before_accumulating_labels(self):
        reader = docx_read_elements.ReportReader(type("Package", (), {"namelist": lambda self: []})(), set(), lambda node: None, {})
        root = ElementTree.fromstring(f"<w:body xmlns:w='{fixtures.W}'>" + numbered("x") * 10 + "</w:body>")
        numbers = docx_read_elements.DecimalNumbering(ElementTree.fromstring(numbering()))
        with patch.object(docx_read_elements, "MAX_REPORT_TEXT_CHARS", 5):
            with self.assertRaisesRegex(ValueError, "report_text_limit_exceeded"):
                reader._prepare_numbering(root, numbers)
        self.assertEqual(len(reader.paragraph_numbering), 2)

    def test_unresolved_cell_numbering_is_partial(self):
        self.write_docx(table(numbered("unknown", "99")))
        report = self.read_report("--table-header", "1:0")
        self.assertEqual(report["issues"], {"paragraph_numbering_unresolved": 1})
        self.assertEqual(report["status"], "partial")

    def test_cell_alternate_content_and_unknown_blocks_are_diagnosed(self):
        self.write_docx(table("<w:sdt><w:sdtContent><w:altChunk/></w:sdtContent></w:sdt>"
                              "<w:customUnsupported/>" + fixtures.paragraph("kept")))
        report = self.read_report("--table-header", "1:0")
        self.assertEqual(report["issues"], {"alternate_document_content_not_read": 1, "unrecognized_block_not_read": 1})
        self.assertEqual(report["blocks"][1]["cells"][0]["text"], "kept")
        self.assertEqual(report["status"], "partial")

    def test_cell_revisions_are_preserved_but_not_silently_interpreted(self):
        for marker in ("cellIns", "cellDel", "tcPrChange"):
            self.write_docx(table(fixtures.paragraph("stored"), f"<w:{marker} w:id='1' w:author='fixture'/>"))
            for revision in ("accepted", "original", "all"):
                with self.subTest(marker=marker, revision=revision):
                    report = self.read_report("--revisions", revision, "--table-header", "1:0")
                    cell = report["blocks"][1]["cells"][0]
                    self.assertEqual(cell["text"], "stored")
                    self.assertEqual(cell["unresolved_revisions"], [marker])
                    self.assertEqual(report["issues"], {"table_cell_revision_unresolved": 1})
                    self.assertEqual(report["status"], "partial")


if __name__ == "__main__":
    unittest.main()
