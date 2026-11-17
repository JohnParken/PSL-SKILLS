"""Bounded local input and exclusive publication for TL PPTX.

The converter only receives immutable, validated SVG snapshots. Network and
filesystem references in user SVG are not part of this standalone contract.
"""
from __future__ import annotations

import base64
import binascii
import io
import hashlib
import os
from pathlib import Path
import re
import stat
import unicodedata
from urllib.parse import unquote_to_bytes

MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_NODES = 20000
MAX_DEPTH = 80
MAX_SLIDES = 50
MAX_GEOMETRY_TOKENS = 50000
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
REFERENCE_ATTRIBUTES = frozenset({
    'data-pptx-native-ref', 'data-pptx-ref',
    'data-pptx-shape-style-ref', 'data-pptx-custgeom-ref', 'data-icon',
})


class ContractError(ValueError):
    """An input or output falls outside the standalone safety contract."""


def bare_name(value: str, suffix: str) -> str:
    """Require a literal, non-hidden task-root filename, not a path."""
    if (not isinstance(value, str) or not value or value in {'.', '..'} or '/' in value or '\\' in value
            or '\x00' in value or ':' in value or value.startswith('.')
            or Path(value).suffix.lower() != suffix or len(value.encode('utf-8')) > 255
            or any(unicodedata.category(char).startswith('C') for char in value)):
        raise ContractError(f'Expected a new task-root filename ending in {suffix}')
    return value


def read_local(name: str, suffix: str, limit: int = MAX_INPUT_BYTES) -> bytes:
    """Read a regular file without following links, with bounded stable size."""
    bare_name(name, suffix)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(name, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ContractError('Input is not a bounded regular file')
        with os.fdopen(descriptor, 'rb', closefd=False) as handle:
            data = handle.read(limit + 1)
        after = os.fstat(descriptor)
        if len(data) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ContractError('Input changed during reading or exceeds its budget')
        return data
    finally:
        os.close(descriptor)


def _embedded_image(value: str, depth: int, budget: list[int]) -> None:
    """Validate decoded image and recursively check embedded SVG resources."""
    from PIL import Image
    if depth > 4:
        raise ContractError('Embedded SVG nesting exceeds four levels')
    header, separator, payload = value.partition(',')
    media = header[5:].split(';')[0].lower()
    if not separator or media not in {'image/png', 'image/jpeg', 'image/svg+xml'}:
        raise ContractError('Only embedded PNG, JPEG and SVG images are supported')
    try:
        data = base64.b64decode(payload, validate=True) if ';base64' in header.lower() else unquote_to_bytes(payload)
    except (ValueError, binascii.Error) as exc:
        raise ContractError('Invalid image encoding') from exc
    budget[0] += len(data)
    if budget[0] > MAX_TOTAL_BYTES:
        raise ContractError('Embedded resource byte budget exceeded')
    if media == 'image/svg+xml':
        validate_svg(data, depth + 1, budget)
    else:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16000000:
                raise ContractError('Raster image exceeds sixteen million pixels')
            if image.format not in {'PNG', 'JPEG'}:
                raise ContractError('Image bytes do not match an accepted raster type')
            image.verify()


def validate_svg(data: bytes, depth: int = 0, budget: list[int] | None = None) -> dict:
    """Parse bounded XML with entities disabled and reject all external resources."""
    from lxml import etree
    if len(data) > MAX_INPUT_BYTES:
        raise ContractError('SVG byte budget exceeded')
    if budget is None:
        budget = [len(data)]
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                             huge_tree=False, remove_comments=False)
    try:
        tree = etree.parse(io.BytesIO(data), parser)
    except etree.XMLSyntaxError as exc:
        raise ContractError('Invalid or unsafe SVG XML') from exc
    if tree.docinfo.doctype:
        raise ContractError('DTD and entity declarations are forbidden')
    root = tree.getroot()
    if root.tag != '{http://www.w3.org/2000/svg}svg':
        raise ContractError('A namespaced SVG root is required')
    nodes = 0
    geometry_tokens = 0
    stack = [(root, 1)]
    while stack:
        element, level = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or level > MAX_DEPTH:
            raise ContractError('SVG node or depth budget exceeded')
        if not isinstance(element.tag, str):
            if isinstance(element, etree._Entity):
                raise ContractError('Entities are forbidden')
            continue
        local = etree.QName(element).localname
        if local in {'script', 'foreignObject', 'style', 'animate', 'animateTransform', 'set'}:
            raise ContractError(f'Active or unsupported SVG element: {local}')
        for key, value in element.attrib.items():
            attribute = etree.QName(key).localname
            if attribute in {'d', 'points'}:
                geometry_tokens += len(re.findall(r'[A-Za-z]|[-+]?(?:[0-9]*\.)?[0-9]+(?:[eE][-+]?[0-9]+)?', value))
                if geometry_tokens > MAX_GEOMETRY_TOKENS:
                    raise ContractError('SVG geometry token budget exceeded')
            if attribute.lower().startswith('on') or attribute in REFERENCE_ATTRIBUTES:
                raise ContractError('Active attributes and external native payload stores are forbidden')
            if attribute in {'href', 'src', 'poster', 'data-pptx-shape-hyperlink', 'data-pptx-source-href'}:
                if value.startswith('#'):
                    pass
                elif value.startswith('data:') and local == 'image':
                    _embedded_image(value, depth, budget)
                elif local == 'a' or attribute in {'data-pptx-shape-hyperlink', 'data-pptx-source-href'}:
                    # Preserve a user-authored link in the file, without visiting it.
                    from hyperlink_contract import HyperlinkContractError, parse_hyperlink_target
                    try:
                        parse_hyperlink_target(value)
                    except HyperlinkContractError as exc:
                        raise ContractError(str(exc)) from exc
                else:
                    raise ContractError('External resource loading is not allowed')
            for reference in re.findall(r'url\s*\(([^)]*)\)', value, re.IGNORECASE):
                if not reference.strip().strip('\'"').startswith('#'):
                    raise ContractError('Only in-document CSS resource references are allowed')
        stack.extend((child, level + 1) for child in element)
    from tl_payload_guard import NativePayloadError, validate_native_payloads
    try:
        validate_native_payloads(root, budget)
    except NativePayloadError as exc:
        raise ContractError(str(exc)) from exc
    # Processing instructions may contain external stylesheets outside the root.
    if tree.xpath('//processing-instruction()'):
        raise ContractError('XML processing instructions are forbidden')
    return {'nodes': nodes, 'source_bytes': len(data), 'resource_bytes': budget[0]}


def publish_exclusive(name: str, data: bytes, suffix: str = '.pptx') -> dict:
    """Publish only verified bytes; clean our own incomplete file on failure."""
    if suffix not in {'.pptx', '.json', '.zip', '.svg', '.wav', '.mp3', '.m4a', '.mp4', '.srt'}:
        raise ContractError('Unsupported verified output type')
    bare_name(name, suffix)
    if len(data) > MAX_OUTPUT_BYTES:
        raise ContractError('Output byte budget exceeded')
    descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, 'wb', closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(descriptor)
        current = os.stat(name, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino) or current.st_size != len(data):
            raise ContractError('Published path identity or size changed; unknown replacement retained')
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        if digest.digest() != hashlib.sha256(data).digest():
            raise ContractError('Published content did not match verified bytes')
        current = os.stat(name, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise ContractError('Published path identity changed; unknown replacement retained')
    except BaseException:
        try:
            current = os.stat(name, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                os.unlink(name)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
    return {'published': True, 'file': name, 'bytes': len(data)}
