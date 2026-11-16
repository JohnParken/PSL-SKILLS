"""Readable DOCX content projection and exact UTF-8 text pagination.

This is a presentation of the report reader's stored content, not a second XML
parser. Source/option binding and safe cache ownership stay in docx_read_pages.
"""

import json

MAX_CONTENT_BYTES = 32 * 1024 * 1024


def _location(anchor):
    if anchor.get("table_index"):
        return f"表{anchor['table_index']}第{anchor.get('row', '?')}行第{anchor.get('column', '?')}列"
    if anchor.get("textbox_index"):
        return f"文本框{anchor['textbox_index']}"
    if anchor.get("paragraph_index"):
        story = {"header": "页眉", "footer": "页脚", "footnote": "脚注", "endnote": "尾注", "comment": "批注"}.get(anchor.get("story"), "正文")
        return f"{story}第{anchor['paragraph_index']}段"
    return ""


def _cell_text(cell):
    text = cell["text"]
    labels = cell.get("paragraph_numbering", [])
    if labels:
        # A stored line break is not a paragraph separator. Keep the labels
        # separate rather than inserting them into guessed text offsets.
        text += "（段落编号：" + "；".join(f"第{x['paragraph_index']}段 {x['numbering_label']}" for x in labels) + "）"
    merge = cell.get("merge_anchor")
    if merge:
        label = merge.get("label")
        text += "（合并项：" + (label if label is not None else f"第{merge['row']}行第{merge['column']}列") + "）"
    if cell.get("column_span", 1) > 1:
        text += f"（跨{cell['column_span']}列）"
    if cell.get("unresolved_revisions"):
        text += "（单元格修订未解释）"
    return text or "（空）"


def _notice_text(data):
    issues = data["issues"]
    notices = []
    if any("header" in issue for issue in issues):
        notices.append("部分表格表头未确认；仍保留全部存储行。")
    if any("merge" in issue or "grid" in issue for issue in issues):
        notices.append("部分表格合并或网格关系未确定；不推断最终布局。")
    if any("table" in issue and "revision" in issue for issue in issues):
        notices.append("表格修订尚未准确解释；保留存储文字，不将其当作确定的修订后内容。")
    if "paragraph_numbering_unresolved" in issues:
        notices.append("部分自动编号未能解释。")
    if "equation_structure_not_interpreted" in issues:
        notices.append("公式仅提供有序文字片段，不等同于线性算式；数学结构尚未解释。")
    chart_tokens = ("chart", "cache", "category", "value", "point", "series", "plot", "title")
    if any(any(token in issue for token in chart_tokens) for issue in issues):
        notices.append("部分图表数据或格式未能读取；只显示已保存值，缺失值不补猜，也不更新外部工作簿。")
    if data.get("stats", {}).get("image"):
        notices.append("图片仅提供已有描述，未识别图片像素中的文字或数值。")
    if data.get("stats", {}).get("drawings_without_supported_content"):
        notices.append("有绘图未提取到受支持的内容。")
    known = ("table", "merge", "repeat_header", "paragraph_numbering", "equation", *chart_tokens, "image")
    if any(not any(token in issue for token in known) for issue in issues):
        notices.append("还有嵌入对象、兼容内容或其他未支持内容未读取；可用 --report 查看详细缺项。")
    if any("image" in issue for issue in issues):
        notices.append("部分图片资源不可用。")
    if "scope" in data:
        notices.append("仅包含所选章节；脚注、尾注和批注正文须用全文读取。")
    if data.get("stats", {}).get("chart"):
        notices.append("图表数值为文档保存值；数字格式另列，未重算或核对当前工作簿。")
    return notices


def project_content(data):
    """Retain readable values and necessary attribution, not OOXML diagnostics."""
    blocks = []
    total_bytes = 0
    story_key = None

    def add(text):
        nonlocal total_bytes
        if not text:
            return
        total_bytes += len(text.encode("utf-8")) + 1
        if total_bytes > MAX_CONTENT_BYTES:
            raise ValueError("content_total_output_limit_exceeded")
        blocks.append(text)

    for block in data["blocks"]:
        story = block.get("story")
        if story:
            current = (story, block.get("part"), block.get("story_id"))
            if current != story_key:
                names = {"header": "页眉", "footer": "页脚", "footnote": "脚注", "endnote": "尾注", "comment": "批注"}
                identifier = block.get("story_id")
                add("[" + names.get(story, "附加内容") + (f" {identifier}" if identifier is not None else "") + "]")
                story_key = current
        kind = block["type"]
        if kind in ("heading", "paragraph"):
            text = block["text"]
            if block.get("numbering_label"):
                text = block["numbering_label"] + " " + text
            add(("#" * min(block.get("level", 1), 6) + " " if kind == "heading" else "") + text)
        elif kind == "table":
            context = block.get("parent_cell")
            suffix = "（位于" + _location(context) + "）" if context else ""
            if block.get("textbox_index"):
                suffix += f"（文本框{block['textbox_index']}内）"
            add(f"[表{block['table_index']}{suffix}]")
        elif kind == "table_row":
            pieces = [_cell_text(cell) for cell in block["cells"]]
            if block["grid_before"]:
                pieces.insert(0, f"（前{block['grid_before']}列省略）")
            if block["grid_after"]:
                pieces.append(f"（后{block['grid_after']}列省略）")
            add(f"表{block['table_index']}第{block['row_index']}行：" + " ｜ ".join(pieces))
        elif kind == "textbox":
            location = _location(block.get("anchor", {}))
            add(f"[文本框{block['textbox_index']}" + ("，" + location if location else "") + "]")
            for paragraph in block["paragraphs"]:
                add(paragraph)
        elif kind == "equation":
            location = _location(block.get("anchor", {}))
            # JSON string quoting keeps distinct text runs visibly distinct.
            fragments = " | ".join(json.dumps(value, ensure_ascii=False) for value in block["text_runs"])
            add("公式文字片段" + ("（" + location + "）" if location else "") + "：" + (fragments or "（未提取到文字）"))
        elif kind == "chart":
            title = block.get("title")
            add(f"[图表{block['chart_index']}" + ("：" + title if title is not None else "（标题未提供）") + "]")
        elif kind == "chart_series":
            name = block.get("name")
            text = f"图表{block['chart_index']}，系列{block['series_index']}" + ("：" + name if name is not None else "")
            if block.get("format_code"):
                text += "；数字格式：" + block["format_code"]
            add(text)
        elif kind == "chart_point":
            category = block.get("category")
            value = block.get("value")
            label = category if category is not None else f"（第{block['index'] + 1}项，类别缺失）"
            add(f"图表{block['chart_index']}／系列{block['series_index']}：{label} = " + (value if value is not None else "（值缺失）"))
        elif kind == "image":
            anchor = block.get("anchor", {})
            descriptions = list(dict.fromkeys(anchor[key] for key in ("object_title", "description") if anchor.get(key)))
            add("[图片" + ("：" + "；".join(descriptions) if descriptions else "（无文字描述）") + "]")
        elif kind == "reference":
            names = {"footnoteReference": "脚注", "endnoteReference": "尾注", "commentReference": "批注"}
            add("[" + names.get(block["kind"], "附注") + " " + str(block.get("id", "")) + "]")
        else:
            raise ValueError("content projection encountered an unsupported report block")

    notices = _notice_text(data)
    return {"schema_version": 1, "format": "docx-content", "revisions": data["revisions"],
            "status": "partial" if data["issues"] or data.get("stats", {}).get("image") or data.get("stats", {}).get("drawings_without_supported_content") else "ok",
            "issues": data["issues"], "notices": notices, "blocks": blocks}


def serialize_content_page(page):
    """Render only content, a short reading caveat and an optional cursor footer."""
    revisions = {"accepted": "接受修订后的存储内容", "original": "原始修订视图", "all": "全部修订（新旧内容同时保留）"}
    result = "读取视图：" + revisions[page["revisions"]] + "；不改原件。\n"
    if page["status"] == "partial":
        result += "读取状态：部分内容或语义尚未完整解释。\n"
    if page["notices"]:
        result += "读取提示：" + " ".join(page["notices"]) + "\n"
    result += "\n"
    result += "".join(text + "\n" for text in page["blocks"])
    if not page["total_blocks"]:
        result += "未提取到可读文字。\n"
    if page["bounded"]:
        start = page["start_block"] + 1 if page["end_block"] > page["start_block"] else page["start_block"]
        state = "尚未读完" if page["has_more"] else "末页；须与前页合并" if page["start_block"] else "本次内容视图已读完"
        result += f"\n[内容块 {start}–{page['end_block']} / {page['total_blocks']}；{state}]\n"
        result += "next_cursor: " + (page["next_cursor"] or "none") + "\n"
    return result


def content_page(data, position, budget):
    """Paginate complete readable chunks, including caveats and cursor bytes."""
    blocks = data["blocks"]
    total, start = len(blocks), position.start
    if start and start >= total:
        raise ValueError("continuation cursor position is out of range")
    base = {key: value for key, value in data.items() if key != "blocks"}

    def page(stop, items):
        more = stop < total
        return dict(base, blocks=items, start_block=start, end_block=stop, total_blocks=total,
                    has_more=more, next_block=stop if more else None, next_cursor=position.cursor(stop) if more else None, bounded=budget is not None)

    if budget is None:
        return page(total, blocks[start:])
    sizes = [0]
    for block in blocks[start:]:
        sizes.append(sizes[-1] + len(block.encode("utf-8")) + 1)
        if sizes[-1] > budget:
            break

    def size(stop):
        return len(serialize_content_page(page(stop, [])).encode("utf-8")) + sizes[stop - start]

    if start + len(sizes) - 1 == total and size(total) <= budget:
        return page(total, blocks[start:])
    if len(serialize_content_page(page(start, [])).encode("utf-8")) > budget:
        raise ValueError("--max-output-bytes is too small for complete content caveats/cursor")
    accepted = start
    low, high = start + 1, min(total - 1, start + len(sizes) - 1)
    while low <= high:
        stop = (low + high) // 2
        if size(stop) <= budget:
            accepted, low = stop, stop + 1
        else:
            high = stop - 1
    if accepted == start and start < total:
        raise ValueError("single block exceeds --max-output-bytes; increase budget or narrow selection")
    return page(accepted, blocks[start:accepted])
