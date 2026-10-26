"""Bounded saved BIFF row/column visibility; never executes filters."""
import struct

class SavedVisibility:
    def __init__(self):
        self.rows = {}
        self.columns = []
        self.filter_records = []
        self.filter_mode = False
        self.filter_count = None

    def add(self, kind, payload, offset):
        if kind == 0x208:
            if len(payload) != 16: raise ValueError('Malformed Row at '+str(offset))
            row, first, last = struct.unpack_from('<HHH', payload)
            if not first <= last <= 256 or row in self.rows: raise ValueError('Invalid/duplicate Row at '+str(offset))
            flags = struct.unpack_from('<H', payload, 12)[0]
            self.rows[row] = {'row_zero_based': row, 'hidden_saved': bool(flags & 32), 'hidden_cause': 'unknown', 'record_offset': offset}
        elif kind == 0x7D:
            if len(payload) != 12: raise ValueError('Malformed ColInfo at '+str(offset))
            first, last = struct.unpack_from('<HH', payload)
            if not first <= last <= 256: raise ValueError('Invalid ColInfo at '+str(offset))
            if any(not(last < x['first_zero_based'] or first > x['last_zero_based']) for x in self.columns): raise ValueError('Overlapping ColInfo at '+str(offset))
            flags = struct.unpack_from('<H', payload, 8)[0]
            self.columns.append({'first_zero_based': first, 'last_zero_based': last, 'hidden_saved': bool(flags & 1), 'hidden_cause': 'unknown', 'record_offset': offset, 'default_column_format_included': last == 256, 'actual_last_zero_based': min(last,255) if first < 256 else None})
        elif kind == 0x9B:
            if payload or self.filter_mode: raise ValueError('Malformed/duplicate FilterMode at '+str(offset))
            self.filter_mode = True
        elif kind == 0x9D:
            if len(payload) < 24 or len(self.filter_records) >= 256: raise ValueError('Malformed/oversized AutoFilter at '+str(offset))
            column = struct.unpack_from('<H', payload)[0]
            if column >= 256 or any(x['column_relative'] == column for x in self.filter_records): raise ValueError('Invalid/duplicate AutoFilter at '+str(offset))
            self.filter_records.append({'column_relative': column, 'record_offset': offset, 'payload_hex': payload.hex(), 'conditions_decoded': False})
        elif kind == 0x9E:
            if len(payload) != 2 or self.filter_count is not None: raise ValueError('Malformed/duplicate AutoFilterInfo at '+str(offset))
            self.filter_count = struct.unpack('<H', payload)[0]
            if not 1 <= self.filter_count <= 256: raise ValueError('Invalid AutoFilterInfo at '+str(offset))
        else: return False
        return True

    def report(self):
        if self.filter_records and (self.filter_count is None or any(x['column_relative'] >= self.filter_count for x in self.filter_records)):
            raise ValueError('AutoFilter column outside AutoFilterInfo')
        return {'rows': list(self.rows.values()), 'columns': self.columns,
                'unspecified_visibility': 'unknown', 'default_selection': 'all',
                'filters': {'saved_filter_mode': self.filter_mode, 'column_count': self.filter_count,
                            'records': self.filter_records, 'conditions_evaluated': False},
                'hidden_cause_policy': 'Saved hidden flags do not distinguish manual hiding from filtering'}


def select_rows(sheet, mode='all'):
    """Return saved row visibility decisions without dropping unknown rows."""
    if mode not in ('all', 'saved_visible'): raise ValueError('Unknown row selection')
    rows = {x['row_zero_based']: x['hidden_saved'] for x in sheet.get('visibility', {}).get('rows', [])}
    result = []
    for address, cell in sheet['cells'].items():
        row = int(''.join(x for x in address if x.isdigit()))-1
        hidden = rows.get(row)
        if mode == 'all' or hidden is not True:
            result.append((address, cell, 'unknown' if hidden is None else 'hidden' if hidden else 'visible'))
    return result
