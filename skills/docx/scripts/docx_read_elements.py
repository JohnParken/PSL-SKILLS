"""Read supported stored DOCX elements for a bounded report view."""

from collections import Counter
import json
from pathlib import PurePosixPath
from xml.etree.ElementTree import TreeBuilder
import re

from docx_read_charts import CHART_NS, DRAWING_NS, REL_NS, read_chart_cache
from docx_read_numbering import DecimalNumbering
from docx_read_tables import read_table
from office import xmlio
from office.helpers import opc_target, read_part
from office.validators.advanced import w

MAIN_PART = "word/document.xml"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
VML = "{urn:schemas-microsoft-com:vml}"
WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"
WPG = "{http://schemas.microsoft.com/office/word/2010/wordprocessingGroup}"
PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
CHART_EX = "{http://schemas.microsoft.com/office/drawing/2014/chartex}"
CHART = "{" + CHART_NS + "}"
A = "{" + DRAWING_NS + "}"
R = "{" + REL_NS + "}"
PACKAGE_RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
SUPPORTED_CHOICES = {WPS[1:-1], WPG[1:-1], CHART_NS, DRAWING_NS, PIC[1:-1], WP[1:-1]}
WRAPPERS = frozenset(map(w, ("sdt", "sdtContent", "customXml", "ins", "del", "moveFrom", "moveTo", "smartTag")))
PROPERTY_TAGS = frozenset(map(w, ("pPr", "rPr", "tcPr", "trPr", "tblPr", "sdtPr", "sdtEndPr", "sectPr")))
REFERENCE_TAGS = frozenset(map(w, ("footnoteReference", "endnoteReference", "commentReference")))
IGNORABLE_BLOCKS = frozenset(map(w, ("bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd")))
RELATED_ROOTS = (
    ("header", re.compile(r"word/header[0-9]+\.xml\Z"), "hdr"),
    ("footer", re.compile(r"word/footer[0-9]+\.xml\Z"), "ftr"),
    ("footnote", re.compile(r"word/footnotes\.xml\Z"), "footnotes"),
    ("endnote", re.compile(r"word/endnotes\.xml\Z"), "endnotes"),
    ("comment", re.compile(r"word/comments\.xml\Z"), "comments"),
)
MAX_REPORT_BLOCKS = 20_000
MAX_REPORT_TEXT_CHARS = 16 * 1024 * 1024
MAX_REPORT_BLOCK_BYTES = 32 * 1024 * 1024


class ReportTree(TreeBuilder):
    """Keep namespace scope while choosing one compatibility branch."""

    def __init__(self):
        super().__init__()
        self.namespaces = {}
        self.history = []
        self.choices = {}

    def start_ns(self, prefix, uri):
        """Record the declaration until its scope ends."""
        self.history.append(self.namespaces.get(prefix))
        self.namespaces[prefix] = uri

    def end_ns(self, prefix):
        """Restore the binding from the outer XML scope."""
        previous = self.history.pop()
        if previous is None:
            self.namespaces.pop(prefix)
        else:
            self.namespaces[prefix] = previous

    def start(self, tag, attrs):
        node = super().start(tag, attrs)
        if tag == MC + "Choice":
            requirements = attrs.get("Requires", "").split()
            self.choices[node] = bool(requirements) and all(self.namespaces.get(prefix) in SUPPORTED_CHOICES for prefix in requirements)
        return node


def _rel_part(part):
    path = PurePosixPath(part)
    return str(path.parent / "_rels" / (path.name + ".rels"))


def _anchor_geometry(node):
    placement = "floating" if node.tag == WP + "anchor" else "inline"
    result = {"placement": placement}
    doc_properties = node.find(WP + "docPr")
    if doc_properties is not None:
        for key, attribute in (("object_name", "name"), ("object_title", "title"), ("description", "descr")):
            value = doc_properties.get(attribute)
            if value:
                result[key] = value
    for axis in ("H", "V"):
        position = node.find(WP + "position" + axis)
        if position is not None:
            result["horizontal" if axis == "H" else "vertical"] = {
                "relative_to": position.get("relativeFrom"),
                "offset_emu": position.findtext(WP + "posOffset"),
                "align": position.findtext(WP + "align"),
            }
    return result


def section_bounds(blocks, heading, occurrence):
    """Return the block range for an exact main-body heading and its descendants."""
    title = heading.strip()
    matches = [index for index, block in enumerate(blocks) if block["type"] == "heading" and block["text"].strip() == title]
    if not matches:
        raise ValueError(f"section heading not found: {heading!r}")
    if occurrence is None and len(matches) != 1:
        raise ValueError("section heading is ambiguous; use --section-occurrence N")
    if occurrence is not None and occurrence > len(matches):
        raise ValueError("section occurrence is out of range")
    start = matches[(occurrence or 1) - 1]
    for index in range(start + 1, len(blocks)):
        block = blocks[index]
        if block["type"] == "heading" and block["level"] <= blocks[start]["level"]:
            return start, index
    return start, len(blocks)


class ReportReader:
    """Read known stored elements with their source location and bounded diagnostics."""

    def __init__(self, package, excluded, heading_level, header_rows):
        self.package = package
        self.members = set(package.namelist())
        self.excluded = excluded
        self._heading_level = heading_level
        self.header_rows = header_rows
        self.budget = xmlio.XmlBudget()
        self.relationships = {}
        self.chart_cache = {}
        self.image_parts = set()
        self.blocks = []
        self.stats = Counter()
        self.issues = Counter()
        self.paragraph_count = Counter()
        self.table_count = 0
        self.text_chars = 0
        self.block_bytes = 0
        self.heading_checkpoints = None

    def _add(self, block):
        if len(self.blocks) >= MAX_REPORT_BLOCKS:
            raise ValueError("report_block_limit_exceeded")
        self.block_bytes += len(json.dumps(block, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if self.block_bytes > MAX_REPORT_BLOCK_BYTES:
            raise ValueError("report_total_output_limit_exceeded")
        self.blocks.append(block)
        self.stats[block["type"]] += 1
        if block.get("numbering_label") is not None:
            self.stats["numbered_paragraphs"] += 1

    def _checked_text(self, text):
        self.text_chars += len(text)
        if self.text_chars > MAX_REPORT_TEXT_CHARS:
            raise ValueError("report_text_limit_exceeded")
        return text

    def _xml(self, part, tree=None):
        return xmlio.fromstring(read_part(self.package, part), budget=self.budget, tree_builder=tree)

    def _story_xml(self, part):
        tree = ReportTree()
        root = self._xml(part, tree)
        self._select_compatibility(root, tree.choices)
        return root

    def _select_compatibility(self, container, choices):
        """Process selected stored branches once, discarding their alternatives in memory."""
        selected = []
        for node in container:
            if node.tag == MC + "AlternateContent":
                branch = next((choice for choice in node.findall(MC + "Choice") if choices[choice]), node.find(MC + "Fallback"))
                if branch is None:
                    self.issues["compatibility_branch_unavailable"] += 1
                    continue
                if branch.tag == MC + "Fallback":
                    self.stats["compatibility_fallbacks"] += 1
                else:
                    self.stats["compatibility_choices"] += 1
                self._select_compatibility(branch, choices)
                selected.extend(branch)
            else:
                if node.tag not in self.excluded and node.tag not in PROPERTY_TAGS:
                    self._select_compatibility(node, choices)
                selected.append(node)
        container[:] = selected

    def _relationship(self, owner, identifier, kind):
        if owner not in self.relationships:
            part = _rel_part(owner)
            self.relationships[owner] = self._read_relationships(part) if part in self.members else {}
        node = self.relationships[owner].get(identifier)
        if node is None or node.get("Type") != REL_NS + "/" + kind:
            return "missing_relationship", None
        mode = node.get("TargetMode", "Internal")
        target = opc_target(node.get("Target", ""), owner, mode)
        if mode == "External":
            return "external", None
        return ("internal", target) if target in self.members else ("missing_part", None)

    def _read_relationships(self, part):
        root = self._xml(part)
        if root.tag != PACKAGE_RELS + "Relationships":
            raise ValueError("invalid_relationships_root")
        items = {}
        for node in root:
            if node.tag != PACKAGE_RELS + "Relationship":
                raise ValueError("invalid_relationship_element")
            identifier = node.get("Id")
            if not identifier or identifier in items:
                raise ValueError("missing_or_duplicate_relationship_id")
            if node.get("TargetMode", "Internal") not in ("Internal", "External"):
                raise ValueError("invalid_relationship_target_mode")
            items[identifier] = node
        return items

    def _children(self, container):
        return (node for node in container if node.tag not in self.excluded and node.tag not in PROPERTY_TAGS)

    def _direct(self, container, name):
        for node in self._children(container):
            if node.tag == w(name):
                yield node
            elif node.tag in WRAPPERS:
                yield from self._direct(node, name)

    def _text(self, paragraph):
        pieces = []

        def visit(node):
            for child in self._children(node):
                if child.tag in (w("t"), w("delText")):
                    pieces.append(child.text or "")
                elif child.tag == w("tab"):
                    pieces.append("\t")
                elif child.tag in (w("br"), w("cr")):
                    pieces.append("\n")
                elif child.tag == w("noBreakHyphen"):
                    pieces.append("\u2011")
                elif child.tag == w("softHyphen"):
                    pieces.append("\u00ad")
                elif child.tag not in (w("p"), w("txbxContent"), w("drawing"), w("pict"), w("object")):
                    visit(child)

        visit(paragraph)
        return self._checked_text("".join(pieces))

    def _paragraph(self, paragraph, anchor, numbering=None):
        level = self._heading_level(paragraph) if anchor["part"] == MAIN_PART else None
        block = {"type": "heading" if level else "paragraph", **anchor, "text": self._text(paragraph)}
        if level:
            block["level"] = level
            if self.heading_checkpoints is not None:
                self.heading_checkpoints[len(self.blocks)] = (self.stats.copy(), self.issues.copy())
        if numbering is not None:
            label, issue = numbering.label(paragraph)
            if label is not None:
                block["numbering_label"] = self._checked_text(label)
            if issue:
                self.issues[issue] += 1
        self._add(block)
        self._paragraph_objects(paragraph, anchor)

    def _paragraph_objects(self, container, source):
        for node in self._children(container):
            if node.tag in (w("drawing"), w("pict")):
                self._drawing(node, source)
            elif node.tag in (w("object"), w("control")):
                self.issues["embedded_object_not_decoded"] += 1
            elif node.tag in REFERENCE_TAGS:
                self._add({"type": "reference", "anchor": source, "kind": node.tag.rsplit("}", 1)[-1], "id": node.get(w("id"))})
            elif node.tag in (w("sym"), w("subDoc"), w("contentPart"), w("altChunk")):
                self.issues["unsupported_inline_content"] += 1
            elif node.tag not in (w("p"), w("txbxContent")):
                self._paragraph_objects(node, source)

    def _drawing(self, drawing, source):
        self.stats["drawings"] += 1
        identifier = self.stats["drawings"]
        placement = next((node for node in drawing if node.tag in (WP + "anchor", WP + "inline")), None)
        anchor = {"drawing_index": identifier, **source}
        if placement is not None:
            anchor.update(_anchor_geometry(placement))
        elif drawing.tag == w("pict"):
            anchor["placement"] = "legacy_vml"
        before = len(self.blocks)
        self._drawing_content(drawing, anchor)
        if len(self.blocks) == before:
            self.stats["drawings_without_supported_content"] += 1

    def _drawing_content(self, container, anchor):
        for node in self._children(container):
            if node.tag == w("txbxContent"):
                self._textbox(node, anchor)
            elif node.tag in (CHART + "chart", CHART_EX + "chart"):
                self._chart(node, anchor)
            elif node.tag in (A + "blip", VML + "imagedata"):
                self._image(node, anchor)
            elif node.tag == A + "txBody":
                self._drawing_text(node, anchor)
            else:
                self._drawing_content(node, anchor)

    def _drawing_text(self, container, anchor):
        paragraphs = []
        for paragraph in container.findall(A + "p"):
            pieces = []
            for node in paragraph:
                if node.tag == A + "br":
                    pieces.append("\n")
                elif node.tag in (A + "r", A + "fld"):
                    pieces.append(node.findtext(A + "t", default=""))
            paragraphs.append(self._checked_text("".join(pieces)))
        if paragraphs:
            self._add({"type": "textbox", "textbox_index": self.stats["textbox"] + 1,
                       "anchor": anchor, "paragraphs": paragraphs, "text_format": "drawingml"})

    def _textbox(self, node, anchor):
        source_paragraphs = list(self._direct(node, "p"))
        paragraphs = [self._text(paragraph) for paragraph in source_paragraphs]
        tables = list(self._direct(node, "tbl"))
        index = self.stats["textbox"] + 1
        self._add({"type": "textbox", "textbox_index": index, "anchor": anchor, "paragraphs": paragraphs})
        for position, paragraph in enumerate(source_paragraphs, 1):
            self._paragraph_objects(paragraph, {**anchor, "textbox_index": index, "textbox_paragraph": position})
        for table in tables:
            self._table(table, {"part": anchor["part"], "textbox_index": index})

    def _image(self, node, anchor):
        identifier = node.get(R + "embed") or node.get(R + "link") or node.get(R + "id")
        if not identifier:
            if node.tag == A + "blip":
                self.issues["image_relationship_unavailable"] += 1
            return
        status, target = self._relationship(anchor["part"], identifier, "image")
        self._add({"type": "image", "anchor": anchor, "resource_status": status, "resource_part": target})
        if status == "internal":
            self.image_parts.add(target)
        else:
            self.issues["image_" + status] += 1

    def _chart(self, node, anchor):
        if node.tag == CHART_EX + "chart":
            status, target = "extended_chart_unsupported", None
        else:
            status, target = self._relationship(anchor["part"], node.get(R + "id"), "chart")
        chart_index = self.stats["chart"] + 1
        parsed = None
        if status == "internal":
            parsed = self.chart_cache.get(target)
            if parsed is None:
                parsed = self._read_chart(target)
                self.chart_cache[target] = parsed
        if parsed is None:
            parsed = {"title": None, "series": [], "data_link": "unknown", "issues": [status]}
        self.issues.update(parsed["issues"])
        self._add({"type": "chart", "chart_index": chart_index, "anchor": anchor, "part": target, "title": parsed["title"],
                  "data_link": parsed["data_link"], "series_count": len(parsed["series"]), "issues": parsed["issues"]})
        self.stats["chart_parts_read"] = len(self.chart_cache)
        for series in parsed["series"]:
            self._chart_series(chart_index, series)

    def _read_chart(self, part):
        root = self._xml(part)
        try:
            parsed = read_chart_cache(root)
        except ValueError as exc:
            raise ValueError(f"invalid chart cache in {part}: {exc}") from exc
        identifier = parsed["external_data_id"]
        link = self._chart_link(part, identifier) if identifier else "none"
        parsed["data_link"] = link
        if link != "none":
            parsed["issues"].append("chart_" + link)
        return parsed

    def _chart_link(self, part, identifier):
        status, _ = self._relationship(part, identifier, "package")
        if status == "missing_relationship":
            status, _ = self._relationship(part, identifier, "oleObject")
        return {"internal": "embedded_workbook_not_read", "external": "external_workbook_not_followed"}.get(status, status)

    def _chart_series(self, chart_index, series):
        points = series["points"]
        self.issues.update(series["issues"])
        self._add({"type": "chart_series", "chart_index": chart_index, "series_index": series["index"],
                  **{key: value for key, value in series.items() if key not in ("points", "index")}, "point_count": len(points)})
        for point in points:
            self._add({"type": "chart_point", "chart_index": chart_index, "series_index": series["index"], **point})

    def _table(self, table, anchor):
        self.table_count += 1
        index = self.table_count
        source = {**anchor, "table_index": index}
        declared_headers = self.header_rows.get(index)

        def cell_text(cell):
            return "\n".join(self._text(paragraph) for paragraph in self._direct(cell, "p"))

        meta, rows = read_table(table, self._direct, cell_text, declared_headers)
        self.issues.update(meta["issues"])
        self.stats["table_cells"] += meta["physical_cell_count"]
        self._add({**meta, **source})
        for row, elements in rows:
            self._add({**row, **source})
            for cell, element in zip(row["cells"], elements):
                location = {**source, "row": row["row_index"], "column": cell["column"]}
                self._cell_objects(element, location)

    def _cell_objects(self, cell, location):
        for index, paragraph in enumerate(self._direct(cell, "p"), 1):
            self._paragraph_objects(paragraph, {**location, "cell_paragraph": index})
        for nested in self._direct(cell, "tbl"):
            self._table(nested, {"part": location["part"], "parent_cell": location})

    def _walk(self, container, part, numbering=None, **context):
        for node in self._children(container):
            if node.tag == w("p"):
                self.paragraph_count[part] += 1
                self._paragraph(node, {"part": part, "paragraph_index": self.paragraph_count[part], **context}, numbering)
            elif node.tag == w("tbl"):
                self._table(node, {"part": part, **context})
            elif node.tag in WRAPPERS:
                self._walk(node, part, numbering, **context)
            elif node.tag == w("altChunk"):
                self.issues["alternate_document_content_not_read"] += 1
            elif node.tag not in IGNORABLE_BLOCKS:
                self.issues["unrecognized_block_not_read"] += 1

    def _section_view(self, heading, occurrence):
        start, stop = section_bounds(self.blocks, heading, occurrence)
        before_stats, before_issues = self.heading_checkpoints[start]
        after_stats, after_issues = self.heading_checkpoints.get(stop, (self.stats, self.issues))
        blocks = self.blocks[start:stop]
        stats = after_stats - before_stats
        issues = after_issues - before_issues
        if after_issues["compatibility_branch_unavailable"]:
            # Compatibility is resolved for the whole body before heading
            # selection. A missing branch can affect the inferred boundaries.
            issues["compatibility_branch_unavailable"] = after_issues["compatibility_branch_unavailable"]
        stats["image_resources"] = len({block["resource_part"] for block in blocks
                                        if block["type"] == "image" and block["resource_status"] == "internal"})
        if stats["chart"]:
            stats["chart_parts_read"] = len({block["part"] for block in blocks if block["type"] == "chart" and block["part"] is not None})
        return blocks, dict(stats), dict(sorted(issues.items())), {}

    def read(self, *, section=None, section_occurrence=None):
        """Return blocks, counts, diagnostic counts and related-part counts."""
        if MAIN_PART not in self.members:
            raise ValueError("DOCX package is missing word/document.xml")
        document = self._story_xml(MAIN_PART)
        bodies = document.findall(w("body"))
        if document.tag != w("document") or len(bodies) != 1:
            raise ValueError("expected one native DOCX body")
        numbering = self._xml("word/numbering.xml") if "word/numbering.xml" in self.members else None
        if numbering is not None and numbering.tag != w("numbering"):
            raise ValueError("invalid numbering root")
        self.heading_checkpoints = {} if section is not None else None
        self._walk(bodies[0], MAIN_PART, DecimalNumbering(numbering))
        selected = self._section_view(section, section_occurrence) if section is not None else None
        related = Counter()
        for kind, pattern, expected in RELATED_ROOTS:
            for part in sorted(name for name in self.members if pattern.fullmatch(name)):
                self._related_part(part, kind, expected)
                related[kind] += 1
        if set(self.header_rows).difference(range(1, self.table_count + 1)):
            raise ValueError("explicit table header refers to a nonexistent table")
        if selected is not None:
            return selected
        self.stats["image_resources"] = len(self.image_parts)
        return self.blocks, dict(self.stats), dict(sorted(self.issues.items())), dict(related)

    def _related_part(self, part, kind, expected):
        root = self._story_xml(part)
        if root.tag != w(expected):
            raise ValueError("invalid related story root")
        if kind in ("header", "footer"):
            self._walk(root, part, story=kind)
        else:
            identifiers = set()
            for entry in self._direct(root, kind):
                identifier = entry.get(w("id"))
                if identifier is None or identifier in identifiers:
                    raise ValueError(f"missing or duplicate {kind} id")
                identifiers.add(identifier)
                if entry.get(w("type")) not in ("separator", "continuationSeparator"):
                    self._walk(entry, part, story=kind, story_id=identifier)
