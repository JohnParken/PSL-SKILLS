"""Assemble an editable PPTX from converted slides using only the standard library.

This module owns the Open Packaging Conventions shell. Slide drawing content is
supplied by the adapted PPT Master converter; it is preserved without rewriting
namespace prefixes or Office compatibility attributes.
"""

from __future__ import annotations

import io
import math
import posixpath
import re
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET


PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
RELATIONSHIP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CONTENT_TYPE_BASE = "application/vnd.openxmlformats-officedocument."
EMU_PER_PIXEL = 9525
MIN_SLIDE_EMU = 914400
MAX_SLIDE_EMU = 51206400
MAX_SLIDES = 200
MAX_PART_BYTES = 32 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_PARTS = 10000
REL_BASE = RELATIONSHIP_NS + "/"
CHARTEX_REL = "http://schemas.microsoft.com/office/2014/relationships/chartEx"
CHART_STYLE_REL = "http://schemas.microsoft.com/office/2011/relationships/chartStyle"
CHART_COLORS_REL = "http://schemas.microsoft.com/office/2011/relationships/chartColorStyle"
CORE_PROPERTIES_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
RELATIONSHIP_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
PART_NAME_RE = re.compile(r"[A-Za-z0-9_.\-/]+\Z")
INTERNAL_RELATIONSHIP_TYPES = frozenset(
    REL_BASE + name
    for name in (
        "officeDocument", "extended-properties", "slideMaster", "slideLayout",
        "theme", "slide", "notesMaster", "notesSlide", "tableStyles", "image",
        "chart", "package",
    )
) | {CORE_PROPERTIES_REL, CHARTEX_REL, CHART_STYLE_REL, CHART_COLORS_REL}
MEDIA_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}
DEFAULT_CONTENT_TYPES = {
    "rels": "application/vnd.openxmlformats-package.relationships+xml",
    "xml": "application/xml",
    "xlsx": CONTENT_TYPE_BASE + "spreadsheetml.sheet",
    **MEDIA_CONTENT_TYPES,
}
SHARED_NAMESPACES = {
    "xmlns:p": PRESENTATION_NS,
    "xmlns:a": DRAWING_NS,
    "xmlns:r": RELATIONSHIP_NS,
}
COLOR_MAP = {
    "bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2",
    "accent1": "accent1", "accent2": "accent2", "accent3": "accent3",
    "accent4": "accent4", "accent5": "accent5", "accent6": "accent6",
    "hlink": "hlink", "folHlink": "folHlink",
}


class PackageError(ValueError):
    """The supplied content cannot form a supported, self-contained PPTX."""


@dataclass(frozen=True)
class ConvertedSlide:
    """A converter result together with optional plain-text speaker notes."""

    xml: str
    media: Mapping[str, bytes] = field(default_factory=dict)
    relationships: Sequence[Mapping[str, str]] = field(default_factory=tuple)
    package_parts: Mapping[str, bytes] = field(default_factory=dict)
    content_types: Mapping[str, str] = field(default_factory=dict)
    notes: str = ""

    @classmethod
    def from_conversion(
        cls,
        result: tuple,
        *,
        notes: str = "",
    ) -> ConvertedSlide:
        """Adapt PPT Master's six-value conversion result; omit animation data."""
        if len(result) != 6:
            raise PackageError("Expected a six-value SVG conversion result")
        slide_xml, media, relationships, _animation_targets, parts, types = result
        return cls(slide_xml, media, relationships, parts, types, notes)


def _child(
    parent: ET.Element,
    tag: str,
    attributes: Mapping[str, str] | None = None,
    text: str | None = None,
) -> ET.Element:
    element = ET.SubElement(parent, tag, dict(attributes or {}))
    if text is not None:
        _validate_text(text, tag)
        element.text = text
    return element


def _xml(element: ET.Element) -> bytes:
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def _validate_text(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise PackageError(f"{label} must be a string")
    for character in value:
        point = ord(character)
        if point in (9, 10, 13) or 32 <= point <= 0xD7FF or 0xE000 <= point <= 0xFFFD or 0x10000 <= point <= 0x10FFFF:
            continue
        raise PackageError(f"{label} contains a character forbidden in XML 1.0")


def _parse_xml(payload: bytes, label: str) -> ET.Element:
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper or b"\x00" in payload:
        raise PackageError(f"DTD, entities and non-UTF-8 XML are unsupported: {label}")
    try:
        return ET.fromstring(payload)
    except (ET.ParseError, ValueError) as error:
        raise PackageError(f"Invalid XML in {label}") from error


def _part_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or not PART_NAME_RE.fullmatch(name)
        or name.startswith("/")
        or any(segment in ("", ".", "..") for segment in name.split("/"))
    ):
        raise PackageError(f"Invalid package part name: {name!r}")
    return name


def _slide_dimension(value: float, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise PackageError(f"{label} must be a finite pixel dimension")
    try:
        numeric = float(value)
    except OverflowError as error:
        raise PackageError(f"{label} is too large") from error
    if not math.isfinite(numeric):
        raise PackageError(f"{label} must be a finite pixel dimension")
    result = round(numeric * EMU_PER_PIXEL)
    if not MIN_SLIDE_EMU <= result <= MAX_SLIDE_EMU:
        raise PackageError(f"{label} must be between 96 and 5376 pixels (1–56 inches)")
    return result


def _shape_tree(parent: ET.Element) -> ET.Element:
    shape_tree = _child(parent, "p:spTree")
    nonvisual = _child(shape_tree, "p:nvGrpSpPr")
    _child(nonvisual, "p:cNvPr", {"id": "1", "name": ""})
    _child(nonvisual, "p:cNvGrpSpPr")
    _child(nonvisual, "p:nvPr")
    properties = _child(shape_tree, "p:grpSpPr")
    transform = _child(properties, "a:xfrm")
    for tag, first, second in (
        ("a:off", "x", "y"), ("a:ext", "cx", "cy"),
        ("a:chOff", "x", "y"), ("a:chExt", "cx", "cy"),
    ):
        _child(transform, tag, {first: "0", second: "0"})
    return shape_tree


def _text_style(parent: ET.Element, tag: str, size: str = "1800") -> None:
    style = _child(parent, tag)
    paragraph = _child(style, "a:lvl1pPr", {"algn": "l", "defTabSz": "914400"})
    properties = _child(paragraph, "a:defRPr", {"sz": size, "kern": "1200"})
    solid_fill = _child(properties, "a:solidFill")
    _child(solid_fill, "a:schemeClr", {"val": "tx1"})
    _child(properties, "a:latin", {"typeface": "+mn-lt"})
    _child(properties, "a:ea", {"typeface": "+mn-ea"})
    _child(properties, "a:cs", {"typeface": "+mn-cs"})


def _master_xml() -> bytes:
    root = ET.Element("p:sldMaster", SHARED_NAMESPACES)
    common = _child(root, "p:cSld", {"name": "PSL"})
    background = _child(common, "p:bg")
    properties = _child(background, "p:bgPr")
    fill = _child(properties, "a:solidFill")
    _child(fill, "a:srgbClr", {"val": "FFFFFF"})
    _child(properties, "a:effectLst")
    _shape_tree(common)
    _child(root, "p:clrMap", COLOR_MAP)
    layouts = _child(root, "p:sldLayoutIdLst")
    _child(layouts, "p:sldLayoutId", {"id": "2147483649", "r:id": "rId1"})
    styles = _child(root, "p:txStyles")
    _text_style(styles, "p:titleStyle", "4400")
    _text_style(styles, "p:bodyStyle")
    _text_style(styles, "p:otherStyle")
    return _xml(root)


def _layout_xml() -> bytes:
    root = ET.Element("p:sldLayout", {**SHARED_NAMESPACES, "type": "blank", "preserve": "1"})
    common = _child(root, "p:cSld", {"name": "Blank"})
    _shape_tree(common)
    override = _child(root, "p:clrMapOvr")
    _child(override, "a:masterClrMapping")
    return _xml(root)


def _theme_xml() -> bytes:
    root = ET.Element("a:theme", {"xmlns:a": DRAWING_NS, "name": "PSL"})
    elements = _child(root, "a:themeElements")
    colors = _child(elements, "a:clrScheme", {"name": "PSL"})
    for name, color in (
        ("dk1", "000000"), ("lt1", "FFFFFF"), ("dk2", "1F2937"), ("lt2", "F3F4F6"),
        ("accent1", "2563EB"), ("accent2", "0D9488"), ("accent3", "F59E0B"),
        ("accent4", "7C3AED"), ("accent5", "DB2777"), ("accent6", "64748B"),
        ("hlink", "0563C1"), ("folHlink", "954F72"),
    ):
        color_element = _child(colors, "a:" + name)
        _child(color_element, "a:srgbClr", {"val": color})
    fonts = _child(elements, "a:fontScheme", {"name": "PSL"})
    for font_tag in ("a:majorFont", "a:minorFont"):
        font = _child(fonts, font_tag)
        _child(font, "a:latin", {"typeface": "Arial"})
        _child(font, "a:ea", {"typeface": ""})
        _child(font, "a:cs", {"typeface": ""})
    formats = _child(elements, "a:fmtScheme", {"name": "PSL"})
    fills = _child(formats, "a:fillStyleLst")
    for _ in range(3):
        fill = _child(fills, "a:solidFill")
        _child(fill, "a:schemeClr", {"val": "phClr"})
    lines = _child(formats, "a:lnStyleLst")
    for width in ("6350", "12700", "19050"):
        line = _child(lines, "a:ln", {"w": width, "cap": "flat", "cmpd": "sng", "algn": "ctr"})
        fill = _child(line, "a:solidFill")
        _child(fill, "a:schemeClr", {"val": "phClr"})
        _child(line, "a:prstDash", {"val": "solid"})
        _child(line, "a:miter", {"lim": "800000"})
    effects = _child(formats, "a:effectStyleLst")
    for _ in range(3):
        effect = _child(effects, "a:effectStyle")
        _child(effect, "a:effectLst")
    backgrounds = _child(formats, "a:bgFillStyleLst")
    for _ in range(3):
        fill = _child(backgrounds, "a:solidFill")
        _child(fill, "a:schemeClr", {"val": "phClr"})
    return _xml(root)


def _notes_placeholder(
    tree: ET.Element,
    shape_id: str,
    name: str,
    kind: str,
    *,
    text: str | None = None,
    geometry: tuple[int, int, int, int] | None = None,
) -> None:
    shape = _child(tree, "p:sp")
    nonvisual = _child(shape, "p:nvSpPr")
    _child(nonvisual, "p:cNvPr", {"id": shape_id, "name": name})
    properties = _child(nonvisual, "p:cNvSpPr")
    _child(properties, "a:spLocks", {"noGrp": "1"})
    other = _child(nonvisual, "p:nvPr")
    placeholder = {"type": kind}
    if kind == "body":
        placeholder["idx"] = "1"
    _child(other, "p:ph", placeholder)
    properties = _child(shape, "p:spPr")
    if geometry is not None:
        x_position, y_position, width, height = geometry
        transform = _child(properties, "a:xfrm")
        _child(transform, "a:off", {"x": str(x_position), "y": str(y_position)})
        _child(transform, "a:ext", {"cx": str(width), "cy": str(height)})
        preset = _child(properties, "a:prstGeom", {"prst": "rect"})
        _child(preset, "a:avLst")
    if text is not None:
        body = _child(shape, "p:txBody")
        _child(body, "a:bodyPr")
        _child(body, "a:lstStyle")
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            paragraph = _child(body, "a:p")
            if line:
                run = _child(paragraph, "a:r")
                _child(run, "a:rPr", {"lang": "en-US", "dirty": "0"})
                _child(run, "a:t", {"xml:space": "preserve"}, line)
            _child(paragraph, "a:endParaRPr", {"lang": "en-US"})


def _notes_master_xml(width: int, height: int) -> bytes:
    root = ET.Element("p:notesMaster", SHARED_NAMESPACES)
    common = _child(root, "p:cSld")
    tree = _shape_tree(common)
    image_scale = min(5486400 / width, 3086100 / height)
    image_width = round(width * image_scale)
    image_height = round(height * image_scale)
    image_x = 685800 + (5486400 - image_width) // 2
    image_y = 685800 + (3086100 - image_height) // 2
    _notes_placeholder(tree, "2", "Slide image", "sldImg", geometry=(image_x, image_y, image_width, image_height))
    _notes_placeholder(tree, "3", "Notes", "body", text="", geometry=(685800, 4114800, 5486400, 4343400))
    _child(root, "p:clrMap", COLOR_MAP)
    _text_style(root, "p:notesStyle", "1200")
    return _xml(root)


def _notes_xml(text: str) -> bytes:
    root = ET.Element("p:notes", SHARED_NAMESPACES)
    common = _child(root, "p:cSld")
    tree = _shape_tree(common)
    _notes_placeholder(tree, "2", "Slide image", "sldImg")
    _notes_placeholder(tree, "3", "Notes", "body", text=text)
    override = _child(root, "p:clrMapOvr")
    _child(override, "a:masterClrMapping")
    return _xml(root)


def _relationship(identifier: str, kind: str, target: str) -> dict[str, str]:
    return {"id": identifier, "type": REL_BASE + kind, "target": target}


def _relationships_xml(entries: Sequence[Mapping[str, str]]) -> bytes:
    root = ET.Element("Relationships", {"xmlns": PACKAGE_REL_NS})
    identifiers = set()
    for entry in entries:
        if set(entry) - {"id", "type", "target", "target_mode"} or not {"id", "type", "target"} <= set(entry):
            raise PackageError("Invalid relationship fields")
        identifier = entry["id"]
        if not isinstance(identifier, str) or not RELATIONSHIP_ID_RE.fullmatch(identifier) or identifier in identifiers:
            raise PackageError("Duplicate or invalid relationship ID")
        identifiers.add(identifier)
        for value in entry.values():
            _validate_text(value, "relationship")
        attributes = {"Id": identifier, "Type": entry["type"], "Target": entry["target"]}
        if "target_mode" in entry:
            attributes["TargetMode"] = entry["target_mode"]
        _child(root, "Relationship", attributes)
    return _xml(root)


def _presentation_xml(slide_count: int, width: int, height: int, notes: bool) -> bytes:
    root = ET.Element("p:presentation", SHARED_NAMESPACES)
    masters = _child(root, "p:sldMasterIdLst")
    _child(masters, "p:sldMasterId", {"id": "2147483648", "r:id": "rId1"})
    if notes:
        note_masters = _child(root, "p:notesMasterIdLst")
        _child(note_masters, "p:notesMasterId", {"r:id": f"rId{slide_count + 3}"})
    slides = _child(root, "p:sldIdLst")
    for index in range(1, slide_count + 1):
        _child(slides, "p:sldId", {"id": str(255 + index), "r:id": f"rId{index + 1}"})
    _child(root, "p:sldSz", {"cx": str(width), "cy": str(height), "type": "custom"})
    _child(root, "p:notesSz", {"cx": "6858000", "cy": "9144000"})
    _text_style(root, "p:defaultTextStyle")
    return _xml(root)


def _core_properties_xml(title: str) -> bytes:
    root = ET.Element("cp:coreProperties", {
        "xmlns:cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
        "xmlns:dc": "http://purl.org/dc/elements/1.1/",
    })
    _child(root, "dc:title", text=title)
    _child(root, "dc:creator", text="PSL PPTX")
    _child(root, "cp:lastModifiedBy", text="PSL PPTX")
    _child(root, "cp:revision", text="1")
    return _xml(root)


def _app_properties_xml(slide_count: int, notes_count: int) -> bytes:
    root = ET.Element("Properties", {"xmlns": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"})
    _child(root, "Application", text="PSL PPTX")
    _child(root, "PresentationFormat", text="Custom")
    _child(root, "Slides", text=str(slide_count))
    _child(root, "Notes", text=str(notes_count))
    _child(root, "HiddenSlides", text="0")
    _child(root, "MMClips", text="0")
    _child(root, "ScaleCrop", text="false")
    _child(root, "Company", text="")
    _child(root, "LinksUpToDate", text="false")
    _child(root, "SharedDoc", text="false")
    _child(root, "HyperlinksChanged", text="false")
    _child(root, "AppVersion", text="1.0")
    return _xml(root)


def _content_types_xml(parts: Mapping[str, bytes], overrides: Mapping[str, str]) -> bytes:
    root = ET.Element("Types", {"xmlns": CONTENT_TYPES_NS})
    extensions = {name.rsplit(".", 1)[-1] for name in parts}
    for extension in sorted(extensions):
        if extension in DEFAULT_CONTENT_TYPES:
            _child(root, "Default", {"Extension": extension, "ContentType": DEFAULT_CONTENT_TYPES[extension]})
    for name, content_type in sorted(overrides.items()):
        if name not in parts:
            raise PackageError(f"Content type references missing part: {name}")
        _validate_text(content_type, "content type")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-/]+", content_type):
            raise PackageError("Invalid content type")
        _child(root, "Override", {"PartName": "/" + name, "ContentType": content_type})
    for name in parts:
        if name not in overrides and name.rsplit(".", 1)[-1] not in DEFAULT_CONTENT_TYPES:
            raise PackageError(f"No content type for package part: {name}")
    return _xml(root)


def _relationship_source(name: str) -> str:
    if name == "_rels/.rels":
        return ""
    directory, filename = posixpath.split(name)
    if posixpath.basename(directory) != "_rels" or not filename.endswith(".rels"):
        raise PackageError(f"Invalid relationships part: {name}")
    return posixpath.join(posixpath.dirname(directory), filename[:-5])


def _validate_relationship_graph(parts: Mapping[str, bytes]) -> None:
    relation_ids: dict[str, set[str]] = {}
    parsed_xml: dict[str, ET.Element] = {}
    for name, payload in parts.items():
        if not name.endswith((".xml", ".rels")):
            continue
        root = _parse_xml(payload, name)
        parsed_xml[name] = root
        if not name.endswith(".rels"):
            continue
        if root.tag != f"{{{PACKAGE_REL_NS}}}Relationships":
            raise PackageError(f"Invalid relationships root in {name}")
        source = _relationship_source(name)
        if source and source not in parts:
            raise PackageError(f"Relationships source part is missing: {source}")
        identifiers: set[str] = set()
        relation_ids[source] = identifiers
        for relationship in root:
            if relationship.tag != f"{{{PACKAGE_REL_NS}}}Relationship":
                raise PackageError(f"Unsupported relationships child in {name}")
            identifier = relationship.get("Id", "")
            kind = relationship.get("Type", "")
            target = relationship.get("Target", "")
            mode = relationship.get("TargetMode", "Internal")
            if not RELATIONSHIP_ID_RE.fullmatch(identifier) or identifier in identifiers:
                raise PackageError(f"Duplicate or invalid relationship ID in {name}")
            identifiers.add(identifier)
            if not target or any(ord(character) < 32 or ord(character) == 127 for character in target):
                raise PackageError(f"Invalid relationship target in {name}")
            try:
                parsed_target = urlsplit(target)
            except ValueError as error:
                raise PackageError(f"Invalid relationship target in {name}") from error
            if mode == "External":
                if kind != REL_BASE + "hyperlink" or parsed_target.scheme not in ("https", "http", "mailto"):
                    raise PackageError(f"Unsupported external relationship in {name}")
                if parsed_target.scheme in ("https", "http") and not parsed_target.netloc:
                    raise PackageError(f"Invalid HTTP hyperlink in {name}")
                if parsed_target.scheme == "mailto" and not parsed_target.path:
                    raise PackageError(f"Invalid mail hyperlink in {name}")
                continue
            if mode != "Internal" or kind not in INTERNAL_RELATIONSHIP_TYPES:
                raise PackageError(f"Unsupported relationship type or target mode in {name}")
            if (
                parsed_target.scheme or parsed_target.netloc or parsed_target.query or parsed_target.fragment
                or not PART_NAME_RE.fullmatch(target) or target.startswith("/")
            ):
                raise PackageError(f"Invalid internal relationship in {name}")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
            if resolved not in parts or resolved.startswith("../"):
                raise PackageError(f"Missing relationship target in {name}: {target}")
    for name, root in parsed_xml.items():
        if name.endswith(".rels"):
            continue
        available = relation_ids.get(name, set())
        for element in root.iter():
            for attribute, value in element.attrib.items():
                if attribute.startswith("{" + RELATIONSHIP_NS + "}") and value not in available:
                    raise PackageError(f"Unresolved relationship reference in {name}: {value}")


def _validate_slide(payload: bytes) -> None:
    root = _parse_xml(payload, "converted slide")
    if root.tag != f"{{{PRESENTATION_NS}}}sld" or root.find(f"{{{PRESENTATION_NS}}}cSld/{{{PRESENTATION_NS}}}spTree") is None:
        raise PackageError("Converted slide must contain a PresentationML slide and shape tree")
    identifiers = set()
    for properties in root.iter(f"{{{PRESENTATION_NS}}}cNvPr"):
        identifier = properties.get("id", "")
        if not re.fullmatch(r"[0-9]+", identifier) or not 1 <= int(identifier) <= 4294967295 or int(identifier) in identifiers:
            raise PackageError("Converted slide contains duplicate or invalid shape IDs")
        identifiers.add(int(identifier))


def build_presentation(
    slides: Sequence[ConvertedSlide],
    *,
    width: float = 1280,
    height: float = 720,
    title: str = "Presentation",
) -> bytes:
    """Build a self-contained PPTX in memory, validating its package graph.

    Dimensions are CSS pixels at 96 dpi. Existing files are never read or
    written. Desktop Office rendering and schema conformance are not asserted
    by this structural package check.
    """
    if not 1 <= len(slides) <= MAX_SLIDES:
        raise PackageError(f"A presentation must contain 1–{MAX_SLIDES} slides")
    width_emu = _slide_dimension(width, "width")
    height_emu = _slide_dimension(height, "height")
    _validate_text(title, "title")
    parts: dict[str, bytes] = {}
    overrides: dict[str, str] = {}
    folded_names: dict[str, str] = {}
    total_bytes = 0

    def add_part(name: str, payload: bytes, content_type: str | None = None) -> None:
        nonlocal total_bytes
        _part_name(name)
        if not isinstance(payload, bytes) or len(payload) > MAX_PART_BYTES:
            raise PackageError(f"Invalid or oversized package payload: {name}")
        if name.casefold() in folded_names and folded_names[name.casefold()] != name:
            raise PackageError(f"Case-insensitive package part collision: {name}")
        existing = parts.get(name)
        if existing is not None and existing != payload:
            raise PackageError(f"Package part collision: {name}")
        if content_type is not None:
            if name in overrides and overrides[name] != content_type:
                raise PackageError(f"Content type collision: {name}")
            overrides[name] = content_type
        if existing is not None:
            return
        total_bytes += len(payload)
        if total_bytes > MAX_PACKAGE_BYTES or len(parts) >= MAX_PARTS:
            raise PackageError("Presentation exceeds package resource limits")
        parts[name] = payload
        folded_names[name.casefold()] = name

    def add_xml(name: str, payload: bytes, suffix: str) -> None:
        add_part(name, payload, CONTENT_TYPE_BASE + suffix)

    for slide in slides:
        if not isinstance(slide, ConvertedSlide):
            raise PackageError("slides must contain ConvertedSlide instances")
        _validate_text(slide.notes, "notes")
    notes_count = sum(bool(slide.notes) for slide in slides)
    add_xml("ppt/presentation.xml", _presentation_xml(len(slides), width_emu, height_emu, bool(notes_count)), "presentationml.presentation.main+xml")
    presentation_rels = [_relationship("rId1", "slideMaster", "slideMasters/slideMaster1.xml")]
    for index in range(1, len(slides) + 1):
        presentation_rels.append(_relationship(f"rId{index + 1}", "slide", f"slides/slide{index}.xml"))
    presentation_rels.append(_relationship(f"rId{len(slides) + 2}", "tableStyles", "tableStyles.xml"))
    if notes_count:
        presentation_rels.append(_relationship(f"rId{len(slides) + 3}", "notesMaster", "notesMasters/notesMaster1.xml"))
    add_part("ppt/_rels/presentation.xml.rels", _relationships_xml(presentation_rels))
    add_xml("ppt/slideMasters/slideMaster1.xml", _master_xml(), "presentationml.slideMaster+xml")
    add_part("ppt/slideMasters/_rels/slideMaster1.xml.rels", _relationships_xml([
        _relationship("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
        _relationship("rId2", "theme", "../theme/theme1.xml"),
    ]))
    add_xml("ppt/slideLayouts/slideLayout1.xml", _layout_xml(), "presentationml.slideLayout+xml")
    add_part("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _relationships_xml([
        _relationship("rId1", "slideMaster", "../slideMasters/slideMaster1.xml"),
    ]))
    add_xml("ppt/theme/theme1.xml", _theme_xml(), "theme+xml")
    table_styles = ET.Element("a:tblStyleLst", {"xmlns:a": DRAWING_NS, "def": "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"})
    add_xml("ppt/tableStyles.xml", _xml(table_styles), "presentationml.tableStyles+xml")
    if notes_count:
        add_xml("ppt/notesMasters/notesMaster1.xml", _notes_master_xml(width_emu, height_emu), "presentationml.notesMaster+xml")
        add_part("ppt/notesMasters/_rels/notesMaster1.xml.rels", _relationships_xml([
            _relationship("rId1", "theme", "../theme/theme1.xml"),
        ]))
    for index, slide in enumerate(slides, 1):
        _validate_text(slide.xml, "slide XML")
        payload = slide.xml.encode("utf-8")
        _validate_slide(payload)
        add_xml(f"ppt/slides/slide{index}.xml", payload, "presentationml.slide+xml")
        relationships = [_relationship("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]
        relationships.extend(slide.relationships)
        for name, media in slide.media.items():
            if "/" in name or name.rsplit(".", 1)[-1] not in MEDIA_CONTENT_TYPES:
                raise PackageError(f"Unsupported media filename: {name}")
            add_part("ppt/media/" + name, media)
        for name, part in slide.package_parts.items():
            if not name.startswith(("ppt/charts/", "ppt/embeddings/")):
                raise PackageError(f"Unsupported auxiliary package part: {name}")
            if name.startswith("ppt/embeddings/") and not name.endswith(".xlsx"):
                raise PackageError("Only XLSX chart workbooks can be embedded")
            add_part(name, part, slide.content_types.get(name))
        if set(slide.content_types) - set(slide.package_parts):
            raise PackageError("Content type override has no matching auxiliary part")
        if slide.notes:
            used_ids = {entry["id"] for entry in relationships}
            next_index = 2
            while f"rId{next_index}" in used_ids:
                next_index += 1
            relationships.append(_relationship(f"rId{next_index}", "notesSlide", f"../notesSlides/notesSlide{index}.xml"))
            add_xml(f"ppt/notesSlides/notesSlide{index}.xml", _notes_xml(slide.notes), "presentationml.notesSlide+xml")
            add_part(f"ppt/notesSlides/_rels/notesSlide{index}.xml.rels", _relationships_xml([
                _relationship("rId1", "notesMaster", "../notesMasters/notesMaster1.xml"),
                _relationship("rId2", "slide", f"../slides/slide{index}.xml"),
            ]))
        add_part(f"ppt/slides/_rels/slide{index}.xml.rels", _relationships_xml(relationships))
    add_part("docProps/core.xml", _core_properties_xml(title), "application/vnd.openxmlformats-package.core-properties+xml")
    add_xml("docProps/app.xml", _app_properties_xml(len(slides), notes_count), "extended-properties+xml")
    add_part("_rels/.rels", _relationships_xml([
        _relationship("rId1", "officeDocument", "ppt/presentation.xml"),
        {"id": "rId2", "type": CORE_PROPERTIES_REL, "target": "docProps/core.xml"},
        _relationship("rId3", "extended-properties", "docProps/app.xml"),
    ]))
    _validate_relationship_graph(parts)
    content_types = _content_types_xml(parts, overrides)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in [("[Content_Types].xml", content_types), *sorted(parts.items())]:
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.create_system = 3
            member.external_attr = 0o100644 << 16
            archive.writestr(member, payload)
    return stream.getvalue()
