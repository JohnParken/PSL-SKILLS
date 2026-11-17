"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import re
from datetime import datetime, timezone
from pathlib import (
    Path,
)
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import (
    escape,
)
from pptx_transitions import (
    serialize_source_xml,
)
from language_tags import (
    language_uses_rtl,
)
from ..drawingml.utils import (
    detect_text_lang,
)

def _rtl_text_levels(xml: str) -> str:
    """Flip template text-level defaults (``rtl="0"``) to right-to-left."""
    def flip(match: re.Match[str]) -> str:
        tag = match.group(0).replace('rtl="0"', 'rtl="1"')
        return tag.replace('algn="l"', 'algn="r"')

    return re.sub(r'<a:(?:lvl\dpPr|pPr)\b[^>]*\brtl="0"[^>]*>', flip, xml)


def _apply_template_text_language(extract_dir: Path, language: str) -> None:
    """Tag template defaults with the deck language and authored runs by script.

    Covers the presentation default text style (new text boxes) and master and
    layout placeholders, so proofing follows the deck rather than en-US; a
    right-to-left deck also gets right-to-left, right-aligned default levels.
    """
    from .builder import (
        DML_NS,
        PML_NS,
    )
    rtl = language_uses_rtl(language)
    ppt_dir = extract_dir / "ppt"
    parts = [ppt_dir / "presentation.xml"]
    parts += sorted((ppt_dir / "slideMasters").glob("slideMaster*.xml"))
    parts += sorted((ppt_dir / "slideLayouts").glob("slideLayout*.xml"))
    for part in parts:
        if not part.is_file():
            continue
        xml = part.read_text(encoding="utf-8")
        root = ET.fromstring(xml)
        language_updates = {
            props: language
            for tag in ("defRPr", "endParaRPr")
            for props in root.iter(f"{{{DML_NS}}}{tag}")
        }
        for tag in ("r", "fld"):
            for run in root.iter(f"{{{DML_NS}}}{tag}"):
                text = run.find(f"{{{DML_NS}}}t")
                if text is None or not (text.text or "").strip():
                    continue
                props = run.find(f"{{{DML_NS}}}rPr")
                if props is None:
                    props = ET.Element(f"{{{DML_NS}}}rPr")
                    run.insert(0, props)
                language_updates[props] = detect_text_lang(text.text, language)
        for shape in root.iter(f"{{{PML_NS}}}sp"):
            placeholder = shape.find(
                f"{{{PML_NS}}}nvSpPr/{{{PML_NS}}}nvPr/{{{PML_NS}}}ph"
            )
            if placeholder is not None and not any(
                (text.text or "").strip() for text in shape.iter(f"{{{DML_NS}}}t")
            ):
                for props in shape.iter(f"{{{DML_NS}}}rPr"):
                    language_updates[props] = language
        changed = False
        for props, text_language in language_updates.items():
            if props.get("lang") != text_language:
                props.set("lang", text_language)
                changed = True
        updated = serialize_source_xml(root, xml).decode("utf-8") if changed else xml
        if rtl:
            updated = _rtl_text_levels(updated)
        if updated != xml:
            part.write_text(updated, encoding="utf-8")


def _presentation_format(width: float, height: float) -> str:
    """Map the slide aspect ratio to PowerPoint's PresentationFormat label.
    Non-standard ratios (square, portrait, banner crops) report 'Custom'.
    """
    if width <= 0 or height <= 0:
        return 'Custom'
    ratio = width / height
    for target, label in (
        (4 / 3, 'On-screen Show (4:3)'),
        (16 / 9, 'On-screen Show (16:9)'),
        (16 / 10, 'On-screen Show (16:10)'),
    ):
        if abs(ratio - target) < 0.02:
            return label
    return 'Custom'


def _stamp_docprops(
    extract_dir: Path,
    slide_count: int,
    pres_format: str,
    meta: dict[str, Any] | None = None,
) -> None:
    """Overwrite the misleading python-pptx default metadata with accurate
    values. Factual fields (slide count, export timestamp, presentation format,
    application) are always machine-derived. Authored fields — including the
    title — come solely from an optional per-project ``metadata.json``
    (``meta``); whatever it omits stays blank. ``lastModifiedBy`` follows
    ``creator`` rather than ever carrying the base template's author or a tool
    name. No field is guessed from slide content: a blank title is preferable
    to an unreliable heuristic pick.
    """
    meta = meta or {}

    def field(key: str, default: str = '') -> str:
        value = meta.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else default

    title = field('title')
    creator = field('creator')

    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    core_path = extract_dir / 'docProps' / 'core.xml'
    if core_path.exists():
        core_path.write_text(
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
            '<cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(title)}</dc:title>'
            f'<dc:subject>{escape(field("subject"))}</dc:subject>'
            f'<dc:creator>{escape(creator)}</dc:creator>'
            f'<cp:keywords>{escape(field("keywords"))}</cp:keywords>'
            f'<dc:description>{escape(field("description"))}</dc:description>'
            f'<dc:language>{escape(field("language"))}</dc:language>'
            f'<cp:lastModifiedBy>{escape(creator)}</cp:lastModifiedBy>'
            '<cp:revision>1</cp:revision>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
            f'<cp:category>{escape(field("category"))}</cp:category>'
            f'<cp:contentStatus>{escape(field("contentStatus"))}</cp:contentStatus>'
            '</cp:coreProperties>',
            encoding='utf-8',
        )

    app_path = extract_dir / 'docProps' / 'app.xml'
    if app_path.exists():
        app = app_path.read_text(encoding='utf-8')
        app = re.sub(r'<Slides>.*?</Slides>', f'<Slides>{slide_count}</Slides>', app)
        app = re.sub(
            r'<Company>.*?</Company>',
            f'<Company>{escape(field("company"))}</Company>',
            app,
        )
        app = re.sub(
            r'<Manager>.*?</Manager>',
            f'<Manager>{escape(field("manager"))}</Manager>',
            app,
        )
        app = re.sub(
            r'<Application>.*?</Application>',
            '<Application>Microsoft Office PowerPoint</Application>',
            app,
        )
        app = re.sub(
            r'<PresentationFormat>.*?</PresentationFormat>',
            f'<PresentationFormat>{escape(pres_format)}</PresentationFormat>',
            app,
        )
        app_path.write_text(app, encoding='utf-8')
