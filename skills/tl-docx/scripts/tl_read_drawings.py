"""Drawing, textbox, image, and chart record extraction for tl-docx reading."""

from hashlib import sha256

from tl_read_charts import read_chart


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
V_NS = "urn:schemas-microsoft-com:vml"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
WPG_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"


def _name(tag):
    if not isinstance(tag, str):
        return "", ""
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _is_word(node, local):
    return node.tag == "{" + W_NS + "}" + local


def _is_ns(node, namespace, local):
    return node.tag == "{" + namespace + "}" + local


def drawing_properties(node):
    """Extract alt-text description, title, and name from the nearest drawing metadata node."""
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


def drawing(reader, node, part, context):
    """Walk a WordprocessingML drawing or VML container and record supported objects."""
    before_records, before_issues = len(reader.records), len(reader.issues)

    def visit(current):
        for child in reader._selected_children(current, part):
            namespace, local = _name(child.tag)
            if (namespace == WPS_NS and local == "wsp") or (
                namespace == V_NS
                and local in {"shape", "rect", "oval", "line", "polyline", "roundrect", "arc", "curve"}
            ):
                reader._issue(
                    "drawing_geometry_not_rendered",
                    part,
                    reader._path(child),
                    available_content="supported_text_and_image_references_only",
                )
            if local == "relIds" and namespace == "http://schemas.openxmlformats.org/drawingml/2006/diagram":
                reader._issue("smartart_content_not_decoded", part, reader._path(child))
            if _is_ns(child, A_NS, "graphicData"):
                uri = child.get("uri")
                if uri and uri not in (C_NS, PIC_NS, WPS_NS, WPG_NS):
                    reader._issue("drawing_graphic_data_unsupported", part, reader._path(child), namespace=uri)
            if _is_word(child, "txbxContent"):
                reader._textbox(child, part, context)
                continue
            if _is_ns(child, A_NS, "txBody"):
                reader._drawing_textbox(child, part, context)
                continue
            if _is_ns(child, A_NS, "blip") or _is_ns(child, V_NS, "imagedata"):
                reader._image(child, part, context)
                continue
            if _is_ns(child, C_NS, "chart") or child.tag.endswith("}chart"):
                reader._chart(child, part, context)
                continue
            visit(child)

    visit(node)
    if len(reader.records) == before_records and len(reader.issues) == before_issues:
        reader._issue("drawing_content_not_read", part, reader._path(node))


def textbox(reader, node, part, context):
    """Record a WordprocessingML textbox and inspect its embedded paragraphs and tables."""
    paragraphs = reader._find_wrapped(node, "p", stop={"tbl", "txbxContent"})
    texts = [reader._text(paragraph) for paragraph in paragraphs]
    reader.textbox_count += 1
    record = reader._record(
        "textbox",
        part,
        node,
        text="\n".join(texts),
        paragraph_count=len(texts),
        text_format="wordprocessingml",
    )
    record.update(context)
    for paragraph in paragraphs:
        reader._paragraph_objects(paragraph, part, context)
    for table in reader._find_wrapped(node, "tbl", stop={"tbl"}):
        reader._table(table, part, context)


def drawing_textbox(reader, node, part, context):
    """Record a DrawingML txBody textbox."""
    paragraphs = []
    for paragraph in node.iter():
        if _is_ns(paragraph, A_NS, "p"):
            pieces = []
            for child in paragraph:
                if _is_ns(child, A_NS, "br"):
                    pieces.append("\n")
                elif _is_ns(child, A_NS, "r") or _is_ns(child, A_NS, "fld"):
                    pieces.extend(
                        item.text or ""
                        for item in child.iter()
                        if _is_ns(item, A_NS, "t")
                    )
            paragraphs.append("".join(pieces))
    reader.textbox_count += 1
    record = reader._record(
        "textbox",
        part,
        node,
        text="\n".join(paragraphs),
        paragraph_count=len(paragraphs),
        text_format="drawingml",
    )
    record.update(context)


def image(reader, node, part, context):
    """Record an image blip or VML imagedata reference and hash its embedded part."""
    relationship_id = node.get("{" + R_NS + "}embed")
    if relationship_id is None:
        relationship_id = node.get("{" + R_NS + "}link")
    if relationship_id is None:
        relationship_id = node.get("{" + R_NS + "}id")
    relation = reader._relation(part, relationship_id, "image") if relationship_id else None
    properties = reader._drawing_properties(node)
    reader.image_references += 1
    target = None if relation is None else relation.get("target")
    external = None if relation is None else relation.get("external")
    resource_part = target if not external and target in reader.names else None
    if resource_part is not None:
        reader.image_targets.add(resource_part)
    elif relation is not None and not external:
        reader._issue(
            "image_part_missing",
            part,
            reader._path(node),
            relationship_id=relationship_id,
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
    if resource_part in reader.names:
        if resource_part not in reader.image_hashes:
            reader.image_hashes[resource_part] = sha256(reader.package.read(resource_part)).hexdigest()
        fields["resource_sha256"] = reader.image_hashes[resource_part]
    reader._record("image", part, node, **fields)
    if relationship_id is None or relationship_id == "" or relation is None:
        reader._issue("image_relationship_unavailable", part, reader._path(node), relationship_id=relationship_id)


def chart(reader, node, part, context):
    """Record a DrawingML chart part, its external workbook link state, and its series."""
    relationship_id = node.get("{" + R_NS + "}id")
    relation = reader._relation(part, relationship_id, "chart") if relationship_id else None
    chart_index = reader.chart_count + 1
    target = None if relation is None or relation.get("external") else relation.get("target")
    chart_data = {"title": None, "series": [], "external_data_id": None, "issues": []}
    if target is not None and target in reader.names:
        try:
            chart_root = reader.package.xml(target)
        except ValueError as exc:
            if str(exc) != "Malformed XML part":
                raise
            chart_data["issues"].append("chart_part_unreadable")
        else:
            try:
                chart_data = read_chart(chart_root)
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
        workbook = reader._relation(target, external_id, None)
        if workbook is None:
            chart_data["issues"].append("chart_workbook_relationship_unavailable")
        elif not workbook.get("type", "").endswith(("/package", "/oleObject")):
            reader._issue(
                "relationship_type_unexpected",
                target,
                reader._path(node),
                relationship_id=external_id,
                expected=["package", "oleObject"],
                actual=workbook.get("type"),
            )
            chart_data["issues"].append("chart_workbook_relationship_unavailable")
        elif workbook.get("external"):
            chart_data["issues"].append("external_workbook_not_followed")
        elif workbook.get("target") in reader.names:
            chart_data["issues"].append("embedded_workbook_not_read")
        else:
            chart_data["issues"].append("embedded_workbook_part_missing")

    reader.chart_count += 1
    chart_record = reader._record(
        "chart",
        part,
        node,
        chart_index=chart_index,
        chart_part=target,
        relationship_id=relationship_id,
        title=chart_data.get("title"),
        annotations=chart_data.get("annotations", []),
        series_count=len(chart_data.get("series", [])),
        workbook_source=workbook,
        issues=list(dict.fromkeys(chart_data.get("issues", []))),
    )
    chart_record.update(context)
    for code in chart_record["issues"]:
        reader._issue(code, target or part, reader._path(node), chart_index=chart_index)
    for series in chart_data.get("series", []):
        reader._chart_series(chart_index, target or part, series)


def chart_series(reader, chart_index, part, series):
    """Record one parsed chart series and update numeric point counters."""
    points = series.get("points", [])
    numeric_count = 0
    for point in points:
        value = point.get("value")
        if value is not None:
            numeric_count += 1
    reader.chart_series_count += 1
    reader.chart_numeric_count += numeric_count
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
    for key in (
        "category_levels",
        "x_formula",
        "x_source",
        "x_format_code",
        "y_formula",
        "y_source",
        "y_format_code",
        "bubble_size_formula",
        "bubble_size_source",
        "bubble_size_format_code",
    ):
        if key in series:
            record[key] = series[key]
    reader.records.append(record)
    reader.counts["chart_series"] += 1
    for code in record["issues"]:
        reader._issue(
            code,
            part,
            record["path"],
            chart_index=chart_index,
            series_index=record["series_index"],
        )
