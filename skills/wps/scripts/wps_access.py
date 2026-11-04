"""Shared bounded file access and CFB policy checks; no format-reader imports."""
import os
import stat
import struct
from wps_cfb import (CompoundFile, MAX_FILE_BYTES, SIGNATURE,
                     LEGACY_EXCEL_CLSID, LEGACY_WORD_CLSID)

class ParseError(ValueError):
    def __init__(self, status, location, message):
        self.status = status
        self.location = location
        super().__init__(message)



def require(condition, location, message, status="corrupt"):
    if not condition:
        raise ParseError(status, location, message)


def diagnostic_context(status, location):
    """Add a small layer distinction without guessing a failed payload's contents."""
    location = location if isinstance(location, dict) else {}
    stage, stream = location.get('stage', ''), location.get('stream', '')
    if stream == 'CFB header' or stage == 'CFB' or stage == 'container':
        layer = 'cfb_container'
    elif stage == 'BIFF' or 'record_id' in location:
        layer = 'biff_record'
    elif stage == 'routing':
        layer = 'payload_routing'
    elif stream in ('WordDocument', '0Table', '1Table'):
        layer = 'ms_doc_payload'
    elif str(stage).startswith('OOXML') or 'part' in location:
        layer = 'ooxml_payload'
    elif stage in ('input', 'output', 'options'):
        layer = stage
    else:
        layer = 'unknown'
    context = dict(layer=layer, outcome=status, container_readable_does_not_prove_payload_support=True)
    if location.get('reason_code') is not None:
        context['reason_code'] = location['reason_code']
    elif stream == 'CFB header' and status == 'unsupported' and location.get('offset') in (8, 24):
        context['reason_code'] = 'cfb_clsid_policy_not_supported' if location['offset'] == 8 else 'cfb_minor_policy_not_supported'
    if location.get('record_id') == '0x88d':
        context.update(record_name='DXF', record_id='0x088D',
                       policy_scope='data/legacy_excel_clsid retain validated DXF metadata; strict refuses; formatting not applied')
    if location.get('compatibility_mode') is not None:
        context['compatibility_mode'] = location['compatibility_mode']
    return context


def read_input(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    try:
        with os.fdopen(descriptor, 'rb') as handle:
            descriptor = None
            info = os.fstat(handle.fileno())
            require(stat.S_ISREG(info.st_mode), {'stage': 'input'}, 'Regular file required', 'unsupported')
            require(info.st_size <= MAX_FILE_BYTES, {'stage': 'input'}, 'File exceeds 32 MiB', 'resource_limit')
            data = handle.read(info.st_size + 1)
            after = os.fstat(handle.fileno())
            require(len(data) == info.st_size and after.st_size == info.st_size
                    and after.st_mtime_ns == info.st_mtime_ns and after.st_ctime_ns == info.st_ctime_ns,
                    {'stage': 'input'}, 'Input changed during read', 'io_error')
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return data


def container_mode(data, compatibility):
    """Select one evidenced header profile, without parser retries."""
    if compatibility == 'data':
        return {LEGACY_EXCEL_CLSID: 'legacy_excel_clsid',
                LEGACY_WORD_CLSID: 'legacy_word_clsid'}.get(data[8:24], 'strict')
    return compatibility


def load_compound(data, compatibility):
    if compatibility not in ('strict', 'data', 'legacy_excel_clsid', 'legacy_word_clsid'):
        raise ParseError('unsupported', {'stage': 'options'}, 'Unknown compatibility mode')
    if not data.startswith(SIGNATURE):
        raise ParseError('unsupported', {'stage': 'container'}, 'Require CFB container')
    if len(data) < 26:
        raise ParseError('corrupt', {'stream': 'CFB header', 'offset': 24}, 'Structure outside stream/region')
    minor_version = struct.unpack_from('<H', data, 24)[0]
    # MS-CFB 2.2 recommends 3E (SHOULD, not MUST). Accept the observed
    # legacy 3B only in compatibility modes; sector/allocation rules are identical.
    legacy_minor = minor_version == 0x003B and compatibility != 'strict'
    if minor_version != 0x003E and not legacy_minor:
        raise ParseError('unsupported', {'stream': 'CFB header', 'offset': 24}, 'Unsupported CFB minor version')
    clsid = data[8:24]
    allowed = bytes(16)
    profile = container_mode(data, compatibility)
    expected = {'legacy_excel_clsid': bytes.fromhex('2008020000000000c000000000000046'),
                'legacy_word_clsid': bytes.fromhex('0609020000000000c000000000000046')}.get(profile)
    if clsid != allowed and not (clsid == expected and len(data) >= 28 and struct.unpack_from('<H', data, 26)[0] == 3):
        raise ParseError('unsupported', {'stream': 'CFB header', 'offset': 8},
                         'Nonzero CFB header CLSID outside the explicitly supported profile')
    try:
        compound = CompoundFile(data, compatibility=container_mode(data, compatibility),
                                allow_missing_padding=compatibility != 'strict')
    except ValueError as exc:
        message = str(exc)
        location = {'stage': 'CFB'}
        if message.startswith('CFB physical length is not sector aligned'):
            location['reason_code'] = 'cfb_unaligned_physical_length'
        elif message.startswith('FAT allocates sectors beyond physical input'):
            location['reason_code'] = 'cfb_fat_unbacked_allocation'
        if 'reason_code' not in location:
            if any(token in message for token in ('cycle', 'cross-link', 'duplicate allocation')):
                location['reason_code'] = 'cfb_allocation_conflict'
            elif 'Unreferenced' in message:
                location['reason_code'] = 'cfb_unreferenced_allocation'
            elif any(token in message for token in ('exceeds', 'limit')):
                location['reason_code'] = 'cfb_resource_or_subset_limit'
            else:
                location['reason_code'] = 'cfb_validation_or_subset_rejection'
        raise ParseError('invalid_or_unsupported', location, message) from exc
    if legacy_minor:
        compound.issues.append({'code': 'legacy_cfb_minor_version',
                                'stream': 'CFB header', 'offset': 24,
                                'minor_version': minor_version,
                                'message': 'Legacy minor version 0x003B accepted with unchanged major-version, sector and allocation validation; not full format certification.'})
    encrypted = [path for path in compound.streams
                 if path[-1].casefold() in ('encryptedpackage', 'encryptioninfo', 'encryptedsummary', 'drmcontent')]
    if encrypted:
        raise ParseError('encrypted', {'stage': 'CFB', 'stream': '/'.join(encrypted[0])}, 'Encrypted package')
    word = ('WordDocument',) in compound.streams
    workbooks = [path for path in compound.streams if len(path) == 1 and path[0].casefold() in ('workbook', 'book')]
    if word and workbooks:
        raise ParseError('unsupported', {'stage': 'routing'}, 'Ambiguous document and workbook payloads; no branch chosen')
    return compound


def resolve_chart_policy(chart_policy, compatibility):
    if chart_policy not in (None, 'reject', 'opaque'):
        raise ParseError('unsupported', {'stage': 'options'}, 'Unknown chart policy')
    return ('opaque' if compatibility == 'data' else 'reject') if chart_policy is None else chart_policy
