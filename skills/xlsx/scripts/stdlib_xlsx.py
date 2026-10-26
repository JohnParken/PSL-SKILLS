#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Standard-library XLSX creation, local editing, inspection and limited calculation.

Python 3.9+. See references/python-calculation.md for the JSON contract.
"""
import argparse
import csv
import io
import json
import posixpath
import re
import struct
import sys
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

# Explicit local import also works when launched with python -I -S -B.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from formula_engine import FormulaError, evaluate
from xlsx_xml import parse_xml

class UnsupportedInputError(ValueError):
    """The input is outside native spreadsheet reading support."""


WPS_UNSUPPORTED_MESSAGE = '不支持读取 WPS 格式（.wps/.et/.ett），请使用 wps skill 的 scripts/read_wps.py；本入口不会自动调用该技能。'

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = "{" + MAIN + "}"
MAX_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_CELLS = 200000
MAX_CSV_BYTES = 16 * 1024 * 1024
MAX_CSV_FIELD = 131072


def xml(data):
    return parse_xml(data)


def address(ref):
    if not isinstance(ref, str):
        raise ValueError("Cell addresses must be strings")
    match = re.fullmatch(r"([A-Za-z]{1,3})([1-9][0-9]{0,6})", ref)
    if not match:
        raise ValueError("Invalid cell address: " + ref)
    col = 0
    for char in match[1].upper():
        col = col * 26 + ord(char) - 64
    row = int(match[2])
    if col > 16384 or row > 1048576:
        raise ValueError("Cell address outside XLSX bounds: " + ref)
    return row, col


def clean_text(value):
    if len(value) > 32767 or any(
        ord(c) < 32 and c not in "\t\r\n" or 0xD800 <= ord(c) <= 0xDFFF
        or ord(c) in (0xFFFE, 0xFFFF) for c in value
    ):
        raise ValueError("Invalid XML text or Excel cell text length")
    return value


def numeric(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite numbers are not supported")
    # Bound serialization and avoid gigantic decimal representations.
    if result and not -307 <= result.adjusted() <= 307:
        raise ValueError("Number outside supported XLSX numeric range")
    return result


def encoded_xml(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def relation_target(base, target):
    path = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                             else posixpath.join(posixpath.dirname(base), target))
    if path.startswith("../") or "\\" in path:
        raise ValueError("Invalid package relationship target")
    return path


class Workbook:
    def __init__(self, parts, infos=None):
        self.parts = parts
        self.infos = infos or {}
        self.changed = set()
        self.documents = {}
        self.sheets = {}
        self.cells = {}
        self.date1904 = False
        self.full_precision = True
        self._index()

    @classmethod
    def load(cls, path):
        if Path(path).suffix.lower() in (".wps", ".et", ".ett"):
            raise UnsupportedInputError(WPS_UNSUPPORTED_MESSAGE)
        if Path(path).suffix.lower() not in (".xlsx", ".xlsm"):
            raise ValueError("Only .xlsx and .xlsm packages are supported")
        with open(path, "rb") as probe:
            if probe.read(8) == bytes.fromhex("d0cf11e0a1b11ae1"):
                raise UnsupportedInputError("不支持读取 CFB 二进制内容；它不是原生 XLSX/XLSM，请使用 wps skill 的 scripts/read_wps.py 识别其有限支持范围；CFB 签名不证明 WPS 来源。")
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_BYTES:
                raise ValueError("Workbook exceeds package limits")
            names = [i.filename for i in infos]
            if len(set(names)) != len(names):
                raise ValueError("Duplicate ZIP members")
            if any(n.startswith("/") or "\\" in n or ".." in n.split("/") for n in names):
                raise ValueError("Unsafe ZIP member name")
            if any(n.startswith("_xmlsignatures/") for n in names):
                raise ValueError("Signed workbooks are not supported for this workflow")
            parts = {i.filename: archive.read(i) for i in infos}
        return cls(parts, {i.filename: i for i in infos})

    @classmethod
    def create(cls, spec, appearance=None):
        sheets = validate_spec(spec)
        template = Path(__file__).resolve().parent.parent / "assets" / "minimal_xlsx"
        # Source asset names satisfy skill naming rules; ZIP names follow OOXML.
        # Restore names in memory, never by renaming files in the installed skill.
        package_names = {
            "content_types.xml": "[Content_Types].xml",
            "_rels/root.rels": "_rels/.rels",
        }
        parts = {}
        for path in template.rglob("*"):
            if path.is_file():
                source_name = path.relative_to(template).as_posix()
                package_name = package_names.get(source_name, source_name)
                if package_name in parts:
                    raise ValueError("Duplicate template package member: " + package_name)
                parts[package_name] = path.read_bytes()
        wb = ET.Element(NS + "workbook")
        listing = ET.SubElement(wb, NS + "sheets")
        rels = ET.Element("{" + PKG + "}Relationships")
        types = xml(parts["[Content_Types].xml"])
        for item in list(types):
            if item.get("PartName", "").startswith("/xl/worksheets/"):
                types.remove(item)
        for name in list(parts):
            if name.startswith("xl/worksheets/"):
                del parts[name]
        for index, sheet in enumerate(sheets, 1):
            part = "xl/worksheets/sheet%d.xml" % index
            ET.SubElement(listing, NS + "sheet", {
                "name": sheet["name"], "sheetId": str(index), "{" + REL + "}id": "s%d" % index})
            ET.SubElement(rels, "{" + PKG + "}Relationship", {
                "Id": "s%d" % index, "Type": REL + "/worksheet", "Target": "worksheets/sheet%d.xml" % index})
            ET.SubElement(types, "{" + CT + "}Override", {
                "PartName": "/" + part, "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"})
            parts[part] = ('<?xml version="1.0" encoding="utf-8"?><worksheet xmlns="%s"><sheetData/></worksheet>' % MAIN).encode()
        for name, kind in (("styles", "styles"), ("sharedStrings", "sharedStrings")):
            ET.SubElement(rels, "{" + PKG + "}Relationship", {
                "Id": name, "Type": REL + "/" + kind, "Target": name + ".xml"})
        # Generate the package entrypoint for the newly created workbook.
        root_rels = ET.Element("{" + PKG + "}Relationships")
        ET.SubElement(root_rels, "{" + PKG + "}Relationship", {
            "Id": "workbook", "Type": REL + "/officeDocument", "Target": "xl/workbook.xml"})
        parts["_rels/.rels"] = encoded_xml(root_rels)
        parts["xl/workbook.xml"] = encoded_xml(wb)
        parts["xl/_rels/workbook.xml.rels"] = encoded_xml(rels)
        parts["[Content_Types].xml"] = encoded_xml(types)
        book = cls(parts)
        book.patch(spec)
        book._created_here = True
        book.appearance_report = None
        if appearance is not None:
            from xlsx_appearance import apply_appearance
            book.appearance_report = apply_appearance(book, appearance, spec)
        return book

    def _index(self):
        root = xml(self.parts["xl/workbook.xml"])
        if root.tag != NS + "workbook":
            raise ValueError("Only transitional OOXML workbooks are supported")
        props = root.find(NS + "workbookPr")
        self.date1904 = props is not None and props.get("date1904") in ("1", "true")
        calc = root.find(NS + "calcPr")
        self.full_precision = calc is None or calc.get("fullPrecision") not in ("0", "false")
        rels = {r.get("Id"): r for r in xml(self.parts["xl/_rels/workbook.xml.rels"])}
        strings = []
        for rel in rels.values():
            if rel.get("Type", "").endswith("/sharedStrings"):
                shared_path = relation_target("xl/workbook.xml", rel.get("Target", ""))
                strings = ["".join(t.text or "" for t in item.iter(NS + "t"))
                           for item in xml(self.parts[shared_path])]
        self.cells = {}
        self.sheets = {}
        count = 0
        for sheet in root.findall(NS + "sheets/" + NS + "sheet"):
            name = sheet.get("name")
            if not name or name.casefold() in (s.casefold() for s in self.sheets):
                raise ValueError("Missing or duplicate sheet name")
            rel = rels[sheet.get("{" + REL + "}id")]
            if rel.get("TargetMode") == "External" or not rel.get("Type", "").endswith("/worksheet"):
                raise ValueError("Only internal worksheets are supported")
            part = relation_target("xl/workbook.xml", rel.get("Target", ""))
            self.sheets[name] = part
            sheet_cells = {}
            for cell in xml(self.parts[part]).findall(NS + "sheetData/" + NS + "row/" + NS + "c"):
                ref = cell.get("r", "").upper()
                address(ref)
                if ref in sheet_cells:
                    raise ValueError("Duplicate cell: " + name + "!" + ref)
                formula = cell.find(NS + "f")
                v = cell.find(NS + "v")
                kind = cell.get("t", "n")
                text = v.text if v is not None else None
                value = None
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall(NS + "is/" + NS + "t"))
                    if not value:
                        value = "".join(t.text or "" for t in cell.findall(NS + "is/" + NS + "r/" + NS + "t"))
                elif text is not None:
                    if kind == "s":
                        index = int(text)
                        if not 0 <= index < len(strings):
                            raise ValueError("Invalid shared string index")
                        value = strings[index]
                    elif kind == "b":
                        if text not in ("0", "1"):
                            raise ValueError("Invalid boolean value")
                        value = text == "1"
                    elif kind in ("str", "e", "d"):
                        value = text
                    elif kind == "n":
                        value = numeric(text)
                    else:
                        raise ValueError("Unknown cell type: " + kind)
                sheet_cells[ref] = {"value": value, "type": kind, "style": cell.get("s", "0"),
                                    "formula": formula.text or "" if formula is not None else None,
                                    "formula_attributes": dict(formula.attrib) if formula is not None else {},
                                    "cache_status": "unverified" if formula is not None and text is not None else
                                    "missing" if formula is not None else "not_applicable"}
                count += 1
                if count > MAX_CELLS:
                    raise ValueError("Workbook exceeds cell limit")
            self.cells[name] = sheet_cells

    def document(self, part):
        if part not in self.documents:
            self.documents[part] = parse_xml(self.parts[part], dom=True)
        return self.documents[part]

    @staticmethod
    def element(doc, local):
        prefix = doc.documentElement.prefix
        return doc.createElementNS(MAIN, (prefix + ":" if prefix else "") + local)

    def cell_node(self, sheet, ref):
        doc = self.document(self.sheets[sheet])
        data = doc.getElementsByTagNameNS(MAIN, "sheetData")[0]
        row_num, col_num = address(ref)
        rows = [n for n in data.childNodes if n.nodeType == n.ELEMENT_NODE and n.localName == "row"]
        row = next((n for n in rows if int(n.getAttribute("r")) == row_num), None)
        if row is None:
            row = self.element(doc, "row")
            row.setAttribute("r", str(row_num))
            later = next((n for n in rows if int(n.getAttribute("r")) > row_num), None)
            data.insertBefore(row, later)
        nodes = [n for n in row.childNodes if n.nodeType == n.ELEMENT_NODE and n.localName == "c"]
        cell = next((n for n in nodes if n.getAttribute("r").upper() == ref), None)
        if cell is None:
            cell = self.element(doc, "c")
            cell.setAttribute("r", ref)
            later = next((n for n in nodes if address(n.getAttribute("r"))[1] > col_num), None)
            row.insertBefore(cell, later)
        self.changed.add(self.sheets[sheet])
        return doc, cell

    def put_value(self, doc, cell, value, cache=False):
        for child in list(cell.childNodes):
            if child.nodeType == child.ELEMENT_NODE and child.localName in ("v", "is"):
                cell.removeChild(child)
        if cell.hasAttribute("t"):
            cell.removeAttribute("t")
        if value is None:
            return
        if isinstance(value, bool):
            cell.setAttribute("t", "b")
            text = "1" if value else "0"
        elif isinstance(value, (Decimal, int, float)):
            text = str(numeric(value))
        elif isinstance(value, str):
            clean_text(value)
            if cache:
                cell.setAttribute("t", "str")
                text = value
            else:
                cell.setAttribute("t", "inlineStr")
                inline = self.element(doc, "is")
                node = self.element(doc, "t")
                node.setAttribute("xml:space", "preserve")
                node.appendChild(doc.createTextNode(value))
                inline.appendChild(node)
                cell.insertBefore(inline, next((n for n in cell.childNodes if n.nodeType == n.ELEMENT_NODE and n.localName == "extLst"), None))
                return
        else:
            raise ValueError("Cell values must be JSON scalar values")
        node = self.element(doc, "v")
        node.appendChild(doc.createTextNode(text))
        cell.insertBefore(node, next((n for n in cell.childNodes if n.nodeType == n.ELEMENT_NODE and n.localName == "extLst"), None))

    def patch(self, spec):
        for item in validate_spec(spec):
            sheet = item["name"]
            if sheet not in self.sheets:
                raise ValueError("Patch cannot add sheets: " + sheet)
            tree = xml(self.parts[self.sheets[sheet]])
            if any(c["formula_attributes"] for c in self.cells[sheet].values()):
                raise ValueError("Editing sheets with shared/array/special formulas is not supported")
            merges = [n.get("ref") for n in tree.findall(NS + "mergeCells/" + NS + "mergeCell")]
            for ref, entry in item["cells"].items():
                ref = ref.upper()
                row, col = address(ref)
                for merged in merges:
                    first, last = merged.split(":") if ":" in merged else (merged, merged)
                    r1, c1 = address(first)
                    r2, c2 = address(last)
                    if r1 <= row <= r2 and c1 <= col <= c2 and ref != first:
                        raise ValueError("Write only the merged cell anchor: " + first)
                doc, node = self.cell_node(sheet, ref)
                if "style" in entry:
                    style = entry["style"]
                    styles = xml(self.parts["xl/styles.xml"]).find(NS + "cellXfs")
                    if styles is None or type(style) is not int or not 0 <= style < len(styles):
                        raise ValueError("Invalid style index")
                    node.setAttribute("s", str(style))
                for child in list(node.childNodes):
                    if child.nodeType == child.ELEMENT_NODE and child.localName == "f":
                        node.removeChild(child)
                self.put_value(doc, node, entry.get("value"))
                if "formula" in entry:
                    formula = entry["formula"]
                    if not isinstance(formula, str) or not formula.lstrip("=").strip():
                        raise ValueError("Formula must be nonempty text")
                    clean_text(formula)
                    f = self.element(doc, "f")
                    f.appendChild(doc.createTextNode(formula[1:] if formula.startswith("=") else formula))
                    node.insertBefore(f, node.firstChild)
            # dimension is optional; remove a stale extent instead of guessing bounds.
            doc = self.document(self.sheets[sheet])
            for node in list(doc.getElementsByTagNameNS(MAIN, "dimension")):
                node.parentNode.removeChild(node)
        self.flush()
        self._index()

    def flush(self):
        for part, doc in self.documents.items():
            self.parts[part] = doc.toxml(encoding="utf-8")

    def calculate(self, values_only=False):
        results, active, failures = {}, set(), {}
        canonical = {s.casefold(): s for s in self.sheets}

        def resolve(sheet, ref):
            name = canonical.get(sheet.casefold())
            if name is None:
                raise FormulaError("calculation_error", "Missing sheet: " + sheet)
            key = (name, ref)
            if key in results:
                return results[key]
            if key in failures:
                raise failures[key]
            if key in active:
                raise FormulaError("cycle", "Circular reference: %s!%s" % key)
            cell = self.cells[name].get(ref)
            if cell is None:
                return None
            if cell["formula"] is None:
                if cell["type"] == "e":
                    raise FormulaError("calculation_error", "Existing error: " + str(cell["value"]))
                if cell["type"] == "d":
                    raise FormulaError("unsupported", "ISO date arithmetic is unsupported")
                return cell["value"]
            if len(active) >= 100:
                raise FormulaError("unsupported", "Dependency depth exceeds 100")
            active.add(key)
            try:
                if not self.full_precision:
                    raise FormulaError("unsupported", "Precision-as-displayed calculation is unsupported")
                if cell["formula_attributes"]:
                    raise FormulaError("unsupported", "Shared/array/special formulas are not evaluated")
                result = evaluate(cell["formula"], name, resolve)
                if result is None:
                    result = Decimal(0)
                if isinstance(result, Decimal):
                    numeric(result)
                results[key] = result
                return result
            except FormulaError as exc:
                failures[key] = exc
                raise
            except (ValueError, ArithmeticError, RecursionError) as exc:
                error = FormulaError("calculation_error", str(exc))
                failures[key] = error
                raise error
            finally:
                active.remove(key)

        formula_keys = [(s, ref) for s, cells in self.cells.items() for ref, c in cells.items() if c["formula"] is not None]
        errors = []
        for sheet, cells in self.cells.items():
            for ref, cell in cells.items():
                if cell["formula"] is None and cell["type"] == "e":
                    errors.append({"sheet": sheet, "cell": ref, "code": "calculation_error", "message": "Existing error: " + str(cell["value"])})
        for sheet, ref in formula_keys:
            try:
                resolve(sheet, ref)
            except FormulaError as exc:
                errors.append({"sheet": sheet, "cell": ref, "code": exc.code, "message": str(exc)})
        fatal = any(e["code"] != "unsupported" for e in errors)
        report = {"status": "failed" if fatal else "incomplete" if errors else "calculated_subset",
                  "engine": "python_decimal_subset", "formula_count": len(formula_keys),
                  "calculated_count": len(results), "issues": errors,
                  "output_written": False, "excel_equivalence_verified": False,
                  "retained_not_refreshed": [kind for kind in ("charts", "pivotTables", "pivotCache", "externalLinks")
                                             if any(p.startswith("xl/" + kind) for p in self.parts)]}
        if fatal or values_only and errors:
            report["status"] = "failed"
            return report
        for sheet, ref in formula_keys:
            doc, node = self.cell_node(sheet, ref)
            # Unknown formulas never retain potentially stale cached results.
            result = results.get((sheet, ref))
            # Excel's direct blank reference yields zero; evaluator returns None for blank.
            if (sheet, ref) in results and result is None:
                result = Decimal(0)
            self.put_value(doc, node, result, cache=not values_only)
            if values_only:
                for child in list(node.childNodes):
                    if child.nodeType == child.ELEMENT_NODE and child.localName == "f":
                        node.removeChild(child)
        if formula_keys:
            doc = self.document("xl/workbook.xml")
            calc = doc.getElementsByTagNameNS(MAIN, "calcPr")
            node = calc[0] if calc else self.element(doc, "calcPr")
            if not calc:
                # calcPr precedes these optional workbook children in OOXML order.
                later = {"oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes", "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst"}
                anchor = next((n for n in doc.documentElement.childNodes if n.nodeType == n.ELEMENT_NODE and n.localName in later), None)
                doc.documentElement.insertBefore(node, anchor)
            for key, val in {"calcMode": "auto", "fullCalcOnLoad": "1", "forceFullCalc": "1"}.items():
                node.setAttribute(key, val)
            self.changed.add("xl/workbook.xml")
            self.drop_calc_chain()
        self.flush()
        report["changed_parts"] = sorted(self.changed)
        return report

    def drop_calc_chain(self):
        path = "xl/_rels/workbook.xml.rels"
        doc = parse_xml(self.parts[path], dom=True)
        removed = set()
        for rel in list(doc.getElementsByTagNameNS(PKG, "Relationship")):
            if rel.getAttribute("Type").endswith("/calcChain"):
                part = relation_target("xl/workbook.xml", rel.getAttribute("Target"))
                removed.add(part)
                self.parts.pop(part, None)
                rel.parentNode.removeChild(rel)
        if not removed:
            return
        self.parts[path] = doc.toxml(encoding="utf-8")
        types = parse_xml(self.parts["[Content_Types].xml"], dom=True)
        for node in list(types.getElementsByTagNameNS(CT, "Override")):
            if node.getAttribute("PartName").lstrip("/") in removed:
                node.parentNode.removeChild(node)
        self.parts["[Content_Types].xml"] = types.toxml(encoding="utf-8")
        self.changed.update(removed | {path, "[Content_Types].xml"})

    def save(self, path):
        self.flush()
        path = Path(path)
        if path.exists():
            raise ValueError("Output already exists; choose a new filename")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            raise ValueError("Output must be .xlsx or .xlsm")
        macro = b"macroEnabled" in self.parts["[Content_Types].xml"]
        if macro != (path.suffix.lower() == ".xlsm"):
            raise ValueError("Output extension must match workbook macro content type")
        for name, data in self.parts.items():
            if name.endswith((".xml", ".rels")):
                xml(data)
        # Exclusive final creation protects existing files; clean only our own failed output.
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.parts.items():
                archive.writestr(self.infos.get(name, name), data)
        created = False
        try:
            with path.open("xb") as output:
                created = True
                output.write(payload.getvalue())
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise

    def inspect(self):
        return {"date_system": "1904" if self.date1904 else "1900", "dates": "numeric serials are not converted",
                "sheets": [{"name": name, "cells": cells} for name, cells in self.cells.items()]}


def validate_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {"sheets"} or not isinstance(spec["sheets"], list) or not spec["sheets"]:
        raise ValueError('Expected {"sheets": [{"name": "Sheet1", "cells": {...}}]}')
    seen = set()
    total = 0
    for sheet in spec["sheets"]:
        if not isinstance(sheet, dict) or set(sheet) != {"name", "cells"}:
            raise ValueError("Each sheet requires name and cells")
        name = sheet["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 31 or re.search(r"[\\/?*\[\]:]", name) or name.startswith("'") or name.endswith("'"):
            raise ValueError("Invalid sheet name")
        clean_text(name)
        if name.casefold() in seen:
            raise ValueError("Duplicate sheet name")
        seen.add(name.casefold())
        if not isinstance(sheet["cells"], dict):
            raise ValueError("cells must be an object")
        refs = set()
        for ref, entry in sheet["cells"].items():
            address(ref)
            if ref.upper() in refs:
                raise ValueError("Duplicate cell address")
            refs.add(ref.upper())
            if not isinstance(entry, dict) or set(entry) - {"value", "formula", "style"} or ("value" in entry) == ("formula" in entry):
                raise ValueError("Each cell needs exactly one of value/formula and optional style")
            total += 1
            if total > MAX_CELLS:
                raise ValueError("Too many cells")
    return spec["sheets"]


MAX_JSON_DEPTH = 128


def check_json_depth(text):
    """Bound structural nesting independently of CPython decoder recursion."""
    depth = 0
    quoted = False
    escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON nesting exceeds supported depth: " + str(MAX_JSON_DEPTH))
        elif character in "}]":
            depth -= 1
    # Syntax validity, including mismatched delimiters, remains json.loads' job.


def read_spec(path, max_bytes=None):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result
    def reject(value):
        raise ValueError("Invalid JSON number: " + value)
    try:
        if max_bytes is None:
            with open(path, encoding="utf-8-sig") as handle:
                text = handle.read()
            check_json_depth(text)
            return json.loads(text, parse_float=Decimal, parse_constant=reject, object_pairs_hook=pairs)
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("JSON byte limit must be a positive integer")
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("JSON query exceeds byte limit: " + str(max_bytes))
        text = data.decode("utf-8-sig")
        check_json_depth(text)
        return json.loads(text, parse_float=Decimal,
                          parse_constant=reject, object_pairs_hook=pairs)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds supported parser depth") from exc


def read_csv(path, encoding="utf-8-sig", delimiter=","):
    with open(path, "rb") as handle:
        data = handle.read(MAX_CSV_BYTES + 1)
    if len(data) > MAX_CSV_BYTES:
        raise ValueError("CSV/TSV exceeds byte limit: " + str(MAX_CSV_BYTES))
    previous_limit = csv.field_size_limit()
    rows = []
    fields = 0
    try:
        csv.field_size_limit(MAX_CSV_FIELD)
        for row in csv.reader(io.StringIO(data.decode(encoding), newline=""), delimiter=delimiter):
            fields += len(row)
            if fields > MAX_CELLS or len(rows) >= MAX_CELLS:
                raise ValueError("CSV/TSV exceeds row/field limit: " + str(MAX_CELLS))
            rows.append(row)
    finally:
        csv.field_size_limit(previous_limit)
    return {"rows": rows, "types": "text"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Standard-library runtime and template self-check")
    read = commands.add_parser("read")
    read.add_argument("input")
    read.add_argument("--encoding", default="utf-8-sig", help="CSV/TSV encoding")
    for name in ("create", "edit", "calculate"):
        sub = commands.add_parser(name)
        if name != "create":
            sub.add_argument("input")
        if name != "calculate":
            sub.add_argument("spec", help="JSON sheet/cell specification")
        sub.add_argument("output")
        if name == "create":
            sub.add_argument("--appearance", help="Readable appearance JSON; new workbooks only")
            sub.add_argument("--values-only", action="store_true", help="Replace all successfully evaluated formulas with static results")
    args = parser.parse_args()
    try:
        if args.command == "check":
            book = Workbook.create({"sheets": [{"name": "Check", "cells": {"A1": {"formula": "ROUND(1.005,2)"}}}]})
            result = book.calculate()
            if result["calculated_count"] != 1 or result["issues"]:
                raise ValueError("Calculation self-check failed")
            result = {"status": "ready", "python": sys.version.split()[0], "dependencies": "standard_library_only"}
        elif args.command == "read":
            suffix = Path(args.input).suffix.lower()
            if suffix in (".wps", ".et", ".ett"):
                raise UnsupportedInputError(WPS_UNSUPPORTED_MESSAGE)
            if suffix in (".csv", ".tsv"):
                result = read_csv(args.input, args.encoding, "\t" if suffix == ".tsv" else ",")
            else:
                result = Workbook.load(args.input).inspect()
        else:
            appearance = read_spec(args.appearance, max_bytes=65536) if args.command == "create" and args.appearance else None
            book = Workbook.create(read_spec(args.spec), appearance=appearance) if args.command == "create" else Workbook.load(args.input)
            if args.command == "edit":
                book.patch(read_spec(args.spec))
            result = book.calculate(values_only=getattr(args, "values_only", False))
            if getattr(book, "appearance_report", None) is not None:
                result["appearance"] = book.appearance_report
            if result["status"] != "failed":
                book.save(args.output)
                result["output_written"] = True
                result["output"] = args.output
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 1 if result.get("status") == "failed" else 2 if result.get("status") == "incomplete" else 0
    except UnsupportedInputError as exc:
        print(json.dumps({"status": "unsupported", "output_written": False, "message": str(exc)}, ensure_ascii=False))
        return 1
    except (ValueError, csv.Error, LookupError, OSError, struct.error, OverflowError, InvalidOperation, zipfile.BadZipFile, ET.ParseError) as exc:
        print(json.dumps({"status": "failed", "output_written": False, "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
