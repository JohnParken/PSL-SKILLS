#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""

Historical interface reference only; do not run these examples in the maintained
workflow. <skill_dir> denotes the confirmed installation directory, and example data
paths are task-relative. These examples do not certify legacy runtime or temporary-file
behavior, enable disabled commands, or remove legacy dependencies. Any future supported
use needs separate maintenance and validation against SKILL.md first.
DISABLED: historical reference only; the CLI refuses all structural edits.

xlsx_insert_row.py — Insert a new data row into a worksheet in an unpacked xlsx.

Usage examples:
    # Insert "Utilities" row at position 6, copying styles from row 5
    python3 "<skill_dir>/scripts/xlsx_insert_row.py" ./work/ --at 6 \\
        --sheet "Budget FY2025" \\
        --text A=Utilities \\
        --values B=3000 C=3000 D=3500 E=3500 \\
        --formula 'F=SUM(B{row}:E{row})' \\
        --copy-style-from 5

What it does:
  1. Shifts all rows >= at down by 1 (calls xlsx_shift_rows.py)
  2. Adds text values to sharedStrings.xml
  3. Inserts new row with specified cells (text, numbers, formulas)
  4. Copies cell styles from a reference row
  5. Updates dimension ref

The shift operation automatically expands SUM formulas that span the
insertion point, so total-row formulas are updated without extra work.

IMPORTANT: Run on an UNPACKED directory (from xlsx_unpack.py).
After running, repack with xlsx_pack.py.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import xml.dom.minidom
import xml.etree.ElementTree as ET

NS_SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

ET.register_namespace('', NS_SS)
ET.register_namespace('r', NS_REL)
ET.register_namespace('xdr', 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing')
ET.register_namespace('x14', 'http://schemas.microsoft.com/office/spreadsheetml/2009/9/main')
ET.register_namespace('xr2', 'http://schemas.microsoft.com/office/spreadsheetml/2015/revision2')
ET.register_namespace('mc', 'http://schemas.openxmlformats.org/markup-compatibility/2006')


def _tag(local: str) -> str:
    return f"{{{NS_SS}}}{local}"


def _write_tree(tree: ET.ElementTree, path: str) -> None:
    tree.write(path, encoding="unicode", xml_declaration=False)
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    try:
        dom = xml.dom.minidom.parseString(raw.encode("utf-8"))
        pretty = dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
        lines = [line for line in pretty.splitlines() if line.strip()]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass


def col_number(s: str) -> int:
    n = 0
    for c in s.upper():
        n = n * 26 + (ord(c) - 64)
    return n


def find_ws_path(work_dir: str, sheet_name: str | None) -> str:
    wb_tree = ET.parse(os.path.join(work_dir, "xl", "workbook.xml"))
    rid = None
    for sheet in wb_tree.getroot().iter(_tag("sheet")):
        if sheet_name is None or sheet.get("name") == sheet_name:
            rid = sheet.get(f"{{{NS_REL}}}id")
            break

    if rid is None:
        print(f"ERROR: Sheet not found: {sheet_name}")
        sys.exit(1)

    rels_tree = ET.parse(os.path.join(work_dir, "xl", "_rels", "workbook.xml.rels"))
    for rel in rels_tree.getroot():
        if rel.get("Id") == rid:
            return os.path.join(work_dir, "xl", rel.get("Target"))

    print(f"ERROR: Relationship not found: {rid}")
    sys.exit(1)


def add_shared_string(work_dir: str, text: str) -> int:
    ss_path = os.path.join(work_dir, "xl", "sharedStrings.xml")
    tree = ET.parse(ss_path)
    root = tree.getroot()

    idx = 0
    for si in root.findall(_tag("si")):
        t_el = si.find(_tag("t"))
        if t_el is not None and t_el.text == text:
            return idx
        idx += 1

    si = ET.SubElement(root, _tag("si"))
    t = ET.SubElement(si, _tag("t"))
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text

    root.set("count", str(int(root.get("count", "0")) + 1))
    root.set("uniqueCount", str(int(root.get("uniqueCount", "0")) + 1))

    _write_tree(tree, ss_path)
    return idx


def get_row_styles(ws_tree: ET.ElementTree, row_num: int) -> dict[str, int]:
    """Get {col_letter: style_index} for all cells in a row."""
    styles = {}
    for row_el in ws_tree.getroot().iter(_tag("row")):
        if row_el.get("r") == str(row_num):
            for c in row_el:
                ref = c.get("r", "")
                col_str = re.match(r"([A-Z]+)", ref)
                if col_str:
                    styles[col_str.group(1)] = int(c.get("s", "0"))
            break
    return styles


def parse_kv(specs: list[str] | None) -> dict[str, str]:
    if not specs:
        return {}
    result = {}
    for spec in specs:
        col, _, val = spec.partition("=")
        result[col.upper()] = val
    return result


def main() -> None:
    print("ERROR: This legacy structural-edit command is disabled because it cannot safely "
          "rewrite formulas. Use a spreadsheet application for row insertion/deletion.",
          file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
