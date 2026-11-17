"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .elements import ProjectImageSource

import base64
import binascii
import io
import re
from pathlib import Path
from urllib.parse import unquote_to_bytes
from xml.etree import ElementTree as ET

from resource_paths import (
    resolve_external_image_reference,
    svg_image_payload_error,
)

from .utils import (
    SVG_NS,
    XLINK_NS,
    parse_inline_style,
    parse_project_geometry_length,
)



# Adapted from elements.py; upstream MIT, Hugo He, 2025-2026.
def _resolve_external_image(
    svg_dir: Path,
    href: str,
    resource_root: Path | None = None,
) -> Path:
    """Resolve a non-data-URI image href to a file on disk.

    The href is interpreted exactly relative to the owning SVG and must remain
    inside the project. No root, ``images/``, or template-path guessing occurs.
    """
    candidate = resolve_external_image_reference(
        svg_dir,
        href,
        project_root=resource_root,
    )
    if candidate is not None:
        return candidate
    raise FileNotFoundError(f'External image not found: {href}')


def _normalize_project_image_format(raw: str) -> str | None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _PROJECT_IMAGE_FORMATS,
    )
    return _PROJECT_IMAGE_FORMATS.get(raw.strip().lower().lstrip('.'))


def _little_uint(data: bytes, offset: int, size: int) -> int:
    return int.from_bytes(data[offset:offset + size], 'little', signed=False)


def _valid_emf_payload(data: bytes) -> bool:
    """Validate the EMF header and complete bounded record stream."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _little_uint,
    )
    if len(data) < 88:
        return False
    header_size = _little_uint(data, 4, 4)
    total_size = _little_uint(data, 48, 4)
    record_count = _little_uint(data, 52, 4)
    header_palette_entries = _little_uint(data, 68, 4)
    if (
        _little_uint(data, 0, 4) != 1
        or data[40:44] != b' EMF'
        or header_size < 88
        or header_size > total_size
        or total_size != len(data)
        or record_count < 1
    ):
        return False

    offset = 0
    count = 0
    last_type = 0
    last_size = 0
    while offset < total_size:
        if offset + 8 > total_size:
            return False
        record_type = _little_uint(data, offset, 4)
        record_size = _little_uint(data, offset + 4, 4)
        if record_size < 8 or record_size % 4 or offset + record_size > total_size:
            return False
        record_end = offset + record_size
        if count == 0 and (record_type != 1 or record_size != header_size):
            return False
        if count > 0 and record_type == 1:
            return False
        if record_type == 14:
            if (
                record_size < 20
                or record_end != total_size
                or _little_uint(data, record_end - 4, 4) != record_size
            ):
                return False
            palette_entries = _little_uint(data, offset + 8, 4)
            palette_offset = _little_uint(data, offset + 12, 4)
            if (
                palette_entries != header_palette_entries
                or palette_entries and (
                    palette_offset < 16
                    or palette_offset + palette_entries * 4 > record_size - 4
                )
            ):
                return False
        offset = record_end
        count += 1
        last_type = record_type
        last_size = record_size
    return (
        offset == total_size
        # MS-EMF counts all records. LibreOffice-generated EMF files in the
        # wild count records after the header, so retain that interoperable
        # spelling while keeping the complete stream bounded.
        and count in {record_count, record_count + 1}
        and last_type == 14
        and last_size >= 20
    )


def _valid_wmf_payload(data: bytes) -> bool:
    """Validate a standard or placeable WMF header and record stream."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _little_uint,
    )
    meta_offset = 0
    if data.startswith(b'\xd7\xcd\xc6\x9a'):
        if len(data) < 40:
            return False
        checksum = 0
        for offset in range(0, 20, 2):
            checksum ^= _little_uint(data, offset, 2)
        if checksum != _little_uint(data, 20, 2):
            return False
        meta_offset = 22
    if len(data) < meta_offset + 24:
        return False
    meta_type = _little_uint(data, meta_offset, 2)
    header_words = _little_uint(data, meta_offset + 2, 2)
    version = _little_uint(data, meta_offset + 4, 2)
    total_words = _little_uint(data, meta_offset + 6, 4)
    max_record_words = _little_uint(data, meta_offset + 12, 4)
    if (
        meta_type not in {1, 2}
        or header_words != 9
        or version not in {0x0100, 0x0300}
        or total_words < 12
        or max_record_words < 3
    ):
        return False
    total_end = meta_offset + total_words * 2
    if total_end != len(data):
        return False

    offset = meta_offset + header_words * 2
    last_function = -1
    last_record_words = 0
    observed_max_record_words = 0
    while offset < total_end:
        if offset + 6 > total_end:
            return False
        record_words = _little_uint(data, offset, 4)
        function = _little_uint(data, offset + 4, 2)
        if (
            record_words < 3
            or record_words > max_record_words
            or offset + record_words * 2 > total_end
        ):
            return False
        record_end = offset + record_words * 2
        if function == 0 and (record_words != 3 or record_end != total_end):
            return False
        offset = record_end
        last_function = function
        last_record_words = record_words
        observed_max_record_words = max(
            observed_max_record_words,
            record_words,
        )
    return (
        offset == total_end
        and last_function == 0
        and last_record_words == 3
        and observed_max_record_words == max_record_words
    )


def _valid_project_image_payload(img_format: str, img_data: bytes) -> bool:
    """Return whether bytes are a supported image of the declared format."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _PIL_IMAGE_FORMATS,
        _valid_emf_payload,
        _valid_wmf_payload,
    )
    if not img_data:
        return False
    if img_format == 'svg':
        return svg_image_payload_error(img_data) is None
    if img_format == 'emf':
        return _valid_emf_payload(img_data)
    if img_format == 'wmf':
        return _valid_wmf_payload(img_data)

    expected = _PIL_IMAGE_FORMATS.get(img_format)
    if expected is None:
        return False
    try:
        from PIL import Image, UnidentifiedImageError  # type: ignore
    except ImportError:
        return False
    try:
        with Image.open(io.BytesIO(img_data)) as image:
            actual = (image.format or '').upper()
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return False
    if expected == 'JPEG' and actual == 'MPO':
        # A camera multi-picture JPEG is a JPEG stream followed by sibling
        # frames; every JPEG decoder renders its primary frame.
        return True
    return actual == expected


def _decode_data_image_uri(href: str) -> tuple[str, bytes] | None:
    """Decode and validate one closed-project image data URI."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _normalize_project_image_format,
        _valid_project_image_payload,
    )
    if not href.startswith('data:') or ',' not in href:
        return None

    header, payload = href.split(',', 1)
    match = re.fullmatch(
        r'data:image/([A-Za-z0-9.+-]+)(?:;[^;,]*)*?(?:;base64)?',
        header,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    img_format = _normalize_project_image_format(match.group(1))
    if img_format is None:
        return None

    is_base64 = any(
        part.strip().lower() == 'base64'
        for part in header.split(';')[1:]
    )
    try:
        if is_base64:
            img_data = base64.b64decode(payload, validate=True)
        else:
            img_data = unquote_to_bytes(payload)
    except (ValueError, binascii.Error):
        return None
    if not _valid_project_image_payload(img_format, img_data):
        return None
    return img_format, img_data


def _project_image_href(elem: ET.Element) -> str:
    href_keys = tuple(
        key for key in ('href', f'{{{XLINK_NS}}}href')
        if key in elem.attrib
    )
    if len(href_keys) != 1:
        raise ValueError('requires exactly one href or xlink:href')
    href = elem.get(href_keys[0])
    if href is None or not href.strip():
        raise ValueError('href cannot be empty')
    return href


def load_project_image_source(
    elem: ET.Element,
    svg_dir: Path | None,
    resource_root: Path | None = None,
) -> ProjectImageSource:
    """Load one exact SVG image source or raise a contract error."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        ProjectImageSource,
        _decode_data_image_uri,
        _normalize_project_image_format,
        _project_image_href,
        _resolve_external_image,
        _valid_project_image_payload,
    )
    if elem.tag != f'{{{SVG_NS}}}image':
        raise ValueError('expected an SVG-namespace <image> element')
    href = _project_image_href(elem)
    if href.startswith('data:'):
        decoded = _decode_data_image_uri(href)
        if decoded is None:
            raise ValueError(
                'data URI must contain a supported, non-empty image with '
                'valid encoding and bytes'
            )
        img_format, img_data = decoded
        return ProjectImageSource(img_format, img_data)

    if svg_dir is None:
        raise ValueError('external image requires an SVG directory context')
    try:
        img_path = _resolve_external_image(svg_dir, href, resource_root)
    except FileNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    img_format = _normalize_project_image_format(img_path.suffix)
    if img_format is None:
        raise ValueError(
            f'external image has unsupported file extension {img_path.suffix!r}'
        )
    try:
        img_data = img_path.read_bytes()
    except OSError as exc:
        raise ValueError(f'cannot read external image {href!r}: {exc}') from exc
    if not _valid_project_image_payload(img_format, img_data):
        raise ValueError(
            f'external image {href!r} is empty, corrupt, or does not match '
            f'its {img_path.suffix} extension'
        )
    return ProjectImageSource(img_format, img_data)


def project_image_errors(
    root: ET.Element,
    svg_dir: Path | None,
    *,
    allow_template_placeholders: bool = False,
    resource_root: Path | None = None,
) -> list[str]:
    """Return source and frame errors for exact SVG image elements."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _element_contract_label,
        _project_image_href,
        load_project_image_source,
    )
    errors: list[str] = []
    for elem in root.iter():
        if elem.tag.rsplit('}', 1)[-1] != 'image':
            continue
        label = _element_contract_label(elem)
        if elem.tag != f'{{{SVG_NS}}}image':
            errors.append(
                f'{label} must use the SVG namespace '
                f'{SVG_NS!r}'
            )
            continue
        style_values = parse_inline_style(elem.get('style'))
        for attribute in ('width', 'height'):
            raw = style_values.get(attribute)
            if raw is None:
                raw = elem.get(attribute)
            if raw is None:
                errors.append(
                    f'{label} requires explicit positive {attribute}'
                )
                continue
            try:
                value = parse_project_geometry_length(raw, attribute)
            except ValueError:
                # The shared geometry-length contract owns syntax diagnostics.
                continue
            if value <= 0:
                errors.append(
                    f'{label} {attribute} must be positive; got {raw!r}'
                )
        try:
            raw_href = _project_image_href(elem)
        except ValueError as exc:
            errors.append(f'{label} invalid image source: {exc}')
            continue
        if (
            allow_template_placeholders
            and '{{' in raw_href
            and '}}' in raw_href
        ):
            continue
        try:
            load_project_image_source(elem, svg_dir, resource_root)
        except ValueError as exc:
            errors.append(f'{label} invalid image source: {exc}')
    return sorted(errors)
