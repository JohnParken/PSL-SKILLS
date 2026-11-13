"""Build an in-memory DOCX from a strict, small block specification."""

from __future__ import annotations

import io
import math
import re
import unicodedata
from collections.abc import Callable

from docx import Document
from docx.enum.section import WD_SECTION
from docx.image.exceptions import InvalidImageStreamError, UnexpectedEndOfFileError, UnrecognizedImageError
from docx.shared import Cm

from tl_create_layout import (
    THEME_DEFAULTS,
    HeaderFooterController,
    add_tab_stops,
    apply_page_setup,
    apply_paragraph_defaults,
    apply_run_format,
    apply_run_overrides,
    apply_section_options,
    apply_theme,
    create_list_numbering,
    insert_bookmark,
    insert_field,
    set_picture_alt,
    set_toc_field,
)
from tl_create_numbering import (
    create_heading_numbering,
    create_multilevel_list_numbering,
    set_paragraph_numbering,
)
from tl_create_tables import build_table, validate_table as _validate_advanced_table
from tl_create_tables import validate_table_format


_BOOKMARK_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}\Z")
_FIELD_REFERENCE = re.compile(r"(?:REF|PAGEREF) ([A-Za-z_][A-Za-z0-9_]{0,39})\Z")
_FIELD_SIMPLE = {"PAGE", "NUMPAGES"}
_START_TYPES = {
    "next_page": WD_SECTION.NEW_PAGE,
    "continuous": WD_SECTION.CONTINUOUS,
    "even_page": WD_SECTION.EVEN_PAGE,
    "odd_page": WD_SECTION.ODD_PAGE,
}
_PAGE_MARGINS = {"top", "bottom", "left", "right"}
_METADATA_FIELDS = {
    "title",
    "subject",
    "author",
    "keywords",
    "comments",
    "category",
    "identifier",
    "language",
    "last_modified_by",
}
_PAGE_NUMBER_FORMATS = {"decimal", "upperRoman", "lowerRoman", "upperLetter", "lowerLetter"}
_HEADER_FOOTER_VARIANTS = {"default", "first", "even"}


def _expect_object(value, path: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} keys must be strings")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise ValueError(f"{path} has unsupported keys: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"{path} is missing required keys: {', '.join(sorted(missing))}")
    return value


def _text(value, path: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{path} must not be empty")
    for character in value:
        codepoint = ord(character)
        if unicodedata.category(character) == "Cc" or 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError(f"{path} must not contain control characters")
        if codepoint in (0xFFFE, 0xFFFF):
            raise ValueError(f"{path} contains a character unsupported by XML")
    return value


def _boolean(value, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _number(value, path: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{path} must be between {minimum:g} and {maximum:g}")
    return number


def _integer(value, path: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{path} must be between {minimum} and {maximum}")
    return value


def _validate_margins(value, path: str) -> dict:
    margins = _expect_object(value, path, _PAGE_MARGINS)
    result = {}
    for side, amount in margins.items():
        result[side] = _number(amount, f"{path}.{side}", minimum=0, maximum=50)
    return result


def _validate_runs(value, path: str, bookmark_references: list[str]) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array of runs")
    for index, run in enumerate(value):
        run_path = f"{path}[{index}]"
        if not isinstance(run, dict):
            raise ValueError(f"{run_path} must be an object")
        if "field" in run:
            _expect_object(run, run_path, {"field", "display"}, {"field"})
            field = _text(run["field"], f"{run_path}.field", allow_empty=False)
            if field not in _FIELD_SIMPLE:
                match = _FIELD_REFERENCE.fullmatch(field)
                if match is None:
                    raise ValueError(f"{run_path}.field must be PAGE, NUMPAGES, REF name, or PAGEREF name")
                bookmark_references.append(match.group(1))
            if "display" in run:
                _text(run["display"], f"{run_path}.display")
            continue
        if "tab" in run:
            _expect_object(run, run_path, {"tab"}, {"tab"})
            if _boolean(run["tab"], f"{run_path}.tab") is not True:
                raise ValueError(f"{run_path}.tab must be true when present")
            continue
        _expect_object(
            run,
            run_path,
            {"text", "bold", "italic", "underline", "font", "size_pt", "color"},
            {"text"},
        )
        _text(run["text"], f"{run_path}.text")
        for flag in ("bold", "italic", "underline"):
            if flag in run:
                _boolean(run[flag], f"{run_path}.{flag}")
        if "font" in run:
            _text(run["font"], f"{run_path}.font", allow_empty=False)
        if "size_pt" in run:
            _number(run["size_pt"], f"{run_path}.size_pt", minimum=1, maximum=300)
        if "color" in run:
            color = _text(run["color"], f"{run_path}.color", allow_empty=False)
            if re.fullmatch(r"[0-9A-Fa-f]{6}", color) is None:
                raise ValueError(f"{run_path}.color must be a six-digit RGB value")


def _validate_tab_stops(value, path: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    for index, stop in enumerate(value):
        stop_path = f"{path}[{index}]"
        _expect_object(
            stop,
            stop_path,
            {"position_cm", "alignment", "leader"},
            {"position_cm"},
        )
        _number(stop["position_cm"], f"{stop_path}.position_cm", minimum=0.01, maximum=50)
        if "alignment" in stop and (
            not isinstance(stop["alignment"], str)
            or stop["alignment"] not in {"left", "center", "right", "decimal"}
        ):
            raise ValueError(f"{stop_path}.alignment has an unsupported value")
        if "leader" in stop and (
            not isinstance(stop["leader"], str)
            or stop["leader"] not in {"none", "dots", "hyphens", "underline"}
        ):
            raise ValueError(f"{stop_path}.leader has an unsupported value")


def _validate_rich_value(value, path: str, bookmark_references: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        _text(value, path)
    else:
        _validate_runs(value, path, bookmark_references)


def _validate_header_footer_variants(value, path: str, bookmark_references: list[str]) -> dict:
    variants = _expect_object(value, path, _HEADER_FOOTER_VARIANTS)
    for variant, contents in variants.items():
        _validate_rich_value(contents, f"{path}.{variant}", bookmark_references)
    return variants


def _validate_page_number_options(value: dict, path: str) -> None:
    if "page_start" in value:
        _integer(value["page_start"], f"{path}.page_start", minimum=1, maximum=32767)
    if "page_number_start" in value:
        _integer(value["page_number_start"], f"{path}.page_number_start", minimum=1, maximum=32767)
    if "page_start" in value and "page_number_start" in value:
        if value["page_start"] != value["page_number_start"]:
            raise ValueError(f"{path}.page_start and page_number_start must agree")
    if "page_number_format" in value:
        if (
            not isinstance(value["page_number_format"], str)
            or value["page_number_format"] not in _PAGE_NUMBER_FORMATS
        ):
            raise ValueError(f"{path}.page_number_format has an unsupported value")


def _validate_page_number_conflicts(page: dict, spec: dict) -> dict:
    options = dict(page)
    root_values = {
        name: spec[name]
        for name in ("page_start", "page_number_start", "page_number_format")
        if name in spec
    }
    _validate_page_number_options(options, "spec.page")
    _validate_page_number_options(root_values, "spec")
    page_start_values = [
        options[name]
        for name in ("page_start", "page_number_start")
        if name in options
    ]
    root_start_values = [
        root_values[name]
        for name in ("page_start", "page_number_start")
        if name in root_values
    ]
    if page_start_values and root_start_values and page_start_values[0] != root_start_values[0]:
        raise ValueError("spec page-number start conflicts with spec.page")
    if root_start_values:
        options["page_number_start"] = root_start_values[0]
    elif page_start_values:
        options["page_number_start"] = page_start_values[0]
    options.pop("page_start", None)
    if "page_number_format" in root_values:
        if (
            "page_number_format" in options
            and options["page_number_format"] != root_values["page_number_format"]
        ):
            raise ValueError("spec.page_number_format conflicts with spec.page")
        options["page_number_format"] = root_values["page_number_format"]
    return options


def _has_page_field(value) -> bool:
    return isinstance(value, list) and any(
        isinstance(run, dict) and run.get("field") == "PAGE"
        for run in value
    )


def _validate_merges(rows: list[list[str | None]], value, path: str) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    row_count = len(rows)
    column_count = len(rows[0])
    covered: set[tuple[int, int]] = set()
    merges = []
    for index, merge in enumerate(value):
        merge_path = f"{path}[{index}]"
        _expect_object(merge, merge_path, {"row", "col", "rowspan", "colspan"},
                       {"row", "col", "rowspan", "colspan"})
        row = _integer(merge["row"], f"{merge_path}.row", minimum=0, maximum=row_count - 1)
        column = _integer(merge["col"], f"{merge_path}.col", minimum=0, maximum=column_count - 1)
        rowspan = _integer(merge["rowspan"], f"{merge_path}.rowspan", minimum=1, maximum=row_count)
        colspan = _integer(merge["colspan"], f"{merge_path}.colspan", minimum=1, maximum=column_count)
        if rowspan == 1 and colspan == 1:
            raise ValueError(f"{merge_path} must cover more than one cell")
        if row + rowspan > row_count or column + colspan > column_count:
            raise ValueError(f"{merge_path} extends beyond table dimensions")
        if rows[row][column] is None:
            raise ValueError(f"{merge_path} anchor cell must contain text or an empty string")
        coordinates = {
            (target_row, target_column)
            for target_row in range(row, row + rowspan)
            for target_column in range(column, column + colspan)
        }
        if covered.intersection(coordinates):
            raise ValueError(f"{merge_path} overlaps another merge")
        for target_row, target_column in coordinates - {(row, column)}:
            cell_value = rows[target_row][target_column]
            if cell_value not in {None, ""}:
                raise ValueError(f"{merge_path} covers a non-empty cell")
        covered.update(coordinates)
        merges.append({"row": row, "col": column, "rowspan": rowspan, "colspan": colspan})
    for row_index, row_values in enumerate(rows):
        for column_index, cell_value in enumerate(row_values):
            if cell_value is None and (row_index, column_index) not in covered:
                raise ValueError(f"{path} contains null outside a declared merge")
    return merges


def _validate_table(block: dict, path: str) -> None:
    if not isinstance(block["rows"], list) or not block["rows"]:
        raise ValueError(f"{path}.rows must be a non-empty array")
    rows = block["rows"]
    if any(not isinstance(row, list) or not row for row in rows):
        raise ValueError(f"{path}.rows must contain non-empty arrays")
    column_count = len(rows[0])
    if any(len(row) != column_count for row in rows):
        raise ValueError(f"{path}.rows must have a consistent number of columns")
    normalized_rows = []
    for row_index, row in enumerate(rows):
        normalized = []
        for column_index, cell in enumerate(row):
            cell_path = f"{path}.rows[{row_index}][{column_index}]"
            if cell is not None:
                cell = _text(cell, cell_path)
            normalized.append(cell)
        normalized_rows.append(normalized)
    if "header" in block:
        _boolean(block["header"], f"{path}.header")
    if "repeat_header" in block:
        _boolean(block["repeat_header"], f"{path}.repeat_header")
        if not block.get("header", False):
            raise ValueError(f"{path}.repeat_header requires header: true")
    if "col_widths_cm" in block:
        widths = block["col_widths_cm"]
        if not isinstance(widths, list) or len(widths) != column_count:
            raise ValueError(f"{path}.col_widths_cm must have one width per column")
        for index, width in enumerate(widths):
            _number(width, f"{path}.col_widths_cm[{index}]", minimum=0.1, maximum=50)
    if "merges" in block:
        _validate_merges(normalized_rows, block["merges"], f"{path}.merges")
    else:
        _validate_merges(normalized_rows, [], f"{path}.merges")


def _validate_block(block, path: str, bookmarks: set[str], bookmark_references: list[str]) -> None:
    if not isinstance(block, dict):
        raise ValueError(f"{path} must be an object")
    kind = block.get("type")
    if not isinstance(kind, str):
        raise ValueError(f"{path}.type must be a string")
    if kind in {"title", "subtitle", "heading", "paragraph"}:
        flags = ("page_break_before", "keep_with_next", "keep_together", "widow_control")
        allowed = {"type", "text", "runs", "tab_stops", *flags}
        if kind in {"heading", "paragraph"}:
            allowed.add("bookmark")
        if kind == "heading":
            allowed.add("level")
        required = {"type"}
        if ("text" in block) == ("runs" in block):
            raise ValueError(f"{path} must contain exactly one of text or runs")
        _expect_object(block, path, allowed, required)
        if "text" in block:
            _text(block["text"], f"{path}.text")
        else:
            _validate_runs(block["runs"], f"{path}.runs", bookmark_references)
        if kind == "heading":
            _integer(block.get("level"), f"{path}.level", minimum=1, maximum=9)
        elif "level" in block:
            raise ValueError(f"{path}.level is only valid for heading blocks")
        for flag in flags:
            if flag in block:
                _boolean(block[flag], f"{path}.{flag}")
        if "tab_stops" in block:
            _validate_tab_stops(block["tab_stops"], f"{path}.tab_stops")
        if "bookmark" in block:
            name = _text(block["bookmark"], f"{path}.bookmark", allow_empty=False)
            if _BOOKMARK_NAME.fullmatch(name) is None:
                raise ValueError(f"{path}.bookmark must be a 1–40 character ASCII bookmark name")
            if name in bookmarks:
                raise ValueError(f"bookmark name is duplicated: {name}")
            bookmarks.add(name)
        return

    if kind == "list":
        _expect_object(
            block,
            path,
            {"type", "items", "ordered", "list_id", "level"},
            {"type", "items"},
        )
        items = block["items"]
        if not isinstance(items, list) or not items:
            raise ValueError(f"{path}.items must be a non-empty array")
        for index, item in enumerate(items):
            _text(item, f"{path}.items[{index}]")
        if "ordered" in block:
            _boolean(block["ordered"], f"{path}.ordered")
        level = _integer(block.get("level", 0), f"{path}.level", minimum=0, maximum=8)
        if "list_id" in block:
            list_id = _text(block["list_id"], f"{path}.list_id", allow_empty=False)
            if len(list_id) > 64 or list_id != list_id.strip():
                raise ValueError(f"{path}.list_id must be at most 64 characters without edge whitespace")
        elif level != 0:
            raise ValueError(f"{path}.level must be 0 when list_id is omitted")
        return

    if kind == "table":
        _validate_advanced_table(block, path)
        return

    if kind == "image":
        _expect_object(block, path, {"type", "path", "width_cm", "alt"}, {"type", "path"})
        _text(block["path"], f"{path}.path", allow_empty=False)
        if "width_cm" in block:
            _number(block["width_cm"], f"{path}.width_cm", minimum=0.01, maximum=50)
        if "alt" in block:
            _text(block["alt"], f"{path}.alt")
        return

    if kind == "section_break":
        _expect_object(
            block,
            path,
            {
                "type",
                "orientation",
                "start_type",
                "page_start",
                "page_number_start",
                "page_number_format",
                "margins_cm",
                "header",
                "footer",
                "headers",
                "footers",
                "different_first_page",
                "first_page_numbers",
                "page_numbers",
            },
            {"type"},
        )
        if "orientation" in block and (
            not isinstance(block["orientation"], str)
            or block["orientation"] not in {"portrait", "landscape"}
        ):
            raise ValueError(f"{path}.orientation must be portrait or landscape")
        if "start_type" in block and (
            not isinstance(block["start_type"], str) or block["start_type"] not in _START_TYPES
        ):
            raise ValueError(f"{path}.start_type has an unsupported value")
        _validate_page_number_options(block, path)
        if "margins_cm" in block:
            _validate_margins(block["margins_cm"], f"{path}.margins_cm")
        for flag in ("different_first_page", "first_page_numbers", "page_numbers"):
            if flag in block:
                _boolean(block[flag], f"{path}.{flag}")
        for part_name in ("header", "footer"):
            if part_name in block:
                _validate_rich_value(block[part_name], f"{path}.{part_name}", bookmark_references)
            variants_name = f"{part_name}s"
            if variants_name in block:
                _validate_header_footer_variants(
                    block[variants_name], f"{path}.{variants_name}", bookmark_references
                )
        return

    if kind == "toc":
        _expect_object(block, path, {"type", "levels", "title"}, {"type"})
        if "levels" in block:
            _integer(block["levels"], f"{path}.levels", minimum=1, maximum=9)
        if "title" in block:
            _text(block["title"], f"{path}.title")
        return

    if kind == "page_break":
        _expect_object(block, path, {"type"}, {"type"})
        return

    raise ValueError(f"{path}.type is unsupported: {kind}")


def _validate_spec(spec) -> tuple[str, dict, dict, set[str], list[dict], str, dict]:
    _expect_object(
        spec,
        "spec",
        {
            "theme",
            "blocks",
            "page",
            "metadata",
            "header",
            "footer",
            "headers",
            "footers",
            "heading_numbering",
            "table_format",
            "odd_even_headers",
            "different_first_page",
            "page_numbers",
            "first_page_numbers",
            "page_start",
            "page_number_start",
            "page_number_format",
        },
        {"blocks"},
    )
    theme = spec.get("theme", "official")
    if not isinstance(theme, str) or theme not in THEME_DEFAULTS:
        raise ValueError("spec.theme must be official, business, or academic")
    heading_numbering = spec.get("heading_numbering", "none")
    if not isinstance(heading_numbering, str) or heading_numbering not in {"none", "chinese", "decimal"}:
        raise ValueError("spec.heading_numbering must be none, chinese, or decimal")
    page = spec.get("page", {})
    _expect_object(
        page,
        "spec.page",
        {
            "orientation",
            "margins_cm",
            "page_start",
            "page_number_start",
            "page_number_format",
        },
    )
    if "orientation" in page and (
        not isinstance(page["orientation"], str)
        or page["orientation"] not in {"portrait", "landscape"}
    ):
        raise ValueError("spec.page.orientation must be portrait or landscape")
    if "margins_cm" in page:
        page_margins = _validate_margins(page["margins_cm"], "spec.page.margins_cm")
    else:
        page_margins = {}
    page_options = _validate_page_number_conflicts(page, spec)
    page_options["margins_cm"] = page_margins
    table_format = spec.get("table_format", {})
    if "table_format" in spec:
        validate_table_format(table_format, "spec.table_format")
    for flag in ("odd_even_headers", "different_first_page", "page_numbers", "first_page_numbers"):
        if flag in spec:
            _boolean(spec[flag], f"spec.{flag}")
    odd_even_headers = spec.get("odd_even_headers", False)
    different_first_page = spec.get("different_first_page", False)
    first_page_numbers = spec.get("first_page_numbers", False)
    if first_page_numbers and not different_first_page:
        raise ValueError("spec.first_page_numbers requires different_first_page: true")
    if "metadata" in spec:
        metadata = _expect_object(spec["metadata"], "spec.metadata", _METADATA_FIELDS)
        for key, value in metadata.items():
            _text(value, f"spec.metadata.{key}")
    bookmarks: set[str] = set()
    bookmark_references: list[str] = []
    for part_name in ("header", "footer"):
        if part_name in spec:
            _validate_rich_value(spec[part_name], f"spec.{part_name}", bookmark_references)
        variants_name = f"{part_name}s"
        if variants_name in spec:
            variants = _validate_header_footer_variants(
                spec[variants_name], f"spec.{variants_name}", bookmark_references
            )
            if "first" in variants and not different_first_page:
                raise ValueError(f"spec.{variants_name}.first requires different_first_page: true")
            if "even" in variants and not odd_even_headers:
                raise ValueError(f"spec.{variants_name}.even requires odd_even_headers: true")
    top_footers = spec.get("footers", {})
    footer_default = top_footers.get("default", spec.get("footer"))
    footer_even_follows_default = odd_even_headers and "even" not in top_footers
    footer_even = top_footers.get("even", footer_default if footer_even_follows_default else None)
    footer_first = top_footers.get("first")
    effective_page_numbers = spec.get("page_numbers", False)
    effective_first_page_numbers = first_page_numbers
    if effective_page_numbers and (
        _has_page_field(footer_default) or _has_page_field(footer_even)
    ):
        raise ValueError("automatic page_numbers cannot duplicate an explicit PAGE field in the footer")
    if effective_first_page_numbers and _has_page_field(footer_first):
        raise ValueError("automatic first_page_numbers cannot duplicate an explicit PAGE field")
    blocks = spec["blocks"]
    if not isinstance(blocks, list):
        raise ValueError("spec.blocks must be an array")
    list_instances: dict[str, dict] = {}
    effective_different_first_page = different_first_page
    effective_first_page_numbers = first_page_numbers
    for index, block in enumerate(blocks):
        block_path = f"spec.blocks[{index}]"
        _validate_block(block, block_path, bookmarks, bookmark_references)
        if block["type"] == "list" and "list_id" in block:
            list_id = block["list_id"]
            level = block.get("level", 0)
            ordered = block.get("ordered", False)
            previous = list_instances.get(list_id)
            if previous is None:
                if level != 0:
                    raise ValueError(f"{block_path}.level must start at 0 for a new list_id")
                list_instances[list_id] = {"ordered": ordered, "level": level}
            else:
                if previous["ordered"] != ordered:
                    raise ValueError(f"{block_path}.ordered conflicts with earlier list_id {list_id!r}")
                if level > previous["level"] + 1:
                    raise ValueError(f"{block_path}.level skips a parent level for list_id {list_id!r}")
                previous["level"] = level
        if block["type"] == "section_break":
            effective_different_first_page = block.get(
                "different_first_page", effective_different_first_page
            )
            if "first_page_numbers" in block:
                effective_first_page_numbers = block["first_page_numbers"]
            elif not effective_different_first_page:
                effective_first_page_numbers = False
            if effective_first_page_numbers and not effective_different_first_page:
                raise ValueError(f"{block_path}.first_page_numbers requires different_first_page: true")
            for part_name in ("headers", "footers"):
                variants = block.get(part_name, {})
                if "first" in variants and not effective_different_first_page:
                    raise ValueError(f"{block_path}.{part_name}.first requires different_first_page: true")
                if "even" in variants and not odd_even_headers:
                    raise ValueError(f"{block_path}.{part_name}.even requires odd_even_headers: true")
            section_footers = block.get("footers", {})
            if "footer" in block:
                footer_default = block["footer"]
            if "default" in section_footers:
                footer_default = section_footers["default"]
            if "even" in section_footers:
                footer_even = section_footers["even"]
                footer_even_follows_default = False
            elif footer_even_follows_default:
                footer_even = footer_default
            if "first" in section_footers:
                footer_first = section_footers["first"]
            effective_page_numbers = block.get("page_numbers", effective_page_numbers)
            if effective_page_numbers and (
                _has_page_field(footer_default) or _has_page_field(footer_even)
            ):
                raise ValueError(
                    f"{block_path}.page_numbers cannot duplicate an explicit PAGE field in the footer"
                )
            if effective_first_page_numbers and _has_page_field(footer_first):
                raise ValueError(
                    f"{block_path}.first_page_numbers cannot duplicate an explicit PAGE field"
                )
    missing = sorted(set(bookmark_references) - bookmarks)
    if missing:
        raise ValueError(f"field references unknown bookmark(s): {', '.join(missing)}")
    return (
        theme,
        page_options,
        spec.get("metadata", {}),
        bookmarks,
        blocks,
        heading_numbering,
        table_format,
    )


def _append_rich_runs(paragraph, runs: list[dict], theme: str, kind: str, level: int | None) -> None:
    for item in runs:
        if "field" in item:
            insert_field(paragraph, item["field"], item.get("display"))
        elif "tab" in item:
            paragraph.add_run().add_tab()
        else:
            run = paragraph.add_run(item["text"])
            apply_run_format(run, theme, kind, level)
            apply_run_overrides(run, item)


def _apply_core_properties(document, metadata: dict) -> None:
    properties = document.core_properties
    for key, value in metadata.items():
        setattr(properties, key, value)


def _add_text_block(
    document,
    block: dict,
    theme: str,
    bookmark_ids: dict[str, int],
    heading_number_id: int | None,
) -> None:
    kind = block["type"]
    style_name = {
        "title": "Title",
        "subtitle": "Subtitle",
        "paragraph": "Normal",
    }.get(kind)
    level = None
    paragraph_kind = "body" if kind == "paragraph" else "title"
    if kind == "heading":
        level = block["level"]
        style_name = f"Heading {level}"
        paragraph_kind = "heading"
    paragraph = document.add_paragraph(style=style_name)
    apply_paragraph_defaults(paragraph, theme, paragraph_kind)
    for flag in ("page_break_before", "keep_with_next", "keep_together", "widow_control"):
        if flag in block:
            setattr(paragraph.paragraph_format, flag, block[flag])
    if "tab_stops" in block:
        add_tab_stops(paragraph, block["tab_stops"])
    if "text" in block:
        run = paragraph.add_run(block["text"])
        apply_run_format(run, theme, kind, level)
    else:
        _append_rich_runs(paragraph, block["runs"], theme, kind, level)
    if kind == "heading" and level is not None and level <= 4 and heading_number_id is not None:
        set_paragraph_numbering(paragraph, heading_number_id, level - 1)
    if "bookmark" in block:
        name = block["bookmark"]
        insert_bookmark(paragraph, name, bookmark_ids[name])


def _add_image(document, block: dict, theme: str, read_image: Callable[[str], bytes]) -> None:
    image_bytes = read_image(block["path"])
    if not isinstance(image_bytes, (bytes, bytearray, memoryview)):
        raise ValueError(f"read_image must return bytes for {block['path']!r}")
    paragraph = document.add_paragraph()
    apply_paragraph_defaults(paragraph, theme, "image")
    run = paragraph.add_run()
    try:
        picture = run.add_picture(
            io.BytesIO(bytes(image_bytes)),
            width=Cm(block["width_cm"]) if "width_cm" in block else None,
        )
    except (InvalidImageStreamError, UnexpectedEndOfFileError, UnrecognizedImageError) as error:
        raise ValueError(f"image data is not a supported image for {block['path']!r}") from error
    set_picture_alt(picture, block.get("alt", ""))


def _add_section_break(document, block: dict, header_footer_controller: HeaderFooterController) -> None:
    start_type = _START_TYPES.get(block.get("start_type", "next_page"), WD_SECTION.NEW_PAGE)
    section = document.add_section(start_type)
    apply_section_options(section, block, clear_missing_page_start=True)
    header_footer_controller.advance(section, block)


def build_document(spec: dict, read_image: Callable[[str], bytes]):
    """Build a python-docx Document without accessing task files directly.

    Images are the only external bytes consumed and are obtained exclusively
    through ``read_image(path)``. The returned document remains in memory.
    """
    if not callable(read_image):
        raise ValueError("read_image must be callable")
    (
        theme,
        page_options,
        metadata,
        bookmark_names,
        blocks,
        heading_numbering,
        table_format,
    ) = _validate_spec(spec)
    document = Document()
    apply_theme(document, theme)
    apply_page_setup(document.sections[0], theme, page_options)
    apply_section_options(document.sections[0], page_options)
    _apply_core_properties(document, metadata)
    bookmark_ids = {name: index for index, name in enumerate(sorted(bookmark_names), start=1)}
    heading_number_id = create_heading_numbering(document, heading_numbering)
    header_footer_controller = HeaderFooterController(document, theme, spec, _append_rich_runs)
    list_number_ids = {}

    for block in blocks:
        kind = block["type"]
        if kind in {"title", "subtitle", "heading", "paragraph"}:
            _add_text_block(document, block, theme, bookmark_ids, heading_number_id)
        elif kind == "list":
            list_id = block.get("list_id")
            if list_id is not None:
                if list_id not in list_number_ids:
                    list_number_ids[list_id] = create_multilevel_list_numbering(
                        document, block.get("ordered", False)
                    )
                number_id = list_number_ids[list_id]
                level = block.get("level", 0)
                style = None
            else:
                number_id = create_list_numbering(document, block.get("ordered", False))
                level = 0
                style = "List Number" if block.get("ordered", False) else "List Bullet"
            for text in block["items"]:
                paragraph = document.add_paragraph(style=style)
                apply_paragraph_defaults(paragraph, theme, "list")
                run = paragraph.add_run(text)
                apply_run_format(run, theme, "body")
                set_paragraph_numbering(paragraph, number_id, level)
        elif kind == "table":
            build_table(document, block, theme, table_format, read_image)
        elif kind == "image":
            _add_image(document, block, theme, read_image)
        elif kind == "section_break":
            _add_section_break(document, block, header_footer_controller)
        elif kind == "toc":
            title = block.get("title", "目录")
            heading = document.add_paragraph(style="Heading 1")
            apply_paragraph_defaults(heading, theme, "heading")
            heading.paragraph_format.keep_with_next = True
            title_run = heading.add_run(title)
            apply_run_format(title_run, theme, "heading", 1)
            field_paragraph = document.add_paragraph()
            apply_paragraph_defaults(field_paragraph, theme, "toc")
            set_toc_field(field_paragraph, block.get("levels", 3))
        elif kind == "page_break":
            document.add_page_break()

    return document


__all__ = ["build_document"]
