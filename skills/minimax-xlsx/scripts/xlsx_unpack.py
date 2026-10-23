#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""

Historical interface reference only; do not run these examples in the maintained
workflow. <skill_dir> denotes the confirmed installation directory, and example data
paths are task-relative. These examples do not certify legacy runtime or temporary-file
behavior, enable disabled commands, or remove legacy dependencies. Any future supported
use needs separate maintenance and validation against SKILL.md first.
xlsx_unpack.py — Unpack an xlsx file into a working directory for XML editing.

Usage:
    python3 "<skill_dir>/scripts/xlsx_unpack.py" ./input.xlsx ./unpacked/

What it does:
1. Unzips the xlsx (which is a ZIP archive)
2. Pretty-prints all XML and .rels files for readability
3. Prints a summary of key files to edit
"""

import sys
import zipfile
import os
import shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_xml import parse_xml

MAX_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 10000


def pretty_print_xml(content: bytes) -> str:
    """Pretty-print validated, DTD-free XML; reject invalid input."""
    dom = parse_xml(content, dom=True)
    pretty = dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    lines = [line for line in pretty.splitlines() if line.strip()]
    return "\n".join(lines) + "\n"


def unpack(xlsx_path: str, output_dir: str) -> None:
    if not os.path.isfile(xlsx_path):
        print(f"ERROR: File not found: {xlsx_path}", file=sys.stderr)
        sys.exit(1)

    if not xlsx_path.lower().endswith((".xlsx", ".xlsm")):
        print(f"WARNING: '{xlsx_path}' does not have an .xlsx/.xlsm extension", file=sys.stderr)

    # Never remove or reuse caller-owned paths, including dangling symlinks.
    if os.path.lexists(output_dir):
        raise ValueError("Output path already exists; choose a new directory")
    prepared = []
    total = 0
    xml_count = 0
    with zipfile.ZipFile(xlsx_path, "r") as archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_BYTES:
            raise ValueError("Workbook exceeds package limits")
        names = set()
        for info in infos:
            name = info.filename
            if (name in names or name.startswith("/") or "\\" in name
                    or ":" in name or ".." in name.split("/") or not name):
                raise ValueError("Unsafe or duplicate ZIP member name")
            names.add(name)
            if info.is_dir():
                continue
            with archive.open(info) as handle:
                raw = handle.read(MAX_BYTES - total + 1)
            total += len(raw)
            if total > MAX_BYTES:
                raise ValueError("Workbook exceeds package limits")
            if name.endswith((".xml", ".rels")):
                raw = pretty_print_xml(raw).encode("utf-8")
                xml_count += 1
            prepared.append((name, raw))
    # Only a directory created by this invocation may be cleaned up on failure.
    os.makedirs(output_dir, exist_ok=False)
    try:
        for name, raw in prepared:
            target = Path(output_dir) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(raw)
    except Exception:
        shutil.rmtree(output_dir)
        raise
    print(f"Unpacked '{xlsx_path}' → '{output_dir}'")
    print(f"Pretty-printed {xml_count} XML/rels files\n")

    # Print key files grouped by category
    categories = {
        "Package root": ["[Content_Types].xml", "_rels/.rels"],
        "Workbook": ["xl/workbook.xml", "xl/_rels/workbook.xml.rels"],
        "Styles & Strings": ["xl/styles.xml", "xl/sharedStrings.xml"],
        "Worksheets": [],
    }

    all_files = []
    for dirpath, _, filenames in os.walk(output_dir):
        for fname in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fname), output_dir)
            all_files.append(rel)

    # Collect worksheets
    for rel in sorted(all_files):
        if rel.startswith("xl/worksheets/") and rel.endswith(".xml"):
            categories["Worksheets"].append(rel)

    print("Key files to inspect/edit:")
    for category, files in categories.items():
        if not files:
            continue
        print(f"\n  [{category}]")
        for f in files:
            full = os.path.join(output_dir, f)
            if os.path.isfile(full):
                size = os.path.getsize(full)
                print(f"    {f}  ({size:,} bytes)")
            else:
                print(f"    {f}  (not found)")

    # Warn about high-risk files present
    risky = {
        "xl/vbaProject.bin": "VBA macros — DO NOT modify",
        "xl/pivotTables": "Pivot tables — update source ranges carefully if shifting rows",
        "xl/charts": "Charts — update data ranges if shifting rows",
    }
    print("\n  [High-risk content detected:]")
    found_any = False
    for path, warning in risky.items():
        full = os.path.join(output_dir, path)
        if os.path.exists(full):
            print(f"    ⚠️  {path} — {warning}")
            found_any = True
    if not found_any:
        print("    ✓ None (safe to edit)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: <skill_dir>/scripts/xlsx_unpack.py <input.xlsx> <output_dir>")
        sys.exit(1)
    try:
        unpack(sys.argv[1], sys.argv[2])
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
