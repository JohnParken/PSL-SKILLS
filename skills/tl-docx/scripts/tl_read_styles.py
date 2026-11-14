"""Resolve stored paragraph heading styles without rendering or mutation."""

import re

WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_styles(package, catalog=None):
    """Read just the fields needed for bounded heading inheritance."""
    if catalog is None:
        from tl_read_parts import PartCatalog  # pylint: disable=import-outside-toplevel
        catalog = PartCatalog(package, lambda *args, **kwargs: None)
    parts = catalog.locate('styles')
    result = {}
    for part in parts:
        root = catalog.xml(part, 'styles')
        if root is not None:
            _collect_styles(root, result, catalog, part)
    return result


def _collect_styles(root, result, catalog, part):
    for style in root.findall(WORD + "style"):
        identifier = style.get(WORD + "styleId")
        if not identifier:
            continue
        if identifier in result:
            catalog.issue('duplicate_style_definition', part, style_id=identifier)
            continue
        name = style.find(WORD + "name")
        parent = style.find(WORD + "basedOn")
        outline = style.find(WORD + "pPr/" + WORD + "outlineLvl")
        result[identifier] = {
            "name": None if name is None else name.get(WORD + "val"),
            "type": style.get(WORD + "type"),
            "based_on": None if parent is None else parent.get(WORD + "val"),
            "outline_level": None if outline is None else outline.get(WORD + "val"),
        }


def heading_outline(styles, identifier):
    """Follow finite basedOn chains; an explicit body outline stops inheritance."""
    seen = set()
    while identifier:
        if identifier in seen:
            return None, "heading_style_inheritance_cycle"
        if len(seen) >= 256:
            return None, "heading_style_inheritance_limit"
        seen.add(identifier)
        style = styles.get(identifier, {})
        if style.get("outline_level") is not None:
            return style["outline_level"], None
        for candidate in (style.get("name") or "", identifier):
            match = re.fullmatch(r"(?:heading|标题)[\s-]*([1-9])", candidate.strip(), re.IGNORECASE)
            if match:
                return str(int(match[1]) - 1), None
        identifier = style.get("based_on")
    return None, None
