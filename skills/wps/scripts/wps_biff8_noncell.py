"""Narrow MS-XLS non-cell record validators for BIFF8 data-only reading.

Never an unknown-record fallback. The caller still checks substream ordering,
duplicates, encryption and complete BIFF stream boundaries.
"""


def inspect_noncell(kind, payload, scope):
    if scope == 'globals' and kind == 0x0086:
        # WriteProtect §2.4.350: presence-only global write protection.
        if payload:
            raise ValueError('WriteProtect has no payload')
        return {'kind': 'WriteProtect', 'affects_stored_cell_values': False,
                'write_protected_raw': True, 'effective_edit_permissions': 'unknown'}
    if scope == 'worksheet' and kind == 0x005E:
        # Uncalced §2.4.331: reserved (2 bytes) MUST be zero.
        if payload != b'\x00\x00':
            raise ValueError('Uncalced reserved field must be exactly two zero bytes')
        return {'kind': 'Uncalced', 'affects_stored_cell_values': False,
                'saved_formula_recalculation_pending': True,
                'cache_freshness': 'unverified'}
    raise ValueError('No declared BIFF8 non-cell metadata rule')
