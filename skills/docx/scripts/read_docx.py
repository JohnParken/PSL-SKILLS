"""Read native DOCX/DOTX text and explicit XML structure."""

from __future__ import annotations

import argparse
import hashlib
import io
from contextlib import nullcontext
import json
import os
import re
import stat
import sys
import zipfile
import zlib
from pathlib import Path
from xml.etree.ElementTree import Element

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from office import xmlio as SafeET
from office.runtime import check_runtime
from office.archiveio import open_archive, _preflight, MAX_ARCHIVE_BYTES
from office.helpers import atomic_write_bytes, check_archive, read_part

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CFB_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
WPS_SUFFIXES = {".wps", ".et", ".ett"}


class UnsupportedInputError(ValueError):
    """The input belongs outside the native document reader."""


WPS_UNSUPPORTED_MESSAGE = '不支持读取 WPS 格式：.wps 使用 wps skill，.et/.ett 使用 et skill；请依据实际主入口定位，本文档读取器不会自动调用。'

def _check_native_input(path):
    """Suffix sets skill ownership; signature still gates the native parser."""
    if Path(path).suffix.lower() in WPS_SUFFIXES:
        raise UnsupportedInputError(WPS_UNSUPPORTED_MESSAGE)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("DOCX input must be a regular file")
        prefix = os.read(descriptor, 8)
    finally:
        os.close(descriptor)
    if prefix == CFB_SIGNATURE:
        raise UnsupportedInputError("不支持读取 CFB 二进制内容；它不是原生 DOCX/DOTX，请按实际内容确认文字或表格并分别使用 wps skill 或 et skill；CFB 签名不证明 WPS 来源。")


_REVISION_TAGS = {
    "accepted": {"del", "moveFrom"},
    "original": {"ins", "moveTo"},
    "all": set(),
}
_BLOCK_WRAPPERS = {"sdt", "sdtContent", "customXml", "ins", "del", "moveFrom", "moveTo", "smartTag"}
MAX_STRUCTURED_NODES = 200_000
_STRUCTURED_TYPES = {
    "document": "document", "body": "body", "p": "paragraph", "r": "run",
    "t": "text", "delText": "text", "tab": "tab", "br": "break", "cr": "break",
    "tbl": "table", "tr": "row", "tc": "cell", "tblGrid": "table_grid",
    "gridCol": "grid_column", "hyperlink": "hyperlink", "sdt": "content_control",
    "sdtContent": "content", "customXml": "custom_xml", "smartTag": "smart_tag",
    "ins": "revision", "del": "revision", "moveFrom": "revision", "moveTo": "revision",
    "hdr": "header", "ftr": "footer", "footnotes": "footnotes", "endnotes": "endnotes",
    "footnote": "note", "endnote": "note", "comments": "comments", "comment": "annotation",
    "styles": "styles", "style": "style", "numbering": "numbering", "num": "numbering_instance",
    "abstractNum": "abstract_numbering", "lvl": "numbering_level", "lvlOverride": "level_override",
}
_PROPERTY_ROOTS = {"pPr", "rPr", "tblPr", "trPr", "tcPr", "sectPr", "sdtPr", "tblPrEx"}
_RELATED_STRUCTURE_PARTS = (
    ("headers", r"word/header[0-9]+\.xml", "hdr"),
    ("footers", r"word/footer[0-9]+\.xml", "ftr"),
    ("footnotes", r"word/footnotes\.xml", "footnotes"),
    ("endnotes", r"word/endnotes\.xml", "endnotes"),
    ("comments", r"word/comments\.xml", "comments"),
)


def _tag(local: str) -> str:
    return f"{{{W}}}{local}"


def _paragraph_text(p: Element, revisions: str) -> str:
    out: list[str] = []

    def visit(node: Element, blocked: bool = False) -> None:
        name = node.tag.rsplit("}", 1)[-1]
        blocked = blocked or name in _REVISION_TAGS[revisions]
        if blocked or name in ("txbxContent", "pPr", "rPr"):
            return
        if name in ("t", "delText"):
            if node.text:
                out.append(node.text)
            return
        if name == "tab":
            out.append("\t")
            return
        if name in ("br", "cr"):
            out.append("\n")
            return
        for child in node:
            # Nested paragraphs belong to separate blocks and are handled by block traversal.
            if child.tag == _tag("p"):
                continue
            visit(child, blocked)

    visit(p)
    return "".join(out)


def _blocks(node: Element, revisions: str) -> list[str]:
    result: list[str] = []
    for child in node:
        name = child.tag.rsplit("}", 1)[-1]
        if child.tag == _tag("p"):
            result.append(_paragraph_text(child, revisions))
        elif child.tag == _tag("tbl"):
            result.extend(_table(child, revisions))
        elif name in _BLOCK_WRAPPERS:
            if name not in _REVISION_TAGS[revisions]:
                result.extend(_blocks(child, revisions))
    return result


def _table(tbl: Element, revisions: str) -> list[str]:
    rows: list[str] = []
    for tr in tbl.findall(_tag("tr")):
        cells = []
        for tc in tr.findall(_tag("tc")):
            cells.append("\n".join(_blocks(tc, revisions)))
        rows.append("\t".join(cells))
    return rows


def _part_text(root: Element, part: str, revisions: str) -> str:
    if part.endswith("footnotes.xml") or part.endswith("endnotes.xml"):
        kind = "footnote" if part.endswith("footnotes.xml") else "endnote"
        elements = [e for e in root if e.tag == _tag(kind) and e.get(_tag("type")) not in ("separator", "continuationSeparator")]
        chunks = []
        for e in elements:
            blocks = _blocks(e, revisions)
            if blocks:
                chunks.append("\n".join(blocks))
        return "\n".join(chunks)
    if part.endswith("comments.xml"):
        chunks = []
        for e in root.findall(_tag("comment")):
            blocks = _blocks(e, revisions)
            if blocks:
                chunks.append("\n".join(blocks))
        return "\n".join(chunks)
    return "\n".join(_blocks(root, revisions))


def read_docx(path: str | Path, *, revisions: str | None = None, include_related: bool = False) -> str:
    """Return document text in reading order; this does not accept revisions or reproduce layout."""
    if revisions is not None and revisions not in _REVISION_TAGS:
        raise ValueError("revisions must be 'accepted', 'original', or 'all'")
    xml_budget = SafeET.XmlBudget()
    _check_native_input(path)
    revisions = revisions or "accepted"
    try:
        with open_archive(path) as package:
            check_archive(package)
            try:
                document = SafeET.fromstring(read_part(package, "word/document.xml"), budget=xml_budget)
            except KeyError as exc:
                raise ValueError("DOCX package is missing word/document.xml") from exc
            if document.tag != _tag("document"):
                raise ValueError("word/document.xml does not contain a w:document root")
            body = document.find(_tag("body"))
            if body is None:
                raise ValueError("word/document.xml is missing w:body")
            pieces = _blocks(body, revisions)
            if include_related:
                names = package.namelist()
                categories = (
                    ("Headers", "word/header"),
                    ("Footers", "word/footer"),
                    ("Footnotes", "word/footnotes.xml"),
                    ("Endnotes", "word/endnotes.xml"),
                    ("Comments", "word/comments.xml"),
                )
                for label, prefix in categories:
                    for part in sorted(n for n in names if n.startswith(prefix) and n.endswith(".xml")):
                        root = SafeET.fromstring(read_part(package, part), budget=xml_budget)
                        text = _part_text(root, part, revisions)
                        if text.strip():
                            pieces.extend((f"[{label}: {part}]", text))
    except SafeET.ParseError as exc:
        raise ValueError(f"cannot read DOCX package: invalid or unsafe XML/archive input: {exc}") from exc
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"cannot read DOCX package: {exc}") from exc
    return "\n".join(pieces)


def _dom_child(node, name):
    return next((child for child in node.childNodes
                 if child.nodeType == child.ELEMENT_NODE
                 and child.namespaceURI == W and child.localName == name), None)


def _word_value(node, name):
    if node is None:
        return None
    child = _dom_child(node, name)
    return child.getAttributeNS(W, "val") if child is not None and child.hasAttributeNS(W, "val") else None


def _structured_node(node, revisions, budget, *, in_properties=False):
    budget[0] += 1
    if budget[0] > MAX_STRUCTURED_NODES:
        raise ValueError("Structured output exceeds the 200000 total node limit")
    if node.nodeType != node.ELEMENT_NODE:
        kinds = {node.TEXT_NODE: "text_value", node.CDATA_SECTION_NODE: "cdata",
                 node.COMMENT_NODE: "comment", node.PROCESSING_INSTRUCTION_NODE: "processing_instruction"}
        result = {"type": kinds.get(node.nodeType, "opaque_node"), "value": node.nodeValue}
        if node.nodeType == node.PROCESSING_INSTRUCTION_NODE:
            result["target"] = node.target
        return result
    name = node.localName
    word_node = node.namespaceURI == W
    property_root = word_node and name in _PROPERTY_ROOTS
    if property_root:
        kind = "properties"
    elif word_node and in_properties:
        kind = "property"
    else:
        kind = _STRUCTURED_TYPES.get(name, "opaque") if word_node else "opaque"
    result = {
        "type": kind, "tag": node.tagName, "namespace": node.namespaceURI,
        "attributes": {attr.name: attr.value for attr in node.attributes.values()},
    }
    if kind == "paragraph":
        properties = _dom_child(node, "pPr")
        result["style_id"] = _word_value(properties, "pStyle")
        numbering = _dom_child(properties, "numPr") if properties is not None else None
        result["numbering"] = None if numbering is None else {
            "num_id": _word_value(numbering, "numId"), "level": _word_value(numbering, "ilvl"),
        }
    elif kind == "run":
        result["style_id"] = _word_value(_dom_child(node, "rPr"), "rStyle")
    elif kind == "row":
        properties = _dom_child(node, "trPr")
        result["grid_before"] = _word_value(properties, "gridBefore")
        result["grid_after"] = _word_value(properties, "gridAfter")
    elif kind == "cell":
        properties = _dom_child(node, "tcPr")
        result["grid_span"] = _word_value(properties, "gridSpan")
        merge = _dom_child(properties, "vMerge") if properties is not None else None
        result["vertical_merge"] = None if merge is None else (
            merge.getAttributeNS(W, "val") if merge.hasAttributeNS(W, "val") else "continue"
        )
    elif kind == "text":
        result["text"] = "".join(child.data for child in node.childNodes
                                 if child.nodeType in (child.TEXT_NODE, child.CDATA_SECTION_NODE))
    if word_node and name in _REVISION_TAGS[revisions]:
        result["included"] = False
        result["omitted"] = "revision-view"
        result["children"] = []
    else:
        if kind == "revision":
            result["included"] = True
        result["children"] = [_structured_node(child, revisions, budget,
                               in_properties=in_properties or property_root) for child in node.childNodes]
    return result


def read_docx_structure(path: str | Path, *, revisions: str = "accepted",
                        include_related: bool = False) -> dict:
    """Return explicit XML structure; never resolve styles, compute numbering or edit XML.

    Qualified element/attribute names and namespace declarations are retained.
    Unknown elements are opaque nodes with their children, not silently dropped.
    This JSON is a versioned inspection format, not a lossless DOCX import format.
    """
    _check_native_input(path)
    if revisions not in _REVISION_TAGS:
        raise ValueError("revisions must be 'accepted', 'original', or 'all'")
    result = {
        "schema_version": 1, "format": "docx-structure", "revisions": revisions,
        "semantics": {"styles": "explicit-only", "list_numbers": "not-calculated",
                      "part_order": "main-then-related-not-page-order"},
        "limitations": ["not-a-lossless-edit-format", "no-rendering-or-style-inheritance",
                        "no-field-or-numbering-calculation", "revision-view-does-not-save-accepted-revisions"],
        "parts": [], "definitions": {},
    }
    budget = [0]
    xml_budget = SafeET.XmlBudget()
    try:
        with open_archive(path) as package:
            check_archive(package)
            names = set(package.namelist())

            def read_root(part, expected):
                dom = SafeET.parse_dom(read_part(package, part), budget=xml_budget)
                root = dom.documentElement
                if root.namespaceURI != W or root.localName != expected:
                    raise ValueError(f"{part} does not contain a w:{expected} root")
                return root

            if "word/document.xml" not in names:
                raise ValueError("DOCX package is missing word/document.xml")
            document = read_root("word/document.xml", "document")
            if _dom_child(document, "body") is None:
                raise ValueError("word/document.xml is missing w:body")
            result["parts"].append({"name": "word/document.xml", "category": "main",
                                    "root": _structured_node(document, revisions, budget)})
            for part, expected in (("word/styles.xml", "styles"), ("word/numbering.xml", "numbering")):
                if part in names:
                    result["definitions"][part] = _structured_node(read_root(part, expected), revisions, budget)
            if include_related:
                for category, pattern, expected in _RELATED_STRUCTURE_PARTS:
                    for part in sorted(name for name in names if re.fullmatch(pattern, name)):
                        result["parts"].append({"name": part, "category": category,
                                               "root": _structured_node(read_root(part, expected), revisions, budget)})
    except SafeET.ParseError as exc:
        raise ValueError(f"cannot read DOCX package: invalid or unsafe XML/archive input: {exc}") from exc
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"cannot read DOCX package: {exc}") from exc
    return result



# Light-weight read modes: preserve the legacy text and explicit-structure APIs.
# The blocks below deliberately contain no raw OOXML or expanded style definitions.
_FASTPATH_DEFAULT_CHARS = 12000
_FASTPATH_MAX_CHARS = 2_000_000


def _style_heading_levels(package, budget):
    """Resolve common built-in/localized heading styles and outline levels safely."""
    try:
        root = SafeET.fromstring(read_part(package, "word/styles.xml"), budget=budget)
    except KeyError:
        return {}
    raw = {}
    for style in root.findall(_tag("style")):
        sid = style.get(_tag("styleId"))
        if not sid:
            continue
        name_node = style.find(_tag("name"))
        name = name_node.get(_tag("val"), "") if name_node is not None else ""
        base_node = style.find(_tag("basedOn"))
        parent = base_node.get(_tag("val")) if base_node is not None else None
        ppr = style.find(_tag("pPr"))
        outline_node = ppr.find(_tag("outlineLvl")) if ppr is not None else None
        outline = outline_node.get(_tag("val")) if outline_node is not None else None
        raw[sid] = (name, parent, outline)

    def direct_level(identifier, name, outline):
        if outline is not None and outline.isdigit() and 0 <= int(outline) <= 8:
            return int(outline) + 1
        for value in (identifier, name):
            match = re.fullmatch(r"(?:heading|标题|标题\s*|标题-|heading\s*)([1-9])", value.strip(), re.I)
            if match:
                return int(match.group(1))
        return None

    memo = {}
    def lookup(sid, seen):
        if sid in memo:
            return memo[sid]
        if sid not in raw or sid in seen:
            return None
        name, parent, outline = raw[sid]
        level = direct_level(sid, name, outline)
        if level is None and parent:
            level = lookup(parent, seen | {sid})
        memo[sid] = level
        return level
    for sid in raw:
        lookup(sid, set())
    return {sid: level for sid, level in memo.items() if level}


def _paragraph_heading_level(p, styles):
    ppr = p.find(_tag("pPr"))
    if ppr is None:
        return None
    outline = ppr.find(_tag("outlineLvl"))
    if outline is not None:
        value = outline.get(_tag("val"))
        if value and value.isdigit() and 0 <= int(value) <= 8:
            return int(value) + 1
    style = ppr.find(_tag("pStyle"))
    sid = style.get(_tag("val"), "") if style is not None else ""
    if sid in styles:
        return styles[sid]
    matched = re.fullmatch(r"(?:heading|标题)\s*([1-9])", sid, re.I)
    return int(matched.group(1)) if matched else None


def _body_entries(body, revisions, styles, *, tables="summary"):
    entries = []
    paragraph_count = 0
    table_count = 0
    table_rows = 0
    heading_count = 0

    def walk(node):
        nonlocal paragraph_count, table_count, table_rows, heading_count
        for child in node:
            name = child.tag.rsplit("}", 1)[-1]
            if child.tag == _tag("p"):
                paragraph_count += 1
                level = _paragraph_heading_level(child, styles)
                item = {"type": "heading" if level else "paragraph", "paragraph_index": paragraph_count,
                        "text": _paragraph_text(child, revisions)}
                if level:
                    heading_count += 1
                    item["level"] = level
                entries.append(item)
            elif child.tag == _tag("tbl"):
                table_count += 1
                rows = []
                for tr in child.findall(_tag("tr")):
                    cells = []
                    for tc in tr.findall(_tag("tc")):
                        cells.append("\n".join(_blocks(tc, revisions)))
                    rows.append(cells)
                table_rows += len(rows)
                count = max((len(r) for r in rows), default=0)
                entries.append({"type": "table", "table_index": table_count,
                                "row_count": len(rows), "column_count": count,
                                "preview_rows": min(len(rows), 2) if tables == "summary" else 0,
                                "rows": rows[:2] if tables == "summary" else []})
                if tables == "rows":
                    for idx, cells in enumerate(rows, 1):
                        entries.append({"type": "table_row", "table_index": table_count,
                                        "row_index": idx, "cells": cells})
            elif name in _BLOCK_WRAPPERS and name not in _REVISION_TAGS[revisions]:
                walk(child)
    walk(body)
    return entries, {"paragraph_count": paragraph_count, "heading_count": heading_count,
                     "table_count": table_count, "table_row_count": table_rows}


def read_docx_compact(path: str | Path, *, mode="compact", revisions="accepted",
                      include_related=False, paragraph_range=None, section=None,
                      section_occurrence=None, tables="summary"):
    """Return the legacy unpaged compact view; use read_docx_page for bound pages."""
    return _read_docx_compact(path, mode=mode, revisions=revisions, include_related=include_related,
                              paragraph_range=paragraph_range, section=section,
                              section_occurrence=section_occurrence, tables=tables)


def _read_docx_compact(path: str | Path, *, mode="compact", revisions="accepted",
                      include_related=False, paragraph_range=None, section=None,
                      section_occurrence=None, tables="summary", _package=None):
    """Read a low-overhead subset. Indices are 1-based; paragraph bounds inclusive.

    This is a lossy read-only inspection format, never a DOCX edit interchange.
    Paragraph indexing excludes table cell paragraphs and related parts.
    """
    if _package is None:
        _check_native_input(path)
    if revisions not in _REVISION_TAGS:
        raise ValueError("revisions must be 'accepted', 'original', or 'all'")
    if mode not in ("compact", "summary", "headings"):
        raise ValueError("mode must be compact, summary, or headings")
    if tables not in ("summary", "rows"):
        raise ValueError("tables must be 'summary' or 'rows'")
    if section_occurrence is not None and (section is None or section_occurrence < 1):
        raise ValueError("--section-occurrence requires --section and a positive index")
    if paragraph_range is not None:
        if (not isinstance(paragraph_range, tuple) or len(paragraph_range) != 2
                or not all(isinstance(x, int) for x in paragraph_range)
                or paragraph_range[0] < 1 or paragraph_range[1] < paragraph_range[0]):
            raise ValueError("paragraph range must be two positive 1-based inclusive indices")
    budget = SafeET.XmlBudget()
    try:
        with (open_archive(path) if _package is None else nullcontext(_package)) as package:
            check_archive(package)
            try:
                document = SafeET.fromstring(read_part(package, "word/document.xml"), budget=budget)
            except KeyError as exc:
                raise ValueError("DOCX package is missing word/document.xml") from exc
            if document.tag != _tag("document"):
                raise ValueError("word/document.xml does not contain a w:document root")
            body = document.find(_tag("body"))
            if body is None:
                raise ValueError("word/document.xml is missing w:body")
            styles = _style_heading_levels(package, budget)
            entries, stats = _body_entries(body, revisions, styles, tables=tables)
            related = []
            if include_related:
                categories = (("Headers", "word/header"), ("Footers", "word/footer"),
                              ("Footnotes", "word/footnotes.xml"), ("Endnotes", "word/endnotes.xml"),
                              ("Comments", "word/comments.xml"))
                for label, prefix in categories:
                    for part in sorted(n for n in package.namelist() if n.startswith(prefix) and n.endswith(".xml")):
                        root = SafeET.fromstring(read_part(package, part), budget=budget)
                        content = _part_text(root, part, revisions)
                        if content.strip():
                            related.append({"category": label, "part": part, "text": content})
    except SafeET.ParseError as exc:
        raise ValueError(f"cannot read DOCX package: invalid or unsafe XML/archive input: {exc}") from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"cannot read DOCX package: {exc}") from exc

    if paragraph_range is not None:
        start, end = paragraph_range
        if start > stats["paragraph_count"]:
            raise ValueError("paragraph range starts past the end of the document")
        entries = [e for e in entries if e["type"] in ("paragraph", "heading")
                   and start <= e["paragraph_index"] <= end]
    if section is not None:
        headings = [(i, e) for i, e in enumerate(entries)
                    if e["type"] == "heading" and e["text"].strip() == section.strip()]
        if not headings:
            raise ValueError(f"section heading not found: {section!r}")
        if section_occurrence is None and len(headings) != 1:
            raise ValueError("section heading is ambiguous; use --section-occurrence N")
        if section_occurrence is not None and section_occurrence > len(headings):
            raise ValueError("section occurrence is out of range")
        index, selected = headings[(section_occurrence or 1) - 1]
        stop = len(entries)
        for j in range(index + 1, len(entries)):
            e = entries[j]
            if e["type"] == "heading" and e["level"] <= selected["level"]:
                stop = j
                break
        entries = entries[index:stop]
    if mode == "summary" and include_related:
        related = [{"category": x["category"], "part": x["part"],
                    "text_chars": len(x["text"])} for x in related]
    elif mode == "compact" and include_related:
        related = [{"category": x["category"], "part": x["part"],
                    "text_chars": len(x["text"]), "text_preview": x["text"][:200],
                    "preview_truncated": len(x["text"]) > 200} for x in related]
    if mode == "headings":
        entries = [e for e in entries if e["type"] == "heading"]
    elif mode == "summary":
        entries = [{"type": "heading", "level": e["level"],
                    "paragraph_index": e["paragraph_index"], "text": e["text"]}
                   for e in entries if e["type"] == "heading"]
    return {"schema_version": 1, "format": "docx-compact", "mode": mode,
            "revisions": revisions, "scope": {"paragraph_range": paragraph_range,
            "section": section, "section_occurrence": section_occurrence},
            "stats": stats, "blocks": entries, "related": related,
            "limitations": ["main-body-paragraph-indices-exclude-table-cells",
                            "styles-not-fully-resolved", "not-page-layout", "not-lossless-edit-format"]}


def _snapshot_input(path):
    """Read one bounded, no-symlink task-relative snapshot without reopening data."""
    from archive_deliverables import validate_input_path
    path = validate_input_path(Path(path))
    if path.suffix.lower() not in (".docx", ".dotx"):
        raise ValueError("bound pages require a .docx or .dotx input")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY") or os.open not in os.supports_dir_fd:
        raise ValueError("bound pages require safe dir_fd/O_NOFOLLOW support")
    descriptors = []
    try:
        directory = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        for part in path.parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= MAX_ARCHIVE_BYTES:
            raise ValueError("bound page input must be a regular file within the ZIP compressed size limit")
        chunks = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        raw = b"".join(chunks)
        if identity(before) != identity(after) or len(raw) != before.st_size:
            raise ValueError("input changed while reading snapshot")
        if raw.startswith(CFB_SIGNATURE):
            raise UnsupportedInputError("CFB binary content is not native DOCX/DOTX")
        return raw
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def serialize_docx_page(data):
    """Canonical UTF-8 page representation; the budget includes its final LF."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


def _page_cursor(source, options, position):
    payload = f"1.{source}.{options}.{position}"
    return payload + "." + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _cursor_position(cursor, source, options, total):
    if not isinstance(cursor, str) or not re.fullmatch(r"1\.[0-9a-f]{64}\.[0-9a-f]{64}\.[1-9][0-9]{0,8}\.[0-9a-f]{64}", cursor):
        raise ValueError("invalid continuation cursor")
    _, old_source, old_options, position, _ = cursor.split(".")
    if cursor != _page_cursor(old_source, old_options, int(position)):
        raise ValueError("continuation cursor checksum mismatch")
    if source != old_source:
        raise ValueError("continuation source bytes changed")
    if options != old_options:
        raise ValueError("continuation reading options changed")
    if not 0 < int(position) < total:
        raise ValueError("continuation cursor position is out of range")
    return int(position)


def read_docx_page(path: str | Path, *, max_output_bytes=12000, cursor=None, mode="compact",
                   revisions="accepted", include_related=False, paragraph_range=None, section=None,
                   section_occurrence=None, tables="summary"):
    """Return a UTF-8-bounded compact page bound to source bytes and view options.

    Source paths are relative to the confirmed task directory. Budgets may change
    between pages. Cursors detect mismatch/corruption, not malicious forgery.
    This paginates the lossy selected view, never an entire lossless document.
    """
    if type(max_output_bytes) is not int or not 128 <= max_output_bytes <= _FASTPATH_MAX_CHARS:
        raise ValueError("--max-output-bytes must be between 128 and 2000000")
    if mode not in ("compact", "summary", "headings") or revisions not in _REVISION_TAGS or tables not in ("summary", "rows"):
        raise ValueError("invalid bound page reading options")
    if type(include_related) is not bool or (section is not None and not isinstance(section, str)):
        raise ValueError("invalid related/section option types")
    if section_occurrence is not None and (type(section_occurrence) is not int or section_occurrence < 1 or section is None):
        raise ValueError("section occurrence requires a section and positive integer")
    if paragraph_range is not None:
        if (not isinstance(paragraph_range, tuple) or len(paragraph_range) != 2
                or any(type(x) is not int for x in paragraph_range)
                or not 1 <= paragraph_range[0] <= paragraph_range[1]):
            raise ValueError("paragraph range requires two positive inclusive integer indices")
    if paragraph_range is not None and section is not None:
        raise ValueError("paragraph range and section are mutually exclusive")
    if include_related and (paragraph_range is not None or section is not None or mode == "headings"):
        raise ValueError("related parts are unsupported with scoped or heading-only reads")
    options = dict(mode=mode, revisions=revisions, include_related=include_related,
                   paragraph_range=paragraph_range, section=section.strip() if section is not None else None,
                   section_occurrence=section_occurrence, tables=tables)
    option_hash = hashlib.sha256(json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    raw = _snapshot_input(path)
    source = hashlib.sha256(raw).hexdigest()
    with io.BytesIO(raw) as stream:
        _preflight(stream, len(raw))
        try:
            with zipfile.ZipFile(stream, "r") as package:
                data = _read_docx_compact(path, _package=package, **options)
        except zlib.error as exc:
            raise ValueError("Invalid compressed ZIP data: decompression failed") from exc
    blocks = data["blocks"]
    total = len(blocks)
    start = _cursor_position(cursor, source, option_hash, total) if cursor is not None else 0
    base = {k: v for k, v in data.items() if k != "blocks"}
    base.update(page_version=1, source_sha256=source, total_blocks=total, start_block=start)
    omissions = []
    if mode != "compact":
        omissions.append("headings-only-view")
    if tables == "summary" and mode == "compact" and any(e["type"] == "table" and e["row_count"] > e["preview_rows"] for e in blocks):
        omissions.append("table-rows-after-preview-omitted")
    omissions.append("related-counts-or-previews" if include_related else "related-parts-not-requested")

    def page(stop):
        more = stop < total
        result = dict(base, blocks=blocks[start:stop], truncated=more, next_block=stop if more else None,
                      has_more=more, next_cursor=_page_cursor(source, option_hash, stop) if more else None,
                      coverage={"block_start": start, "block_end_exclusive": stop, "selected_total_blocks": total,
                                "selected_view_complete": start == 0 and not more, "document_complete": False,
                                "tables": tables, "omissions": omissions})
        return result

    result = page(start)
    # Size is monotonic except the final page (which drops the cursor). Check the
    # complete tail first so an exactly fitting final page is never rejected.
    complete = page(total)
    if len(serialize_docx_page(complete).encode("utf-8")) <= max_output_bytes:
        return complete
    if len(serialize_docx_page(result).encode("utf-8")) > max_output_bytes:
        raise ValueError("--max-output-bytes is too small for complete page metadata/diagnostics")
    low, high = start + 1, total - 1
    while low <= high:
        stop = (low + high) // 2
        candidate = page(stop)
        if len(serialize_docx_page(candidate).encode("utf-8")) > max_output_bytes:
            high = stop - 1
        else:
            result = candidate
            low = stop + 1
    if not result["blocks"] and start < total:
        raise ValueError("single block exceeds --max-output-bytes with complete metadata; increase budget or narrow selection")
    return result


def _limited_text(value, max_chars):
    if max_chars is None or len(value) <= max_chars:
        return value
    notice = f"\n[TRUNCATED: output exceeds {max_chars} characters; select a paragraph range or section for more]\n"
    if len(notice) >= max_chars:
        raise ValueError("--max-output-chars is too small for the truncation notice")
    return value[:max_chars - len(notice)] + notice


def _limited_compact(data, max_chars, *, start_block=0):
    """Paginate compact JSON at block boundaries, preserving valid JSON and a cursor."""
    blocks = data["blocks"]
    if start_block > len(blocks):
        raise ValueError("--start-block exceeds available blocks")
    base = {k: v for k, v in data.items() if k != "blocks"}
    base["total_blocks"] = len(blocks)
    base["start_block"] = start_block
    base["blocks"] = []
    base["truncated"] = start_block < len(blocks)
    base["next_block"] = start_block if base["truncated"] else None
    def encode():
        return json.dumps(base, ensure_ascii=False, separators=(",", ":"))
    if len(encode()) + 1 > max_chars:
        raise ValueError("--max-output-chars is too small for compact JSON metadata")
    next_index = start_block
    while next_index < len(blocks):
        base["blocks"].append(blocks[next_index])
        base["next_block"] = next_index + 1 if next_index + 1 < len(blocks) else None
        base["truncated"] = base["next_block"] is not None
        if len(encode()) + 1 > max_chars:
            base["blocks"].pop()
            base["next_block"] = next_index
            base["truncated"] = True
            if not base["blocks"]:
                raise ValueError("single block exceeds --max-output-chars; increase limit or select narrower content")
            break
        next_index += 1
    return encode() + "\n"


def _compact_plain(data):
    lines = []
    for item in data["blocks"]:
        if item["type"] in ("paragraph", "heading"):
            if data["mode"] == "headings":
                lines.append(f"[H{item['level']}] [p{item['paragraph_index']}] {item['text']}")
            else:
                lines.append(item["text"])
        elif item["type"] == "table":
            if item.get("rows"):
                lines.extend("\t".join(row) for row in item["rows"])
            else:
                lines.append(f"[Table {item['table_index']}: {item['row_count']} rows x {item['column_count']} columns]")
        elif item["type"] == "table_row":
            lines.append("\t".join(item["cells"]))
    if data["mode"] == "headings" and not lines:
        lines.append("[NO_HEADINGS_RECOGNIZED: no explicit or recognized heading style]")
    if data["related"]:
        for part in data["related"]:
            lines.extend((f"[{part['category']}: {part['part']}]", part["text"]))
    return "\n".join(lines)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, nargs="?", help="input .docx or .dotx file")
    parser.add_argument("--check", action="store_true", help="check read-only runtime support without output probes")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--revisions", choices=tuple(_REVISION_TAGS), default=None,
                        help="OOXML defaults to accepted")
    parser.add_argument("--include-related", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--delivery", action="store_true", help="explicitly archive a saved result and return JSON summary")
    selectors = parser.add_mutually_exclusive_group()
    selectors.add_argument("--summary", action="store_true", help="small JSON document overview and headings")
    selectors.add_argument("--heading-only", action="store_true", help="only heading text and levels")
    selectors.add_argument("--paragraph-range", metavar="START:END", help="1-based inclusive main-body paragraphs")
    selectors.add_argument("--section", metavar="EXACT_HEADING", help="read section until next same/higher-level heading")
    parser.add_argument("--section-occurrence", type=int, help="disambiguate repeated section heading")
    parser.add_argument("--compact-json", action="store_true", help="small structured JSON without raw OOXML")
    parser.add_argument("--tables", choices=("summary", "rows"), default="summary", help="compact table preview or paged rows")
    parser.add_argument("--start-block", type=int, default=None, help="zero-based cursor for paged compact JSON")
    parser.add_argument("--max-output-chars", type=int, help="limit returned text/compact JSON characters")
    parser.add_argument("--max-output-bytes", type=int, help="UTF-8 budget for bound compact JSON, including metadata and final LF")
    parser.add_argument("--cursor", help="source/options-bound continuation token; repeat the same reading options")
    args = parser.parse_args(argv)
    bound_page = args.max_output_bytes is not None or args.cursor is not None
    if bound_page:
        if not (args.compact_json or args.summary):
            parser.error("--max-output-bytes/--cursor requires --compact-json or --summary")
        if args.max_output_chars is not None or args.start_block is not None or args.delivery:
            parser.error("bound pages cannot combine --max-output-chars, --start-block or --delivery")
    using_fastpath = bool(args.summary or args.heading_only or args.paragraph_range or args.section or args.compact_json)
    if args.max_output_chars is not None and not 128 <= args.max_output_chars <= _FASTPATH_MAX_CHARS:
        parser.error("--max-output-chars must be between 128 and 2000000")
    if args.section_occurrence is not None and (args.section is None or args.section_occurrence < 1):
        parser.error("--section-occurrence requires --section and a positive index")
    if args.start_block is not None and (args.start_block < 0 or (args.start_block and not args.compact_json)):
        parser.error("--start-block requires --compact-json and must be nonnegative")
    if args.tables != "summary" and not args.compact_json:
        parser.error("--tables rows requires --compact-json")
    if args.include_related and (args.paragraph_range or args.section or args.heading_only):
        parser.error("--include-related is not supported with a scoped or heading-only read")
    if args.format == "json" and using_fastpath and not (args.compact_json or args.summary):
        parser.error("--format json with selective reading requires --compact-json")
    if args.delivery and (args.check or args.output is None):
        parser.error("--delivery requires -o and cannot be used with --check")
    if args.check:
        if (args.input is not None or args.output is not None or args.revisions is not None
                or args.include_related or args.format != "text" or using_fastpath
                or args.max_output_chars is not None or args.start_block or args.tables != "summary"):
            parser.error("--check does not accept input, output or reading options")
        if not check_runtime():
            print("check_kind=environment artifact_validation=not_run", file=sys.stderr, flush=True)
            print("check-failed: runtime environment check failed", file=sys.stderr, flush=True)
            return 1
        print("check_kind=environment artifact_validation=not_run")
        print("read-check-ok", flush=True)
        return 0
    if args.input is None:
        parser.error("input is required unless --check is used")
    if args.input.suffix.lower() not in WPS_SUFFIXES | {".docx", ".dotx"}:
        parser.error("input must have a .docx or .dotx extension")
    try:
        if args.input.suffix.lower() in WPS_SUFFIXES:
            raise UnsupportedInputError(WPS_UNSUPPORTED_MESSAGE)
        from archive_deliverables import validate_input_path, validate_output_path
        validate_input_path(args.input)
        if args.output is not None:
            validate_output_path(args.output, protected_inputs=[args.input])
        if args.delivery:
            if args.output.exists():
                raise ValueError('Delivery output already exists; choose a new filename')
        if args.output and args.output.exists():
            try:
                same_file = args.input.samefile(args.output)
            except OSError:
                same_file = args.input.resolve() == args.output.resolve()
            if same_file:
                parser.error("output must not refer to the input file")
        if using_fastpath:
            paragraph_range = None
            if args.paragraph_range:
                match = re.fullmatch(r"([1-9]\d*):([1-9]\d*)", args.paragraph_range)
                if not match or int(match.group(1)) > int(match.group(2)):
                    parser.error("--paragraph-range requires START:END, 1-based inclusive")
                paragraph_range = (int(match.group(1)), int(match.group(2)))
            mode = "summary" if args.summary else "headings" if args.heading_only else "compact"
            reader = read_docx_page if bound_page else read_docx_compact
            extra = {"max_output_bytes": args.max_output_bytes if args.max_output_bytes is not None else 12000, "cursor": args.cursor} if bound_page else {}
            data = reader(args.input, mode=mode, revisions=args.revisions or "accepted",
                                     include_related=args.include_related, paragraph_range=paragraph_range,
                                     section=args.section, section_occurrence=args.section_occurrence,
                                     tables="rows" if args.section and not args.compact_json else args.tables, **extra)
            if bound_page:
                text = serialize_docx_page(data)
            elif args.summary:
                data["blocks"] = data["blocks"][:100]
                data["headings_truncated"] = data["stats"]["heading_count"] > 100
                text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
                if len(text) > (args.max_output_chars or _FASTPATH_DEFAULT_CHARS):
                    raise ValueError("summary exceeds output limit; increase --max-output-chars")
            elif args.compact_json:
                text = _limited_compact(data, args.max_output_chars or _FASTPATH_DEFAULT_CHARS,
                                        start_block=args.start_block or 0)
            else:
                text = _limited_text(_compact_plain(data), args.max_output_chars)
        elif args.format == "json":
            structure = read_docx_structure(args.input, revisions=args.revisions or "accepted", include_related=args.include_related)
            text = json.dumps(structure, ensure_ascii=False, indent=2) + "\n"
            if args.max_output_chars is not None and len(text) > args.max_output_chars:
                raise ValueError("full structure exceeds --max-output-chars; use --compact-json or a scoped read")
        else:
            text = _limited_text(read_docx(args.input, revisions=args.revisions, include_related=args.include_related), args.max_output_chars)
        if args.output:
            atomic_write_bytes(args.output, text.encode("utf-8"))
            if args.delivery:
                from archive_deliverables import finalize_delivery
                result = finalize_delivery(args.output, [args.input], status='read_exported')
                result.update(format="docx-compact" if using_fastpath and (args.compact_json or args.summary) else args.format, output_written=True)
                print(json.dumps(result, ensure_ascii=False))
        elif bound_page:
            sys.stdout.buffer.write(text.encode("utf-8"))
        else:
            sys.stdout.write(text)
    except UnsupportedInputError as exc:
        if args.format == "json":
            print(json.dumps({"status": "unsupported", "message": str(exc)}, ensure_ascii=False))
        else:
            print("unsupported: " + str(exc), file=sys.stderr)
        return 1
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
