#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Controlled in-place and derivative editing for WPS (.wps) documents."""

from typing import Dict, Any, List, Optional
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from wps_rtf_builder import build_document_from_spec
from read_wps import read_bytes, ParseError
from wps_selective import detect_heading
from wps_access import read_input


MAX_EDIT_SPEC_BYTES = 2 * 1024 * 1024  # 2 MiB


def load_wps_to_blocks(input_path: Path, parsed=None) -> Any:
    """Parse existing WPS document into structured blocks and tables."""
    try:
        if parsed is None:
            parsed = read_bytes(read_input(input_path), source=str(input_path))
    except ParseError as exc:
        raise RuntimeError(f"Failed to read input WPS document: {exc}") from exc

    if parsed.get("status") not in ("ok", "partial"):
        raise RuntimeError(f"Document parse status rejected: {parsed.get('status')}")

    raw_paragraphs = parsed.get("paragraphs", [])
    paragraphs = []
    for p in raw_paragraphs:
        if isinstance(p, dict):
            paragraphs.append(str(p.get("text", "")))
        else:
            paragraphs.append(str(p))

    tables = []
    for tbl in parsed.get("tables", []):
        rows = []
        for r in tbl.get("rows", []):
            row_vals = [c.get("text", "") if isinstance(c, dict) else str(c) for c in r]
            rows.append(row_vals)
        tables.append(rows)

    return paragraphs, tables


def apply_edit_operations(
    paragraphs: List[str],
    tables: List[List[List[str]]],
    operations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply an ordered list of edit operations to paragraphs and tables."""
    stats = {
        "text_replacements": 0,
        "paragraphs_modified": 0,
        "paragraphs_deleted": 0,
        "cells_updated": 0,
        "appended_blocks": [],
    }

    for op in operations:
        op_type = op.get("operation") or op.get("op")
        if op_type == "replace_text":
            find_str = op.get("find", "")
            replace_str = op.get("replace", "")
            if not find_str:
                continue

            for idx, p in enumerate(paragraphs):
                if find_str in p:
                    count = p.count(find_str)
                    paragraphs[idx] = p.replace(find_str, replace_str)
                    stats["text_replacements"] += count

            for t_idx, tbl in enumerate(tables):
                for r_idx, row in enumerate(tbl):
                    for c_idx, val in enumerate(row):
                        if find_str in val:
                            count = val.count(find_str)
                            tables[t_idx][r_idx][c_idx] = val.replace(find_str, replace_str)
                            stats["text_replacements"] += count

        elif op_type == "replace_paragraph":
            p_idx = int(op.get("index", 1)) - 1
            new_text = str(op.get("text", ""))
            if 0 <= p_idx < len(paragraphs):
                paragraphs[p_idx] = new_text
                stats["paragraphs_modified"] += 1
            else:
                raise IndexError(f"Paragraph index {p_idx + 1} out of range (1..{len(paragraphs)})")

        elif op_type == "delete_paragraph":
            p_idx = int(op.get("index", 1)) - 1
            if 0 <= p_idx < len(paragraphs):
                paragraphs.pop(p_idx)
                stats["paragraphs_deleted"] += 1
            else:
                raise IndexError(f"Paragraph index {p_idx + 1} out of range (1..{len(paragraphs)})")

        elif op_type == "update_cell":
            t_idx = int(op.get("table_index", 0))
            r_idx = int(op.get("row", 0))
            c_idx = int(op.get("col", 0))
            new_val = str(op.get("text", ""))
            if not (0 <= t_idx < len(tables)):
                raise IndexError(f"Table index {t_idx} out of range (0..{len(tables) - 1})")
            tbl = tables[t_idx]
            if not (0 <= r_idx < len(tbl)):
                raise IndexError(f"Row index {r_idx} out of range (0..{len(tbl) - 1})")
            row = tbl[r_idx]
            if not (0 <= c_idx < len(row)):
                raise IndexError(f"Column index {c_idx} out of range (0..{len(row) - 1})")
            row[c_idx] = new_val
            stats["cells_updated"] += 1

        elif op_type == "append_blocks":
            extra_blocks = op.get("blocks", [])
            if isinstance(extra_blocks, list):
                stats["appended_blocks"].extend(extra_blocks)
        else:
            raise ValueError(f"Unsupported edit operation: {op_type}")

    return stats


def rebuild_spec_from_edited(
    paragraphs: List[str],
    tables: List[List[List[str]]],
    appended_blocks: List[Dict[str, Any]],
    theme: str = "official",
) -> Dict[str, Any]:
    """Rebuild a declarative document spec from edited paragraphs and tables."""
    blocks = []

    for idx, p in enumerate(paragraphs):
        lvl, title = detect_heading(p)
        if lvl is not None:
            blocks.append({"type": "heading", "level": lvl, "text": p})
        elif idx == 0 and len(paragraphs) > 1 and len(p) <= 40:
            # First short paragraph is commonly the document title
            blocks.append({"type": "title", "text": p})
        else:
            blocks.append({"type": "paragraph", "text": p})

    for tbl_rows in tables:
        blocks.append({"type": "table", "rows": tbl_rows})

    blocks.extend(appended_blocks)

    return {
        "theme": theme,
        "blocks": blocks,
    }


def execute_edit_task(
    input_path: Path,
    output_path: Path,
    operations: List[Dict[str, Any]],
    theme: str = "official",
    allow_lossy_reflow: bool = False,
) -> Dict[str, Any]:
    """Run full edit pipeline: read -> modify -> rebuild -> write atomic -> readback verify."""
    from wps_task_paths import delivery_filename
    output_path = delivery_filename(output_path)
    input_path = Path(input_path)
    from wps_output_contract import require_output_path
    require_output_path(output_path, 'rtf')
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if input_path.resolve() == output_path.resolve():
        raise ValueError("Reflow editing requires a new output file; original is protected")
    source_bytes = read_input(input_path)
    original = read_bytes(source_bytes)
    if original.get("status") not in ("ok", "partial"):
        raise ValueError("No reliable content available for reflow")
    order_unknown = bool(original.get("tables") and not original.get("blocks"))
    if allow_lossy_reflow and output_path.exists():
        raise ValueError("Explicit lossy reflow requires an unused output name")
    if original.get("status") == "partial" and not allow_lossy_reflow:
        raise ValueError("Partial input cannot be safely reflow-edited; inspect omissions and prepare an explicit new specification")
    if order_unknown and not allow_lossy_reflow:
        raise ValueError("Input table/paragraph order is unavailable; automatic reconstruction refused")
    order = [dict(item) for item in original.get("blocks", [])]
    paragraph_tokens = [item for item in order if item["type"] == "paragraph"]
    for operation in operations:
        if (operation.get("operation") or operation.get("op")) == "delete_paragraph" and paragraph_tokens:
            index = int(operation.get("index", 1)) - 1
            if not 0 <= index < len(paragraph_tokens):
                raise IndexError("Paragraph index out of range")
            paragraph_tokens.pop(index)["deleted"] = True
    paragraphs, tables = load_wps_to_blocks(input_path, parsed=original)
    stats = apply_edit_operations(paragraphs, tables, operations)
    spec = rebuild_spec_from_edited(paragraphs, tables, stats["appended_blocks"], theme=theme)

    if order:
        ordered = []
        paragraph_index = 0
        for item in order:
            if item.get("deleted"):
                continue
            if item["type"] == "paragraph":
                ordered.append({"type": "paragraph", "text": paragraphs[paragraph_index]})
                paragraph_index += 1
            else:
                table_spec = {"type": "table", "rows": tables[item["index"]]}
                source_table = original['tables'][item['index']]
                if source_table.get('merge_geometry_status') == 'explicit_grid_recovered' and original.get('status') == 'ok':
                    table_spec['merges'] = source_table.get('merges', [])
                    table_spec['repeat_header'] = False
                    bounds = source_table['cell_boundaries_twips']
                    table_spec['col_widths'] = [(right - left) / 20 for left, right in zip([0] + bounds[:-1], bounds)]
                ordered.append(table_spec)
        spec["blocks"] = ordered + stats["appended_blocks"]
    if allow_lossy_reflow and (original.get("status") == "partial" or order_unknown):
        spec["blocks"].insert(0, {"type": "paragraph", "text": "[部分内容重排副本：仅含可读取内容；原格式及未读取内容不保留，表格相对位置可能未知。]"})
    doc = build_document_from_spec(spec)
    raw_bytes = doc.render().encode("ascii")
    from wps_task_paths import output_candidate, write_candidate
    with output_candidate(output_path, prefix='.tmp_wps_edit_', exclusive=allow_lossy_reflow) as (temp_path, publish):
        write_candidate(temp_path, raw_bytes)
        candidate = read_bytes(read_input(temp_path))
        from wps_rtf_read import require_publishable_candidate
        require_publishable_candidate(candidate)
        from wps_content_validation import validate_content, validate_format
        content_validation = validate_content(doc, candidate)
        format_validation = validate_format(doc, candidate)
        if read_input(input_path) != source_bytes:
            raise RuntimeError("Source changed during reflow; publication stopped")
        publish()

    # Readback verification
    read_res = candidate
    if read_res.get("status") not in ("ok", "partial"):
        raise RuntimeError(f"Readback status rejected after edit: {read_res.get('status')}")

    return {
        "status": "success",
        **publish.receipt,
        "content_validation": content_validation,
        "format_validation": format_validation,
        "target": str(output_path),
        "source": str(input_path),
        "file_size": len(raw_bytes),
        "edit_stats": stats,
        "readback_status": read_res.get("status"),
        "edit_mode": "content_reflow_not_lossless",
        "layout_verification": "not_run",
        "native_wps": False,
        "output_format": "rtf",
        "binary_doc": False,
        "partial": original.get("status") == "partial" or read_res.get("status") == "partial" or order_unknown or bool(doc.spec_warning_count),
        "spec_warnings": doc.spec_warnings,
        "spec_warning_count": doc.spec_warning_count,
        "lossy_reflow_authorized": allow_lossy_reflow,
        "order_status": "unknown_paragraphs_then_tables" if order_unknown else "available_source_order",
        "losses": ["Original formatting, fields, objects and pagination are not preserved"]
                  + (["Relative paragraph/table order unknown; paragraphs followed by tables"] if order_unknown else [])
                  + (["Only recovered source content included; omitted regions remain omitted"] if original.get("status") == "partial" else []),
        "source_omitted_regions": original.get("omitted_regions", []),
        "source_issues": original.get("issues", []),
        "source_diagnostics": original.get("diagnostics", []),
        "source_reading_assessment": original.get("reading_assessment", {}),
        "paragraphs_count": len(paragraphs),
        "tables_count": len(tables),
    }


from wps_task_paths import cli_boundary, validate_argument


@cli_boundary
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    # text subcommand
    text_parser = subparsers.add_parser("text", help="replace text occurrences in wps document")
    text_parser.add_argument("input", help="source .wps file")
    text_parser.add_argument("output", help="bare .wps filename in task cwd")
    text_parser.add_argument("--find", required=True, help="text string to find")
    text_parser.add_argument("--replace", required=True, help="replacement text string")
    text_parser.add_argument("--theme", choices=("official", "business", "general"), default="official")

    # structure subcommand
    struct_parser = subparsers.add_parser("structure", help="modify or append paragraphs/blocks")
    struct_parser.add_argument("input", help="source .wps file")
    struct_parser.add_argument("output", help="bare .wps filename in task cwd")
    struct_parser.add_argument("--replace-paragraph", type=int, help="1-based paragraph index to replace")
    struct_parser.add_argument("--delete-paragraph", type=int, help="1-based paragraph index to delete")
    struct_parser.add_argument("--text", help="text for replacement or appending")
    struct_parser.add_argument("--append-block", help="inline JSON block to append")
    struct_parser.add_argument("--theme", choices=("official", "business", "general"), default="official")

    # table subcommand
    tbl_parser = subparsers.add_parser("table", help="update table cell value")
    tbl_parser.add_argument("input", help="source .wps file")
    tbl_parser.add_argument("output", help="bare .wps filename in task cwd")
    tbl_parser.add_argument("--table-index", type=int, default=0, help="0-based table index")
    tbl_parser.add_argument("--row", type=int, required=True, help="0-based row index")
    tbl_parser.add_argument("--col", type=int, required=True, help="0-based column index")
    tbl_parser.add_argument("--text", required=True, help="new cell text")
    tbl_parser.add_argument("--theme", choices=("official", "business", "general"), default="official")

    # batch subcommand
    batch_parser = subparsers.add_parser("batch", help="run arbitrary operations from JSON specification")
    batch_parser.add_argument("input", help="source .wps file")
    batch_parser.add_argument("output", help="bare .wps filename in task cwd")
    batch_parser.add_argument("--spec", help="inline JSON specification or file path")
    batch_parser.add_argument("--theme", choices=("official", "business", "general"), default="official")

    for command_parser in (text_parser, struct_parser, tbl_parser, batch_parser):
        command_parser.add_argument("--allow-lossy-reflow", action="store_true", help="explicitly reflow recovered content to a new unused file")
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help(sys.stderr)
        return 2

    validate_argument(args.input)
    validate_argument(args.output, output=True)
    input_file = Path(args.input)
    output_file = Path(args.output)
    theme = getattr(args, "theme", "official")
    operations = []

    if args.command == "text":
        operations.append({
            "operation": "replace_text",
            "find": args.find,
            "replace": args.replace,
        })
    elif args.command == "structure":
        if args.replace_paragraph is not None:
            if args.text is None:
                parser.error("--replace-paragraph requires --text")
            operations.append({
                "operation": "replace_paragraph",
                "index": args.replace_paragraph,
                "text": args.text,
            })
        elif args.delete_paragraph is not None:
            operations.append({
                "operation": "delete_paragraph",
                "index": args.delete_paragraph,
            })
        elif args.append_block:
            try:
                block_obj = json.loads(args.append_block)
            except json.JSONDecodeError as exc:
                parser.error(f"Invalid JSON in --append-block: {exc}")
            operations.append({
                "operation": "append_blocks",
                "blocks": [block_obj],
            })
        elif args.text:
            operations.append({
                "operation": "append_blocks",
                "blocks": [{"type": "paragraph", "text": args.text}],
            })
        else:
            parser.error("No structure modification specified")
    elif args.command == "table":
        operations.append({
            "operation": "update_cell",
            "table_index": args.table_index,
            "row": args.row,
            "col": args.col,
            "text": args.text,
        })
    elif args.command == "batch":
        if args.spec:
            spec_path = Path(args.spec)
            if spec_path.is_file():
                validate_argument(args.spec)
            raw = read_input(spec_path).decode("utf-8") if spec_path.is_file() else args.spec
        else:
            if sys.stdin.isatty():
                parser.error("Specification required via --spec or stdin for batch mode")
            raw = sys.stdin.read(MAX_EDIT_SPEC_BYTES)
        try:
            batch_spec = json.loads(raw)
        except json.JSONDecodeError as exc:
            parser.error(f"Invalid JSON in batch spec: {exc}")
        if isinstance(batch_spec, list):
            operations = batch_spec
        elif isinstance(batch_spec, dict) and "operations" in batch_spec:
            operations = batch_spec["operations"]
        else:
            parser.error("Batch spec must be a list of operations or a dict containing 'operations'")

    try:
        from wps_delivery import finish_delivery
        res = execute_edit_task(input_file, output_file, operations, theme=theme, allow_lossy_reflow=args.allow_lossy_reflow)
        inputs = [input_file]
        if args.command == 'batch' and args.spec and Path(args.spec).is_file():
            inputs.append(args.spec)
        finish_delivery(res, output_file, inputs)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 3 if args.allow_lossy_reflow and res["partial"] else 0
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "error": str(exc),
            **getattr(exc, "publication", {}),
        }, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
