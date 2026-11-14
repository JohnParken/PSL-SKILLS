"""Low-level python-docx formatting and OOXML helpers for tl-docx creation."""

from __future__ import annotations

from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


THEME_DEFAULTS = {
    "official": {
        "east_asia": "仿宋",
        "latin": "Times New Roman",
        "size_pt": 16,
        "line_spacing_pt": 28,
        "line_spacing_multiple": 1.15,
        "first_indent_pt": 32,
        "space_after_pt": 0,
        "margins_cm": {"top": 2.54, "bottom": 2.54, "left": 3.18, "right": 3.18},
    },
    "business": {
        "east_asia": "微软雅黑",
        "latin": "Calibri",
        "size_pt": 10.5,
        "line_spacing_multiple": 1.25,
        "first_indent_pt": 0,
        "space_after_pt": 4,
        "margins_cm": {"top": 2.54, "bottom": 2.54, "left": 3.18, "right": 3.18},
    },
    "academic": {
        "east_asia": "宋体",
        "latin": "Times New Roman",
        "size_pt": 10.5,
        "line_spacing_multiple": 1.25,
        "first_indent_pt": 21,
        "space_after_pt": 4,
        "margins_cm": {"top": 2.54, "bottom": 2.54, "left": 3.18, "right": 3.18},
    },
}

_OFFICIAL_HEADING_FONTS = {1: "黑体", 2: "楷体", 3: "仿宋", 4: "仿宋"}
_OTHER_HEADING_SIZES = {1: 16, 2: 14, 3: 12, 4: 11}
_OTHER_TITLE_SIZES = {"title": 22, "subtitle": 12}
_BUSINESS_COLOR = "1A365D"


def _set_run_font(run, east_asia: str, latin: str, size_pt: float) -> None:
    run.font.name = latin
    run.font.size = Pt(size_pt)
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), east_asia)
    run._element.rPr.rFonts.set(qn("w:ascii"), latin)
    run._element.rPr.rFonts.set(qn("w:hAnsi"), latin)
    run._element.rPr.rFonts.set(qn("w:cs"), latin)


def apply_theme(document, theme: str) -> None:
    """Set named defaults while keeping all styling local to this document."""
    defaults = THEME_DEFAULTS[theme]
    normal = document.styles["Normal"]
    normal.font.name = defaults["latin"]
    normal.font.size = Pt(defaults["size_pt"])
    normal_properties = normal._element.get_or_add_rPr()
    normal_fonts = normal_properties.get_or_add_rFonts()
    normal_fonts.set(qn("w:eastAsia"), defaults["east_asia"])
    normal_fonts.set(qn("w:ascii"), defaults["latin"])
    normal_fonts.set(qn("w:hAnsi"), defaults["latin"])
    for level in range(1, 10):
        style = document.styles[f"Heading {level}"]
        font_name = _heading_font(theme, level)
        style.font.name = defaults["latin"]
        style.font.size = Pt(_heading_size(theme, level))
        style.font.bold = theme != "official"
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        style.font.color.rgb = RGBColor.from_string(
            _BUSINESS_COLOR if theme == "business" else "000000"
        )
        style_properties = style._element.get_or_add_rPr()
        style_fonts = style_properties.get_or_add_rFonts()
        style_fonts.set(qn("w:eastAsia"), font_name)
        style_fonts.set(qn("w:ascii"), defaults["latin"])
        style_fonts.set(qn("w:hAnsi"), defaults["latin"])
    title = document.styles["Title"]
    title.font.name = defaults["latin"]
    title.font.size = Pt(_OTHER_TITLE_SIZES["title"] if theme != "official" else 22)
    title.font.bold = theme != "official"
    title.font.color.rgb = RGBColor.from_string(
        _BUSINESS_COLOR if theme == "business" else "000000"
    )
    title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_properties = title._element.get_or_add_rPr()
    title_fonts = title_properties.get_or_add_rFonts()
    title_fonts.set(qn("w:eastAsia"), _title_font(theme))
    title_fonts.set(qn("w:ascii"), defaults["latin"])
    title_fonts.set(qn("w:hAnsi"), defaults["latin"])
    subtitle = document.styles["Subtitle"]
    subtitle.font.name = defaults["latin"]
    subtitle.font.size = Pt(_OTHER_TITLE_SIZES["subtitle"] if theme != "official" else 14)
    subtitle.font.bold = theme != "official"
    subtitle.font.color.rgb = RGBColor.from_string(
        _BUSINESS_COLOR if theme == "business" else "000000"
    )
    subtitle.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle_properties = subtitle._element.get_or_add_rPr()
    subtitle_fonts = subtitle_properties.get_or_add_rFonts()
    subtitle_fonts.set(qn("w:eastAsia"), _title_font(theme))
    subtitle_fonts.set(qn("w:ascii"), defaults["latin"])
    subtitle_fonts.set(qn("w:hAnsi"), defaults["latin"])


def _title_font(theme: str) -> str:
    return "方正小标宋简体" if theme == "official" else "黑体"


def _heading_font(theme: str, level: int) -> str:
    if theme == "official":
        return _OFFICIAL_HEADING_FONTS.get(level, "仿宋")
    return "黑体"


def _heading_size(theme: str, level: int) -> float:
    if theme == "official":
        return 16
    return _OTHER_HEADING_SIZES.get(level, 11)


def apply_page_setup(section, theme: str, page: dict | None = None) -> None:
    """Apply A4 page dimensions, theme margins, and explicit page options."""
    defaults = THEME_DEFAULTS[theme]
    options = page or {}
    width = options.get("width_mm", 210) / 10
    height = options.get("height_mm", 297) / 10
    section.page_width = Cm(width)
    section.page_height = Cm(height)
    section.orientation = WD_ORIENT.PORTRAIT
    orientation = options.get("orientation", "portrait")
    if orientation == "landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Cm(max(width, height))
        section.page_height = Cm(min(width, height))
    elif "orientation" in options:
        section.page_width = Cm(min(width, height))
        section.page_height = Cm(max(width, height))
    margins = dict(defaults["margins_cm"])
    margins.update(options.get("margins_cm", {}))
    section.top_margin = Cm(margins["top"])
    section.bottom_margin = Cm(margins["bottom"])
    section.left_margin = Cm(margins["left"])
    section.right_margin = Cm(margins["right"])
    if section.page_width <= section.left_margin + section.right_margin or section.page_height <= section.top_margin + section.bottom_margin:
        raise ValueError("Page margins must leave a positive usable page area")


def apply_section_options(
    section,
    options: dict,
    *,
    clear_missing_page_start: bool = False,
) -> None:
    """Apply optional orientation and margins to a newly-created section."""
    if "width_mm" in options:
        section.page_width = Cm(options["width_mm"] / 10)
    if "height_mm" in options:
        section.page_height = Cm(options["height_mm"] / 10)
    if "orientation" in options:
        current_width, current_height = section.page_width, section.page_height
        if options["orientation"] == "landscape":
            section.orientation = WD_ORIENT.LANDSCAPE
            section.page_width = max(current_width, current_height)
            section.page_height = min(current_width, current_height)
        else:
            section.orientation = WD_ORIENT.PORTRAIT
            section.page_width = min(current_width, current_height)
            section.page_height = max(current_width, current_height)
    if "margins_cm" in options:
        margins = options["margins_cm"]
        for key, property_name in (
            ("top", "top_margin"),
            ("bottom", "bottom_margin"),
            ("left", "left_margin"),
            ("right", "right_margin"),
        ):
            if key in margins:
                setattr(section, property_name, Cm(margins[key]))
    if section.page_width <= section.left_margin + section.right_margin or section.page_height <= section.top_margin + section.bottom_margin:
        raise ValueError("Section margins must leave a positive usable page area")
    page_start = options.get("page_number_start", options.get("page_start"))
    if "page_number_start" in options and "page_start" in options:
        if options["page_number_start"] != options["page_start"]:
            raise ValueError("page_start and page_number_start must agree when both are present")
    if page_start is not None:
        set_page_number_start(section, page_start)
    elif clear_missing_page_start:
        page_numbering = section._sectPr.find(qn("w:pgNumType"))
        if page_numbering is not None:
            page_numbering.attrib.pop(qn("w:start"), None)
            if not page_numbering.attrib:
                section._sectPr.remove(page_numbering)
    if "page_number_format" in options:
        set_page_number_format(section, options["page_number_format"])


def set_page_number_start(section, page_start: int) -> None:
    sect_pr = section._sectPr
    page_numbering = sect_pr.find(qn("w:pgNumType"))
    if page_numbering is None:
        page_numbering = OxmlElement("w:pgNumType")
        sect_pr.insert_element_before(page_numbering, "w:cols")
    page_numbering.set(qn("w:start"), str(page_start))


def set_page_number_format(section, page_number_format: str) -> None:
    """Set the native numbering format for PAGE fields in a section."""
    sect_pr = section._sectPr
    page_numbering = sect_pr.find(qn("w:pgNumType"))
    if page_numbering is None:
        page_numbering = OxmlElement("w:pgNumType")
        sect_pr.insert_element_before(page_numbering, "w:cols")
    page_numbering.set(qn("w:fmt"), page_number_format)


def apply_paragraph_defaults(paragraph, theme: str, kind: str) -> None:
    defaults = THEME_DEFAULTS[theme]
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(_space_after(theme, kind))
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if kind in {"title", "subtitle"} else WD_ALIGN_PARAGRAPH.LEFT
    if kind == "body":
        paragraph.paragraph_format.first_line_indent = Pt(defaults["first_indent_pt"])
        paragraph.paragraph_format.line_spacing = _line_spacing(defaults, body=True)
        return
    paragraph.paragraph_format.first_line_indent = Pt(0)
    paragraph.paragraph_format.line_spacing = _line_spacing(defaults, body=False)
    if kind == "heading":
        paragraph.paragraph_format.keep_with_next = True
        paragraph.paragraph_format.keep_together = True
        paragraph.paragraph_format.widow_control = True


def _line_spacing(defaults: dict, *, body: bool):
    if body and "line_spacing_pt" in defaults:
        return Pt(defaults["line_spacing_pt"])
    return defaults["line_spacing_multiple"]


def _space_after(theme: str, kind: str) -> float:
    if theme == "official" and kind == "title":
        return 12
    if theme == "official" and kind == "subtitle":
        return 8
    if kind == "header_footer":
        return 0
    return THEME_DEFAULTS[theme]["space_after_pt"]


def apply_run_format(run, theme: str, kind: str, level: int | None = None) -> None:
    defaults = THEME_DEFAULTS[theme]
    if kind == "title":
        size_pt = 22
        run.bold = theme != "official"
        east_asia = _title_font(theme)
    elif kind == "subtitle":
        size_pt = 14 if theme == "official" else 12
        run.bold = theme != "official"
        east_asia = _title_font(theme)
    elif kind == "heading":
        heading_level = level or 1
        size_pt = _heading_size(theme, heading_level)
        east_asia = _heading_font(theme, heading_level)
        run.bold = theme != "official"
        if theme != "official":
            run.font.color.rgb = RGBColor.from_string(
                _BUSINESS_COLOR if theme == "business" else "000000"
            )
    else:
        size_pt = defaults["size_pt"]
        east_asia = defaults["east_asia"]
    _set_run_font(run, east_asia, defaults["latin"], size_pt)


def apply_run_overrides(run, item: dict) -> None:
    if "bold" in item:
        run.bold = item["bold"]
    if "italic" in item:
        run.italic = item["italic"]
    if "underline" in item:
        run.underline = item["underline"]
    if "font" in item:
        run.font.name = item["font"]
        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), item["font"])
        run._element.rPr.rFonts.set(qn("w:ascii"), item["font"])
        run._element.rPr.rFonts.set(qn("w:hAnsi"), item["font"])
    if "font_east_asia" in item:
        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), item["font_east_asia"])
    if "font_latin" in item:
        run.font.name = item["font_latin"]
        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:cs"), item["font_latin"])
    if "size_pt" in item:
        run.font.size = Pt(item["size_pt"])
    if "color" in item:
        run.font.color.rgb = RGBColor.from_string(item["color"])


def insert_field(paragraph, instruction: str, display: str | None = None) -> None:
    """Insert a standard complex field, with an optional cached display value."""
    begin_run = OxmlElement("w:r")
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    begin_run.append(begin)
    paragraph._p.append(begin_run)

    instruction_run = OxmlElement("w:r")
    instruction_text = OxmlElement("w:instrText")
    instruction_text.set(qn("xml:space"), "preserve")
    instruction_text.text = f" {instruction} "
    instruction_run.append(instruction_text)
    paragraph._p.append(instruction_run)

    separator_run = OxmlElement("w:r")
    separator = OxmlElement("w:fldChar")
    separator.set(qn("w:fldCharType"), "separate")
    separator_run.append(separator)
    paragraph._p.append(separator_run)

    if display is not None:
        result_run = OxmlElement("w:r")
        result_text = OxmlElement("w:t")
        result_text.text = display
        result_run.append(result_text)
        paragraph._p.append(result_run)

    end_run = OxmlElement("w:r")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    end_run.append(end)
    paragraph._p.append(end_run)


def insert_bookmark(paragraph, name: str, bookmark_id: int) -> None:
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bookmark_id))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bookmark_id))
    index = 1 if paragraph._p.pPr is not None else 0
    paragraph._p.insert(index, start)
    paragraph._p.append(end)


def add_tab_stops(paragraph, stops: list[dict]) -> None:
    alignments = {
        "left": WD_TAB_ALIGNMENT.LEFT,
        "center": WD_TAB_ALIGNMENT.CENTER,
        "right": WD_TAB_ALIGNMENT.RIGHT,
        "decimal": WD_TAB_ALIGNMENT.DECIMAL,
    }
    leaders = {
        "none": WD_TAB_LEADER.SPACES,
        "dots": WD_TAB_LEADER.DOTS,
        "hyphens": WD_TAB_LEADER.DASHES,
        "underline": WD_TAB_LEADER.LINES,
    }
    for stop in stops:
        paragraph.paragraph_format.tab_stops.add_tab_stop(
            Cm(stop["position_cm"]),
            alignment=alignments[stop.get("alignment", "left")],
            leader=leaders[stop.get("leader", "none")],
        )


def create_list_numbering(document, ordered: bool) -> int:
    """Create a distinct native numbering instance for one independent list."""
    numbering = document.part.numbering_part.element
    wanted_format = "decimal" if ordered else "bullet"
    abstracts = numbering.findall(qn("w:abstractNum"))
    abstract_id = None
    for abstract in abstracts:
        level = abstract.find(qn("w:lvl"))
        number_format = level.find(qn("w:numFmt")) if level is not None else None
        if number_format is not None and number_format.get(qn("w:val")) == wanted_format:
            abstract_id = abstract.get(qn("w:abstractNumId"))
            break
    if abstract_id is None:
        raise ValueError(f"python-docx template has no native {wanted_format} numbering definition")
    existing_ids = [int(item.get(qn("w:numId"))) for item in numbering.findall(qn("w:num"))]
    number_id = max(existing_ids, default=0) + 1
    number = OxmlElement("w:num")
    number.set(qn("w:numId"), str(number_id))
    abstract_reference = OxmlElement("w:abstractNumId")
    abstract_reference.set(qn("w:val"), abstract_id)
    number.append(abstract_reference)
    numbering.append(number)
    return number_id


def set_paragraph_numbering(paragraph, number_id: int) -> None:
    properties = paragraph._p.get_or_add_pPr()
    number_properties = properties.find(qn("w:numPr"))
    if number_properties is None:
        number_properties = OxmlElement("w:numPr")
        properties.append(number_properties)
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "0")
    number = OxmlElement("w:numId")
    number.set(qn("w:val"), str(number_id))
    number_properties.append(level)
    number_properties.append(number)


def set_repeat_table_header(row) -> None:
    properties = row._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    properties.append(repeat)


def set_picture_alt(picture, alt: str) -> None:
    picture._inline.docPr.set("descr", alt)


def set_cell_text(cell, text: str, theme: str, *, is_header: bool = False) -> None:
    paragraph = cell.paragraphs[0]
    apply_paragraph_defaults(paragraph, theme, "table_header" if is_header else "table_body")
    run = paragraph.add_run(text)
    apply_run_format(run, theme, "body")
    if is_header:
        run.bold = True
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _header_footer_attribute(part_name: str, variant: str) -> str:
    attributes = {
        ("header", "default"): "header",
        ("footer", "default"): "footer",
        ("header", "first"): "first_page_header",
        ("footer", "first"): "first_page_footer",
        ("header", "even"): "even_page_header",
        ("footer", "even"): "even_page_footer",
    }
    try:
        return attributes[(part_name, variant)]
    except KeyError as error:
        raise ValueError(f"unsupported header/footer variant: {part_name}.{variant}") from error


def set_different_first_page(section, enabled: bool) -> None:
    """Set or clear the section's first-page header/footer switch."""
    section.different_first_page_header_footer = enabled


def set_odd_even_headers(document, enabled: bool) -> None:
    """Set the document-wide even/odd header and footer setting."""
    document.settings.odd_and_even_pages_header_footer = enabled


def clear_header_footer(section, part_name: str, variant: str = "default"):
    attribute = _header_footer_attribute(part_name, variant)
    part = getattr(section, attribute)
    part.is_linked_to_previous = False
    paragraphs = part.paragraphs
    for paragraph in paragraphs[1:]:
        paragraph._element.getparent().remove(paragraph._element)
    first = paragraphs[0]
    first.clear()
    return first


def set_header_footer(
    section,
    part_name: str,
    value,
    theme: str,
    append_rich_runs,
    *,
    variant: str = "default",
    page_number: bool = False,
) -> None:
    paragraph = clear_header_footer(section, part_name, variant)
    apply_paragraph_defaults(paragraph, theme, "header_footer")
    if isinstance(value, str):
        run = paragraph.add_run(value)
        apply_run_format(run, theme, "body")
    elif value is not None:
        append_rich_runs(paragraph, value, theme, "body", None)
    if page_number:
        if paragraph.runs and paragraph.text:
            paragraph.add_run(" ")
        insert_field(paragraph, "PAGE")


class HeaderFooterController:
    """Carry explicit header/footer values across newly-created sections."""

    def __init__(self, document, theme: str, spec: dict, append_rich_runs) -> None:
        self.document = document
        self.theme = theme
        self.append_rich_runs = append_rich_runs
        self.odd_even_headers = spec.get("odd_even_headers", False)
        set_odd_even_headers(document, self.odd_even_headers)
        self.different_first_page = spec.get("different_first_page", False)
        self.page_numbers = spec.get("page_numbers", False)
        self.first_page_numbers = spec.get("first_page_numbers", False)
        self.parts = self._initial_parts(spec)
        self._apply_initial(document.sections[0])

    @staticmethod
    def _state(value=None, *, explicit: bool = False, follows_default: bool = False) -> dict:
        return {"value": value, "explicit": explicit, "follows_default": follows_default}

    def _initial_parts(self, spec: dict) -> dict:
        result = {}
        for part_name in ("header", "footer"):
            variants = spec.get(f"{part_name}s", {})
            if "default" in variants:
                default = self._state(variants["default"], explicit=True)
            elif part_name in spec:
                default = self._state(spec[part_name], explicit=True)
            else:
                default = self._state()
            if "even" in variants:
                even = self._state(variants["even"], explicit=True)
            elif self.odd_even_headers:
                even = self._state(default["value"], explicit=True, follows_default=True)
            else:
                even = self._state()
            if "first" in variants:
                first = self._state(variants["first"], explicit=True)
            elif self.different_first_page:
                first = self._state(explicit=True)
            else:
                first = self._state()
            result[part_name] = {"default": default, "first": first, "even": even}
        return result

    def _page_number_for(self, part_name: str, variant: str) -> bool:
        if part_name != "footer":
            return False
        if variant == "first":
            return self.first_page_numbers
        return self.page_numbers

    @staticmethod
    def _has_page_field(value) -> bool:
        return isinstance(value, list) and any(
            isinstance(run, dict) and run.get("field") == "PAGE"
            for run in value
        )

    def _apply_variant(self, section, part_name: str, variant: str) -> None:
        state = self.parts[part_name][variant]
        page_number = self._page_number_for(part_name, variant)
        if page_number and self._has_page_field(state["value"]):
            raise ValueError(
                f"{part_name}s.{variant} already contains a PAGE field while automatic page numbers are enabled"
            )
        set_header_footer(
            section,
            part_name,
            state["value"],
            self.theme,
            self.append_rich_runs,
            variant=variant,
            page_number=page_number,
        )

    def _apply_initial(self, section) -> None:
        set_different_first_page(section, self.different_first_page)
        for part_name, variants in self.parts.items():
            for variant, state in variants.items():
                active_variant = variant == "default" or (
                    variant == "first" and self.different_first_page
                ) or (variant == "even" and self.odd_even_headers)
                needs_page = self._page_number_for(part_name, variant)
                if active_variant and (state["explicit"] or state["value"] is not None or needs_page):
                    self._apply_variant(section, part_name, variant)

    def advance(self, section, options: dict) -> None:
        previous = {
            part_name: {variant: state.copy() for variant, state in variants.items()}
            for part_name, variants in self.parts.items()
        }
        old_different_first_page = self.different_first_page
        old_page_numbers = self.page_numbers
        old_first_page_numbers = self.first_page_numbers
        new_different_first_page = options.get(
            "different_first_page", self.different_first_page
        )
        if "page_numbers" in options:
            self.page_numbers = options["page_numbers"]
        if "first_page_numbers" in options:
            self.first_page_numbers = options["first_page_numbers"]
        elif "different_first_page" in options and not options["different_first_page"]:
            self.first_page_numbers = False
        self.different_first_page = new_different_first_page
        set_different_first_page(section, self.different_first_page)

        for part_name in ("header", "footer"):
            variants = options.get(f"{part_name}s", {})
            if part_name in options:
                self.parts[part_name]["default"] = self._state(
                    options[part_name], explicit=True
                )
            for variant, value in variants.items():
                self.parts[part_name][variant] = self._state(value, explicit=True)
            if self.parts[part_name]["even"]["follows_default"]:
                self.parts[part_name]["even"]["value"] = self.parts[part_name]["default"]["value"]
            if (
                self.different_first_page
                and not old_different_first_page
                and "first" not in variants
            ):
                self.parts[part_name]["first"] = self._state(explicit=True)

        for part_name, variants in self.parts.items():
            for variant, state in variants.items():
                old_state = previous[part_name][variant]
                page_mode_changed = (
                    old_page_numbers != self.page_numbers
                    if variant != "first" and part_name == "footer"
                    else old_first_page_numbers != self.first_page_numbers
                    if part_name == "footer"
                    else False
                )
                first_page_activated = (
                    variant == "first"
                    and self.different_first_page
                    and not old_different_first_page
                )
                value_changed = state["value"] != old_state["value"]
                explicitly_changed = state["explicit"] and (
                    state["value"] != old_state["value"]
                    or state["follows_default"] != old_state["follows_default"]
                )
                variant_enabled = variant == "default" or (
                    variant == "first" and self.different_first_page
                ) or (variant == "even" and self.odd_even_headers)
                if variant_enabled and (
                    value_changed
                    or page_mode_changed
                    or first_page_activated
                    or explicitly_changed
                ):
                    self._apply_variant(section, part_name, variant)


def set_toc_field(paragraph, levels: int) -> None:
    insert_field(paragraph, f'TOC \\o "1-{levels}" \\h \\z \\u', "请在 Word 中更新目录")
