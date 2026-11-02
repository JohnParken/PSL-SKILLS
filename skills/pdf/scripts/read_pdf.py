#!/usr/bin/env python3
"""Read PDF files under the confirmed task directory; never write a PDF.

stdout: one JSON result. stderr: human-readable diagnostics. Exit codes:
0 successful result or environment check, 2 usable partial result, 1 error.
"""

import argparse
import contextlib
import hashlib
import io
import json
import math
import platform
import re
import sys
from pathlib import Path


sys.dont_write_bytecode = True
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from pdf_io import AssetCollector, PdfTaskError, Workspace, relative_parts
from pdf_outputs import json_bytes, output_summary, to_markdown


PYMUPDF_VERSION = "1.26.6"
DEFAULT_LIMITS = {
    "max_pages": 200,
    "max_chars": 500000,
    "max_images": 200,
    "max_image_pixels": 20000000,
    "max_output_bytes": 67108864,
    "max_input_bytes": 104857600,
}
HARD_LIMITS = {
    "max_pages": 2000,
    "max_chars": 5000000,
    "max_images": 2000,
    "max_image_pixels": 100000000,
    "max_output_bytes": 268435456,
    "max_input_bytes": 536870912,
}
ALLOWED_FIELDS = {
    "operation", "input", "pages", "clip", "extract", "password_file", "table_strategy", "dpi", "output_dir", "formats", *DEFAULT_LIMITS,
}


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise PdfTaskError("invalid_arguments", "命令行参数无效；使用 --help 核对支持的参数。")


def require_engine():
    if sys.version_info < (3, 10):
        raise PdfTaskError("unsupported_python", "需要 Python 3.10 或以上版本。")
    try:
        import pymupdf
    except ImportError as exc:
        raise PdfTaskError("dependency_missing", "缺少 PyMuPDF；请由宿主按 requirements.txt 配置依赖，不自动安装。") from exc
    if pymupdf.VersionBind != PYMUPDF_VERSION:
        raise PdfTaskError("dependency_version", f"当前 Skill 验证并固定使用 PyMuPDF=={PYMUPDF_VERSION}，请核对运行环境。")
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    return pymupdf


def parse_pages(value):
    if value is None:
        return None
    if isinstance(value, list):
        if not value or any(type(number) is not int or number < 1 for number in value):
            raise PdfTaskError("invalid_pages", "pages 列表必须包含正整数页码。")
        numbers = value
    elif isinstance(value, str) and value.strip():
        numbers = []
        for term in value.split(","):
            match = re.fullmatch(r"\s*([0-9]+)(?:\s*-\s*([0-9]+))?\s*", term)
            if not match:
                raise PdfTaskError("invalid_pages", "页范围使用 1-3,5 形式。")
            start = int(match.group(1))
            end = int(match.group(2) or start)
            if start < 1 or end < start or end - start > 10000:
                raise PdfTaskError("invalid_pages", "页范围必须为正整数且有序，单个范围不得超过一万页。")
            numbers.extend(range(start, end + 1))
            if len(numbers) > 10000:
                raise PdfTaskError("invalid_pages", "请求页范围过大，请分批读取。")
    else:
        raise PdfTaskError("invalid_pages", "pages 必须是页范围字符串或正整数列表。")
    if len(numbers) > 10000:
        raise PdfTaskError("invalid_pages", "请求页范围过大，请分批读取。")
    return sorted(set(numbers))


def string_list(value, allowed, name):
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or item not in allowed for item in value):
        raise PdfTaskError("invalid_options", f"{name} 必须为非空的受支持名称列表。")
    if len(set(value)) != len(value):
        raise PdfTaskError("invalid_options", f"{name} 包含重复项。")
    return value


def validate_task(task):
    if not isinstance(task, dict) or set(task) - ALLOWED_FIELDS:
        raise PdfTaskError("invalid_task", "任务必须为对象且仅包含已声明的字段。")
    result = dict(task)
    if result.get("operation") not in ("read", "inspect"):
        raise PdfTaskError("invalid_operation", "第一阶段只支持 read 与 inspect，不支持写入 PDF 或 OCR。")
    relative_parts(result.get("input"))
    for key, default in DEFAULT_LIMITS.items():
        value = result.get(key, default)
        if type(value) is not int or value < 1 or value > HARD_LIMITS[key]:
            raise PdfTaskError("invalid_limit", f"{key} 必须是 1 至 {HARD_LIMITS[key]} 的整数。")
        result[key] = value
    result["pages"] = parse_pages(result.get("pages"))
    result["extract"] = string_list(result.get("extract", ["text"]), {"text", "tables", "images", "preview"}, "extract")
    clip = result.get("clip")
    if clip is not None:
        if not isinstance(clip, list) or len(clip) != 4 or any(type(number) not in (int, float) or not math.isfinite(number) for number in clip):
            raise PdfTaskError("invalid_clip", "clip 必须是四个有限数字，单位为未旋转页面左上原点的 pt。")
        if min(clip) < 0 or clip[2] <= clip[0] or clip[3] <= clip[1]:
            raise PdfTaskError("invalid_clip", "clip 必须为非负且具有正面积的矩形。")
    result["clip"] = clip
    result["table_strategy"] = result.get("table_strategy", "lines")
    if result["table_strategy"] not in ("lines", "lines_strict", "text"):
        raise PdfTaskError("invalid_table_strategy", "表格策略只支持 lines、lines_strict、text。")
    result["dpi"] = result.get("dpi", 120)
    if type(result["dpi"]) is not int or not 36 <= result["dpi"] <= 300:
        raise PdfTaskError("invalid_dpi", "预览 dpi 必须是 36 至 300 的整数。")
    if result.get("password_file") is not None:
        relative_parts(result["password_file"])
    if result.get("output_dir") is not None:
        relative_parts(result["output_dir"])
        result["formats"] = string_list(result.get("formats", ["json", "markdown"]), {"json", "markdown"}, "formats")
    elif "formats" in result or "preview" in result["extract"]:
        raise PdfTaskError("output_required", "导出格式和页面预览需要明确指定新的 output_dir。")
    if result["operation"] == "inspect" and any(key in task for key in ("pages", "clip", "extract", "output_dir", "formats", "table_strategy", "dpi")):
        raise PdfTaskError("invalid_inspect_options", "inspect 只检查文档元信息；按页提取或导出请使用 read。")
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PdfTaskError("invalid_json", "任务 JSON 含重复字段。")
        result[key] = value
    return result


def read_task(workspace, name):
    raw = workspace.read_bytes(name, 1048576)
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise PdfTaskError("invalid_json", "任务文件必须是有效的 UTF-8 JSON。") from exc


def inspect_document(engine, data, password):
    try:
        doc = engine.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise PdfTaskError("invalid_pdf", "无法打开 PDF，文件可能损坏或不受支持。") from exc
    with doc:
        if not doc.is_pdf:
            raise PdfTaskError("not_pdf", "输入不是 PDF 文档。")
        encrypted = bool(doc.needs_pass)
        if encrypted and not password:
            raise PdfTaskError("password_required", "PDF 需要密码，请通过 password_file 提供。")
        if encrypted and not doc.authenticate(password):
            raise PdfTaskError("invalid_password", "提供的密码无法打开 PDF。")
        return {
            "schema_version": "1.0",
            "operation": "inspect",
            "status": "partial" if doc.is_repaired else "ok",
            "document": {
                "page_count": doc.page_count,
                "metadata": doc.metadata,
                "toc": [{"level": item[0], "title": item[1], "page": item[2]} for item in doc.get_toc(simple=True)],
                "encrypted": encrypted,
                "repaired": bool(doc.is_repaired),
                "is_form_pdf": bool(doc.is_form_pdf),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            },
            "coverage": {"requested_pages": [], "processed_pages": [], "omitted_pages": []},
            "warnings": [{"code": "pdf_repaired", "message": "引擎在内存中修复了 PDF 结构；内容完整性需要复核。"}] if doc.is_repaired else [],
        }


def execute(workspace, task):
    task = validate_task(task)
    engine = require_engine()
    data = workspace.check_pdf_input(task["input"], task["max_input_bytes"])
    password = None
    if task.get("password_file"):
        try:
            password = workspace.read_bytes(task["password_file"], 16384).decode("utf-8").rstrip("\r\n")
        except UnicodeError as exc:
            raise PdfTaskError("invalid_password_file", "密码文件必须使用 UTF-8 编码。") from exc
    if task["operation"] == "inspect":
        result = inspect_document(engine, data, password)
        if len(json_bytes(result)) > task["max_output_bytes"]:
            raise PdfTaskError("output_budget_exceeded", "文档信息 JSON 超过输出预算；请增加预算。")
        return result
    from pdf_backend import extract_document

    collector = AssetCollector(task["max_output_bytes"])
    options = {key: value for key, value in task.items() if key not in ("operation", "input", "password_file", "output_dir", "formats")}
    options["password"] = password
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        result = extract_document(data, options, asset_sink=collector if task.get("output_dir") else None)
    if captured.getvalue():
        result.setdefault("warnings", []).append({"code": "engine_diagnostic", "message": "引擎产生额外提示，已与 JSON 数据通道隔离。"})
    result["operation"] = "read"
    result.setdefault("document", {}).update({"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "source": task["input"]})
    result["engine"] = {"name": "PyMuPDF", "version": engine.VersionBind, "ocr": "not_implemented"}
    result["delivery"] = {"archive_status": "not_run", "temporary_files_created": False}
    if task.get("output_dir"):
        files = dict(collector.files)
        if "json" in task["formats"]:
            files["result.json"] = json_bytes(result)
        if "markdown" in task["formats"]:
            files["result.md"] = to_markdown(result).encode("utf-8")
        summary = output_summary(result, task["output_dir"], files)
        if sum(map(len, files.values())) > task["max_output_bytes"] or len(json_bytes(summary)) > task["max_output_bytes"]:
            raise PdfTaskError("output_budget_exceeded", "完整结果或 stdout 摘要超过输出预算，未写出文件；请减少页数或增加预算。")
        workspace.export(task["output_dir"], files)
        return summary
    if len(json_bytes(result)) > task["max_output_bytes"]:
        raise PdfTaskError("output_budget_exceeded", "完整 JSON 超过输出预算；请减少页数或增加预算。")
    return result


def make_parser():
    parser = JsonArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)
    check = subparsers.add_parser("check", help="检查环境；不代替产物验收")
    check.add_argument("--input")
    check.add_argument("--max-input-bytes", type=int, default=DEFAULT_LIMITS["max_input_bytes"])
    check.add_argument("--write-check", action="store_true")
    run = subparsers.add_parser("run", help="执行 UTF-8 JSON 任务")
    run.add_argument("task_file")
    inspect_parser = subparsers.add_parser("inspect", help="读取元数据、页数和目录")
    read = subparsers.add_parser("read", help="按页读取 PDF 内容")
    for command in (inspect_parser, read):
        command.add_argument("input")
        command.add_argument("--password-file")
        for key in DEFAULT_LIMITS:
            command.add_argument("--" + key.replace("_", "-"), type=int)
    read.add_argument("--pages")
    read.add_argument("--extract")
    read.add_argument("--clip")
    read.add_argument("--table-strategy")
    read.add_argument("--dpi", type=int)
    read.add_argument("--output-dir")
    read.add_argument("--formats")
    return parser


def main(argv=None):
    try:
        args = make_parser().parse_args(argv)
        with Workspace(SKILL_ROOT) as workspace:
            if args.command == "check":
                if not 1 <= args.max_input_bytes <= HARD_LIMITS["max_input_bytes"]:
                    raise PdfTaskError("invalid_limit", f"max_input_bytes 必须是 1 至 {HARD_LIMITS['max_input_bytes']} 的整数。")
                engine = require_engine()
                if args.input:
                    workspace.check_pdf_input(args.input, args.max_input_bytes)
                if args.write_check:
                    workspace.write_probe()
                result = {
                    "status": "environment-ok", "check_kind": "environment", "artifact_validation": "not_run",
                    "python": platform.python_version(), "platform": platform.system(), "pymupdf": engine.VersionBind,
                    "input_readable": bool(args.input), "task_writable": True if args.write_check else None,
                    "capabilities": {"read": True, "render": True, "tables": True, "ocr": False, "write_pdf": False},
                }
            else:
                if args.command == "run":
                    task = read_task(workspace, args.task_file)
                else:
                    task = {key: value for key, value in vars(args).items() if value is not None and key != "command"}
                    task["operation"] = args.command
                    for name in ("extract", "formats"):
                        if name in task:
                            task[name] = [value.strip() for value in task[name].split(",")]
                    if "clip" in task:
                        try:
                            task["clip"] = [float(value) for value in task["clip"].split(",")]
                        except ValueError as exc:
                            raise PdfTaskError("invalid_clip", "clip 使用 x0,y0,x1,y1 四个数字。") from exc
                result = execute(workspace, task)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 2 if result["status"] == "partial" else 0
    except Exception as exc:
        if isinstance(exc, PdfTaskError) or (hasattr(exc, "code") and hasattr(exc, "message")):
            code = exc.code
            message = exc.message
        elif isinstance(exc, OSError):
            code = "filesystem_error"
            message = "任务文件操作失败，请核对权限、路径和可用空间。"
        else:
            code = "internal_error"
            message = "读取未完成，请保留原件并报告错误类型：" + type(exc).__name__
        print(json.dumps({"schema_version": "1.0", "status": "error", "error": {"code": code, "message": message}}, ensure_ascii=False))
        print(f"PDF 读取失败 [{code}]：{message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
