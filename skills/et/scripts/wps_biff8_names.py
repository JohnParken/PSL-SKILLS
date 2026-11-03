"""Conservative MS-XLS Lbl/Name stored-value metadata classifier.

Permits a narrow ordinary static area or two-area union, plus built-in
Print_Titles/_FilterDatabase with exact bounded token forms. No name/print/
filter evaluation or external link, macro, or formula execution.
"""
import hashlib
import struct


class UnsafeDefinedName(ValueError):
    """Unsupported or security-relevant Lbl payload."""


MAX_STATIC_NAMES = 256
AREA3D_TOKENS = frozenset((0x3B, 0x5B, 0x7B))


def _refuse(message):
    raise UnsafeDefinedName(message)


PTG_UNION = 0x10
PTG_PAREN = 0x15
# MS-XLS PtgMemFunc PtgDataType reference/value/array variants. The cce
# field gives the exact size of the binary reference expression that follows.
PTG_MEMFUNC = frozenset((0x29, 0x49, 0x69))


def _area3d_record(token, sheet_count, internal_refs, itab):
    """Read one exact 11-byte PtgArea3d; no interpretation of expressions."""
    if len(token) != 11 or token[0] not in AREA3D_TOKENS:
        _refuse('Name token is not an exact bounded PtgArea3d')
    external_index = struct.unpack_from('<H', token, 1)[0]
    if external_index >= len(internal_refs):
        _refuse('Name formula has no verified internal XTI reference')
    first_sheet, last_sheet = internal_refs[external_index]
    if first_sheet != last_sheet or not 0 <= first_sheet < sheet_count:
        _refuse('Name refers to a sheet span outside single-sheet subset')
    if itab and first_sheet != itab - 1:
        _refuse('Name local scope and internal XTI sheet disagree')
    row_first, row_last, col_first_raw, col_last_raw = struct.unpack_from(
        '<4H', token, 3)
    if col_first_raw & 0xC000 or col_last_raw & 0xC000:
        _refuse('Relative defined-name area unsupported')
    col_first, col_last = col_first_raw & 0x00FF, col_last_raw & 0x00FF
    if (col_first_raw & 0x3F00 or col_last_raw & 0x3F00 or
            row_first > row_last or col_first > col_last):
        _refuse('Invalid static defined-name range bounds or reserved column bits')
    return {
        'formula_token_raw': hex(token[0]),
        'internal_xti_index_raw': external_index,
        'internal_sheet_index_zero_based': first_sheet,
        'bounds_zero_based': [row_first, row_last, col_first, col_last],
    }


def _static_area_expression(formula, sheet_count, internal_refs, itab,
                            allow_union):
    if len(formula) == 11:
        return [_area3d_record(formula, sheet_count, internal_refs, itab)], False
    if not (allow_union and len(formula) == 23 and
            formula[-1] == PTG_UNION):
        _refuse('Name contains unsupported complex or extra formula tokens')
    areas = [_area3d_record(formula[0:11], sheet_count, internal_refs, itab),
             _area3d_record(formula[11:22], sheet_count, internal_refs, itab)]
    if (areas[0]['internal_xti_index_raw'] !=
            areas[1]['internal_xti_index_raw'] or
            areas[0]['internal_sheet_index_zero_based'] !=
            areas[1]['internal_sheet_index_zero_based']):
        _refuse('Name union references different sheets or XTI ranges')
    return areas, True


def _unframe_print_title_memfunc(formula):
    """Only exact MemFunc[Area3d Area3d Union][Paren] is supported.

    PtgParen is a display token outside PtgMemFunc.cce. A falsely declared
    cce, extra token, nested expression or dangling presentation opcode is
    never silently dropped.
    """
    if not formula or formula[0] not in PTG_MEMFUNC:
        return formula, False, False
    if len(formula) < 3:
        _refuse('Truncated Print_Titles PtgMemFunc header')
    count = struct.unpack_from('<H', formula, 1)[0]
    if count != 23:
        _refuse('Print_Titles PtgMemFunc cce must equal two-area Union length')
    if len(formula) not in (3 + count, 3 + count + 1):
        _refuse('Print_Titles PtgMemFunc formula boundary mismatch')
    has_paren = len(formula) == 3 + count + 1
    if has_paren and formula[-1] != PTG_PAREN:
        _refuse('Only a trailing presentation PtgParen is supported')
    return formula[3:3+count], True, has_paren


def _lbl_header(payload, sheet_count):
    if len(payload) < 15:
        _refuse('Name header truncated or variable fields outside supported subset')
    (flags, shortcut, name_length, formula_length, reserved3, itab,
     reserved4, reserved5, reserved6, reserved7) = struct.unpack_from(
        '<HBBHHH4B', payload, 0)
    if flags & 0x000E:
        _refuse('Name macro/procedure/function flags unsupported')
    if shortcut or any((reserved3, reserved4, reserved5, reserved6, reserved7)):
        _refuse('Name shortcut or reserved fields unsupported')
    if not 0 <= itab <= sheet_count:
        _refuse('Name local worksheet outside BoundSheet8 collection')
    name_options = payload[14]
    if name_options not in (0, 1):
        _refuse('Name Unicode reserved/encoding flags unsupported')
    end_name = 15 + name_length * (2 if name_options else 1)
    if end_name + formula_length != len(payload):
        _refuse('Only exactly framed Lbl NameParsedFormula without rgcb supported')
    return flags, name_length, formula_length, itab, name_options, end_name


def classify_static_builtin_name(payload, sheet_count, internal_refs):
    """Limited built-in filter/print title Name, never applied to actual cells.

    Print_Titles supports a complete-row or complete-column 3-D area, or
    exactly one complete-row + one complete-column joined by PtgUnion.
    _FilterDatabase supports only one local, direct static 3-D range.
    """
    flags, name_length, formula_length, itab, options, end_name = (
        _lbl_header(payload, sheet_count))
    if flags not in (0x20, 0x21) or name_length != 1 or options != 0 or not itab:
        _refuse('Only local nonmacro Print_Titles or _FilterDatabase Lbl flags supported')
    builtin_id = payload[15]
    if builtin_id not in (0x07, 0x0D):
        _refuse('Unsupported built-in Name; only Print_Titles/_FilterDatabase')
    # Real producers can wrap the two-area Print_Titles Union in PtgMemFunc
    # and append a PtgParen outside cce. No generic token interpretation.
    formula, wrapped_memfunc, display_paren = (
        _unframe_print_title_memfunc(payload[end_name:])
        if builtin_id == 0x07 else (payload[end_name:], False, False))
    areas, is_union = _static_area_expression(
        formula, sheet_count, internal_refs, itab,
        allow_union=builtin_id == 0x07)
    if wrapped_memfunc and not is_union:
        _refuse('Print_Titles PtgMemFunc only supports two-area Union')
    kinds = []
    for area in areas:
        r1, r2, c1, c2 = area['bounds_zero_based']
        # Correct full grid extents for BIFF8 (65536 x 256).
        full_row = c1 == 0 and c2 == 255
        full_col = r1 == 0 and r2 == 65535
        kinds.append('full_row' if full_row and not full_col else
                     'full_column' if full_col and not full_row else
                     'entire_sheet' if full_row and full_col else 'partial_rectangle')
    if builtin_id == 0x07:
        if is_union:
            if set(kinds) != {'full_row','full_column'}:
                _refuse('Print_Titles union requires exactly one full row and one full column')
        elif kinds[0] not in ('full_row','full_column'):
            _refuse('Print_Titles must represent complete rows or columns')
    else:
        if is_union or areas[0]['bounds_zero_based'][0] == areas[0]['bounds_zero_based'][1]:
            _refuse('_FilterDatabase requires one range with header and data rows')
    return {
        'classification': ('built_in_print_titles_static_area_only'
                           if builtin_id == 0x07 else
                           'built_in_filter_database_static_area_only'),
        'builtin_name_id_raw': builtin_id,
        'name_hidden_raw': bool(flags & 1),
        'local_sheet_ordinal_raw': itab,
        'internal_xti_index_raw': areas[0]['internal_xti_index_raw'],
        'internal_sheet_index_zero_based': areas[0]['internal_sheet_index_zero_based'],
        'regions_zero_based': [x['bounds_zero_based'] for x in areas],
        'regions_kind': kinds,
        'ptg_union_raw_present': is_union,
        'ptg_memfunc_wrapper_present': wrapped_memfunc,
        'ptg_paren_display_token_present': display_paren,
        'formula_executed': False,
        'print_settings_applied': False,
        'filter_applied': False,
        'external_link_followed': False,
        'name_producer_verified': False,
    }


def classify_static_name(payload, sheet_count, internal_refs):
    """Accept only a non-macro, direct internal absolute PtgArea3d range.

    The caller has already validated CFB/BIFF framing, BOF/sheets and that
    the ExternSheet array refers only to a self SupBook. Name bytes are not
    included in the returned metadata (the caller retains payload_hex).
    """
    flags, name_length, formula_length, itab, name_flag, name_end = (
        _lbl_header(payload, sheet_count))
    if flags != 0 or not 1 <= name_length <= 255:
        _refuse('Only ordinary non-hidden/non-macro Name header with zero reserved fields supported')
    try:
        name = payload[15:name_end].decode('utf-16le' if name_flag else 'latin1')
    except UnicodeDecodeError as exc:
        raise UnsafeDefinedName('Invalid Name Unicode string') from exc
    if (len(name) != name_length or
            not (name[0].isalpha() or name[0] == '_') or
            not all(ch.isalnum() or ch in '._' for ch in name)):
        _refuse('Name contains unsupported identifier characters')
    areas, is_union = _static_area_expression(
        payload[name_end:], sheet_count, internal_refs, itab, allow_union=True)
    single = areas[0]
    return {
        'classification': 'ordinary_static_internal_area_only',
        'name_character_count': name_length,
        'name_sha256': hashlib.sha256(name.encode('utf-16le')).hexdigest(),
        'name_folded_sha256': hashlib.sha256(name.casefold().encode('utf-16le')).hexdigest(),
        'scope': 'local' if itab else 'workbook',
        'local_sheet_ordinal_raw': itab,
        'formula_token_raw': single['formula_token_raw'],
        'internal_xti_index_raw': single['internal_xti_index_raw'],
        'internal_sheet_index_zero_based': single['internal_sheet_index_zero_based'],
        'bounds_zero_based': single['bounds_zero_based'] if not is_union else None,
        'regions_zero_based': [x['bounds_zero_based'] for x in areas],
        'ptg_union_raw_present': is_union,
        'formula_executed': False,
        'effective_name_binding_applied': False,
        'external_link_followed': False,
        'name_producer_verified': False,
    }
