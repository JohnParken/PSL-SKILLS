"""Read location-addressed text and object records from a DOCX package."""

from collections import Counter
from hashlib import sha256
import re

from tl_read_charts import read_chart
from tl_read_revisions import revision_issues, revision_text, selected_children, text


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
V_NS = "urn:schemas-microsoft-com:vml"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
WPG_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
SUPPORTED_CHOICES = {A_NS, C_NS, PIC_NS, WP_NS, WPS_NS, WPG_NS}
WRAPPERS = {
    "sdt", "sdtContent", "customXml", "ins", "del", "moveFrom", "moveTo",
    "smartTag", "hyperlink", "bdo", "dir",
}
PROPERTY_TAGS = {
    "pPr", "rPr", "tcPr", "trPr", "tblPr", "tblGrid", "sdtPr", "sdtEndPr",
}
STORY_PARTS = {
    "header": re.compile(r"word/header[0-9]+\.xml\Z"),
    "footer": re.compile(r"word/footer[0-9]+\.xml\Z"),
    "footnote": re.compile(r"word/footnotes\.xml\Z"),
    "endnote": re.compile(r"word/endnotes\.xml\Z"),
    "comment": re.compile(r"word/comments\.xml\Z"),
}
STORY_ROOTS = {
    "header": "hdr", "footer": "ftr", "footnote": "footnotes",
    "endnote": "endnotes", "comment": "comments",
}


def _name(tag):
    if not isinstance(tag, str):
        return "", ""
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _qname(node):
    namespace, local = _name(node.tag)
    prefixes = {
        W_NS: "w", R_NS: "r", MC_NS: "mc", WP_NS: "wp", A_NS: "a",
        C_NS: "c", M_NS: "m", V_NS: "v", WPS_NS: "wps", WPG_NS: "wpg",
        PIC_NS: "pic",
    }
    prefix = prefixes.get(namespace)
    if prefix is None:
        return "{" + namespace + "}" + local if namespace else local
    return prefix + ":" + local


def _path(node):
    parts = []
    current = node
    while current is not None:
        if not isinstance(current.tag, str):
            return "/"
        parent = current.getparent()
        index = 1 + sum(1 for _ in current.itersiblings(current.tag, preceding=True)) if parent is not None else 1
        parts.append(_qname(current) + "[" + str(index) + "]")
        current = parent
    return "/" + "/".join(reversed(parts))


def _is_word(node, local):
    return node.tag == "{" + W_NS + "}" + local


def _is_ns(node, namespace, local):
    return node.tag == "{" + namespace + "}" + local


def _children(node):
    return [child for child in node if isinstance(child.tag, str)]


def _local_text(node):
    return "".join(node.itertext())


class ReportReader:
    """Build bounded, location-addressed records using the package interface."""

    def __init__(self, package, revisions=None, include_related=True):
        self.package = package
        self.revisions = revisions
        self.include_related = include_related
        self.names = set(package.names)
        self.records = []
        self.issues = []
        self.counts = Counter()
        self.table_count = 0
        self.chart_count = 0
        self.image_targets = set()
        self.image_hashes = {}
        self.image_references = 0
        self.textbox_count = 0
        self.chart_series_count = 0
        self.chart_numeric_count = 0
        self.vmerge_continuations = 0
        self.vmerge_origins = 0
        self.styles = {}
        self.numbering = {}
        self.numbering_counters = {}
        self.numbering_with_overrides = set()
        self.current_part = None

    def _issue(self, code, part, path=None, **identifiers):
        issue = {"code": code, "part": part}
        if path is not None:
            issue["path"] = path
        issue.update(identifiers)
        self.issues.append(issue)

    def _path(self, node):
        return _path(node)

    def _record(self, record_type, part, node, **fields):
        record = {
            "type": record_type,
            "part": part,
            "path": _path(node) if not isinstance(node, str) else node,
        }
        record.update(fields)
        self.records.append(record)
        self.counts[record_type] += 1
        return record

    def _selected_children(self, node, part):
        yield from selected_children(self, node, part or self.current_part or "")

    def _alternate_branch(self, node, part):
        fallback = None
        for branch in _children(node):
            if _is_ns(branch, MC_NS, "Fallback"):
                fallback = branch
            elif _is_ns(branch, MC_NS, "Choice"):
                requirements = branch.get("Requires", "").split()
                namespace_map = branch.nsmap or {}
                supported = bool(requirements) and all(
                    namespace_map.get(prefix) in SUPPORTED_CHOICES
                    for prefix in requirements
                )
                if supported:
                    return branch
        if fallback is not None:
            return fallback
        self._issue("alternate_content_branch_unavailable", part, _path(node))
        return None

    def _text(self, node, deleted_only=False):
        return text(self, node, deleted_only)

    def _revision_text(self, paragraph):
        if self.revisions is None:
            return revision_text(self, paragraph, legacy=True)
        return revision_text(self, paragraph)

    def _fields(self, paragraph):
        simple_fields = []
        complex_fields = []
        stack = []

        def visit(node):
            for child in self._selected_children(node, ""):
                if _is_word(child, "fldSimple"):
                    simple_fields.append({
                        "kind": "simple",
                        "instruction": child.get("{" + W_NS + "}instr", ""),
                        "text": self._text(child),
                        "path": _path(child),
                    })
                    continue
                if _is_word(child, "fldChar"):
                    field_type = child.get("{" + W_NS + "}fldCharType")
                    if field_type == "begin":
                        stack.append({"instruction": [], "result": [], "phase": "instruction", "path": _path(child)})
                    elif field_type == "separate" and stack:
                        stack[-1]["phase"] = "result"
                    elif field_type == "end" and stack:
                        field = stack.pop()
                        complex_fields.append({
                            "kind": "complex",
                            "instruction": "".join(field["instruction"]),
                            "text": "".join(field["result"]),
                            "path": field["path"],
                        })
                    continue
                if _is_word(child, "instrText") and stack:
                    if stack[-1]["phase"] == "instruction":
                        stack[-1]["instruction"].append(child.text or "")
                    continue
                if _is_word(child, "t") and stack and stack[-1]["phase"] == "result":
                    stack[-1]["result"].append(child.text or "")
                    continue
                visit(child)

        visit(paragraph)
        for field in stack:
            self._issue("complex_field_unclosed", "", field["path"])
        return simple_fields + complex_fields

    def _load_styles(self):
        part = "word/styles.xml"
        if part not in self.names:
            return
        root = self.package.xml(part)
        for style in _children(root):
            if not _is_word(style, "style"):
                continue
            style_id = style.get("{" + W_NS + "}styleId")
            if not style_id:
                continue
            name_node = next((item for item in _children(style) if _is_word(item, "name")), None)
            outline = next((item for item in _children(style) if _is_word(item, "pPr")), None)
            outline_node = None if outline is None else next(
                (item for item in _children(outline) if _is_word(item, "outlineLvl")), None
            )
            based_on = next((item for item in _children(style) if _is_word(item, "basedOn")), None)
            self.styles[style_id] = {
                "name": None if name_node is None else name_node.get("{" + W_NS + "}val"),
                "type": style.get("{" + W_NS + "}type"),
                "based_on": None if based_on is None else based_on.get("{" + W_NS + "}val"),
                "outline_level": None if outline_node is None else outline_node.get("{" + W_NS + "}val"),
            }

    def _load_numbering(self):
        part = "word/numbering.xml"
        if part not in self.names:
            return
        root = self.package.xml(part)
        abstracts = {}
        nums = {}
        for node in _children(root):
            if _is_word(node, "abstractNum"):
                abstract_id = node.get("{" + W_NS + "}abstractNumId")
                levels = []
                for level in _children(node):
                    if not _is_word(level, "lvl"):
                        continue
                    level_info = {"level": level.get("{" + W_NS + "}ilvl")}
                    for child in _children(level):
                        _, local = _name(child.tag)
                        if local in ("start", "numFmt", "lvlText", "lvlJc", "pStyle"):
                            level_info[local] = child.get("{" + W_NS + "}val")
                    levels.append(level_info)
                abstracts[abstract_id] = levels
                if self.include_related:
                    self._record(
                        "numbering_definition", part, node,
                        abstract_num_id=abstract_id, levels=levels,
                    )
            elif _is_word(node, "num"):
                num_id = node.get("{" + W_NS + "}numId")
                abstract = next((item for item in _children(node) if _is_word(item, "abstractNumId")), None)
                abstract_id = None if abstract is None else abstract.get("{" + W_NS + "}val")
                nums[num_id] = abstract_id
                if any(_is_word(item, "lvlOverride") for item in _children(node)):
                    self.numbering_with_overrides.add(num_id)
                if self.include_related:
                    self._record(
                        "numbering_definition", part, node,
                        num_id=num_id, abstract_num_id=abstract_id,
                        levels=abstracts.get(abstract_id, []),
                    )
        self.numbering = {num_id: abstracts.get(abstract_id, []) for num_id, abstract_id in nums.items()}

    def _paragraph_info(self, paragraph):
        properties = next((item for item in _children(paragraph) if _is_word(item, "pPr")), None)
        style_node = None if properties is None else next(
            (item for item in _children(properties) if _is_word(item, "pStyle")), None
        )
        style_id = None if style_node is None else style_node.get("{" + W_NS + "}val")
        style = self.styles.get(style_id, {})
        info = {
            "style_id": style_id,
            "style_name": style.get("name"),
            "style_type": style.get("type"),
            "style_based_on": style.get("based_on"),
        }
        outline_node = None if properties is None else next(
            (item for item in _children(properties) if _is_word(item, "outlineLvl")), None
        )
        outline = None if outline_node is None else outline_node.get("{" + W_NS + "}val")
        if outline is None:
            outline = style.get("outline_level")
        if outline is None:
            heading_name = style.get("name") or style_id or ""
            heading_match = re.search(r"(?:heading|标题)\s*([1-9])\Z", heading_name, re.IGNORECASE)
            if heading_match is not None:
                outline = str(int(heading_match.group(1)) - 1)
        if outline is not None:
            info["outline_level"] = outline
            try:
                heading_level = int(outline) + 1
                if 1 <= heading_level <= 9:
                    info["heading_level"] = heading_level
            except ValueError:
                self._issue("paragraph_outline_level_invalid", self.current_part, _path(paragraph))

        num_properties = None if properties is None else next(
            (item for item in _children(properties) if _is_word(item, "numPr")), None
        )
        if num_properties is not None:
            num_id_node = next((item for item in _children(num_properties) if _is_word(item, "numId")), None)
            level_node = next((item for item in _children(num_properties) if _is_word(item, "ilvl")), None)
            num_id = None if num_id_node is None else num_id_node.get("{" + W_NS + "}val")
            level = "0" if level_node is None else level_node.get("{" + W_NS + "}val", "0")
            level_info = self.numbering.get(num_id, [])
            level_info = next((item for item in level_info if item.get("level") == level), None)
            info["numbering"] = {
                "num_id": num_id,
                "level": level,
                "format": None if level_info is None else level_info.get("numFmt"),
                "text": None if level_info is None else level_info.get("lvlText"),
                "suppressed": num_id == "0",
            }
            if (level == "0" and num_id not in (None, "0") and num_id not in self.numbering_with_overrides
                    and len(self.numbering.get(num_id, [])) == 1 and level_info):
                pattern, start = level_info.get("lvlText") or "", level_info.get("start") or "0"
                if level_info.get("numFmt") == "decimal" and "%1" in pattern and not re.search(r"%[2-9]", pattern) and re.fullmatch(r"[0-9]{1,9}", start):
                    key = (self.current_part, num_id)
                    number = self.numbering_counters.get(key, int(start) - 1) + 1
                    self.numbering_counters[key] = number
                    info["numbering"]["label"] = pattern.replace("%1", str(number))
            if num_id is not None and num_id != "0" and level_info is None:
                self._issue(
                    "numbering_definition_unavailable", self.current_part,
                    _path(paragraph), num_id=num_id, level=level,
                )
        return info

    def _paragraph(self, paragraph, part, context):
        fields = self._fields(paragraph)
        record = self._record(
            "paragraph", part, paragraph,
            text=self._text(paragraph),
            **self._paragraph_info(paragraph),
        )
        record.update(context)
        deleted_text = self._text(paragraph, deleted_only=True)
        if deleted_text:
            record["deleted_text"] = deleted_text
        revisions = self._revision_text(paragraph)
        if revisions:
            record["revisions"] = revisions
        if fields:
            record["fields"] = fields
        self._paragraph_objects(paragraph, part, context)

    def _paragraph_objects(self, paragraph, part, context):
        def visit(node):
            for child in self._selected_children(node, part):
                if _is_word(child, "hyperlink"):
                    self._hyperlink(child, part, context)
                    visit(child)
                elif _is_word(child, "drawing") or _is_word(child, "pict"):
                    self._drawing(child, part, context)
                elif _is_ns(child, M_NS, "oMath") or _is_ns(child, M_NS, "oMathPara"):
                    self._record(
                        "math", part, child,
                        display=child.tag == "{" + M_NS + "}oMathPara",
                        text=self._math_text(child),
                    )
                elif _is_word(child, "sectPr"):
                    self._section(child, part)
                elif _is_word(child, "altChunk"):
                    self._issue("alternate_document_content_not_read", part, _path(child))
                elif _is_word(child, "object") or _is_word(child, "control"):
                    self._issue("embedded_object_not_decoded", part, _path(child))
                elif _is_word(child, "sym"):
                    self._issue("symbol_glyph_mapping_not_resolved", part, _path(child))
                else:
                    visit(child)

        visit(paragraph)

    def _math_text(self, node):
        return "".join(
            child.text or "" for child in node.iter()
            if _is_ns(child, M_NS, "t")
        )

    def _hyperlink(self, node, part, context):
        relation_id = node.get("{" + R_NS + "}id")
        anchor = node.get("{" + W_NS + "}anchor")
        relation = self._relation(part, relation_id, "hyperlink") if relation_id else None
        fields = {
            "text": self._text(node),
            "relationship_id": relation_id,
            "anchor": anchor,
            "target": None if relation is None else relation.get("target"),
            "external": None if relation is None else relation.get("external"),
            "relationship_type": None if relation is None else relation.get("type"),
        }
        fields.update(context)
        self._record("hyperlink", part, node, **fields)
        if relation_id and relation is None:
            self._issue("hyperlink_relationship_unavailable", part, _path(node), relationship_id=relation_id)

    def _relation(self, part, relation_id, expected):
        try:
            relation = self.package.related(part, relation_id)
        except (KeyError, TypeError, ValueError):
            relation = None
        if relation is None:
            return None
        relation_type = relation.get("type", "")
        if expected and not relation_type.endswith("/" + expected):
            self._issue(
                "relationship_type_unexpected", part, relationship_id=relation_id,
                expected=expected, actual=relation_type,
            )
        return relation

    def _drawing_properties(self, node):
        current = node
        while current is not None:
            for child in current.iter():
                if _is_ns(child, WP_NS, "docPr") or _is_ns(child, PIC_NS, "cNvPr"):
                    values = {}
                    for key, attribute in (
                        ("description", "descr"), ("title", "title"), ("name", "name"),
                    ):
                        value = child.get(attribute)
                        if value:
                            values[key] = value
                    if values:
                        return values
            if _is_word(current, "p"):
                break
            current = current.getparent()
        return {}

    def _drawing(self, node, part, context):
        def visit(current):
            for child in self._selected_children(current, part):
                if _is_word(child, "txbxContent"):
                    self._textbox(child, part, context)
                    continue
                if _is_ns(child, A_NS, "txBody"):
                    self._drawing_textbox(child, part, context)
                    continue
                if _is_ns(child, A_NS, "blip") or _is_ns(child, V_NS, "imagedata"):
                    self._image(child, part, context)
                    continue
                if _is_ns(child, C_NS, "chart") or child.tag.endswith("}chart"):
                    self._chart(child, part, context)
                    continue
                visit(child)

        visit(node)

    def _textbox(self, node, part, context):
        paragraphs = self._find_wrapped(node, "p", stop={"tbl", "txbxContent"})
        texts = [self._text(paragraph) for paragraph in paragraphs]
        self.textbox_count += 1
        record = self._record(
            "textbox", part, node,
            text="\n".join(texts),
            paragraph_count=len(texts),
            text_format="wordprocessingml",
        )
        record.update(context)
        for paragraph in paragraphs:
            self._paragraph_objects(paragraph, part, context)
        for table in self._find_wrapped(node, "tbl", stop={"tbl"}):
            self._table(table, part, context)

    def _drawing_textbox(self, node, part, context):
        paragraphs = []
        for paragraph in node.iter():
            if _is_ns(paragraph, A_NS, "p"):
                pieces = []
                for child in paragraph:
                    if _is_ns(child, A_NS, "br"):
                        pieces.append("\n")
                    elif _is_ns(child, A_NS, "r") or _is_ns(child, A_NS, "fld"):
                        pieces.extend(
                            item.text or "" for item in child.iter()
                            if _is_ns(item, A_NS, "t")
                        )
                paragraphs.append("".join(pieces))
        self.textbox_count += 1
        record = self._record(
            "textbox", part, node,
            text="\n".join(paragraphs),
            paragraph_count=len(paragraphs),
            text_format="drawingml",
        )
        record.update(context)

    def _image(self, node, part, context):
        relationship_id = node.get("{" + R_NS + "}embed")
        if relationship_id is None:
            relationship_id = node.get("{" + R_NS + "}link")
        if relationship_id is None:
            relationship_id = node.get("{" + R_NS + "}id")
        relation = self._relation(part, relationship_id, "image") if relationship_id else None
        properties = self._drawing_properties(node)
        self.image_references += 1
        target = None if relation is None else relation.get("target")
        external = None if relation is None else relation.get("external")
        resource_part = target if not external and target in self.names else None
        if resource_part is not None:
            self.image_targets.add(resource_part)
        elif relation is not None and not external:
            self._issue(
                "image_part_missing", part, _path(node), relationship_id=relationship_id,
                target=target,
            )
        fields = {
            "relationship_id": relationship_id,
            "resource_part": resource_part,
            "target": target,
            "external": external,
            "relationship_type": None if relation is None else relation.get("type"),
            **properties,
        }
        fields.update(context)
        if resource_part in self.names:
            if resource_part not in self.image_hashes:
                self.image_hashes[resource_part] = sha256(self.package.read(resource_part)).hexdigest()
            fields["resource_sha256"] = self.image_hashes[resource_part]
        self._record("image", part, node, **fields)
        if relationship_id is None or relationship_id == "" or relation is None:
            self._issue("image_relationship_unavailable", part, _path(node), relationship_id=relationship_id)

    def _chart(self, node, part, context):
        relationship_id = node.get("{" + R_NS + "}id")
        relation = self._relation(part, relationship_id, "chart") if relationship_id else None
        chart_index = self.chart_count + 1
        target = None if relation is None or relation.get("external") else relation.get("target")
        chart_data = {"title": None, "series": [], "external_data_id": None, "issues": []}
        if target is not None and target in self.names:
            try:
                chart_data = read_chart(self.package.xml(target))
            except (KeyError, TypeError, ValueError):
                chart_data["issues"].append("chart_part_unreadable")
        elif relation is None:
            chart_data["issues"].append("chart_relationship_unavailable")
        elif relation.get("external"):
            chart_data["issues"].append("external_chart_not_read")
        else:
            chart_data["issues"].append("chart_part_missing")

        workbook = None
        external_id = chart_data.get("external_data_id")
        if external_id and target:
            workbook = self._relation(target, external_id, None)
            if workbook is None:
                chart_data["issues"].append("chart_workbook_relationship_unavailable")
            elif not workbook.get("type", "").endswith(("/package", "/oleObject")):
                self._issue(
                    "relationship_type_unexpected", target, _path(node),
                    relationship_id=external_id,
                    expected=["package", "oleObject"],
                    actual=workbook.get("type"),
                )
                chart_data["issues"].append("chart_workbook_relationship_unavailable")
            elif workbook.get("external"):
                chart_data["issues"].append("external_workbook_not_followed")
            elif workbook.get("target") in self.names:
                chart_data["issues"].append("embedded_workbook_not_read")
            else:
                chart_data["issues"].append("embedded_workbook_part_missing")

        self.chart_count += 1
        chart_record = self._record(
            "chart", part, node,
            chart_index=chart_index,
            chart_part=target,
            relationship_id=relationship_id,
            title=chart_data.get("title"),
            series_count=len(chart_data.get("series", [])),
            workbook_source=workbook,
            issues=list(dict.fromkeys(chart_data.get("issues", []))),
        )
        chart_record.update(context)
        for code in chart_record["issues"]:
            self._issue(code, target or part, _path(node), chart_index=chart_index)
        for series in chart_data.get("series", []):
            self._chart_series(chart_index, target or part, series)

    def _chart_series(self, chart_index, part, series):
        points = series.get("points", [])
        numeric_count = 0
        for point in points:
            value = point.get("value")
            if value is not None:
                numeric_count += 1
        self.chart_series_count += 1
        self.chart_numeric_count += numeric_count
        record = {
            "type": "chart_series",
            "part": part,
            "path": series.get("path", "/"),
            "chart_index": chart_index,
            "series_index": series.get("series_index"),
            "source_index": series.get("source_index"),
            "chart_type": series.get("chart_type"),
            "name": series.get("name"),
            "name_formula": series.get("name_formula"),
            "category_formula": series.get("category_formula"),
            "value_formula": series.get("value_formula"),
            "category_source": series.get("category_source"),
            "value_source": series.get("value_source"),
            "format_code": series.get("format_code"),
            "points": points,
            "numeric_value_count": numeric_count,
            "issues": series.get("issues", []),
        }
        self.records.append(record)
        self.counts["chart_series"] += 1
        for code in record["issues"]:
            self._issue(code, part, record["path"], chart_index=chart_index,
                        series_index=record["series_index"])

    def _find_wrapped(self, node, local, stop=None):
        stop = stop or set()
        found = []

        def visit(parent):
            for child in self._selected_children(parent, ""):
                if _is_word(child, local):
                    found.append(child)
                elif _name(child.tag)[1] not in stop and _name(child.tag)[1] in WRAPPERS:
                    visit(child)

        visit(node)
        return found

    def _cell_properties(self, cell):
        properties = next((item for item in _children(cell) if _is_word(item, "tcPr")), None)
        if properties is None:
            return None, 1
        span_node = next((item for item in _children(properties) if _is_word(item, "gridSpan")), None)
        try:
            colspan = 1 if span_node is None else int(span_node.get("{" + W_NS + "}val", "1"), 10)
        except ValueError:
            colspan = 1
        merge_node = next((item for item in _children(properties) if _is_word(item, "vMerge")), None)
        merge = None
        if merge_node is not None:
            merge = merge_node.get("{" + W_NS + "}val", "continue")
        return merge, max(1, colspan)

    def _table(self, table, part, context):
        rows = self._find_wrapped(table, "tr", stop={"tc", "tbl"})
        self.table_count += 1
        table_index = self.table_count
        record = self._record(
            "table", part, table,
            table_index=table_index,
            row_count=len(rows),
        )
        record.update(context)
        active = {}
        max_column = 0
        repeated_rows = []
        for row_index, row in enumerate(rows, 1):
            properties = next((item for item in _children(row) if _is_word(item, "trPr")), None)
            before_node = None if properties is None else next(
                (item for item in _children(properties) if _is_word(item, "gridBefore")), None
            )
            header = next((item for item in _children(properties) if _is_word(item, "tblHeader")), None) if properties is not None else None
            repeated = header is not None and header.get("{" + W_NS + "}val", "1") not in ("false", "0", "off")
            if repeated:
                repeated_rows.append(row_index)
            try:
                before = 0 if before_node is None else int(before_node.get("{" + W_NS + "}val", "0"), 10)
            except ValueError:
                before = 0
                self._issue("table_grid_before_invalid", part, _path(row), table_index=table_index)
            column = max(0, before) + 1
            cells = self._find_wrapped(row, "tc", stop={"tbl", "tr"})
            cell_records = []
            next_active = {}
            for cell in cells:
                merge, colspan = self._cell_properties(cell)
                origin = None
                key = (column, colspan)
                if merge == "restart":
                    origin = {"row": row_index, "column": column}
                    next_active[key] = origin
                    self.vmerge_origins += 1
                elif merge == "continue":
                    origin = active.get(key)
                    self.vmerge_continuations += 1
                    if origin is None:
                        self._issue(
                            "vertical_merge_origin_unavailable", part, _path(cell),
                            table_index=table_index, row=row_index, column=column,
                        )
                    else:
                        next_active[key] = origin
                item = {
                    "text": self._text(cell),
                    "column": column,
                    "colspan": colspan,
                    "vmerge": merge,
                }
                if origin is not None:
                    item["origin"] = dict(origin)
                deleted_text = self._text(cell, deleted_only=True)
                if deleted_text:
                    item["deleted_text"] = deleted_text
                cell_records.append(item)
                column += colspan
            max_column = max(max_column, column - 1)
            row_record = self._record(
                "table_row", part, row,
                table_index=table_index,
                row_index=row_index,
                repeat_header=repeated,
                cells=cell_records,
            )
            row_record.update(context)
            active = next_active
            for cell_index, cell in enumerate(cells, 1):
                cell_context = dict(context)
                cell_context.update({
                    "table_index": table_index,
                    "row_index": row_index,
                    "column": cell_records[cell_index - 1]["column"],
                    "cell_paragraph": 0,
                })
                paragraph_index = 0
                events = self._cell_blocks(cell)
                for event_type, event in events:
                    if event_type == "paragraph":
                        paragraph_index += 1
                        cell_context["cell_paragraph"] = paragraph_index
                        self._paragraph(event, part, cell_context)
                    else:
                        nested_context = dict(context)
                        nested_context.update({
                            "parent_table_index": table_index,
                            "parent_row": row_index,
                            "parent_column": cell_records[cell_index - 1]["column"],
                        })
                        self._table(event, part, nested_context)
        record["column_count"] = max_column
        record["repeat_header_rows"] = repeated_rows
        if rows:
            prefix = 0 < len(repeated_rows) < len(rows) and repeated_rows == list(range(1, len(repeated_rows) + 1))
            record["header_candidate"] = {"source": "repeat_header" if prefix else "first_row_candidate",
                                          "rows": repeated_rows if prefix else [1]}

    def _cell_blocks(self, cell):
        events = []

        def visit(node):
            for child in self._selected_children(node, self.current_part or ""):
                if _is_word(child, "p"):
                    events.append(("paragraph", child))
                elif _is_word(child, "tbl"):
                    events.append(("table", child))
                elif _name(child.tag)[1] in WRAPPERS:
                    visit(child)

        visit(cell)
        return events

    def _section(self, section, part):
        fields = {}
        for local in ("pgSz", "pgMar", "cols", "titlePg", "type"):
            child = next((item for item in _children(section) if _is_word(item, local)), None)
            if child is None:
                continue
            values = {
                _name(key)[1]: value
                for key, value in child.attrib.items()
            }
            if local == "type":
                fields["break_type"] = values.get("val")
            else:
                fields[local] = values
        self._record("section", part, section, **fields)

    def _process_blocks(self, container, part, context=None):
        context = context or {}
        for child in self._selected_children(container, part):
            if _is_word(child, "p"):
                self._paragraph(child, part, context)
            elif _is_word(child, "tbl"):
                self._table(child, part, context)
            elif _is_word(child, "sectPr"):
                self._section(child, part)
            elif _name(child.tag)[1] in WRAPPERS:
                self._process_blocks(child, part, context)
            elif _is_word(child, "altChunk"):
                self._issue("alternate_document_content_not_read", part, _path(child))
            elif _is_word(child, "footnote") or _is_word(child, "endnote") or _is_word(child, "comment"):
                identifier = child.get("{" + W_NS + "}id")
                note_type = child.get("{" + W_NS + "}type")
                if note_type not in ("separator", "continuationSeparator"):
                    nested = dict(context)
                    nested["story_id"] = identifier
                    nested["story_kind"] = _name(child.tag)[1]
                    self._process_blocks(child, part, nested)
            elif _name(child.tag)[0] == W_NS and _name(child.tag)[1] not in PROPERTY_TAGS:
                self._process_blocks(child, part, context)

    def _story(self, part, kind):
        self.current_part = part
        root = self.package.xml(part)
        revision_issues(self, root, part)
        self._record(
            "story", part, root,
            story_kind=kind,
            content_type=self.package.types.get(part),
        )
        if kind == "main":
            body = next((item for item in _children(root) if _is_word(item, "body")), None)
            if body is None:
                self._issue("document_body_missing", part, _path(root))
                return
            self._process_blocks(body, part, {"story_kind": "main"})
            return
        expected = STORY_ROOTS[kind]
        if not _is_word(root, expected):
            self._issue("story_root_unexpected", part, _path(root), expected=expected)
            return
        self._process_blocks(root, part, {"story_kind": kind})

    def _metadata(self):
        if not self.include_related:
            return
        part = "docProps/core.xml"
        if part not in self.names:
            return
        root = self.package.xml(part)
        values = {}
        for child in _children(root):
            _, local = _name(child.tag)
            if local in {
                "title", "subject", "creator", "keywords", "description", "lastModifiedBy",
                "revision", "created", "modified", "category", "identifier", "language",
                "contentStatus",
            }:
                values[local] = _local_text(child)
        self._record("metadata", part, root, metadata_kind="core_properties", values=values)

    def _story_parts(self):
        result = []
        for kind, pattern in STORY_PARTS.items():
            result.extend((part, kind) for part in self.names if pattern.fullmatch(part))
        return sorted(result)

    def read(self):
        """Read the main part and supported related story and metadata parts."""
        main = self.package.main
        if main not in self.names:
            self._issue("main_document_part_missing", main)
            return self.records, self.issues, self._coverage({}, main)

        self._load_styles()
        self._load_numbering()
        self._story(main, "main")
        parts = self._story_parts() if self.include_related else []
        for part, kind in parts:
            self._story(part, kind)
        self._metadata()
        availability = {"main": "available"}
        for kind in STORY_PARTS:
            count = sum(1 for _, part_kind in parts if part_kind == kind)
            availability[kind + "s"] = (
                "available" if count else "absent"
            ) if self.include_related else "excluded"
        availability["styles"] = "available" if "word/styles.xml" in self.names else "absent"
        availability["numbering"] = "available" if "word/numbering.xml" in self.names else "absent"
        availability["core_metadata"] = (
            "available" if "docProps/core.xml" in self.names else "absent"
        ) if self.include_related else "excluded"
        coverage = self._coverage(availability, main)
        if self.revisions is not None:
            coverage["revisions"] = self.revisions
        return self.records, self._unique_issues(), coverage

    def _unique_issues(self):
        result = []
        seen = set()
        for issue in self.issues:
            identity = tuple(sorted((key, repr(value)) for key, value in issue.items()))
            if identity not in seen:
                seen.add(identity)
                result.append(issue)
        return result

    def _coverage(self, availability, main):
        counts = dict(sorted(self.counts.items()))
        tables = {
            "count": counts.get("table", 0),
            "rows": counts.get("table_row", 0),
            "physical_cells": sum(
                len(record.get("cells", [])) for record in self.records
                if record.get("type") == "table_row"
            ),
            "vertical_merge_origins": self.vmerge_origins,
            "vertical_merge_continuations": self.vmerge_continuations,
        }
        charts = {
            "count": counts.get("chart", 0),
            "series": self.chart_series_count,
            "cached_numeric_values": self.chart_numeric_count,
        }
        images = {
            "references": self.image_references,
            "unique_internal_targets": len(self.image_targets),
        }
        return {
            "status": "partial" if self.issues else "ok",
            "main_part": main,
            "records_by_type": counts,
            "availability": availability,
            "tables": tables,
            "charts": charts,
            "images": images,
            "textboxes": self.textbox_count,
        }


def report(package, *, revisions=None, include_related=True):
    """Return flat records, location-keyed issues and explicit coverage counts."""
    if revisions not in (None, "accepted", "original", "all"):
        raise ValueError("revisions must be accepted, original, all or None")
    if not isinstance(include_related, bool):
        raise ValueError("include_related must be a boolean")
    reader = ReportReader(package, revisions=revisions, include_related=include_related)
    return reader.read()
