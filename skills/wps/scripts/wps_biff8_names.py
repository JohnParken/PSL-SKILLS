"""Conservative MS-XLS Lbl/Name record classifier for stored-value reading.

Only one static ordinary local/global 3-D range token is recognized. It does
not evaluate names, formula functions, macro definitions or external links.
All results are bounded source metadata, never effective worksheet values.
"""
import hashlib
import struct


class UnsafeDefinedName(ValueError):
    """Unsupported or security-relevant Lbl payload."""


MAX_STATIC_NAMES = 256
AREA3D_TOKENS = frozenset((0x3B, 0x5B, 0x7B))


def _refuse(message):
    raise UnsafeDefinedName(message)


def classify_static_name(payload, sheet_count, internal_refs):
    """Accept only a non-macro, direct internal absolute PtgArea3d range.

    The caller has already validated CFB/BIFF framing, BOF/sheets and that
    the ExternSheet array refers only to a self SupBook. Name bytes are not
    included in the returned metadata (the caller retains payload_hex).
    """
    if len(payload) < 15:
        _refuse('Name header truncated or variable fields outside supported subset')
    (flags, shortcut, name_length, formula_length, reserved3, itab,
     reserved4, reserved5, reserved6, reserved7) = struct.unpack_from(
        '<HBBHHH4B', payload, 0)
    if flags & 0x000E:
        _refuse('Name macro/procedure/function flags unsupported')
    if flags != 0 or shortcut != 0 or any((reserved3, reserved4, reserved5,
                                           reserved6, reserved7)):
        _refuse('Only ordinary non-hidden/non-macro Name header with zero reserved fields supported')
    if not 1 <= name_length <= 255:
        _refuse('Empty or oversized ordinary Name unsupported')
    if not 0 <= itab <= sheet_count:
        _refuse('Name local worksheet outside BoundSheet8 collection')
    # XLUnicodeStringNoCch follows 14-byte fixed Lbl header.
    name_flag = payload[14]
    if name_flag not in (0, 1):
        _refuse('Name Unicode reserved/encoding flags unsupported')
    name_end = 15 + name_length * (2 if name_flag else 1)
    if (formula_length != 11 or
            name_end + formula_length != len(payload)):
        _refuse('Only a single 11-byte NameParsedFormula without rgcb supported')
    try:
        name = payload[15:name_end].decode('utf-16le' if name_flag else 'latin1')
    except UnicodeDecodeError as exc:
        raise UnsafeDefinedName('Invalid Name Unicode string') from exc
    if (len(name) != name_length or
            not (name[0].isalpha() or name[0] == '_') or
            not all(ch.isalnum() or ch in '._' for ch in name)):
        _refuse('Name contains unsupported identifier characters')
    formula = payload[name_end:]
    token = formula[0]
    if token not in AREA3D_TOKENS:
        _refuse('Name expression is not a single static PtgArea3d')
    external_index = struct.unpack_from('<H', formula, 1)[0]
    if external_index >= len(internal_refs):
        _refuse('Name formula has no verified internal XTI reference')
    first_sheet, last_sheet = internal_refs[external_index]
    if (first_sheet != last_sheet or not 0 <= first_sheet < sheet_count):
        _refuse('Name refers to a sheet span outside single-sheet subset')
    if itab and first_sheet != itab - 1:
        _refuse('Name local scope and internal XTI sheet disagree')
    row_first, row_last, col_first_raw, col_last_raw = struct.unpack_from(
        '<4H', formula, 3)
    if col_first_raw & 0xC000 or col_last_raw & 0xC000:
        _refuse('Relative defined-name area unsupported')
    col_first, col_last = col_first_raw & 0x00FF, col_last_raw & 0x00FF
    if (col_first_raw & 0x3F00 or col_last_raw & 0x3F00 or
            row_first > row_last or col_first > col_last):
        _refuse('Invalid static defined-name range bounds or reserved column bits')
    return {
        'classification': 'ordinary_static_internal_area_only',
        'name_character_count': name_length,
        'name_sha256': hashlib.sha256(name.encode('utf-16le')).hexdigest(),
        'scope': 'local' if itab else 'workbook',
        'local_sheet_ordinal_raw': itab,
        'formula_token_raw': hex(token),
        'internal_xti_index_raw': external_index,
        'internal_sheet_index_zero_based': first_sheet,
        'bounds_zero_based': [row_first, row_last, col_first, col_last],
        'formula_executed': False,
        'effective_name_binding_applied': False,
        'external_link_followed': False,
        'name_producer_verified': False,
    }
