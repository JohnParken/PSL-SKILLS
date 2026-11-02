"""Read PDF bytes with PyMuPDF; leave paths, output ownership and CLI to the caller.

Coordinates are points in the unrotated, crop-relative PyMuPDF page space.
Resource images and displayed image occurrences deliberately remain separate:
matching them by dimensions would invent provenance, while hash matching can
decode large images before a caller's resource budget is checked.
"""

from __future__ import annotations

import contextlib
import hashlib
import heapq
import io
import math
import re
import threading
import unicodedata

try:
    import pymupdf
except ImportError:
    pymupdf = None


SCHEMA_VERSION = "1.0"
MAX_PAGE_SELECTION = 100000
ENGINE_LOCK = threading.RLock()
IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "jpx": "image/jp2",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
}


class PdfReadError(Exception):
    """A controlled, non-sensitive document or integration failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@contextlib.contextmanager
def _quiet_engine():
    """Keep library notices and raw parser errors out of machine-readable output.

PyMuPDF table detection and diagnostic switches use process-global state.
Calls through this backend are serialized; other PyMuPDF users must not run
concurrently in this process. The CLI uses one extraction per process.
"""
    with ENGINE_LOCK:
        show_errors = pymupdf.TOOLS.mupdf_display_errors()
        show_warnings = pymupdf.TOOLS.mupdf_display_warnings()
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                yield
        finally:
            pymupdf.TOOLS.mupdf_display_errors(show_errors)
            pymupdf.TOOLS.mupdf_display_warnings(show_warnings)


def _numbers(values):
    output = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite PDF geometry")
        output.append(round(number, 5))
    return output


def _diagnostic(page_result, code, message, *, incomplete=True, **details):
    item = {"code": code, "message": message}
    item.update(details)
    page_result["diagnostics"].append(item)
    if incomplete:
        page_result["status"] = "partial"


def _intersection_area(box, clip):
    rect = pymupdf.Rect(box) & clip
    return max(0.0, rect.width) * max(0.0, rect.height)


def _image_union_area(occurrences, clip):
    # A bounded union avoids counting overlaid/repeated images as scan coverage.
    # With more than 200 images this is a conservative lower bound.
    candidates = (pymupdf.Rect(item["bbox"]) & clip for item in occurrences)
    rectangles = heapq.nlargest(200, (rect for rect in candidates if not rect.is_empty), key=lambda rect: rect.width * rect.height)
    boundaries = sorted({coordinate for rect in rectangles for coordinate in (rect.x0, rect.x1)})
    area = 0.0
    for left, right in zip(boundaries, boundaries[1:]):
        intervals = sorted((rect.y0, rect.y1) for rect in rectangles if rect.x0 < right and rect.x1 > left)
        if not intervals:
            continue
        lower, upper = intervals[0]
        covered_height = 0.0
        for bottom, top in intervals[1:]:
            if bottom > upper:
                covered_height += upper - lower
                lower, upper = bottom, top
            else:
                upper = max(upper, top)
        covered_height += upper - lower
        area += (right - left) * covered_height
    return area


def _native_blocks(raw_blocks):
    blocks = []
    for block in raw_blocks:
        if block.get("type") != 0:
            continue
        lines = []
        for line in block.get("lines", []):
            spans = []
            for span in line.get("spans", []):
                item = {
                    "text": span.get("text", ""),
                    "bbox": _numbers(span["bbox"]),
                    "origin": _numbers(span["origin"]),
                    "font": span.get("font", ""),
                    "size": _numbers([span.get("size", 0.0)])[0],
                    "flags": int(span.get("flags", 0)),
                    "color": int(span.get("color", 0)),
                }
                for key in ("char_flags", "alpha"):
                    if key in span:
                        item[key] = int(span[key])
                spans.append(item)
            lines.append({
                "bbox": _numbers(line["bbox"]),
                "direction": _numbers(line.get("dir", (1.0, 0.0))),
                "writing_mode": int(line.get("wmode", 0)),
                "spans": spans,
            })
        blocks.append({
            "number": int(block.get("number", len(blocks))),
            "type": "text",
            "bbox": _numbers(block["bbox"]),
            "lines": lines,
        })
    return blocks


def _read_native_text(page, page_result, clip, state, requested):
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    try:
        text_page = page.get_textpage(clip=clip, flags=flags)
        native_text = text_page.extractText(sort=False)
        page_result["native_text_characters"] = len(native_text)
        if not requested:
            return native_text
        remaining = state["max_chars"] - state["characters_used"]
        if len(native_text) > remaining:
            page_result["text_status"] = "omitted_budget"
            _diagnostic(
                page_result, "text_budget_exceeded", "本页原生文字超出剩余字符预算，正文和文字块整项省略。",
                omitted_characters=len(native_text), remaining_characters=remaining,
            )
            return native_text
        raw_dict = text_page.extractDICT(sort=False)
        blocks = _native_blocks(raw_dict.get("blocks", []))
        page_result["text"] = native_text
        page_result["blocks"] = blocks
        page_result["text_status"] = "complete"
        page_result["source"] = "native"
        state["characters_used"] += len(native_text)
        return native_text
    except Exception:
        page_result["text_status"] = "failed" if requested else "not_requested"
        _diagnostic(page_result, "native_text_extraction_failed", "无法可靠读取本页原生文字；其它可读取部分仍保留。", incomplete=requested)
        return None


def _table_record(table, strategy):
    cells = table.extract()
    rows = table.rows
    return {
        "source": "native_geometry",
        "strategy": strategy,
        "bbox": _numbers(table.bbox),
        "row_count": int(table.row_count),
        "col_count": int(table.col_count),
        "cells": cells,
        "cell_bboxes": [[_numbers(cell) if cell is not None else None for cell in row.cells] for row in rows],
        "header": {
            "names": list(table.header.names),
            "bbox": _numbers(table.header.bbox),
            "external": bool(table.header.external),
            "inferred": True,
        },
        "structure_inferred": True,
        "merged_cells_inferred": False,
    }


def _read_tables(page, page_result, clip, state, strategy):
    old_rotation = page.rotation
    records = []
    try:
        # Only the private in-memory document is normalized. No PDF is saved.
        # Otherwise find_tables 1.26.6 changes the coordinate space on rotation.
        if old_rotation:
            page.set_rotation(0)
        finder = page.find_tables(clip=clip, strategy=strategy)
        if finder is None:
            raise ValueError("table detector failed")
        page_result["detected_table_count"] = len(finder.tables)
        omitted_count = 0
        omitted_characters = 0
        for index, table in enumerate(finder.tables, 1):
            try:
                record = _table_record(table, strategy)
            except Exception:
                omitted_count += 1
                _diagnostic(page_result, "table_extraction_failed", "一个表格无法可靠提取，已保留其它表格。", table=index)
                continue
            character_count = sum(len(cell) for row in record["cells"] for cell in row if isinstance(cell, str))
            character_count += sum(len(name) for name in record["header"]["names"] if isinstance(name, str))
            if character_count > state["max_chars"] - state["characters_used"]:
                omitted_count += 1
                omitted_characters += character_count
                continue
            record["number"] = index
            records.append(record)
            state["characters_used"] += character_count
        if omitted_characters:
            _diagnostic(
                page_result, "table_text_budget_exceeded", "部分表格文字超出剩余字符预算，相关表格整项省略。",
                omitted_characters=omitted_characters,
            )
        page_result["tables"] = records
        page_result["omitted_table_count"] = omitted_count
        page_result["table_status"] = "partial" if omitted_count else "complete"
    except Exception:
        page_result["tables"] = records
        page_result["table_status"] = "failed"
        _diagnostic(page_result, "table_detection_failed", "本页表格检测失败；已保留正文和其它可读取部分。")
    finally:
        if page.rotation != old_rotation:
            page.set_rotation(old_rotation)


def _read_image_metadata(page, page_result, clip, requested, state):
    try:
        all_occurrences = page.get_image_info(hashes=False, xrefs=False)
        occurrences = [item for item in all_occurrences if _intersection_area(item["bbox"], clip) > 0]
        resources = page.get_images(full=True)
    except Exception:
        page_result["image_metadata_status"] = "failed"
        _diagnostic(page_result, "image_metadata_failed", "本页图像元信息读取失败，无法确认图像覆盖范围。")
        return [], []
    page_result["image_metadata_status"] = "complete"
    page_result["displayed_image_count"] = len(occurrences)
    if not requested:
        return occurrences, resources
    remaining = max(0, state["max_images"] - state["image_occurrences_used"])
    retained = occurrences[:remaining]
    for item in retained:
        page_result["images"].append({
            "number": int(item.get("number", len(page_result["images"]))),
            "source": "displayed_image",
            "bbox": _numbers(item["bbox"]),
            "transform": _numbers(item["transform"]),
            "width": int(item["width"]),
            "height": int(item["height"]),
            "colorspace": item.get("cs-name", ""),
            "bits_per_component": int(item.get("bpc", 0)),
            "has_mask": bool(item.get("has-mask", False)),
            "xref": None,
            "asset": None,
            "resource_association": "unresolved_without_pixel_hashing",
        })
    state["image_occurrences_used"] += len(retained)
    if len(retained) < len(occurrences):
        _diagnostic(
            page_result, "image_count_budget_exceeded", "显示图像实例超出剩余数量预算，部分几何记录省略。",
            omitted_occurrences=len(occurrences) - len(retained),
        )
    return occurrences, resources


def _emit_asset(name, data, page_result, state, asset_sink):
    if len(data) > state["max_output_bytes"] - state["asset_bytes_written"]:
        _diagnostic(page_result, "output_budget_exceeded", "附件超出剩余输出字节预算，已省略该附件。", asset=name, omitted_bytes=len(data))
        return None, "omitted_budget"
    try:
        reference = asset_sink(name, data)
    except Exception as error:
        if getattr(error, "code", None) != "output_budget_exceeded":
            raise
        _diagnostic(page_result, "output_budget_exceeded", "附件写入边界报告输出字节预算不足，已省略该附件。", asset=name, omitted_bytes=len(data))
        return None, "omitted_budget"
    if not isinstance(reference, str) or not reference:
        raise PdfReadError("invalid_asset_sink", "附件写入回调未返回有效引用。")
    state["asset_bytes_written"] += len(data)
    return reference, "written"


def _image_payload(document, xref, width, height, page_result, state):
    if width <= 0 or height <= 0 or width * height > state["max_image_pixels"]:
        _diagnostic(
            page_result, "image_pixel_budget_exceeded", "图像尺寸无效或超过像素预算，未解码或导出图像。",
            xref=xref, width=width, height=height,
        )
        return None
    try:
        extracted = document.extract_image(xref)
        if not extracted or not isinstance(extracted.get("image"), bytes):
            raise ValueError("image extraction returned no bytes")
        actual_width = int(extracted.get("width", width))
        actual_height = int(extracted.get("height", height))
        if actual_width * actual_height > state["max_image_pixels"]:
            raise ValueError("image dimensions changed")
        extension = str(extracted.get("ext", "bin")).lower()
        if re.fullmatch(r"[a-z0-9]{1,8}", extension) is None:
            extension = "bin"
        return extracted["image"], extension
    except Exception:
        _diagnostic(page_result, "image_extraction_failed", "无法可靠提取一个原始图像资源，已保留元信息。", xref=xref)
        return None


def _export_image(document, resource, page_result, state, asset_sink):
    xref = resource["xref"]
    cached = state["image_cache"].get(xref)
    if cached is not None:
        resource.update(cached)
        if cached.get("asset_status") != "written" or (resource["mask_xref"] and cached.get("mask_status") != "written"):
            _diagnostic(page_result, "image_asset_unavailable", "此图像在前页已因预算或解析限制未能导出。", xref=xref)
        return
    payload = _image_payload(document, xref, resource["width"], resource["height"], page_result, state)
    if payload is None:
        resource["asset_status"] = "unavailable"
        state["image_cache"][xref] = {"asset": None, "asset_status": "unavailable"}
        return
    image_bytes, extension = payload
    reference, status = _emit_asset(f"images/image_{xref:06d}.{extension}", image_bytes, page_result, state, asset_sink)
    exported = {
        "asset": reference,
        "asset_status": status,
        "extension": extension,
        "mime_type": IMAGE_MIME_TYPES.get(extension, "application/octet-stream"),
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
        "encoded_bytes": len(image_bytes),
    }
    if resource["mask_xref"] and reference is not None:
        mask_xref = resource["mask_xref"]
        mask_result = state["mask_cache"].get(mask_xref)
        if mask_result is None:
            try:
                mask_width = int(document.xref_get_key(mask_xref, "Width")[1])
                mask_height = int(document.xref_get_key(mask_xref, "Height")[1])
            except Exception:
                mask_width = 0
                mask_height = 0
            mask_payload = _image_payload(document, mask_xref, mask_width, mask_height, page_result, state)
            mask_result = {"mask_asset": None, "mask_status": "unavailable"}
            if mask_payload is not None:
                mask_bytes, mask_extension = mask_payload
                mask_reference, mask_status = _emit_asset(f"images/mask_{mask_xref:06d}.{mask_extension}", mask_bytes, page_result, state, asset_sink)
                mask_result = {"mask_asset": mask_reference, "mask_status": mask_status}
            state["mask_cache"][mask_xref] = mask_result
        exported.update(mask_result)
        _diagnostic(
            page_result, "image_mask_separate", "图像透明蒙版单独导出；原图附件未合成为页面上的最终外观。",
            incomplete=exported["mask_asset"] is None, xref=xref, mask_xref=mask_xref,
        )
    state["image_cache"][xref] = exported
    resource.update(exported)


def _read_resources(document, page_result, raw_resources, state, asset_sink, clipped):
    if clipped:
        if raw_resources:
            _diagnostic(
                page_result, "clip_image_assets_unavailable", "裁剪模式仅返回区域内图像实例；无法可靠映射到资源，未导出可能包含区域外像素的整幅原图。",
                incomplete=asset_sink is not None,
            )
        return
    grouped = {}
    for item in raw_resources:
        xref = int(item[0])
        if xref not in grouped:
            grouped[xref] = {
                "xref": xref,
                "source": "page_resource_dictionary",
                "width": int(item[2]),
                "height": int(item[3]),
                "bits_per_component": int(item[4]),
                "colorspace": str(item[5]),
                "filter": str(item[8]),
                "mask_xref": int(item[1]),
                "resource_names": [],
                "referencer_xrefs": [],
                "bbox": None,
                "asset": None,
                "asset_status": "metadata_only",
                "display_association": "unresolved_may_include_unused_resources",
            }
        grouped[xref]["resource_names"].append(str(item[7]))
        grouped[xref]["referencer_xrefs"].append(int(item[9]))
    remaining = max(0, state["max_images"] - state["image_resources_used"])
    resources = list(grouped.values())
    retained = resources[:remaining]
    page_result["image_resource_count"] = len(resources)
    if len(retained) < len(resources):
        _diagnostic(
            page_result, "image_resource_budget_exceeded", "图像资源超出剩余数量预算，部分资源记录与附件省略。",
            omitted_resources=len(resources) - len(retained),
        )
    for resource in retained:
        if asset_sink is not None:
            _export_image(document, resource, page_result, state, asset_sink)
        page_result["image_resources"].append(resource)
    state["image_resources_used"] += len(retained)


def _read_preview(page, page_result, clip, occurrences, resources, state, asset_sink, dpi):
    if asset_sink is None:
        _diagnostic(page_result, "preview_sink_missing", "预览需要附件写入回调；未生成预览。")
        return
    if page_result.get("image_metadata_status") != "complete":
        _diagnostic(page_result, "preview_source_dimensions_unknown", "图像元信息不完整，无法先核对源图像素预算，未调用光栅渲染。")
        return
    for item in occurrences:
        if int(item["width"]) * int(item["height"]) > state["max_image_pixels"]:
            _diagnostic(page_result, "preview_source_image_budget_exceeded", "页面包含超过像素预算的图像，未调用光栅渲染。")
            return
    for item in resources:
        if int(item[2]) * int(item[3]) > state["max_image_pixels"]:
            _diagnostic(page_result, "preview_source_image_budget_exceeded", "页面图像资源超过像素预算，无法确认渲染内存上限，未生成预览。")
            return
    display_clip = clip * page.rotation_matrix
    scale = dpi / 72.0
    width = math.ceil(display_clip.x1 * scale) - math.floor(display_clip.x0 * scale)
    height = math.ceil(display_clip.y1 * scale) - math.floor(display_clip.y0 * scale)
    if width <= 0 or height <= 0 or width * height > state["max_image_pixels"]:
        _diagnostic(page_result, "preview_pixel_budget_exceeded", "请求预览尺寸超过像素预算，未调用光栅渲染。", width=width, height=height)
        return
    try:
        pixmap = page.get_pixmap(dpi=dpi, clip=display_clip, colorspace=pymupdf.csRGB, alpha=False, annots=True)
        png = pixmap.tobytes("png")
        preview = {
            "source": "rendered_page",
            "width": pixmap.width,
            "height": pixmap.height,
            "dpi": dpi,
            "unrotated_clip": _numbers(clip),
            "display_clip": _numbers(display_clip),
            "rotation": page.rotation,
            "unrotated_to_display_matrix": _numbers(page.rotation_matrix),
            "pixel_origin": [pixmap.x, pixmap.y],
            "annotations_rendered": True,
            "sha256": hashlib.sha256(png).hexdigest(),
            "encoded_bytes": len(png),
        }
    except Exception:
        _diagnostic(page_result, "preview_render_failed", "本页预览渲染失败，已保留其它可读取部分。")
        return
    reference, status = _emit_asset(f"previews/page_{page_result['number']:05d}.png", png, page_result, state, asset_sink)
    preview["asset"] = reference
    preview["asset_status"] = status
    page_result["preview"] = preview


def _assess_text(page, page_result, native_text, occurrences, clip, text_requested):
    if native_text is None:
        page_result["needs_visual_review"] = True
        return
    visible_text = "".join(char for char in native_text if not char.isspace())
    page_area = max(1.0, clip.width * clip.height)
    largest_image_fraction = max((_intersection_area(item["bbox"], clip) / page_area for item in occurrences), default=0.0)
    image_area_fraction = min(1.0, _image_union_area(occurrences, clip) / page_area)
    page_result["largest_image_area_fraction"] = round(min(1.0, largest_image_fraction), 5)
    page_result["image_area_fraction_lower_bound"] = round(image_area_fraction, 5)
    bad_characters = sum(char == "\ufffd" or (unicodedata.category(char) == "Cc" and char not in "\n\r\t") for char in native_text)
    if bad_characters >= 3 and bad_characters / max(1, len(visible_text)) >= 0.02:
        page_result["needs_ocr"] = True
        page_result["needs_visual_review"] = True
        _diagnostic(
            page_result, "unreliable_text_encoding", "原生文字包含较多替换字符或控制字符；保留原文，需视觉核对或后续OCR。",
            incomplete=text_requested, suspicious_characters=bad_characters, heuristic=True,
        )
    if image_area_fraction >= 0.65 and len(visible_text) < 80:
        page_result["needs_ocr"] = True
        page_result["needs_visual_review"] = True
        _diagnostic(
            page_result, "ocr_candidate", "页面以栅格图像为主且原生文字很少；可能是扫描页或插图，图片内文字未读取。请查看预览确认是否需要OCR。",
            incomplete=text_requested, heuristic=True, native_visible_characters=len(visible_text),
        )
    elif occurrences and not visible_text:
        page_result["needs_visual_review"] = True
        _diagnostic(
            page_result, "image_only_content", "本区域存在图像但没有原生文字；无法判断图像是否包含文字，需视觉核对。",
            incomplete=text_requested, heuristic=True,
        )
    elif image_area_fraction >= 0.5 and len(visible_text) < 200:
        page_result["needs_visual_review"] = True
        _diagnostic(
            page_result, "mixed_content_review", "大幅图像与原生文字并存；未判断图像内是否还有正文，请视觉核对读取覆盖范围。",
            incomplete=text_requested, heuristic=True,
        )
    elif not visible_text and text_requested:
        try:
            graphics_present = any(
                item[0] in ("fill-path", "stroke-path", "fill-shade") and _intersection_area(item[1], clip) > 0
                for item in page.get_bboxlog()
            )
        except Exception:
            graphics_present = True
        if graphics_present:
            page_result["needs_visual_review"] = True
            _diagnostic(page_result, "nontext_graphics_only", "未找到原生文字，但有矢量图形；可能包含轮廓化文字，需查看预览。", heuristic=True)


def _page_result(page, number, clip_option):
    bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
    clip = bounds if clip_option is None else bounds & pymupdf.Rect(clip_option)
    result = {
        "number": number,
        "width": round(bounds.width, 5),
        "height": round(bounds.height, 5),
        "rotation": page.rotation,
        "cropbox": _numbers(page.cropbox),
        "mediabox": _numbers(page.mediabox),
        "coordinate_system": {
            "unit": "pt",
            "origin": "top_left_of_unrotated_cropbox",
            "x_direction": "right",
            "y_direction": "down",
            "bbox_order": ["x0", "y0", "x1", "y1"],
            "unrotated_to_display_matrix": _numbers(page.rotation_matrix),
        },
        "effective_clip": _numbers(clip),
        "status": "ok",
        "source": "unavailable",
        "text": None,
        "text_status": "not_requested",
        "blocks": [],
        "tables": [],
        "table_status": "not_requested",
        "images": [],
        "image_resources": [],
        "preview": None,
        "needs_ocr": False,
        "needs_visual_review": False,
        "diagnostics": [],
    }
    if clip_option is not None:
        _diagnostic(result, "clipped_selection", "只读取有效裁剪区域；文字按引擎字符框包含规则筛选，整页其它内容不属于本次覆盖范围。", incomplete=False)
    return result, clip


def _read_document(data, options, asset_sink):
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise PdfReadError("invalid_pdf", "无法打开输入PDF；文件可能损坏、为空或不是PDF。") from None
    with document:
        if not document.is_pdf:
            raise PdfReadError("invalid_pdf", "输入不是PDF文档。")
        encrypted = bool(document.needs_pass)
        if encrypted:
            password = options.get("password")
            if password is None:
                raise PdfReadError("password_required", "PDF需要密码；请通过受控密码输入提供。")
            try:
                authenticated = bool(document.authenticate(password))
            except Exception:
                authenticated = False
            if not authenticated:
                raise PdfReadError("invalid_password", "PDF认证失败；未读取加密内容。")
        page_count = document.page_count
        if page_count <= 0:
            raise PdfReadError("empty_pdf", "PDF没有可读取页面。")
        if options.get("pages") is None:
            if page_count > MAX_PAGE_SELECTION:
                raise PdfReadError("page_selection_too_large", "文档页数超过页索引预算；请显式选择较小页码范围。")
            requested_pages = list(range(1, page_count + 1))
        else:
            requested_pages = list(dict.fromkeys(options["pages"]))
        if not requested_pages or len(requested_pages) > MAX_PAGE_SELECTION or any(number < 1 or number > page_count for number in requested_pages):
            raise PdfReadError("invalid_pages", "所选页码为空、超出文档范围或超过页索引预算。")
        state = {
            "max_chars": options.get("max_chars", 500000),
            "max_images": options.get("max_images", 200),
            "max_image_pixels": options.get("max_image_pixels", 20000000),
            "max_output_bytes": options.get("max_output_bytes", 67108864),
            "characters_used": 0,
            "image_occurrences_used": 0,
            "image_resources_used": 0,
            "asset_bytes_written": 0,
            "image_cache": {},
            "mask_cache": {},
        }
        extraction = set(options.get("extract", ["text"]))
        selected_pages = requested_pages[:options.get("max_pages", 200)]
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "document": {
                "page_count": page_count,
                "sha256": hashlib.sha256(data).hexdigest(),
                "input_bytes": len(data),
                "encrypted": encrypted,
                "permissions": int(document.permissions),
                "repaired": bool(document.is_repaired),
                "metadata": document.metadata,
                "toc": [],
                "engine": {"name": "PyMuPDF", "version": pymupdf.VersionBind},
            },
            "selection": {
                "extract": sorted(extraction),
                "clip": options.get("clip"),
                "reading_order": "native_content_order_not_semantic_reconstruction",
                "image_asset_scope": "original_page_resources_not_cropped_appearance",
            },
            "coverage": {"requested_pages": requested_pages, "processed_pages": [], "omitted_pages": [], "complete": True},
            "pages": [],
            "warnings": [],
        }
        try:
            result["document"]["toc"] = [{"level": item[0], "title": item[1], "page": item[2]} for item in document.get_toc(simple=True)]
        except Exception:
            result["warnings"].append({"code": "toc_unavailable", "message": "目录读取失败；页面读取继续。"})
        if document.is_repaired:
            result["warnings"].append({"code": "pdf_repaired_in_memory", "message": "引擎在内存中修复了PDF结构；完整性未得到保证，源文件未修改。"})
        if len(selected_pages) < len(requested_pages):
            result["warnings"].append({
                "code": "page_budget_exceeded", "message": "所选页数超过本次处理预算，部分页面未读取。",
                "omitted_count": len(requested_pages) - len(selected_pages),
            })
        for number in selected_pages:
            try:
                page = document.load_page(number - 1)
                page_result, clip = _page_result(page, number, options.get("clip"))
            except Exception:
                result["warnings"].append({"code": "page_load_failed", "message": "一个所选页面无法加载，已跳过。", "page": number})
                continue
            if clip.is_empty:
                page_result["text"] = "" if "text" in extraction else None
                page_result["text_status"] = "outside_clip" if "text" in extraction else "not_requested"
                _diagnostic(page_result, "clip_outside_page", "裁剪区域与本页没有交集，未读取页面内容。", incomplete=False)
            else:
                native_text = _read_native_text(page, page_result, clip, state, "text" in extraction)
                occurrences, resources = _read_image_metadata(page, page_result, clip, "images" in extraction, state)
                _assess_text(page, page_result, native_text, occurrences, clip, bool(extraction & {"text", "tables"}))
                if "tables" in extraction:
                    _read_tables(page, page_result, clip, state, options.get("table_strategy", "lines"))
                if "images" in extraction:
                    _read_resources(document, page_result, resources, state, asset_sink, options.get("clip") is not None)
                    if occurrences and not resources and asset_sink is not None:
                        _diagnostic(page_result, "inline_image_assets_unavailable", "显示图像没有可导出的xref资源，可能是内联图像；仅保留实例几何，可通过页面预览查看。")
                if "preview" in extraction:
                    _read_preview(page, page_result, clip, occurrences, resources, state, asset_sink, options.get("dpi", 120))
            result["pages"].append(page_result)
            result["coverage"]["processed_pages"].append(number)
        processed = set(result["coverage"]["processed_pages"])
        result["coverage"]["omitted_pages"] = [number for number in requested_pages if number not in processed]
        result["coverage"]["complete"] = not result["coverage"]["omitted_pages"] and not result["warnings"] and all(page["status"] == "ok" for page in result["pages"])
        result["status"] = "ok" if result["coverage"]["complete"] else "partial"
        result["needs_ocr_pages"] = [page["number"] for page in result["pages"] if page["needs_ocr"]]
        result["resource_usage"] = {
            "text_characters": state["characters_used"],
            "character_budget_scope": "native_text_once_plus_table_cells_and_header_names",
            "image_occurrences": state["image_occurrences_used"],
            "image_resource_records": state["image_resources_used"],
            "asset_bytes_written": state["asset_bytes_written"],
            "output_byte_budget_scope": "backend_assets_only_caller_must_bound_serialized_result_and_total_export",
        }
        return result


def extract_document(data: bytes, options: dict, *, asset_sink=None) -> dict:
    """Return provenance-bearing native PDF content, without filesystem writes.

The caller validates options, limits input bytes, and bounds final JSON/Markdown
plus all assets. ``max_images`` separately bounds retained image occurrence and
resource records across the document. ``max_chars`` counts native text once and
table cell/header text. Budget omissions return partial data, never truncated
text disguised as a complete extraction. Unexpected sink errors propagate;
only errors carrying code ``output_budget_exceeded`` are recoverable.
"""
    if pymupdf is None:
        raise PdfReadError("dependency_missing", "缺少已声明的PyMuPDF运行依赖；未尝试安装。")
    if not isinstance(data, bytes) or not data:
        raise PdfReadError("invalid_pdf", "输入必须是非空PDF字节。")
    with _quiet_engine():
        return _read_document(data, options, asset_sink)
