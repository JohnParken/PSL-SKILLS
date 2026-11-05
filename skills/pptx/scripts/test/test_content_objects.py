"""Independent native-content expectations and corruption checks."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "_engine"), str(Path(__file__).resolve().parent)]

from pptx_core import runtime
from pptx_core.content import ContentError, compare_table_input, expected_slide, read_formula
from pptx_core.validation import inspect_presentation, validate_presentation
from test_v680_native import chart_payload, native_marker, svg_page, zip_parts
from test_validation import zipped


NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main", "m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}


def render(body: str, notes: str = "") -> bytes:
    deck = runtime.Deck("Content checks", 1280, 720, "preserve", True, (runtime.PreparedSlide(svg_page(body), notes),))
    return runtime.render_deck(deck)


def expected(body: str, notes: str = "") -> dict:
    return {"title": "Content checks", "width_emu": 12192000, "height_emu": 6858000, "slides": [expected_slide(svg_page(body), notes)]}


def table_payload() -> dict:
    return {
        "schema": "ppt-master.semantic-table.v2", "x": 80, "y": 100, "width": 900, "height": 360,
        "header_rows": 0, "column_widths": [300, 300, 300], "row_heights": [120, 120, 120],
        "defaults": {"cell": {"padding": 6}, "run": {"bold": True}},
        "rows": [
            [{"text": "合并项", "row_span": 2, "col_span": 2}, "", "Top"],
            ["", {"merge_continuation": True}, "Bottom"],
            [{"paragraphs": [{"runs": [{"text": "A"}, {"text": "β"}]}, "Second"]}, 0, None],
        ],
    }


class ContentObjectTests(unittest.TestCase):
    def test_table_details_preserve_grid_paragraphs_and_merge_anchors(self) -> None:
        body = native_marker("table", table_payload())
        data = render(body)
        details = inspect_presentation(data, details=True)
        table = details["slides"][0]["native_objects"][0]["data"]
        self.assertEqual((table["row_count"], table["column_count"]), (3, 3))
        self.assertEqual(table["column_widths_emu"], [2857500] * 3)
        self.assertEqual(table["row_heights_emu"], [1143000] * 3)
        self.assertEqual(table["merges"], [{"row": 1, "column": 1, "row_span": 2, "column_span": 2}])
        self.assertEqual(table["rows"][1][1]["merge_anchor"], {"row": 1, "column": 1})
        self.assertEqual(table["rows"][2][0]["paragraphs"], ["Aβ", "Second"])
        self.assertEqual(table["rows"][2][1]["text"], "0")
        self.assertEqual(table["rows"][2][2]["text"], "")
        self.assertEqual(validate_presentation(data, expected=expected(body))["source_comparison"], "passed")

    def test_changed_table_text_fails_source_comparison(self) -> None:
        payload = table_payload()
        actual = inspect_presentation(render(native_marker("table", payload)), details=True)["slides"][0]["native_objects"][0]["data"]
        actual["rows"][2][0]["paragraphs"][1] = "Changed"
        with self.assertRaises(ContentError) as failure:
            compare_table_input(payload, actual)
        self.assertEqual(failure.exception.details["field_path"], "table.paragraphs")

    def test_broken_merge_continuation_is_detected_in_content_mode(self) -> None:
        parts = zip_parts(render(native_marker("table", table_payload())))
        slide = ET.fromstring(parts["ppt/slides/slide1.xml"])
        cells = slide.findall(".//a:tbl/a:tr/a:tc", NS)
        cells[1].attrib.pop("hMerge")
        parts["ppt/slides/slide1.xml"] = ET.tostring(slide)
        data = zipped(parts)
        self.assertEqual(validate_presentation(data)["structural_validation"], "passed")
        with self.assertRaises(ContentError) as failure:
            validate_presentation(data, content=True)
        self.assertEqual(failure.exception.details["slide_number"], 1)
        self.assertEqual(failure.exception.details["object_kind"], "table")
        self.assertIn("continuation", str(failure.exception))

    def test_headers_and_unequal_rows_preserve_resolved_grid(self) -> None:
        payload = {"schema": "ppt-master.semantic-table.v2", "x": 20, "y": 30, "width": 900, "height": 300,
                   "columns": ["Name", "Value"], "rows": [["Only"], ["B", 12]]}
        body = native_marker("table", payload)
        data = render(body)
        table = inspect_presentation(data, details=True)["slides"][0]["native_objects"][0]["data"]
        self.assertEqual([[cell["text"] for cell in row] for row in table["rows"]], [["Name", "Value"], ["Only", ""], ["B", "12"]])
        self.assertEqual(validate_presentation(data, expected=expected(body))["source_comparison"], "passed")

    def test_table_xml_line_endings_preserve_text_meaning(self) -> None:
        payload = {"schema": "ppt-master.semantic-table.v2", "x": 20, "y": 30, "width": 900, "height": 300,
                   "rows": [[{"text": "A\r\nB\rC\tD"}, {"paragraphs": [{"runs": [{"text": "中\r\n文"}, {"text": " & "}]}]}]]}
        body = native_marker("table", payload)
        data = render(body)
        table = inspect_presentation(data, details=True)["slides"][0]["native_objects"][0]["data"]
        self.assertEqual([cell["text"] for cell in table["rows"][0]], ["A\nB\nC\tD", "中\n文 & "])
        self.assertEqual(validate_presentation(data, expected=expected(body))["source_comparison"], "passed")

    def test_formula_readback_keeps_fraction_roles_and_ignores_font_color(self) -> None:
        payload = {"latex": r"\frac{2}{3}", "x": 100, "y": 100, "width": 600, "height": 200, "font_size": 32, "color": "#123456"}
        body = native_marker("formula", payload)
        data = render(body)
        formula = inspect_presentation(data, details=True)["slides"][0]["native_objects"][0]["data"]
        self.assertEqual(formula["representation"], "omml")
        fraction = next(node for node in formula["structure"]["children"] if node["tag"] == "f")
        for role, text in (("num", "2"), ("den", "3")):
            operand = next(node for node in fraction["children"] if node["tag"] == role)
            self.assertEqual(operand["children"][0]["children"][0], {"tag": "t", "text": text})
        self.assertEqual(validate_presentation(data, expected=expected(body))["source_comparison"], "passed")
        recolored = copy.deepcopy(payload)
        recolored["color"] = "#FEDCBA"
        other = inspect_presentation(render(native_marker("formula", recolored)), details=True)["slides"][0]["native_objects"][0]["data"]
        self.assertEqual(formula, other)

    def test_changed_formula_numerator_is_reported_before_publication(self) -> None:
        payload = {"latex": r"\frac{2}{3}", "x": 100, "y": 100, "width": 600, "height": 200}
        body = native_marker("formula", payload)
        parts = zip_parts(render(body))
        original = parts["ppt/slides/slide1.xml"]
        self.assertIn(b"<m:t>2</m:t>", original)
        parts["ppt/slides/slide1.xml"] = original.replace(b"<m:t>2</m:t>", b"<m:t>9</m:t>", 1)
        with self.assertRaises(ContentError) as failure:
            validate_presentation(zipped(parts), expected=expected(body))
        self.assertEqual(failure.exception.details["object_kind"], "formula")
        self.assertEqual(failure.exception.details["field_path"], "formula.structure")

    def test_inline_math_reads_one_branch_and_compares_structure(self) -> None:
        body = '<text x="60" y="120">Ratio <tspan data-pptx-inline-formula="x^2">preview</tspan></text>'
        data = render(body)
        details = inspect_presentation(data, details=True)
        objects = details["slides"][0]["native_objects"]
        self.assertEqual([item["kind"] for item in objects], ["formula"])
        self.assertNotIn("preview", " ".join(details["slides"][0]["text"]))
        self.assertEqual(validate_presentation(data, expected=expected(body))["source_comparison"], "passed")

    def test_unsupported_math_profile_is_explicit(self) -> None:
        root = ET.fromstring(f'<m:oMath xmlns:m="{NS["m"]}"><m:future/></m:oMath>')
        with self.assertRaises(ContentError) as failure:
            read_formula(root)
        self.assertEqual(failure.exception.code, "unsupported_content")

    def test_math_alphabet_properties_are_retained(self) -> None:
        payload = {"latex": r"\mathbb{R}", "x": 100, "y": 100, "width": 600, "height": 200}
        body = native_marker("formula", payload)
        data = render(body)
        tree = inspect_presentation(data, details=True)["slides"][0]["native_objects"][0]["data"]["structure"]
        run = tree["children"][0]
        properties = next(item for item in run["children"] if item["tag"] == "rPr")
        self.assertIn({"tag": "scr", "attributes": {"val": "double-struck"}}, properties["children"])
        self.assertEqual(validate_presentation(data, expected=expected(body))["source_comparison"], "passed")

    def test_notes_and_title_mutations_are_not_accepted_as_source_matches(self) -> None:
        body = '<text x="40" y="80">Page</text>'
        data = render(body, "first\r\nsecond\n")
        source = expected(body, "first\r\nsecond\n")
        self.assertEqual(validate_presentation(data, expected=source)["source_comparison"], "passed")
        for key, value in (("title", "Changed"), ("width_emu", 914400)):
            other = copy.deepcopy(source)
            other[key] = value
            with self.subTest(key=key), self.assertRaises(ContentError):
                validate_presentation(data, expected=other)
        source["slides"][0]["notes"][0] = "Changed"
        with self.assertRaises(ContentError) as failure:
            validate_presentation(data, expected=source)
        self.assertEqual(failure.exception.details["field_path"], "notes")

    def test_hidden_native_declarations_are_excluded_from_source_count(self) -> None:
        hidden = native_marker("table", table_payload()).replace('<g ', '<g display="none" ', 1)
        body = hidden + '<text x="40" y="80">Visible</text>'
        data = render(body)
        self.assertEqual(validate_presentation(data, expected=expected(body))["source_comparison"], "passed")

    def test_mce_table_process_content_preserves_physical_grid(self) -> None:
        body = native_marker("table", table_payload())
        parts = zip_parts(render(body))
        xml = parts["ppt/slides/slide1.xml"]
        namespace = b' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:f="urn:test:future"'
        xml = xml.replace(b"<a:tbl>", b'<a:tbl' + namespace + b' mc:Ignorable="f" mc:ProcessContent="f:rows">', 1)
        xml = xml.replace(b"<a:tr ", b"<f:rows><a:tr ").replace(b"</a:tr>", b"</a:tr></f:rows>")
        xml = xml.replace(b"</a:tbl>", b"<f:noise><a:tr/></f:noise></a:tbl>", 1)
        parts["ppt/slides/slide1.xml"] = xml
        data = zipped(parts)
        self.assertEqual(validate_presentation(data, expected=expected(body))["source_comparison"], "passed")
        table = inspect_presentation(data, details=True)["slides"][0]["native_objects"][0]["data"]
        self.assertEqual((table["row_count"], table["column_count"]), (3, 3))

    def test_mce_formula_ignorable_attributes_and_wrappers_are_projected(self) -> None:
        body = native_marker("formula", {"latex": r"\frac{2}{3}", "x": 100, "y": 100, "width": 600, "height": 200})
        parts = zip_parts(render(body))
        namespace = b' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:f="urn:test:future"'
        attributes = namespace + b' mc:Ignorable="f" mc:ProcessContent="f:wrapper" f:annotation="optional"'
        xml, count = re.subn(rb"(<m:oMath\b)([^>]*>)", lambda match: match[1] + attributes + match[2], parts["ppt/slides/slide1.xml"], count=1)
        self.assertEqual(count, 1)
        xml = xml.replace(b"<m:f>", b"<f:wrapper><m:f>", 1).replace(b"</m:f>", b"</m:f></f:wrapper>", 1)
        xml = xml.replace(b"</m:oMath>", b"<f:noise><m:r><m:t>ignored</m:t></m:r></f:noise></m:oMath>", 1)
        parts["ppt/slides/slide1.xml"] = xml
        self.assertEqual(validate_presentation(zipped(parts), expected=expected(body))["source_comparison"], "passed")

    def test_mce_chart_and_workbook_use_the_same_semantic_profile(self) -> None:
        body = native_marker("chart", chart_payload())
        parts = zip_parts(render(body))
        namespace = b' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:f="urn:test:future"'
        chart = parts["ppt/charts/chart101.xml"]
        chart = chart.replace(b"<c:chartSpace ", b'<c:chartSpace' + namespace + b' mc:Ignorable="f" mc:ProcessContent="f:series" ', 1)
        chart = chart.replace(b"<c:ser>", b"<f:series><c:ser>").replace(b"</c:ser>", b"</c:ser></f:series>")
        parts["ppt/charts/chart101.xml"] = chart
        workbook_name = next(name for name in parts if name.endswith(".xlsx"))
        workbook = zip_parts(parts[workbook_name])
        sheet = workbook["xl/worksheets/sheet1.xml"]
        sheet = sheet.replace(b"<worksheet ", b'<worksheet' + namespace + b' mc:Ignorable="f" mc:ProcessContent="f:row" ', 1)
        sheet = sheet.replace(b"<row ", b"<f:row><row ").replace(b"</row>", b"</row></f:row>")
        workbook["xl/worksheets/sheet1.xml"] = sheet
        parts[workbook_name] = zipped(workbook)
        self.assertEqual(validate_presentation(zipped(parts), expected=expected(body))["source_comparison"], "passed")


class ContentCliTests(unittest.TestCase):
    def test_create_details_and_content_flags_report_actual_scopes(self) -> None:
        body = native_marker("table", table_payload())
        payload = {"schema": "psl.pptx.v1", "slides": [{"svg": svg_page(body)}]}
        with tempfile.TemporaryDirectory(prefix="pptx-content-cli-") as directory:
            for arguments in (("create", "-", "deck.pptx"), ("inspect", "deck.pptx", "--details"), ("validate", "deck.pptx", "--content")):
                process = subprocess.run(
                    [sys.executable, "-I", "-S", "-B", str(SCRIPTS / "pptx.py"), *arguments], cwd=directory,
                    input=json.dumps(payload) if arguments[0] == "create" else None, capture_output=True, text=True, timeout=45,
                    env={**os.environ, "PATH": ""}, check=False,
                )
                result = json.loads(process.stdout)
                self.assertEqual(process.returncode, 0, (result, process.stderr))
                self.assertEqual(result["content_validation"], "passed")
                self.assertEqual(result["source_comparison"], "passed" if arguments[0] == "create" else "not_run")
                self.assertEqual(result["visual_validation"], "not_run")
                if arguments[0] == "inspect":
                    self.assertEqual(result["slides"][0]["native_objects"][0]["kind"], "table")
                else:
                    self.assertNotIn("slides", result)

    def test_creation_rejects_changed_native_output_without_final_file(self) -> None:
        body = native_marker("table", table_payload())
        parts = zip_parts(render(body))
        slide = ET.fromstring(parts["ppt/slides/slide1.xml"])
        slide.find(".//a:tbl//a:t", NS).text = "Changed"
        parts["ppt/slides/slide1.xml"] = ET.tostring(slide)
        with tempfile.TemporaryDirectory(prefix="pptx-content-write-") as directory:
            root = Path(directory)
            config = root / "deck.json"
            config.write_text(json.dumps({"schema": "psl.pptx.v1", "title": "Content checks", "slides": [{"svg": svg_page(body)}]}))
            with patch("pptx_core.runtime.task_directory", return_value=root), patch("pptx_core.runtime.render_deck", return_value=zipped(parts)):
                with self.assertRaises(runtime.TaskError) as failure:
                    runtime.create("deck.json", "deck.pptx")
            self.assertEqual(failure.exception.code, "content_validation_failed")
            self.assertEqual(failure.exception.details["slide_number"], 1)
            self.assertEqual({path.name for path in root.iterdir()}, {"deck.json"})


if __name__ == "__main__":
    unittest.main()
