"""Byte-format detection split from upstream pptx_workspace; no filesystem scanning."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET


def _sniff_zip_format(payload: bytes) -> str | None:
    """Return the semantic package family for one ZIP payload."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            names = set(package.namelist())
    except (OSError, zipfile.BadZipFile):
        return None
    if "xl/workbook.xml" in names or "xl/workbook.bin" in names:
        return "ooxml-xlsx"
    if "word/document.xml" in names:
        return "ooxml-docx"
    if "ppt/presentation.xml" in names:
        return "ooxml-pptx"
    if any(name.lower().endswith(".model") for name in names):
        return "3mf"
    return "zip"


def _sniff_resource_format(payload: bytes) -> str | None:
    """Identify common PPTX resource formats from their bytes."""
    header = payload[:64]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header.startswith(b"BM"):
        return "bmp"
    if header.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
        return "tiff"
    if header.startswith((b"II\xbc\x01", b"MM\x01\xbc")):
        return "wdp"
    if len(header) >= 44 and header[40:44] == b" EMF":
        return "emf"
    if header.startswith(b"\xd7\xcd\xc6\x9a"):
        return "wmf"
    if (
        len(header) >= 6
        and header[:2] in {b"\x01\x00", b"\x02\x00"}
        and header[2:4] == b"\x09\x00"
    ):
        return "wmf"
    if header.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    if header.startswith(b"%PDF-"):
        return "pdf"
    if header.startswith(b"%!PS-Adobe-"):
        return "postscript"
    if len(header) >= 12 and header[:4] in {b"RIFF", b"RF64"}:
        form = header[8:12]
        if form == b"WAVE":
            return "wav"
        if form == b"AVI ":
            return "avi"
        if form == b"WEBP":
            return "webp"
    if header.startswith(b"FORM") and header[8:12] in {b"AIFF", b"AIFC"}:
        return "aiff"
    if header.startswith(b"fLaC"):
        return "flac"
    if header.startswith(b"OggS"):
        return "ogg"
    if header.startswith(b"ID3") or (
        len(header) >= 2
        and header[0] == 0xFF
        and header[1] & 0xE0 == 0xE0
        and header[1] & 0x06 != 0
    ):
        return "mp3"
    if (
        len(header) >= 2
        and header[0] == 0xFF
        and header[1] & 0xF6 == 0xF0
    ):
        return "aac"
    if header.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
        return "asf"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "ebml"
    if header.startswith(b"glTF"):
        return "glb"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        box_size = int.from_bytes(header[:4], "big")
        brands = {
            header[offset:offset + 4]
            for offset in range(8, min(len(header), box_size or len(header)), 4)
        }
        if brands & {b"avif", b"avis"}:
            return "avif"
        return "iso-bmff"
    if header.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3")):
        return "mpeg"
    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"
    if header.startswith(b"PK"):
        return _sniff_zip_format(payload)
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    if root.tag == "svg" or root.tag.endswith("}svg"):
        return "svg"
    return None
