"""Validate document specifications and load bounded image resources."""
from __future__ import annotations

from docx_create_common import (
    hashlib, os, re, stat,
    struct, zlib, Path, Any,
    apply_profile, parse_section, SECTION_KEYS, BREAK_TYPES,
    normalize_merged_grid, _MAX_PAGE_MM, _MAX_IMAGE_BYTES, _MAX_PACKAGE_BYTES,
    _COLOR_RE, _ALIGNMENTS, _TABLE_LAYOUT_DEFAULTS, _object,
    _number, _single_line_text, _validate_style_fields, _validate_table_text_fields,
    _validate_table_layout, _validate_borders, _merge_table_layout, _cell_borders,
    _theme_base_format, _validated_styles, _text, _content_widths,
    _twips, _emu,
)

def _image_size(data: bytes, label: str) -> tuple[int, int]:
    """Read PNG IHDR or JPEG SOF dimensions without decoding image pixels."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) >= 33 and data[8:16] == b"\x00\x00\x00\rIHDR":
            if zlib.crc32(data[12:29]) & 0xFFFFFFFF == int.from_bytes(data[29:33], "big"):
                width, height = struct.unpack(">II", data[16:24])
                if width and height:
                    return width, height
    elif data.startswith(b"\xff\xd8"):
        cursor = 2
        while cursor + 4 <= len(data):
            if data[cursor] != 0xFF:
                break
            while cursor < len(data) and data[cursor] == 0xFF:
                cursor += 1
            if cursor >= len(data):
                break
            marker = data[cursor]
            cursor += 1
            if marker in {0xD9, 0xDA, 0x00}:
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(data[cursor:cursor + 2], "big")
            if length < 2 or cursor + length > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 8:
                height, width = struct.unpack(">HH", data[cursor + 3:cursor + 7])
                if width and height:
                    return width, height
            cursor += length
    raise ValueError(f"{label}: cannot infer image ratio from a valid PNG/JPEG dimension header; provide both dimensions")


class ImageResources:
    """One operation's immutable image bytes; never cache across documents."""

    def __init__(self, deduplicate: bool):
        self.paths = {}
        self.payloads = {}
        self.loaded_bytes = 0
        self.packaged_bytes = 0
        self.packaged_digests = set()
        self.deduplicate = deduplicate

    def load(self, path: Path, label: str) -> dict:
        if path not in self.paths:
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError(f"{label}.path must reference a regular image file")
            with path.open("rb") as image_file:
                info = os.fstat(image_file.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"{label}.path must reference a regular image file")
                if info.st_size > _MAX_IMAGE_BYTES:
                    raise ValueError(f"{label}.path exceeds the 32 MiB image limit")
                if self.loaded_bytes + info.st_size > _MAX_PACKAGE_BYTES:
                    raise ValueError(f"{label}.path exceeds the 128 MiB image resource budget")
                # Bound by the observed file size, not a 32 MiB allocation for
                # every tiny image. Reject growth/truncation during this read.
                data = image_file.read(info.st_size + 1)
                if len(data) != info.st_size:
                    raise ValueError(f"{label}.path changed size while reading")
            if data.startswith(b"\x89PNG\r\n\x1a\n") and data.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82"):
                extension = "png"
            elif data.startswith(b"\xff\xd8") and b"\xff\xd9" in data[2:]:
                extension = "jpg"
            else:
                raise ValueError(f"{label}.path must reference an existing PNG or JPEG image")
            digest = hashlib.sha256(data).digest()
            resource = self.payloads.get(digest)
            if resource is None:
                resource = {"data": data, "extension": extension, "digest": digest}
                self.payloads[digest] = resource
                self.loaded_bytes += len(data)
            self.paths[path] = resource
        resource = self.paths[path]
        if not self.deduplicate or resource["digest"] not in self.packaged_digests:
            self.packaged_bytes += len(resource["data"])
            if self.packaged_bytes > _MAX_PACKAGE_BYTES:
                raise ValueError(f"{label}.path exceeds the 128 MiB package image budget")
            self.packaged_digests.add(resource["digest"])
        return resource


def _image(path_value: Any, width: float | None, height: float | None, base_dir: Path,
           alt: Any, label: str, resources: ImageResources) -> dict:
    alt_text = _text(alt, f"{label}.alt")
    rel_path = _text(path_value, f"{label}.path", allow_empty=False)
    path = Path(rel_path)
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    resource = resources.load(path, label)
    if width is None or height is None:
        if "dimensions" not in resource:
            resource["dimensions"] = _image_size(resource["data"], label)
        pixels_wide, pixels_high = resource["dimensions"]
        if width is None:
            width = height * pixels_wide / pixels_high
        else:
            height = width * pixels_high / pixels_wide
    return {
        "type": "image", "image_path": path, "image_data": resource["data"],
        "image_digest": resource["digest"],
        "extension": resource["extension"], "cx": _emu(width), "cy": _emu(height),
        "alt": alt_text,
    }


def _prepare_image(fields: dict, base_dir: Path, label: str, max_width: int, max_height: int,
                   resources: ImageResources) -> dict:
    if "width_mm" not in fields and "height_mm" not in fields:
        raise ValueError(f"{label} requires width_mm and/or height_mm")
    width = _number(fields["width_mm"], f"{label}.width_mm", 0.1, _MAX_PAGE_MM) if "width_mm" in fields else None
    height = _number(fields["height_mm"], f"{label}.height_mm", 0.1, _MAX_PAGE_MM) if "height_mm" in fields else None
    if width is not None and _emu(width) > max_width or height is not None and _emu(height) > max_height:
        raise ValueError(f"{label} dimensions exceed the cell width/page content area")
    image = _image(fields["path"], width, height, base_dir, fields["alt"], label, resources)
    if image["cx"] > max_width or image["cy"] > max_height or min(image["cx"], image["cy"]) < _emu(0.1):
        raise ValueError(f"{label} dimensions exceed the cell width/page content area or are too small")
    return image


def _validate_run(value: Any, label: str) -> dict:
    run = _object(value, label,
                  {"text", "tab", "bold", "italic", "underline", "font", "font_east_asia",
                   "font_latin", "size_pt", "color"})
    if ("text" in run) == ("tab" in run):
        raise ValueError(f"{label} must contain exactly one of text or tab")
    if "tab" in run and run["tab"] is not True:
        raise ValueError(f"{label}.tab must be true")
    result = {"tab": True} if "tab" in run else {"text": _text(run["text"], f"{label}.text")}
    for key in ("bold", "italic", "underline"):
        if key in run:
            if not isinstance(run[key], bool):
                raise ValueError(f"{label}.{key} must be a boolean")
            result[key] = run[key]
    for key in ("font", "font_east_asia", "font_latin"):
        if key in run:
            result[key] = _single_line_text(run[key], f"{label}.{key}")
    if "size_pt" in run:
        result["size_pt"] = _number(run["size_pt"], f"{label}.size_pt", 1, 200)
    if "color" in run:
        color = _text(run["color"], f"{label}.color", allow_empty=False)
        if not _COLOR_RE.fullmatch(color):
            raise ValueError(f"{label}.color must be six hexadecimal RGB characters without #")
        result["color"] = color.upper()
    return result


def _validate_spec(spec: Any, base_dir: Path) -> dict:
    source = _object(apply_profile(spec), "spec", {
        "page", "styles", "table_format", "header", "footer", "headers", "footers",
        "page_numbers", "different_first_page", "first_page_numbers",
        "odd_even_headers", "page_number_start", "page_number_format",
        "blocks", "theme", "profile", "deduplicate_images", "heading_numbering",
    }, {"blocks"})
    odd_even_headers = source.get("odd_even_headers", False)
    if type(odd_even_headers) is not bool:
        raise ValueError("odd_even_headers must be a boolean")
    initial_options = {key: source[key] for key in SECTION_KEYS if key in source}
    section = parse_section(initial_options, None, "spec", twips=_twips,
                            text_validator=_text, odd_even_headers=odd_even_headers)
    sections = [section]
    page_twips = section["page_twips"]
    available_width = section["available_width_twips"]
    available_height = section["available_height_twips"]
    theme = source.get("theme", "official")
    if not isinstance(theme, str) or theme not in {"official", "business", "academic"}:
        raise ValueError("theme must be official, business, or academic")
    heading_numbering = source.get("heading_numbering", "none")
    if heading_numbering not in {"none", "decimal", "chinese"} or not isinstance(heading_numbering, str):
        raise ValueError("heading_numbering must be none, decimal, or chinese")
    styles = _validated_styles(source.get("styles", {}), theme)
    table_layout = _merge_table_layout(
        _TABLE_LAYOUT_DEFAULTS,
        _validate_table_layout(source["table_format"], "table_format") if "table_format" in source else {},
    )

    header = section["headers"]["default"]
    footer = section["footers"]["default"]
    page_numbers = section["page_numbers"]
    deduplicate_images = source.get("deduplicate_images", False)
    if not isinstance(deduplicate_images, bool):
        raise ValueError("deduplicate_images must be a boolean")
    resources = ImageResources(deduplicate_images)

    blocks_value = source["blocks"]
    if not isinstance(blocks_value, list):
        raise ValueError("blocks must be an array")
    blocks: list[dict] = []
    image_paths: list[Path] = []
    shared_lists = {}
    bookmarks = set()
    references = []
    for index, value in enumerate(blocks_value):
        label = f"blocks[{index}]"
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        kind = value.get("type")
        if not isinstance(kind, str):
            raise ValueError(f"{label}.type must be a string")
        if kind in {"title", "subtitle"}:
            block = _object(value, label, {"type", "text", "format", "align"}, {"type", "text"})
            formatting = _validate_style_fields(block.get("format", {}), f"{label}.format")
            if "align" in block:
                formatting.update(_validate_style_fields({"align": block["align"]}, f"{label}.format"))
            blocks.append({"type": kind, "text": _text(block["text"], f"{label}.text"), "format": formatting})
        elif kind == "heading":
            block = _object(value, label, {"type", "text", "level", "format", "align", "bookmark"},
                            {"type", "text", "level"})
            level = block["level"]
            if isinstance(level, bool) or not isinstance(level, int) or level not in (1, 2, 3, 4):
                raise ValueError(f"{label}.level must be 1, 2, 3, or 4")
            block_format = _validate_style_fields(block["format"], f"{label}.format") \
                if "format" in block else {}
            align = block.get("align")
            if "align" in block and (not isinstance(align, str) or align not in _ALIGNMENTS):
                raise ValueError(f"{label}.align must be left, center, right, or justify")
            bookmark = None
            if "bookmark" in block:
                bookmark = _text(block["bookmark"], f"{label}.bookmark", allow_empty=False)
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", bookmark):
                    raise ValueError(f"{label}.bookmark must be 1-40 ASCII letters, digits or underscores, starting with a letter")
                if bookmark.lower() in bookmarks:
                    raise ValueError(f"{label}.bookmark must be unique (case-insensitive)")
                bookmarks.add(bookmark.lower())
            blocks.append({"type": kind, "text": _text(block["text"], f"{label}.text"),
                           "level": level, "format": block_format, "align": align,
                           "bookmark": bookmark, "bookmark_id": len(bookmarks) if bookmark else None})
        elif kind == "toc":
            block = _object(value, label, {"type", "title", "depth"}, {"type"})
            depth = block.get("depth", 3)
            if type(depth) is not int or not 1 <= depth <= 4:
                raise ValueError(f"{label}.depth must be an integer from 1 to 4")
            title = _single_line_text(block.get("title", "目录"), f"{label}.title")
            blocks.append({"type": kind, "title": title, "depth": depth})
        elif kind == "paragraph":
            block = _object(value, label, {"type", "text", "runs", "format", "align"}, {"type"})
            if ("text" in block) == ("runs" in block):
                raise ValueError(f"{label} must contain exactly one of text or runs")
            align = block.get("align")
            if "align" in block and (not isinstance(align, str) or align not in _ALIGNMENTS):
                raise ValueError(f"{label}.align must be left, center, right, or justify")
            block_format = _validate_style_fields(block["format"], f"{label}.format") \
                if "format" in block else {}
            if "text" in block:
                content: Any = _text(block["text"], f"{label}.text")
            else:
                if not isinstance(block["runs"], list):
                    raise ValueError(f"{label}.runs must be an array")
                content = [_validate_run(run, f"{label}.runs[{i}]") for i, run in enumerate(block["runs"])]
            blocks.append({"type": kind, "content": content, "format": block_format, "align": align})
        elif kind == "list":
            block = _object(value, label, {"type", "items", "ordered", "list_id", "level"},
                            {"type", "items", "ordered"})
            if not isinstance(block["ordered"], bool):
                raise ValueError(f"{label}.ordered must be a boolean")
            if not isinstance(block["items"], list):
                raise ValueError(f"{label}.items must be an array")
            items = [_text(item, f"{label}.items[{i}]") for i, item in enumerate(block["items"])]
            level = block.get("level", 0)
            if type(level) is not int or not 0 <= level <= 8:
                raise ValueError(f"{label}.level must be an integer from 0 to 8")
            list_id = None
            if "list_id" in block:
                list_id = _single_line_text(block["list_id"], f"{label}.list_id")
                if list_id != list_id.strip():
                    raise ValueError(f"{label}.list_id cannot contain edge whitespace")
                if not items:
                    raise ValueError(f"{label}: a named list must contain at least one item")
                if list_id not in shared_lists:
                    if level != 0:
                        raise ValueError(f"{label}: a named list must begin at level 0")
                    shared_lists[list_id] = {"ordered": block["ordered"], "last_level": level}
                previous = shared_lists[list_id]
                if previous["ordered"] != block["ordered"]:
                    raise ValueError(f"{label}: ordered must stay consistent for one list_id")
                if level > previous["last_level"] + 1:
                    raise ValueError(f"{label}: a named list cannot skip an intervening parent level")
                if items:
                    previous["last_level"] = level
            elif level != 0:
                raise ValueError(f"{label}: a nonzero level requires list_id")
            blocks.append({"type": kind, "items": items, "ordered": block["ordered"],
                           "list_id": list_id, "level": level})
        elif kind == "table":
            block = _object(value, label, {
                "type", "rows", "widths_mm", "header", "header_rows", "format", "body_format", "header_format", "caption",
            }, {"type", "rows"})
            if not isinstance(block["rows"], list) or not block["rows"]:
                raise ValueError(f"{label}.rows must be a non-empty array")
            rows = []
            columns = None
            for row_index, row in enumerate(block["rows"]):
                if not isinstance(row, list) or not row:
                    raise ValueError(f"{label}.rows[{row_index}] must be a non-empty array")
                if columns is None:
                    columns = len(row)
                elif len(row) != columns:
                    raise ValueError(f"{label}.rows must be rectangular")
                rows.append(list(row))
            assert columns is not None
            rows, has_merges = normalize_merged_grid(rows, label)
            if "header" in block and not isinstance(block["header"], bool):
                raise ValueError(f"{label}.header must be a boolean")
            header_rows = block.get("header_rows", 1 if block.get("header", False) else 0)
            if type(header_rows) is not int or not 0 <= header_rows <= len(rows):
                raise ValueError(f"{label}.header_rows must be an integer from 0 to the row count")
            if "header" in block and "header_rows" in block and header_rows != int(block["header"]):
                raise ValueError(f"{label}.header and header_rows conflict; choose one header declaration")
            if has_merges and any(
                isinstance(cell, dict) and cell.get("row_span", 1) > 1 and
                row_index < header_rows < row_index + cell["row_span"]
                for row_index, row in enumerate(rows) for cell in row
            ):
                raise ValueError(f"{label}: vertically merged cells cannot cross the repeating-header boundary")
            layout_overrides = _validate_table_layout(block["format"], f"{label}.format") \
                if "format" in block else {}
            body_overrides = _validate_table_text_fields(block["body_format"], f"{label}.body_format") \
                if "body_format" in block else {}
            header_overrides = _validate_table_text_fields(block["header_format"], f"{label}.header_format") \
                if "header_format" in block else {}
            resolved_layout = _merge_table_layout(table_layout, layout_overrides)
            if "widths_mm" in block:
                widths = block["widths_mm"]
                if not isinstance(widths, list) or len(widths) != columns:
                    raise ValueError(f"{label}.widths_mm must contain one width per column")
                widths_twips = [_twips(_number(v, f"{label}.widths_mm[{i}]", 0.1, _MAX_PAGE_MM))
                                for i, v in enumerate(widths)]
                if sum(widths_twips) > available_width:
                    raise ValueError(f"{label}.widths_mm exceeds the page content width")
            else:
                if resolved_layout["column_sizing"] == "content":
                    padding = _twips(resolved_layout["cell_margin_left_mm"]) + _twips(resolved_layout["cell_margin_right_mm"])
                    widths_twips = _content_widths(rows, available_width, padding, label)
                else:
                    quotient, remainder = divmod(available_width, columns)
                    widths_twips = [quotient + (1 if i < remainder else 0) for i in range(columns)]
            horizontal_padding = _twips(resolved_layout["cell_margin_left_mm"]) \
                + _twips(resolved_layout["cell_margin_right_mm"])
            if any(width - horizontal_padding <= 0 for width in widths_twips):
                raise ValueError(f"{label} cell margins must leave positive content width in every column")
            parsed_rows = []
            vertical_padding = _twips(resolved_layout["cell_margin_top_mm"]) \
                + _twips(resolved_layout["cell_margin_bottom_mm"])
            for row_index, row in enumerate(rows):
                parsed = []
                for column, cell in enumerate(row):
                    cell_label = f"{label}.rows[{row_index}][{column}]"
                    if cell is None:
                        parsed.append(None)
                        continue
                    if isinstance(cell, dict) and cell.get("v_merge") == "continue":
                        parsed.append(cell)
                        continue
                    if isinstance(cell, str):
                        parsed.append(_text(cell, cell_label))
                        continue
                    fields = _object(cell, cell_label, {"text", "image", "borders", "col_span", "row_span", "v_merge"})
                    if not ({"text", "image"} & fields.keys()):
                        raise ValueError(f"{cell_label} must contain text and/or image")
                    text = _text(fields.get("text", ""), f"{cell_label}.text")
                    normalized = {"text": text, "image": None}
                    if "col_span" in fields:
                        normalized["col_span"] = fields["col_span"]
                    if "row_span" in fields:
                        normalized["row_span"] = fields["row_span"]
                        if fields["row_span"] > 1:
                            normalized["v_merge"] = "restart"
                    if "borders" in fields:
                        _object(fields["borders"], f"{cell_label}.borders", {"top", "left", "bottom", "right"})
                        normalized["borders"] = _validate_borders(fields["borders"], f"{cell_label}.borders")
                    if "image" in fields:
                        image_label = f"{cell_label}.image"
                        image_fields = _object(fields["image"], image_label,
                                               {"path", "width_mm", "height_mm", "alt"},
                                               {"path", "alt"})
                        image = _prepare_image(image_fields, base_dir, image_label,
                                               (sum(widths_twips[column:column + normalized.get("col_span", 1)]) - horizontal_padding) * 635,
                                               (available_height - vertical_padding) * 635, resources)
                        image_paths.append(image["image_path"])
                        normalized["image"] = image
                    parsed.append(normalized)
                parsed_rows.append(parsed)
            if not has_merges:
                _cell_borders(parsed_rows, resolved_layout, label)
            if not has_merges and resolved_layout["preset"] == "three_line" and 0 < header_rows < len(parsed_rows):
                for column in range(columns):
                    for row_index, edge in ((header_rows - 1, "bottom"), (header_rows, "top")):
                        cell = parsed_rows[row_index][column]
                        if isinstance(cell, str):
                            cell = {"text": cell, "image": None}
                            parsed_rows[row_index][column] = cell
                        cell.setdefault("borders", {}).setdefault(edge, {
                            "style": "single", "width_pt": 0.75, "color": resolved_layout["border_color"],
                        })
            if has_merges and resolved_layout["preset"] == "three_line" and 0 < header_rows < len(parsed_rows):
                for row_index, edge in ((header_rows - 1, "bottom"), (header_rows, "top")):
                    for column, cell in enumerate(parsed_rows[row_index]):
                        if cell is None:
                            continue
                        if isinstance(cell, str):
                            cell = {"text": cell, "image": None}
                            parsed_rows[row_index][column] = cell
                        cell.setdefault("borders", {}).setdefault(edge, {
                            "style": "single", "width_pt": 0.75, "color": resolved_layout["border_color"],
                        })
            caption = _text(block["caption"], f"{label}.caption", allow_empty=False) if "caption" in block else None
            blocks.append({"type": kind, "caption": caption, "rows": parsed_rows, "widths_twips": widths_twips,
                           "header": header_rows > 0, "header_rows": header_rows, "format": resolved_layout,
                           "body_format": body_overrides, "header_format": header_overrides,
                           "has_merges": has_merges})
        elif kind == "image":
            block = _object(value, label, {"type", "path", "width_mm", "height_mm", "alt"},
                            {"type", "path", "alt"})
            image = _prepare_image(block, base_dir, label, available_width * 635, available_height * 635,
                                   resources)
            image_paths.append(image["image_path"])
            blocks.append(image)
        elif kind == "section_break":
            block = _object(value, label, {"type", "break_type"} | SECTION_KEYS, {"type"})
            if not blocks or blocks[-1]["type"] == "section_break":
                raise ValueError(f"{label} cannot start a document or immediately follow another section break")
            break_type = block.get("break_type", "next_page")
            if type(break_type) is not str or break_type not in BREAK_TYPES:
                raise ValueError(f"{label}.break_type must be one of {sorted(BREAK_TYPES)}")
            section = parse_section({key: val for key, val in block.items() if key in SECTION_KEYS},
                                    section, label, twips=_twips, text_validator=_text,
                                    odd_even_headers=odd_even_headers)
            section["break_type"] = BREAK_TYPES[break_type]
            sections.append(section)
            available_width = section["available_width_twips"]
            available_height = section["available_height_twips"]
            blocks.append({"type": kind, "section_index": len(sections) - 1})
        elif kind == "reference":
            block = _object(value, label, {"type", "bookmark", "kind", "prefix", "suffix"}, {"type", "bookmark"})
            bookmark = _text(block["bookmark"], f"{label}.bookmark", allow_empty=False)
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", bookmark):
                raise ValueError(f"{label}.bookmark must be a valid ASCII bookmark name")
            ref_kind = block.get("kind", "text")
            if type(ref_kind) is not str or ref_kind not in {"text", "page"}:
                raise ValueError(f"{label}.kind must be text or page")
            references.append((bookmark.lower(), label))
            blocks.append({"type": kind, "bookmark": bookmark, "kind": ref_kind,
                           "prefix": _text(block.get("prefix", ""), f"{label}.prefix"),
                           "suffix": _text(block.get("suffix", ""), f"{label}.suffix")})
        elif kind == "page_break":
            _object(value, label, {"type"}, {"type"})
            blocks.append({"type": kind})
        else:
            raise ValueError(f"{label}.type has unsupported value {kind!r}")
    if blocks and blocks[-1]["type"] == "section_break":
        raise ValueError("document must not end with section_break")
    for bookmark, location in references:
        if bookmark not in bookmarks:
            raise ValueError(f"{location}.bookmark refers to an undefined heading bookmark")
    return {
        "sections": sections, "odd_even_headers": odd_even_headers,
        "heading_numbering": heading_numbering,
        "page_twips": page_twips, "available_width_twips": available_width,
        "available_height_twips": available_height, "styles": styles, "table_format": table_layout,
        "blocks": blocks,
        "header": header, "footer": footer,
        "page_numbers": page_numbers, "image_paths": image_paths,
        "deduplicate_images": deduplicate_images,
        "list_format": None if theme == "official" else {
            **_theme_base_format(theme), "bold": False, "color": "000000", "align": "left",
            "space_before_pt": 0, "space_after_pt": 4,
        },
        "related_format": None if theme == "official" else {
            **_theme_base_format(theme), "bold": False, "color": "000000", "align": "left",
            "first_line_chars": 0, "space_before_pt": 0, "space_after_pt": 0,
        },
    }
