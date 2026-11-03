#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Controlled in-place and derivative editing for WPS (.wps) documents."""

from typing import Dict, Any, List, Optional
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from wps_rtf_builder import build_document_from_spec
from read_wps import read_bytes, ParseError
from wps_selective import detect_heading


MAX_EDIT_SPEC_BYTES = 2 * 1024 * 1024  # 2 MiB


def load_wps_to_blocks(input_path: Path) -> Any:
    """Parse existing WPS document into structured blocks and tables."""
    raw_bytes = input_path.read_bytes()
    try:
        parsed = read_bytes(raw_bytes, source=str(input_path))
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
) -> Dict[str, Any]:
    """Run full edit pipeline: read -> modify -> rebuild -> write atomic -> readback verify."""
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    paragraphs, tables = load_wps_to_blocks(input_path)
    stats = apply_edit_operations(paragraphs, tables, operations)
    spec = rebuild_spec_from_edited(paragraphs, tables, stats["appended_blocks"], theme=theme)

    doc = build_document_from_spec(spec)
    target_dir = output_path.parent.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=str(target_dir),
        prefix=".tmp_wps_edit_",
        suffix=".wps",
    )
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(temp_fd, "w", encoding="ascii") as fp:
            fp.write(doc.render())
        os.replace(temp_path, output_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise

    # Readback verification
    raw_bytes = output_path.read_bytes()
    read_res = read_bytes(raw_bytes)
    if read_res.get("status") not in ("ok", "partial"):
        raise RuntimeError(f"Readback status rejected after edit: {read_res.get('status')}")

    return {
        "status": "success",
        "target": str(output_path),
        "source": str(input_path),
        "file_size": len(raw_bytes),
        "edit_stats": stats,
        "readback_status": read_res.get("status"),
        "paragraphs_count": len(paragraphs),
        "tables_count": len(tables),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    # text subcommand
    text_parser = subparsers.add_parser("text", help="replace text occurrences in wps document")
    text_parser.add_argument("input", help="source .wps file")
    text_parser.add_argument("output", help="destination .wps file")
    text_parser.add_argument("--find", required=True, help="text string to find")
    text_parser.add_argument("--replace", required=True, help="replacement text string")
    text_parser.add_argument("--theme", choices=("official", "business", "general"), default="official")

    # structure subcommand
    struct_parser = subparsers.add_parser("structure", help="modify or append paragraphs/blocks")
    struct_parser.add_argument("input", help="source .wps file")
    struct_parser.add_argument("output", help="destination .wps file")
    struct_parser.add_argument("--replace-paragraph", type=int, help="1-based paragraph index to replace")
    struct_parser.add_argument("--delete-paragraph", type=int, help="1-based paragraph index to delete")
    struct_parser.add_argument("--text", help="text for replacement or appending")
    struct_parser.add_argument("--append-block", help="inline JSON block to append")
    struct_parser.add_argument("--theme", choices=("official", "business", "general"), default="official")

    # table subcommand
    tbl_parser = subparsers.add_parser("table", help="update table cell value")
    tbl_parser.add_argument("input", help="source .wps file")
    tbl_parser.add_argument("output", help="destination .wps file")
    tbl_parser.add_argument("--table-index", type=int, default=0, help="0-based table index")
    tbl_parser.add_argument("--row", type=int, required=True, help="0-based row index")
    tbl_parser.add_argument("--col", type=int, required=True, help="0-based column index")
    tbl_parser.add_argument("--text", required=True, help="new cell text")
    tbl_parser.add_argument("--theme", choices=("official", "business", "general"), default="official")

    # batch subcommand
    batch_parser = subparsers.add_parser("batch", help="run arbitrary operations from JSON specification")
    batch_parser.add_argument("input", help="source .wps file")
    batch_parser.add_argument("output", help="destination .wps file")
    batch_parser.add_argument("--spec", help="inline JSON specification or file path")
    batch_parser.add_argument("--theme", choices=("official", "business", "general"), default="official")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help(sys.stderr)
        return 2

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
            raw = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else args.spec
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
        res = execute_edit_task(input_file, output_file, operations, theme=theme)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "error": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
