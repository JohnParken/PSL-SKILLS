"""Read pinned offline data packs and select explicit IDs into task bundles.

No installation, network access, external API call or resource execution occurs.
Every input is a bare task-root filename and must match a distributed catalog
SHA-256 before its manifest is consumed. The caller owns final publication.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import stat
import unicodedata
import zipfile

from tl_guard import ContractError, read_local
from tl_package_guard import MAX_ARCHIVE_BYTES, MAX_EXPANDED_BYTES, MAX_MEMBER_BYTES, MAX_MEMBERS
from tl_roundtrip import BUNDLE_MANIFEST, BUNDLE_SCHEMA, _json, _member_path, inspect_workspace_zip

CATALOG = Path(__file__).resolve().parents[1] / 'references/offline-assets.json'
MAX_SELECTION_IDS = 500
MAX_QUERY_RESULTS = 100


def _digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _catalog():
    payload = CATALOG.read_bytes()
    if len(payload) > 128 * 1024:
        raise ContractError('Offline pack catalog exceeds its byte budget')
    result = _json(payload)
    if not isinstance(result, dict) or result.get('schema') != 'tl-pptx.offline-assets.v1':
        raise ContractError('Offline pack catalog schema is unavailable')
    return result


def _load_pack(name):
    """Validate pinned whole archive, CRC, file roster and attribution bytes."""
    payload = read_local(name, '.zip', MAX_ARCHIVE_BYTES)
    catalog = _catalog()
    expected = next((row for row in catalog['packs'] if row['sha256'] == _digest(payload)), None)
    if expected is None:
        raise ContractError('Offline asset pack does not match a known catalog SHA-256')
    if len(payload) != expected['archive_bytes']:
        raise ContractError('Offline asset pack size differs from its catalog')
    entries = {}
    expanded = 0
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) != expected['members'] or len(infos) > MAX_MEMBERS:
                raise ContractError('Offline asset pack member count mismatch')
            for info in infos:
                path = _member_path(info.filename)
                mode = info.external_attr >> 16
                if (info.is_dir() or info.flag_bits & 1 or stat.S_ISLNK(mode)
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                        or info.filename in entries):
                    raise ContractError('Unsafe or repeated offline asset pack member')
                if (path.parts[0] not in {'licenses', 'context', 'blobs'} and str(path) != 'assetpack.json'):
                    raise ContractError('Unknown offline asset pack member namespace')
                if path.parts[0] == 'blobs' and not re.fullmatch(r'blobs/b_[0-9a-f]{64}', str(path)):
                    raise ContractError('Invalid content-addressed asset member')
                expanded += info.file_size
                if info.file_size > MAX_MEMBER_BYTES or expanded > MAX_EXPANDED_BYTES:
                    raise ContractError('Offline asset pack expansion budget exceeded')
                with archive.open(info) as stream:
                    content = stream.read(MAX_MEMBER_BYTES + 1)
                if len(content) != info.file_size:
                    raise ContractError('Offline asset member length mismatch')
                entries[info.filename] = content
            if archive.testzip() is not None:
                raise ContractError('Offline asset pack CRC check failed')
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ContractError('Invalid offline asset ZIP') from exc
    manifest = _json(entries.get('assetpack.json', b''))
    if (manifest.get('schema') != 'tl-pptx.assetpack.v1'
            or manifest.get('pack_id') != expected['pack_id']
            or manifest.get('upstream_commit') != catalog['upstream_commit']):
        raise ContractError('Offline asset pack identity mismatch')
    if not isinstance(manifest.get('files'), dict) or not isinstance(manifest.get('assets'), dict):
        raise ContractError('Offline asset pack file/ID mapping is missing')
    referenced = {'assetpack.json'}
    for original, record in manifest['files'].items():
        _member_path(original)
        content = entries.get(record.get('member'))
        if content is None or len(content) != record.get('bytes') or _digest(content) != record.get('sha256'):
            raise ContractError('Offline asset source byte/hash mismatch')
        referenced.add(record['member'])
    for record in [*manifest.get('licenses', []), *manifest.get('context', [])]:
        content = entries.get(record.get('member'))
        if content is None or _digest(content) != record.get('sha256'):
            raise ContractError('Offline asset notice/context hash mismatch')
        referenced.add(record['member'])
    if referenced != set(entries):
        raise ContractError('Offline asset pack has an unaccounted member')
    if len(manifest['assets']) != expected['assets'] or len(manifest['files']) != expected['source_files']:
        raise ContractError('Offline asset pack ID/file count mismatch')
    for ident, asset in manifest['assets'].items():
        if not isinstance(ident, str) or not isinstance(asset.get('files'), list) or not asset['files']:
            raise ContractError('Invalid canonical asset declaration')
        if any(path not in manifest['files'] for path in asset['files']):
            raise ContractError('Canonical asset references a missing original file')
    return manifest, entries, expected


def _descriptions(manifest, entries):
    """Read original discovery metadata without loading user-facing full texts."""
    pack = manifest['pack_id']
    result = {}
    if pack in {'brands', 'styles', 'layouts', 'decks'}:
        path = f'templates/{pack}/{pack}_index.json'
        result = _json(entries[manifest['files'][path]['member']])
    elif pack == 'sounds':
        path = 'templates/sounds/sounds_index.json'
        original = _json(entries[manifest['files'][path]['member']])
        result = {row['id']: {key: row[key] for key in ('label', 'tags', 'contexts', 'duration_seconds', 'recommended', 'source', 'source_url', 'license') if key in row}
                  for row in original['sounds']}
    return result


def query_asset_pack(name, *, query='', limit=50, offset=0):
    """Return a bounded stable page of matching canonical IDs and rights data."""
    if (not isinstance(query, str) or len(query) > 500 or type(limit) is not int
            or not 1 <= limit <= MAX_QUERY_RESULTS or type(offset) is not int or offset < 0):
        raise ContractError('Invalid offline asset query or pagination')
    manifest, entries, expected = _load_pack(name)
    descriptions = _descriptions(manifest, entries)
    terms = query.casefold().split()
    matches = []
    for ident, item in sorted(manifest['assets'].items()):
        description = descriptions.get(ident, {})
        searchable = ident + ' ' + json.dumps(description, ensure_ascii=False)
        if all(term in searchable.casefold() for term in terms):
            matches.append({'id': ident, 'file_count': len(item['files']), **description})
    page = matches[offset:offset + limit]
    next_offset = offset + len(page) if offset + len(page) < len(matches) else None
    return {'status': 'verified', 'operation': 'asset-query', 'pack_id': manifest['pack_id'],
            'pack_sha256': expected['sha256'], 'matches': page, 'total_matches': len(matches),
            'offset': offset, 'next_offset': next_offset, 'truncated': next_offset is not None,
            'rights': manifest['rights'], 'network_access': False}


def _destination(pack_id, ident, original):
    """Restore only fixed upstream namespace mappings, never arbitrary paths."""
    if pack_id in {'brands', 'styles', 'layouts', 'decks'}:
        prefix = f'templates/{pack_id}/{ident}/'
        if not original.startswith(prefix):
            raise ContractError('Template resource is outside its canonical ID root')
        return f'template-workspaces/{pack_id}/{ident}/' + original[len(prefix):]
    if pack_id.startswith('icons-'):
        if original != 'templates/icons/' + ident + '.svg':
            raise ContractError('Icon resource does not match its canonical ID')
        return 'icons/' + ident + '.svg'
    if pack_id == 'sounds':
        if original != 'templates/sounds/' + ident + '.wav':
            raise ContractError('Sound resource does not match its canonical ID')
        return 'sounds/' + ident + '.wav'
    raise ContractError('Unsupported offline asset pack kind')


def select_asset_packs(requests):
    """Select explicit IDs from one or more known packs into one portable ZIP.

    requests is [{"pack": "bare.zip", "ids": ["canonical-id", ...]}, ...].
    Templates retain complete separate roots under template-workspaces/kind/id;
    icon/sound paths retain their complete canonical namespace. Notices travel
    as transport metadata, not as files that invalidate strict Style content.
    """
    if not isinstance(requests, list) or not 1 <= len(requests) <= 10:
        raise ContractError('Select one to ten explicit offline pack requests')
    entries, equivalents, selected = {}, {}, []
    notices, template_roots = {}, []
    primary_libraries = set()
    selection_count = 0
    source_packs = []
    for request in requests:
        if not isinstance(request, dict) or set(request) != {'pack', 'ids'}:
            raise ContractError('Asset request must contain exactly pack and ids')
        identifiers = request['ids']
        if (not isinstance(identifiers, list) or not identifiers or not all(isinstance(ident, str) for ident in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise ContractError('Canonical IDs must be a nonempty unique string list')
        selection_count += len(identifiers)
        if selection_count > MAX_SELECTION_IDS:
            raise ContractError('Offline asset selection ID budget exceeded')
        manifest, source_entries, expected = _load_pack(request['pack'])
        pack_id = manifest['pack_id']
        if expected['sha256'] in {pack['sha256'] for pack in source_packs}:
            raise ContractError('Each offline asset pack may be requested only once')
        if any(ident not in manifest['assets'] for ident in identifiers):
            raise ContractError('Unknown canonical asset ID; no partial selection produced')
        if pack_id.startswith('icons-') and pack_id != 'icons-simple-icons':
            primary_libraries.add(pack_id)
        source_packs.append({'pack_id': pack_id, 'sha256': expected['sha256'], 'rights': manifest['rights']})
        for license_record in manifest['licenses']:
            content = source_entries[license_record['member']]
            notices[_digest(content)] = {'source': license_record['source'], 'sha256': _digest(content),
                                         'utf8_text': content.decode('utf-8')}
        for ident in identifiers:
            roster = []
            for original in manifest['assets'][ident]['files']:
                destination = _destination(pack_id, ident, original)
                _member_path(destination)
                equivalent = unicodedata.normalize('NFC', destination).casefold()
                content = source_entries[manifest['files'][original]['member']]
                if equivalent in equivalents:
                    raise ContractError('Selected asset paths collide; no overwrite is permitted')
                equivalents[equivalent] = destination
                entries[destination] = content
                roster.append(destination)
            selected.append({'pack_id': pack_id, 'id': ident, 'files': roster})
            if pack_id in {'brands', 'styles', 'layouts', 'decks'}:
                template_roots.append(f'template-workspaces/{pack_id}/{ident}')
        if len(entries) > MAX_MEMBERS - 1 or sum(map(len, entries.values())) > MAX_EXPANDED_BYTES:
            raise ContractError('Selected asset workspace budget exceeded')
    warnings = (['Multiple stylistic icon libraries selected; review visual consistency per deck.']
                if len(primary_libraries) > 1 else [])
    metadata = {'schema': BUNDLE_SCHEMA, 'kind': 'asset-selection', 'source_sha256': '',
                'files': {name: _digest(content) for name, content in entries.items()},
                'asset_selection': {'schema': 'tl-pptx.asset-selection.v1', 'source_packs': source_packs,
                                    'selected': selected, 'template_roots': template_roots,
                                    'notices': list(notices.values()), 'warnings': warnings,
                                    'attribution_preservation_required': True}}
    entries[BUNDLE_MANIFEST] = json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(name, content)
    payload = output.getvalue()
    evidence = inspect_workspace_zip(payload)
    return payload, {'operation': 'asset-select', 'sha256': _digest(payload), 'byte_count': len(payload),
                     'selected_ids': [{'pack_id': item['pack_id'], 'id': item['id']} for item in selected],
                     'template_roots': template_roots, 'source_packs': source_packs, 'warnings': warnings,
                     'attribution_notice_count': len(notices), 'attribution_preservation_required': True,
                     'part_count': evidence['part_count'], 'structural_validation': 'passed',
                     'visual_validation': 'not_run', 'publication': 'not_started', 'network_access': False}


def asset_attributions(metadata_items):
    """Collect transport attribution records; text is data, not instructions."""
    records = []
    seen = set()
    for metadata in metadata_items:
        if not isinstance(metadata, dict):
            raise ContractError('Asset provenance metadata must be an object')
        candidates = metadata.get('asset_provenance', [])
        if not isinstance(candidates, list):
            raise ContractError('Asset provenance must be a list')
        if 'asset_selection' in metadata:
            candidates = [*candidates, metadata['asset_selection']]
        for item in candidates:
            if not isinstance(item, dict) or item.get('schema') != 'tl-pptx.asset-selection.v1':
                raise ContractError('Unsupported asset attribution metadata')
            serial = json.dumps(item, ensure_ascii=False, sort_keys=True).encode()
            ident = _digest(serial)
            if ident not in seen:
                records.append(item)
                seen.add(ident)
    return records


def preserve_asset_attributions(payload, metadata_items):
    """Carry notices through workspace transformations without touching assets."""
    records = asset_attributions(metadata_items)
    if not records:
        return payload
    bundle = inspect_workspace_zip(payload)
    metadata = bundle['metadata']
    metadata['asset_provenance'] = records
    entries = dict(bundle['entries'])
    entries[BUNDLE_MANIFEST] = json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(name, content)
    result = output.getvalue()
    inspect_workspace_zip(result)
    return result
