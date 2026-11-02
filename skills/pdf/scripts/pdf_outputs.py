"""JSON and Markdown views of a PDF read result; no PDF authoring."""

import html
import json
import re


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")

def _cell(value):
    if value is None:
        return ""
    return html.escape(str(value), quote=False).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _literal(text):
    fence = "`" * max(3, max((len(item) + 1 for item in re.findall(r"`+", text)), default=0))
    return f"{fence}text\n{text.rstrip()}\n{fence}"


def to_markdown(result):
    """Keep page provenance; do not infer semantic headings or merge tables."""
    lines = ["# PDF 读取结果", "", f"状态：{result['status']}", ""]
    coverage = result.get("coverage", {})
    lines.append("已处理页：" + ", ".join(str(number) for number in coverage.get("processed_pages", [])))
    if coverage.get("omitted_pages"):
        lines.append("未处理页：" + ", ".join(str(number) for number in coverage["omitted_pages"]))
    lines.extend(["", "原文与表格分别展示，可能包含相同内容；复杂分栏阅读顺序与表格结构需按原页复核。", ""])
    for warning in result.get("warnings", []):
        lines.append(f"- {_cell(warning.get('code', 'warning'))}：{_cell(warning.get('message', ''))}")
    for page in result.get("pages", []):
        number = page["number"]
        lines.extend(["", f"## 第 {number} 页", ""])
        if page.get("text"):
            lines.extend([_literal(page["text"]), ""])
        for diagnostic in page.get("diagnostics", []):
            lines.append(f"- {_cell(diagnostic.get('code', 'warning'))}：{_cell(diagnostic.get('message', ''))}")
        for index, table in enumerate(page.get("tables", []), 1):
            lines.extend(["", f"### 表格 {index}", "", "列标题仅表示索引；原始行均保留在表格正文中。", ""])
            cells = table.get("cells", [])
            count = table.get("col_count", max((len(row) for row in cells), default=0))
            if not count:
                continue
            lines.append("| " + " | ".join(f"列 {column + 1}" for column in range(count)) + " |")
            lines.append("| " + " | ".join("---" for _ in range(count)) + " |")
            for row in cells:
                padded = list(row) + [None] * max(0, count - len(row))
                lines.append("| " + " | ".join(_cell(cell) for cell in padded) + " |")
        for index, resource in enumerate(page.get("image_resources", []), 1):
            if resource.get("asset"):
                lines.extend(["", f"![第 {number} 页图片资源 {index}]({resource['asset']})", ""])
        preview = page.get("preview") or {}
        if preview.get("asset"):
            lines.extend(["", f"### 第 {number} 页预览", "", f"![第 {number} 页预览]({preview['asset']})", ""])
    return "\n".join(lines).rstrip() + "\n"


def output_summary(result, directory, files):
    """Bound stdout when the complete result is delivered as files."""
    prefix = directory.rstrip("/")
    artifacts = [{"path": f"{prefix}/{name}", "bytes": len(data)} for name, data in files.items()]
    return {
        "schema_version": result["schema_version"],
        "operation": result.get("operation", "read"),
        "status": result["status"],
        "document": result.get("document", {}),
        "coverage": result.get("coverage", {}),
        "warnings": result.get("warnings", []),
        "page_diagnostics": [
            {"number": page["number"], "status": page.get("status"), "diagnostics": page.get("diagnostics", [])}
            for page in result.get("pages", []) if page.get("diagnostics")
        ],
        "result_file": f"{prefix}/result.json" if "result.json" in files else None,
        "artifacts": artifacts,
        "delivery": {"files_verified": True, "archive_status": "not_run", "temporary_files_created": False},
    }
