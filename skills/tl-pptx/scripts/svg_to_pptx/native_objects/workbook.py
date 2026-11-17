"""Minimal workbook generation for native chart data."""

from __future__ import annotations

import io
from typing import Any

from xlsxwriter import Workbook as XlsxWriterWorkbook

from ..drawingml.utils import _xml_escape
from .marker_common import _excel_col


def _xlsx_cell_ref(row: int, col: int) -> str:
    return f"{_excel_col(col)}{row}"


def _xlsx_cell(value: Any, row: int, col: int) -> str:
    if value is None:
        return ""
    ref = _xlsx_cell_ref(row, col)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return (
        f'<c r="{ref}" t="inlineStr"><is><t>{_xml_escape(str(value))}</t></is></c>'
    )


def _minimal_workbook(rows: list[list[Any]]) -> bytes:
    buffer = io.BytesIO()
    workbook = XlsxWriterWorkbook(buffer, {
        "in_memory": True,
        "strings_to_formulas": False,
        "strings_to_urls": False,
    })
    worksheet = workbook.add_worksheet("Sheet1")
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            worksheet.write(row_index, col_index, value)
    workbook.close()
    return buffer.getvalue()



def _minimal_category_chart_workbook(chart_data: dict[str, Any]) -> bytes:
    if chart_data.get("kind") == "combo" and chart_data.get("independent_categories"):
        plots = chart_data["plots"]
        column_count = max(
            int(plot["start_column"]) + len(plot["series"]) - 1
            for plot in plots
        )
        rows: list[list[Any]] = [[None] * column_count]
        for plot in plots:
            category_column = int(plot["category_column"]) - 1
            start_column = int(plot["start_column"]) - 1
            for offset, series in enumerate(plot["series"]):
                rows[0][start_column + offset] = series["name"]
            for point_index, category in enumerate(plot["categories"], start=1):
                while len(rows) <= point_index:
                    rows.append([None] * column_count)
                rows[point_index][category_column] = category
                for offset, series in enumerate(plot["series"]):
                    rows[point_index][start_column + offset] = (
                        series["values"][point_index - 1]
                    )
        return _minimal_workbook(rows)

    categories = chart_data["categories"]
    series = chart_data["series"]
    rows: list[list[Any]] = [[None] + [item["name"] for item in series]]
    for row_index, category in enumerate(categories):
        rows.append([category] + [item["values"][row_index] for item in series])
    return _minimal_workbook(rows)


def _minimal_xy_chart_workbook(chart_data: dict[str, Any]) -> bytes:
    series = chart_data["series"]
    is_bubble = chart_data["type"] == "bubble"
    rows: list[list[Any]] = [[]]
    for item in series:
        rows[0].extend([f"{item['name']} X", item["name"]])
        if is_bubble:
            rows[0].append(f"{item['name']} Size")

    max_points = max(len(item["x"]) for item in series)
    for point_idx in range(max_points):
        row: list[Any] = []
        for item in series:
            if point_idx < len(item["x"]):
                row.extend([item["x"][point_idx], item["y"][point_idx]])
                if is_bubble:
                    row.append(item["sizes"][point_idx])
            else:
                row.extend(["", ""])
                if is_bubble:
                    row.append("")
        rows.append(row)
    return _minimal_workbook(rows)


def _minimal_chart_ex_workbook(chart_data: dict[str, Any]) -> bytes:
    chart_type = chart_data["type"]
    if chart_type in {"sunburst", "treemap"}:
        levels = chart_data["levels"]
        rows: list[list[Any]] = [[f"Level {idx + 1}" for idx in range(len(levels))] + ["Value"]]
        for row_idx, value in enumerate(chart_data["values"]):
            rows.append([level[row_idx] for level in levels] + [value])
        return _minimal_workbook(rows)
    if chart_type == "histogram":
        return _minimal_workbook([["Value"]] + [[value] for value in chart_data["values"]])
    if chart_type in {"funnel", "pareto", "waterfall"}:
        rows = [["Category", "Value"]]
        rows.extend(
            [category, chart_data["values"][idx]]
            for idx, category in enumerate(chart_data["categories"])
        )
        return _minimal_workbook(rows)
    if chart_type == "box_whisker":
        rows = [[]]
        for item in chart_data["series"]:
            rows[0].extend([f"{item['name']} Category", item["name"]])
        max_rows = max(len(item["values"]) for item in chart_data["series"])
        for row_idx in range(max_rows):
            row: list[Any] = []
            for item in chart_data["series"]:
                if row_idx < len(item["values"]):
                    row.extend([item["categories"][row_idx], item["values"][row_idx]])
                else:
                    row.extend(["", ""])
            rows.append(row)
        return _minimal_workbook(rows)
    raise RuntimeError(f"Native PPTX {chart_type} chart is outside current basic chart support")
