"""Read location-addressed text and object records from a DOCX package."""

from collections import Counter

from tl_resource_limits import check_count, validate_report
from tl_read_drawings import (
    A_NS,
    C_NS,
    PIC_NS,
    R_NS,
    V_NS,
    WPG_NS,
    WP_NS,
    WPS_NS,
    W_NS,
    _is_ns,
    _is_word,
    _name,
    chart,
    chart_series,
    drawing,
    drawing_properties,
    drawing_textbox,
    image,
    textbox,
)
from tl_read_numbering import NumberingReader
from tl_read_parts import PartCatalog, STORIES, section_references
from tl_read_styles import read_styles, heading_outline
from tl_read_revisions import revision_issues, revision_text, selected_children, text


MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
SUPPORTED_CHOICES = {A_NS, C_NS, PIC_NS, WP_NS, WPS_NS, WPG_NS}
WRAPPERS = {
    "sdt", "sdtContent", "customXml", "ins", "del", "moveFrom", "moveTo",
    "smartTag", "hyperlink", "bdo", "dir",
}
PROPERTY_TAGS = {
    "pPr", "rPr", "tcPr", "trPr", "tblPr", "tblGrid", "sdtPr", "sdtEndPr",
}
STORY_ROOTS = {
    "header": "hdr", "footer": "ftr", "footnote": "footnotes",
    "endnote": "endnotes", "comment": "comments",
}


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


def _path(node, sibling_indexes=None):
    """Return exact QName locations; optional indexes require an immutable tree."""
    parts = []
    current = node
    while current is not None:
        if not isinstance(current.tag, str):
            return "/"
        parent = current.getparent()
        if parent is None:
            index = 1
        elif sibling_indexes is None:
            index = 1 + sum(1 for _ in current.itersiblings(current.tag, preceding=True))
        else:
            if current not in sibling_indexes:
                counts = Counter()
                for child in parent:
                    if isinstance(child.tag, str):
                        counts[child.tag] += 1
                        sibling_indexes[child] = counts[child.tag]
            index = sibling_indexes[current]
        parts.append(_qname(current) + "[" + str(index) + "]")
        current = parent
    return "/" + "/".join(reversed(parts))


def _children(node):
    return [child for child in node if isinstance(child.tag, str)]


def _local_text(node):
    return "".join(node.itertext())


class ReportReader:
    """Build bounded, location-addressed records using the package interface."""

    def __init__(self, package, revisions=None, include_related=True):
        # Trees are immutable during a read. Strong element keys avoid id reuse;
        # scan original siblings, not revision/AlternateContent-selected nodes.
        self._path_indexes = {}
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
        self.numbering_reader = None
        self.numbering_counters = {}
        self.numbering_with_overrides = set()
        self.current_part = None
        self.catalog = PartCatalog(package, self._issue)

    def _issue(self, code, part, path=None, **identifiers):
        check_count("report_issues", len(self.issues) + 1)
        issue = {"code": code, "part": part}
        if path is not None:
            issue["path"] = path
        issue.update(identifiers)
        self.issues.append(issue)

    def _path(self, node):
        return _path(node, self._path_indexes)

    def _record(self, record_type, part, node, **fields):
        check_count("report_records", len(self.records) + 1)
        record = {
            "type": record_type,
            "part": part,
            "path": self._path(node) if not isinstance(node, str) else node,
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
        self._issue("alternate_content_branch_unavailable", part, self._path(node))
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
                        "path": self._path(child),
                    })
                    continue
                if _is_word(child, "fldChar"):
                    field_type = child.get("{" + W_NS + "}fldCharType")
                    if field_type == "begin":
                        stack.append({"instruction": [], "result": [], "phase": "instruction", "path": self._path(child)})
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
        self.styles = read_styles(self.package, self.catalog)

    def _load_numbering(self):
        self.numbering_reader = NumberingReader(self.package, self.catalog)
        parts = self.catalog.locate('numbering')
        for part in parts:
            root = self.catalog.xml(part, 'numbering')
            if root is not None:
                self._numbering_part(part, root)

    def _numbering_part(self, part, root):
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
        self.numbering.update({num_id: abstracts.get(abstract_id, []) for num_id, abstract_id in nums.items()})

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
            outline, issue = heading_outline(self.styles, style_id)
            if issue:
                self._issue(issue, self.current_part, self._path(paragraph))
        if outline is not None:
            info["outline_level"] = outline
            try:
                heading_level = int(outline) + 1
                if 1 <= heading_level <= 9:
                    info["heading_level"] = heading_level
            except ValueError:
                self._issue("paragraph_outline_level_invalid", self.current_part, self._path(paragraph))

        numbering, issues = self.numbering_reader.paragraph_info(paragraph, self.current_part)
        if numbering is not None:
            info['numbering'] = numbering
        for code in issues:
            self._issue(code, self.current_part, self._path(paragraph))
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
                elif _name(child.tag)[0] == W_NS and _name(child.tag)[1] in {
                    "footnoteReference", "endnoteReference", "commentReference",
                }:
                    self._record("reference", part, child, kind=_name(child.tag)[1],
                                 id=child.get("{" + W_NS + "}id"), **context)
                elif _is_word(child, "sectPr"):
                    self._section(child, part)
                elif _is_word(child, "altChunk"):
                    self._issue("alternate_document_content_not_read", part, self._path(child))
                elif _is_word(child, "object") or _is_word(child, "control"):
                    self._issue("embedded_object_not_decoded", part, self._path(child))
                elif _is_word(child, "sym"):
                    self._issue("symbol_glyph_mapping_not_resolved", part, self._path(child))
                else:
                    visit(child)

        visit(paragraph)

    def _math_text(self, node):
        from tl_read_math import math_expression  # pylint: disable=import-outside-toplevel
        result = math_expression(node)
        for issue in result['issues']:
            self._issue(issue['code'], self.current_part, self._path(node), element=issue.get('element'))
        return result['text']

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
            self._issue("hyperlink_relationship_unavailable", part, self._path(node), relationship_id=relation_id)

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
        return drawing_properties(node)

    def _drawing(self, node, part, context):
        drawing(self, node, part, context)

    def _textbox(self, node, part, context):
        textbox(self, node, part, context)

    def _drawing_textbox(self, node, part, context):
        drawing_textbox(self, node, part, context)

    def _image(self, node, part, context):
        image(self, node, part, context)

    def _chart(self, node, part, context):
        chart(self, node, part, context)

    def _chart_series(self, chart_index, part, series):
        chart_series(self, chart_index, part, series)

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
                self._issue("table_grid_before_invalid", part, self._path(row), table_index=table_index)
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
                            "vertical_merge_origin_unavailable", part, self._path(cell),
                            table_index=table_index, row=row_index, column=column,
                        )
                    else:
                        next_active[key] = origin
                item = {
                    "text": self._cell_text(cell),
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

    def _cell_text(self, cell):
        """Preserve paragraph boundaries, including empty and wrapped paragraphs."""
        paragraphs = []

        def visit(node):
            for child in self._selected_children(node, self.current_part or ''):
                if self.revisions is None and (_is_word(child, 'del') or _is_word(child, 'moveFrom')):
                    continue
                if _is_word(child, 'p'):
                    paragraphs.append(self._text(child))
                else:
                    visit(child)

        visit(cell)
        return '\n'.join(paragraphs)

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
        fields["story_references"] = section_references(self, section, part)
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
                self._issue("alternate_document_content_not_read", part, self._path(child))
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
        root = self.package.xml(part) if kind == 'main' else self.catalog.xml(part, kind)
        if root is None:
            return
        revision_issues(self, root, part)
        context = {'story_kind': kind}
        if part in self.catalog.orphans:
            context['orphan_part'] = True
            context['document_display_association'] = 'unconfirmed'
        self._record("story", part, root, content_type=self.package.types.get(part), **context)
        if kind == "main":
            body = next((item for item in _children(root) if _is_word(item, "body")), None)
            if body is None:
                self._issue("document_body_missing", part, self._path(root))
                return
            self._process_blocks(body, part, {"story_kind": "main"})
            return
        expected = STORY_ROOTS[kind]
        if not _is_word(root, expected):
            self._issue("story_root_unexpected", part, self._path(root), expected=expected)
            return
        self._process_blocks(root, part, context)

    def _metadata(self):
        if not self.include_related:
            return
        for part in self.catalog.locate('core'):
            root = self.catalog.xml(part, 'core')
            if root is not None:
                self._core_part(part, root)

    def _core_part(self, part, root):
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
        for kind in STORIES:
            result.extend((part, kind) for part in self.catalog.locate(kind))
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
            try:
                self._story(part, kind)
            except ValueError as exc:
                if str(exc) != 'Malformed XML part':
                    raise
                self.catalog.failed.add(part)
                self._issue('support_story_incomplete', part, kind=kind)
        self._metadata()
        availability = {"main": "available"}
        for kind in STORIES:
            availability[kind + "s"] = self.catalog.state(kind) if self.include_related else 'excluded'
        availability['styles'] = self.catalog.state('styles')
        availability['numbering'] = self.catalog.state('numbering')
        availability['core_metadata'] = self.catalog.state('core') if self.include_related else 'excluded'
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
            "drawing_content": {
                "unsupported_locations": len({(item['part'], item.get('path')) for item in self.issues
                                              if item['code'] in {'drawing_geometry_not_rendered', 'drawing_content_not_read',
                                                                  'smartart_content_not_decoded', 'drawing_graphic_data_unsupported'}}),
                "visual_rendering": "not_performed", "image_ocr": "not_performed",
            },
        }


def report(package, *, revisions=None, include_related=True, cache_root=None, cache_token=None):
    """Return flat records, location-keyed issues and explicit coverage counts."""
    if revisions not in (None, "accepted", "original", "all"):
        raise ValueError("revisions must be accepted, original, all or None")
    if not isinstance(include_related, bool):
        raise ValueError("include_related must be a boolean")
    if cache_token is not None:
        if cache_root is None:
            raise ValueError("A pinned task root is required for read cache reuse")
        from tl_read_cache import load  # pylint: disable=import-outside-toplevel
        cached = load(cache_root, cache_token, package.sha256,
                      {"revisions": revisions, "include_related": include_related})
        if cached is not None:
            return cached
    reader = ReportReader(package, revisions=revisions, include_related=include_related)
    result = reader.read()
    validate_report(result)
    return result
