"""Read DOCX/DOTX, or text from supported OOXML/MS-DOC content in .wps files."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path
from xml.etree.ElementTree import Element

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from office import xmlio as SafeET
from office.archiveio import open_archive
from office.binary_doc import read_ms_doc
from office.helpers import atomic_write_bytes, check_archive, read_part

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
WPS_MAIN_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
}


def _check_wps_signature(path):
    """Classify only the bounded signature; ZIP content still needs validation."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("WPS input must be a regular file")
        prefix = os.read(descriptor, 8)
    finally:
        os.close(descriptor)
    if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "ooxml"
    if prefix == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "cfb"
    elif prefix.startswith(b"{\\rtf"):
        kind = "RTF content"
    elif not prefix:
        kind = "empty file"
    else:
        kind = "unknown content (including unsupported legacy WPS variants)"
    raise ValueError(f"unsupported WPS format: {kind}; only compatible OOXML or unencrypted MS-DOC text reading is supported")


def _check_wps_package(package, budget):
    """Recognize a narrow OOXML subset, without claiming full format validation."""
    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    if not required.issubset(package.namelist()):
        raise ValueError("unsupported WPS ZIP content: required OOXML document parts are missing")
    types = SafeET.fromstring(read_part(package, "[Content_Types].xml"), budget=budget)
    if types.tag != f"{{{CONTENT_TYPES}}}Types":
        raise ValueError("unsupported WPS ZIP content: invalid OOXML content-types root")
    declarations = [node.get("ContentType") for node in types
                    if node.tag == f"{{{CONTENT_TYPES}}}Override"
                    and node.get("PartName") == "/word/document.xml"]
    if len(declarations) != 1 or declarations[0] not in WPS_MAIN_TYPES:
        raise ValueError("unsupported WPS ZIP content: ambiguous or unsupported main content type")
    relationships = SafeET.fromstring(read_part(package, "_rels/.rels"), budget=budget)
    if relationships.tag != f"{{{RELATIONSHIPS}}}Relationships":
        raise ValueError("unsupported WPS ZIP content: invalid OOXML relationships root")
    main = [node for node in relationships if node.tag == f"{{{RELATIONSHIPS}}}Relationship"
            and node.get("Type") == OFFICE_DOCUMENT]
    if (len(main) != 1 or main[0].get("TargetMode", "Internal") != "Internal"
            or main[0].get("Target") not in ("word/document.xml", "/word/document.xml")):
        raise ValueError("unsupported WPS ZIP content: ambiguous or unsupported main document relationship")
    # Check every member's bounded length and CRC, including unparsed binary parts.
    for name in package.namelist():
        read_part(package, name)


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
        if blocked or name == "txbxContent":
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
    is_wps = Path(path).suffix.lower() == ".wps"
    if is_wps and _check_wps_signature(path) == "cfb":
        if include_related:
            raise ValueError("binary WPS supports main body only; --include-related is not supported")
        if revisions not in (None, "all"):
            raise ValueError("binary WPS cannot filter revisions; omit --revisions or use all for stored text")
        return read_ms_doc(path)
    revisions = revisions or "accepted"
    try:
        with open_archive(path) as package:
            check_archive(package)
            if is_wps:
                _check_wps_package(package, xml_budget)
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
    if Path(path).suffix.lower() == ".wps":
        raise ValueError("WPS supports text reading only; structured JSON is not supported")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input .docx, .dotx, or supported OOXML/MS-DOC .wps file (text only)")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--revisions", choices=tuple(_REVISION_TAGS), default=None,
                        help="OOXML defaults to accepted; binary WPS defaults to stored text, only all is supported")
    parser.add_argument("--include-related", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if args.input.suffix.lower() not in (".docx", ".dotx", ".wps"):
        parser.error("input must have a .docx, .dotx or .wps extension")
    try:
        if args.output and args.output.exists():
            try:
                same_file = args.input.samefile(args.output)
            except OSError:
                same_file = args.input.resolve() == args.output.resolve()
            if same_file:
                parser.error("output must not refer to the input file")
        if args.format == "json":
            structure = read_docx_structure(args.input, revisions=args.revisions or "accepted", include_related=args.include_related)
            text = json.dumps(structure, ensure_ascii=False, indent=2) + "\n"
        else:
            text = read_docx(args.input, revisions=args.revisions, include_related=args.include_related)
        if args.output:
            atomic_write_bytes(args.output, text.encode("utf-8"))
        else:
            sys.stdout.write(text)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
