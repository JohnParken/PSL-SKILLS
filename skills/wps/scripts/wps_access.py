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
        compound = CompoundFile(data, compatibility=container_mode(data, compatibility))
    except ValueError as exc:
        raise ParseError('invalid_or_unsupported', {'stage': 'CFB'}, str(exc)) from exc
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
