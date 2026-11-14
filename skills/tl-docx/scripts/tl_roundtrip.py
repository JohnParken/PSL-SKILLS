"""Source-bound, in-memory OPC part bundles; never extract ZIP paths to disk."""

import base64
import binascii
from hashlib import sha256
import json

from tl_package import MAX_PART, MAX_UNCOMPRESSED, TYPES, Package, parse, rels_member

BUNDLE_SCHEMA = 'tl-docx-parts-v1'
# Match the public JSON specification limit; never emit a bundle it cannot read.
MAX_BUNDLE_BYTES = 16 * 1024 * 1024


def _xml_part(package, name):
    kind = package.types[name]
    return name.lower().endswith(('.xml', '.rels')) or kind.endswith(('+xml', '/xml'))


def _verify(package):
    """Read every CRC and validate XML and relationships without executing parts."""
    for name in package.names:
        package.read(name)
        if _xml_part(package, name):
            package.xml(name)
    for source in {''} | package.names - {TYPES}:
        if rels_member(source) in package.names:
            for item in package.relations(source).values():
                if not item['external'] and item['target'] not in package.names:
                    raise ValueError('Repacked document has a missing relationship target')


def unpack_package(package):
    """Return all part payloads in a portable JSON-ready bundle, without writes.

    UTF-8 XML remains editable text; other encodings and binary parts use base64.
    ZIP metadata is deliberately not exported. Unchanged repacking returns the
    original ZIP bytes; changed repacking preserves non-target payload bytes.
    """
    _verify(package)
    parts = []
    budget = 256
    for name in sorted(package.names):
        payload = package.read(name)
        if budget + len(payload) > MAX_BUNDLE_BYTES:
            raise ValueError('Part bundle exceeds the 16 MiB JSON limit; use targeted reading/editing')
        encoding = 'base64'
        content = base64.b64encode(payload).decode('ascii')
        if _xml_part(package, name):
            try:
                content = payload.decode('utf-8')
                encoding = 'utf-8'
            except UnicodeDecodeError:
                pass
        item = {'member': name, 'sha256': sha256(payload).hexdigest(),
                'encoding': encoding, 'content': content}
        budget += len(json.dumps(item, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) + 1
        if budget > MAX_BUNDLE_BYTES:
            raise ValueError('Part bundle exceeds the 16 MiB JSON limit; use targeted reading/editing')
        parts.append(item)
    return {'schema': BUNDLE_SCHEMA, 'source_sha256': package.sha256,
            'extension': package.extension, 'parts': parts}


def _payload(item):
    content = item['content']
    if not isinstance(content, str):
        raise ValueError('Part content must be a string')
    if item['encoding'] == 'utf-8':
        if len(content) > MAX_PART:
            raise ValueError('Part exceeds the expanded size limit')
        try:
            payload = content.encode('utf-8')
        except UnicodeEncodeError as exc:
            raise ValueError('Part text contains invalid Unicode') from exc
    elif item['encoding'] == 'base64':
        if len(content) > ((MAX_PART + 2) // 3) * 4:
            raise ValueError('Part exceeds the encoded size limit')
        try:
            payload = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('Part contains invalid base64') from exc
    else:
        raise ValueError('Unknown part encoding')
    if len(payload) > MAX_PART:
        raise ValueError('Part exceeds the expanded size limit')
    return payload


def repack_package(package, bundle):
    """Apply only changed, source-guarded existing parts and verify the result.

    Adding/removing parts and changing container type are intentionally unsupported.
    No file is created here: the caller validates/publishes through its lifecycle.
    """
    keys = {'schema', 'source_sha256', 'extension', 'parts'}
    if not isinstance(bundle, dict) or set(bundle) != keys or bundle['schema'] != BUNDLE_SCHEMA:
        raise ValueError('Expected a supported complete part bundle')
    if bundle['source_sha256'] != package.sha256 or bundle['extension'] != package.extension:
        raise ValueError('Part bundle does not match the source document')
    parts = bundle['parts']
    if not isinstance(parts, list) or len(parts) != len(package.names):
        raise ValueError('Part bundle must contain every source member exactly once')
    replacements = {}
    seen = set()
    expanded = 0
    for item in parts:
        if not isinstance(item, dict) or set(item) != {'member', 'sha256', 'encoding', 'content'}:
            raise ValueError('Invalid part record')
        name = item['member']
        if not isinstance(name, str) or name not in package.names or name in seen:
            raise ValueError('Unknown or duplicate part member')
        seen.add(name)
        original = package.read(name)
        if item['sha256'] != sha256(original).hexdigest():
            raise ValueError('Part source digest does not match')
        payload = _payload(item)
        expanded += len(payload)
        if expanded > MAX_UNCOMPRESSED:
            raise ValueError('Bundle exceeds the expanded size limit')
        if payload != original:
            if _xml_part(package, name):
                parse(payload)
            replacements[name] = payload
    if replacements and any(name.lower().startswith('_xmlsignatures/') or 'digital-signature' in kind
                            for name, kind in package.types.items()):
        raise ValueError('Digitally signed documents cannot be repacked with changes')
    data = package.overlay(replacements) if replacements else package.data
    result = Package(data)
    if result.extension != package.extension or result.main != package.main:
        raise ValueError('Repacking cannot change the document type or main part')
    _verify(result)
    return data, {'changed_parts': sorted(replacements), 'part_count': len(result.names),
                  'verification': 'static_ooxml', 'unchanged_archive': not replacements}
