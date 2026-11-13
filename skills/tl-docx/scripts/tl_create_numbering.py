"""Native multilevel numbering helpers for tl-docx creation."""

from __future__ import annotations

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _next_id(parent, attribute: str, tag: str) -> int:
    values = [int(item.get(qn(attribute))) for item in parent.findall(qn(tag))]
    return max(values, default=-1) + 1


def _new_abstract(numbering, *, ordered: bool, levels: int, heading_mode: str | None = None) -> int:
    abstract_id = _next_id(numbering, "w:abstractNumId", "w:abstractNum")
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multi_level = OxmlElement("w:multiLevelType")
    multi_level.set(qn("w:val"), "multilevel")
    abstract.append(multi_level)

    for level_index in range(levels):
        level = OxmlElement("w:lvl")
        level.set(qn("w:ilvl"), str(level_index))
        start = OxmlElement("w:start")
        start.set(qn("w:val"), "1")
        level.append(start)

        number_format = OxmlElement("w:numFmt")
        if heading_mode == "chinese":
            number_format.set(
                qn("w:val"),
                "chineseCounting" if level_index < 2 else "decimal",
            )
        elif heading_mode == "decimal":
            number_format.set(qn("w:val"), "decimal")
        else:
            number_format.set(qn("w:val"), "decimal" if ordered else "bullet")
        level.append(number_format)

        if level_index and heading_mode is None:
            restart = OxmlElement("w:lvlRestart")
            restart.set(qn("w:val"), str(level_index))
            level.append(restart)

        if heading_mode is not None:
            suffix = OxmlElement("w:suff")
            suffix.set(qn("w:val"), "nothing")
            level.append(suffix)

        text = OxmlElement("w:lvlText")
        text.set(qn("w:val"), _level_text(level_index, ordered, heading_mode))
        level.append(text)

        justification = OxmlElement("w:lvlJc")
        justification.set(qn("w:val"), "left")
        level.append(justification)

        paragraph_properties = OxmlElement("w:pPr")
        tabs = OxmlElement("w:tabs")
        tab = OxmlElement("w:tab")
        tab.set(qn("w:val"), "num")
        tab.set(qn("w:pos"), str(720 * (level_index + 1)))
        tabs.append(tab)
        paragraph_properties.append(tabs)
        indent = OxmlElement("w:ind")
        indent.set(qn("w:left"), str(720 * (level_index + 1)))
        indent.set(qn("w:hanging"), "360")
        paragraph_properties.append(indent)
        level.append(paragraph_properties)

        if not ordered and heading_mode is None:
            run_properties = OxmlElement("w:rPr")
            fonts = OxmlElement("w:rFonts")
            fonts.set(qn("w:ascii"), "Arial")
            fonts.set(qn("w:hAnsi"), "Arial")
            run_properties.append(fonts)
            level.append(run_properties)

        abstract.append(level)

    first_number = numbering.find(qn("w:num"))
    if first_number is None:
        numbering.append(abstract)
    else:
        numbering.insert(numbering.index(first_number), abstract)
    return abstract_id


def _level_text(level_index: int, ordered: bool, heading_mode: str | None) -> str:
    if heading_mode == "chinese":
        return ("%1、", "（%2）", "%3.", "（%4）")[level_index]
    if heading_mode == "decimal":
        return ".".join(f"%{index + 1}" for index in range(level_index + 1)) + " "
    if not ordered:
        return ("•", "o", "▪", "–")[level_index % 4]
    return ".".join(f"%{index + 1}" for index in range(level_index + 1)) + "."


def _new_number_instance(numbering, abstract_id: int) -> int:
    number_id = _next_id(numbering, "w:numId", "w:num")
    number = OxmlElement("w:num")
    number.set(qn("w:numId"), str(number_id))
    abstract_reference = OxmlElement("w:abstractNumId")
    abstract_reference.set(qn("w:val"), str(abstract_id))
    number.append(abstract_reference)
    numbering.append(number)
    return number_id


def create_heading_numbering(document, mode: str) -> int | None:
    """Create one native four-level heading numbering instance."""
    if mode == "none":
        return None
    numbering = document.part.numbering_part.element
    abstract_id = _new_abstract(
        numbering,
        ordered=mode == "decimal",
        levels=4,
        heading_mode=mode,
    )
    return _new_number_instance(numbering, abstract_id)


def create_multilevel_list_numbering(document, ordered: bool) -> int:
    """Create one independent nine-level list instance."""
    numbering = document.part.numbering_part.element
    abstract_id = _new_abstract(numbering, ordered=ordered, levels=9)
    return _new_number_instance(numbering, abstract_id)


def set_paragraph_numbering(paragraph, number_id: int, level: int = 0) -> None:
    """Attach a native numbering instance and zero-based level to a paragraph."""
    properties = paragraph._p.get_or_add_pPr()
    number_properties = properties.find(qn("w:numPr"))
    if number_properties is None:
        number_properties = OxmlElement("w:numPr")
        properties.append(number_properties)
    else:
        for child in list(number_properties):
            number_properties.remove(child)
    level_element = OxmlElement("w:ilvl")
    level_element.set(qn("w:val"), str(level))
    number_element = OxmlElement("w:numId")
    number_element.set(qn("w:val"), str(number_id))
    number_properties.append(level_element)
    number_properties.append(number_element)


__all__ = [
    "create_heading_numbering",
    "create_multilevel_list_numbering",
    "set_paragraph_numbering",
]
