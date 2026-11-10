# SPDX-License-Identifier: MIT
"""BIFF8 record framing and the reader's metadata allowlists."""
import struct

from wps_biff8_errors import EncryptedError, ResourceLimitError, require


# Recognized structural, value, continuation and policy-controlled records must
# reach their dedicated handlers. Other IDs remain opaque with uncertain coverage.
SALVAGE_FORBIDDEN_RECORDS = frozenset({
    0x000A, 0x0809, 0x002F, 0x003C, 0x00FC, 0x00FD, 0x0204, 0x0207,
    0x0006, 0x0201, 0x0203, 0x0205, 0x027E, 0x00BD, 0x00BE,
    0x04BC, 0x0221, 0x0085, 0x00E0, 0x041E, 0x00E5, 0x0022,
    0x0001, 0x0002, 0x0003, 0x0004, 0x0005, 0x0007, 0x0202,
    0x0009, 0x0209, 0x0409, 0x0206, 0x0406, 0x00D6, 0x0236, 0x0081,
    0x0017, 0x0018, 0x0023, 0x0059, 0x005A, 0x01AE, 0x00D3, 0x00EB, 0x00EC, 0x005D,
    0x005E, 0x0086, 0x01B0, 0x01B1, 0x01B2, 0x01BE, 0x01B6,
    0x088D, 0x088F, 0x0890,
})

ERRORS={0:'#NULL!',7:'#DIV/0!',15:'#VALUE!',23:'#REF!',29:'#NAME?',36:'#NUM!',42:'#N/A',43:'#GETTING_DATA'}
GLOBAL_METADATA={0x0042:'CodePage',0x003D:'Window1',0x0031:'Font',0x0293:'Style'}
SHEET_METADATA={0x0200:'Dimensions',0x0208:'Row',0x020B:'Index',0x00D7:'DBCell',0x023E:'Window2'}
# MS-XLS 2.3.2 record IDs. Opt-in raw retention, not semantic decoding or execution.
LEGACY_GLOBAL_METADATA={
    0x00E1:'InterfaceHdr',0x00C1:'Mms',0x00E2:'InterfaceEnd',0x005C:'WriteAccess',
    0x0161:'DSF',0x01C0:'Excel9File',0x013D:'RRTabId',0x0019:'WinProtect',
    0x0012:'Protect',0x0013:'Password',0x01AF:'Prot4Rev',0x01BC:'Prot4RevPass',
    0x0040:'Backup',0x008D:'HideObj',0x000E:'CalcPrecision',0x01B7:'RefreshAll',
    0x00DA:'BookBool',0x087C:'XFCRC',0x087D:'XFExt',0x0892:'StyleExt',
    0x0863:'BookExt',0x0896:'Theme',0x088C:'Compat12',0x088E:'TableStyles',
    0x0160:'UsesELFs',0x089A:'MTRSettings',0x008C:'Country',0x01C1:'RecalcId',
    0x00FF:'ExtSST'}
LEGACY_SHEET_METADATA={
    0x000D:'CalcMode',0x000C:'CalcCount',0x000F:'CalcRefMode',0x0011:'CalcIter',
    0x0010:'CalcDelta',0x005F:'CalcSaveRecalc',0x002A:'PrintRowCol',0x002B:'PrintGrid',
    0x0082:'GridSet',0x0080:'Guts',0x0225:'DefaultRowHeight',0x0014:'Header',
    0x0015:'Footer',0x0083:'HCenter',0x0084:'VCenter',0x0026:'LeftMargin',
    0x0027:'RightMargin',0x0028:'TopMargin',0x0029:'BottomMargin',0x00A1:'Setup',
    0x089C:'HeaderFooter',0x0055:'DefColWidth',0x007D:'ColInfo',0x088B:'PLV',
    0x001D:'Selection',0x0099:'DxGCol',0x0867:'FeatHdr',
    0x0012:'Protect',0x0013:'Password',0x00DD:'ScenarioProtect',
    0x0063:'ObjProtect',0x00A0:'Scl',0x0862:'SheetExt'}
# These records have bounded local framing checks before their original bytes
# are retained. Their values are not applied to worksheet presentation.
LEGACY_VALIDATED_GLOBAL_METADATA={0x0092:'Palette'}
LEGACY_VALIDATED_SHEET_METADATA={0x0041:'Pane',0x001B:'HorizontalPageBreaks',0x001A:'VerticalPageBreaks'}


def _record_error(error_type, message, offset, kind=None):
    error = error_type(message)
    error.location = {'record_offset': offset}
    if kind is not None:
        error.location['record_id'] = hex(kind)
    raise error


def frame_records(data, *, max_stream_bytes, max_records):
    """Split a bounded BIFF8 stream into offset, type, and payload records."""
    require(isinstance(data, bytes), 'BIFF stream must be bytes')
    if len(data) > max_stream_bytes:
        raise ResourceLimitError('BIFF stream exceeds 8 MiB')
    output = []
    offset = 0
    while offset < len(data):
        if len(output) >= max_records:
            _record_error(ResourceLimitError, 'BIFF record count limit exceeded', offset)
        if len(data) - offset < 4:
            _record_error(ValueError, 'Truncated BIFF record header', offset)
        kind, size = struct.unpack_from('<HH', data, offset)
        if size > 8224 or offset + 4 + size > len(data):
            _record_error(ValueError, 'Unsupported/oversized/truncated BIFF record payload', offset, kind)
        if kind == 0x002F:
            _record_error(EncryptedError, 'Encrypted BIFF FILEPASS unsupported; no decryption attempted', offset, kind)
        output.append((offset, kind, data[offset + 4:offset + 4 + size]))
        offset += 4 + size
    require(output and output[0][1] == 0x0809, 'Stream must start with BIFF8 BOF, not a filename hint')
    return output


def bof(record, expected):
    """Validate a workbook or worksheet BOF and return its payload in hex."""
    offset, kind, data = record
    require(kind == 0x0809 and len(data) == 16, 'Expected 16-byte BIFF8 BOF at ' + str(offset))
    version, substream = struct.unpack_from('<HH', data)
    require(version == 0x0600 and substream == expected, 'Unsupported BIFF version/substream; only BIFF8 globals and worksheets')
    return data.hex()
