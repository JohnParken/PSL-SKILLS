"""PPT Master 6.8 native-object behavior through the isolated public CLI."""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "skills" / "pptx" / "scripts" / "pptx.py"
SVG_NS = "http://www.w3.org/2000/svg"
NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}


def svg_page(body: str) -> str:
    return f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">{body}</svg>'


def native_marker(kind: str, payload: dict, preview: str = "") -> str:
    authority = ' data-pptx-native-authority="json"' if kind in {"chart", "table"} else ""
    metadata = escape(json.dumps(payload, ensure_ascii=False))
    return (
        f'<g data-pptx-replace-with="{kind}"{authority}>'
        f'<metadata type="application/json">{metadata}</metadata>{preview}</g>'
    )


def chart_payload() -> dict:
    return {
        "type": "column", "x": 80, "y": 100, "width": 1000, "height": 500,
        "categories": ["A", "B"], "series": [{"name": "Series", "values": [3, 8]}],
    }


def zip_parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


class NativeV680Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="psl-pptx-v680-native-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(self, *arguments: str, payload: dict | None = None) -> tuple[subprocess.CompletedProcess, dict]:
        process = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SCRIPT), *arguments],
            cwd=self.root, input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            capture_output=True, text=True, encoding="utf-8", check=False, timeout=45,
            env={**os.environ, "PATH": "", "PYTHONNOUSERSITE": "1"},
        )
        self.assertTrue(process.stdout.strip(), process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["ok"], process.returncode == 0, (process.stdout, process.stderr, process.returncode))
        return process, result

    def create(self, *pages: str, filename: str = "deck.pptx") -> dict[str, bytes]:
        process, result = self.invoke(
            "create", "-", filename,
            payload={"schema": "psl.pptx.v1", "slides": [{"svg": page} for page in pages]},
        )
        self.assertEqual(process.returncode, 0, (result, process.stderr))
        self.assertEqual(result["structural_validation"], "passed")
        return zip_parts((self.root / filename).read_bytes())

    def test_scatter_axis_typography_language_and_tick_marks(self) -> None:
        payload = {
            "type": "scatter", "x": 80, "y": 100, "width": 1000, "height": 500, "lang": "ja_jp",
            "series": [{"name": "Observation", "x": [1, 2], "y": [3, 8]}],
            "axes": {
                "x": {"font_family": "Arial", "font_size": 20, "color": "#112233", "tick_marks": "cross"},
                "y": {"font_family": "Courier New", "font_size": 24, "color": "#445566", "tick_marks": "in"},
            },
        }
        parts = self.create(svg_page(native_marker("chart", payload)))
        chart = ET.fromstring(parts["ppt/charts/chart101.xml"])
        self.assertEqual(chart.find("c:lang", NS).get("val"), "ja-JP")
        axes = chart.findall(".//c:valAx", NS)
        self.assertEqual(len(axes), 2)
        expected = [("1500", "112233", "Arial", "cross"), ("1800", "445566", "Courier New", "in")]
        for axis, (size, color, font, tick) in zip(axes, expected):
            properties = axis.find("c:txPr/a:p/a:pPr/a:defRPr", NS)
            self.assertEqual(properties.get("sz"), size)
            self.assertEqual(properties.get("lang"), "ja-JP")
            self.assertEqual(properties.find("a:solidFill/a:srgbClr", NS).get("val"), color)
            self.assertEqual(properties.find("a:latin", NS).get("typeface"), font)
            self.assertEqual(axis.find("c:majorTickMark", NS).get("val"), tick)
        self.assertEqual([node.text for node in chart.findall(".//c:xVal/c:numRef/c:numCache/c:pt/c:v", NS)], ["1", "2"])
        self.assertEqual([node.text for node in chart.findall(".//c:yVal/c:numRef/c:numCache/c:pt/c:v", NS)], ["3", "8"])

    def test_chart_theme_font_slots_and_explicit_layout(self) -> None:
        payload = chart_payload()
        payload.update({
            "lang": "zh-CN", "gap_width": 60, "overlap": -20,
            "plot_area": {"x": 180, "y": 150, "width": 800, "height": 350},
            "style": {"font_family": "+mj-lt, +mn-ea, +mj-cs"},
        })
        parts = self.create(svg_page(native_marker("chart", payload)))
        chart = ET.fromstring(parts["ppt/charts/chart101.xml"])
        props = chart.find("c:txPr/a:p/a:pPr/a:defRPr", NS)
        self.assertEqual(props.get("lang"), "zh-CN")
        self.assertEqual([props.find(f"a:{slot}", NS).get("typeface") for slot in ("latin", "ea", "cs")],
                         ["+mj-lt", "+mn-ea", "+mj-cs"])
        self.assertEqual(chart.find(".//c:barChart/c:gapWidth", NS).get("val"), "60")
        self.assertEqual(chart.find(".//c:barChart/c:overlap", NS).get("val"), "-20")
        layout = chart.find(".//c:plotArea/c:layout/c:manualLayout", NS)
        self.assertEqual(layout.find("c:layoutTarget", NS).get("val"), "inner")
        for field, expected in (("x", 0.1), ("y", 0.1), ("w", 0.8), ("h", 0.7)):
            self.assertAlmostEqual(float(layout.find(f"c:{field}", NS).get("val")), expected)

    def test_invalid_chart_languages_fail_without_publishing(self) -> None:
        for number, language in enumerate(("not a language", "en--US", 12, None)):
            with self.subTest(language=language):
                payload = chart_payload()
                payload["lang"] = language
                filename = f"invalid-{number}.pptx"
                process, result = self.invoke(
                    "create", "-", filename,
                    payload={"schema": "psl.pptx.v1", "slides": [{"svg": svg_page(native_marker("chart", payload))}]},
                )
                self.assertEqual(process.returncode, 2, result)
                self.assertTrue(process.stderr.strip())
                self.assertIn("lang", result["error"]["message"])
                self.assertFalse((self.root / filename).exists())

    def test_merged_table_keeps_covered_cell_fill_padding_and_border(self) -> None:
        payload = {
            "schema": "ppt-master.semantic-table.v2", "x": 80, "y": 100, "width": 900, "height": 360,
            "header_rows": 0, "column_widths": [300, 300, 300], "row_heights": [120, 120, 120],
            "style": {"band_row": False, "border_width": 1, "border_color": "#112233", "valign": "middle"},
            "defaults": {"cell": {"fill": "#E0E8F0", "padding": 6}},
            "rows": [
                [{"text": "合并项", "row_span": 2, "col_span": 2},
                 {"text": "", "fill": "#F6A623", "border_color": "#223344", "border_width": 2,
                  "padding": {"left": 12, "right": 4}, "valign": "bottom"}, "Top"],
                [{"text": "", "fill": "#1188AA"}, {"merge_continuation": True, "fill": "#778899", "padding_top": 9}, "Bottom"],
                ["A", "B", "C"],
            ],
        }
        parts = self.create(svg_page(native_marker("table", payload)))
        slide = ET.fromstring(parts["ppt/slides/slide1.xml"])
        rows = slide.findall(".//a:tbl/a:tr", NS)
        self.assertEqual(len(rows), 3)
        cells = [row.findall("a:tc", NS) for row in rows]
        self.assertEqual(cells[0][0].get("gridSpan"), "2")
        self.assertEqual(cells[0][0].get("rowSpan"), "2")
        self.assertEqual(cells[0][1].get("hMerge"), "1")
        self.assertEqual(cells[1][0].get("vMerge"), "1")
        self.assertEqual((cells[1][1].get("hMerge"), cells[1][1].get("vMerge")), ("1", "1"))
        for row, column, fill in ((0, 1, "F6A623"), (1, 0, "1188AA"), (1, 1, "778899")):
            cell = cells[row][column]
            self.assertEqual(cell.findall(".//a:t", NS), [])
            self.assertEqual(cell.find("a:tcPr/a:solidFill/a:srgbClr", NS).get("val"), fill)
        continuation = cells[0][1].find("a:tcPr", NS)
        self.assertEqual(continuation.get("anchor"), "b")
        self.assertEqual(continuation.get("marL"), str(12 * 9525))
        self.assertEqual(continuation.get("marR"), str(4 * 9525))
        self.assertEqual(continuation.find("a:lnL", NS).get("w"), str(2 * 9525))
        self.assertEqual(continuation.find("a:lnL/a:solidFill/a:srgbClr", NS).get("val"), "223344")
        self.assertEqual(cells[1][1].find("a:tcPr", NS).get("marT"), str(9 * 9525))
        self.assertEqual([node.text for node in slide.findall(".//a:t", NS)], ["合并项", "Top", "Bottom", "A", "B", "C"])

    def test_covered_table_cells_still_reject_text_and_paragraph_bodies(self) -> None:
        for continuation in ({"text": "cannot hide content", "fill": "#112233"}, {"paragraphs": [], "fill": "#112233"}):
            with self.subTest(continuation=continuation):
                payload = {
                    "schema": "ppt-master.semantic-table.v2", "x": 80, "y": 100, "width": 800, "height": 200,
                    "header_rows": 0, "rows": [[{"text": "Owner", "col_span": 2}, continuation]],
                }
                process, result = self.invoke(
                    "create", "-", "invalid.pptx",
                    payload={"schema": "psl.pptx.v1", "slides": [{"svg": svg_page(native_marker("table", payload))}]},
                )
                self.assertEqual(process.returncode, 2, result)
                self.assertTrue(process.stderr.strip())
                self.assertFalse((self.root / "invalid.pptx").exists())

    def test_formula_fallbacks_keep_native_shapes_and_choose_math_on_inspection(self) -> None:
        formula = {"latex": "x^2+1", "x": 100, "y": 120, "width": 600, "height": 180, "font_size": 30}
        preview = (
            '<rect x="100" y="120" width="600" height="180" fill="#EEEEEE"/>'
            '<text x="120" y="180" font-size="30">Fallback only</text>'
        )
        block_page = svg_page(native_marker("formula", formula, preview))
        inline_page = svg_page(
            '<text x="100" y="160" font-size="32">Value: '
            '<tspan data-pptx-inline-formula="x^2">x²</tspan>.</text>'
        )
        parts = self.create(block_page, inline_page)
        for number in (1, 2):
            slide = ET.fromstring(parts[f"ppt/slides/slide{number}.xml"])
            alternates = slide.findall(".//mc:AlternateContent", NS)
            self.assertEqual(len(alternates), 1)
            alternate = alternates[0]
            choice = alternate.find("mc:Choice", NS)
            fallback = alternate.find("mc:Fallback", NS)
            self.assertEqual(choice.get("Requires"), "a14")
            self.assertGreater(len(choice.findall(".//m:oMath", NS)), 0)
            self.assertGreater(len(fallback.findall(".//p:sp", NS)), 0)
            self.assertEqual(fallback.findall(".//m:oMath", NS), [])
            self.assertEqual(fallback.findall(".//p:pic", NS), [])
            identifiers = [element.get("id") for element in slide.findall(".//p:cNvPr", NS)]
            self.assertEqual(len(identifiers), len(set(identifiers)))
        process, result = self.invoke("inspect", "deck.pptx")
        self.assertEqual(process.returncode, 0, (result, process.stderr))
        self.assertEqual(result["slides"][0]["text"], ["x2+1"])
        self.assertEqual(result["slides"][1]["text"], ["Value: x2."])
        self.assertEqual([slide["objects"]["shapes"] for slide in result["slides"]], [1, 1])

    def test_chart_parts_are_unique_across_dense_slide_boundary(self) -> None:
        first_slide = []
        for number in range(101):
            payload = chart_payload()
            payload["categories"] = ["A"]
            payload["series"] = [{"name": f"Series {number}", "values": [number]}]
            first_slide.append(native_marker("chart", payload))
        final_payload = copy.deepcopy(chart_payload())
        final_payload["categories"] = ["A"]
        final_payload["series"] = [{"name": "Final", "values": [999]}]
        parts = self.create(svg_page("".join(first_slide)), svg_page(native_marker("chart", final_payload)))
        chart_parts = {name: data for name, data in parts.items() if name.startswith("ppt/charts/chart") and name.endswith(".xml")}
        workbooks = {name: data for name, data in parts.items() if name.endswith(".xlsx")}
        self.assertEqual(len(chart_parts), 102)
        self.assertEqual(len(workbooks), 102)
        chart_values = []
        workbook_values = []
        for data in chart_parts.values():
            chart = ET.fromstring(data)
            chart_values.extend(int(node.text) for node in chart.findall(".//c:numCache/c:pt/c:v", NS))
        for data in workbooks.values():
            sheet = ET.fromstring(zip_parts(data)["xl/worksheets/sheet1.xml"])
            workbook_values.extend(int(node.text) for node in sheet.findall(".//s:v", NS))
        self.assertEqual(sorted(chart_values), list(range(101)) + [999])
        self.assertEqual(sorted(workbook_values), list(range(101)) + [999])


if __name__ == "__main__":
    unittest.main()
