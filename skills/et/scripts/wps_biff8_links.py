"""Bounded BIFF8 supporting-link metadata. Never resolve an external target."""
import struct

from wps_biff8_errors import ResourceLimitError, require

SUPBOOK = 0x01AE
EXTERNSHEET = 0x0017
EXTERNNAME = 0x0023
XCT = 0x0059
CRN = 0x005A
CONTINUE = 0x003C
LINK_HEADERS = frozenset((SUPBOOK, EXTERNSHEET))
LINK_RECORDS = LINK_HEADERS | {EXTERNNAME, XCT}
UNRESOLVED_XTI = (-1, -1)


def _string_end(payload, position, units):
    require(position < len(payload), 'Truncated SupBook string options')
    stop = position + 1 + units * (2 if payload[position] & 1 else 1)
    require(stop <= len(payload), 'SupBook string exceeds record boundary')
    return stop


class SupportingLinks:
    """Store one slot per SupBook and per XTI, in original record order."""

    def __init__(self, allow_opaque):
        self.allow_opaque = allow_opaque
        self.books = []
        self.local_refs = []
        self.external_book_count = 0
        self._book_group_end = 0

    def read(self, records, index):
        """Return the consumed extent and metadata description for the link record."""
        _, kind, payload = records[index]
        if kind == EXTERNSHEET:
            return self._externsheet(records, index)
        if kind == SUPBOOK:
            description = self._supbook(payload)
            self._book_group_end = index + 1
            return index + 1, description
        require(self.allow_opaque and self.books and self.books[-1][0] != 'self'
                and not self.local_refs and index == self._book_group_end,
                'External link metadata requires an adjacent opaque SupBook group')
        book_index = len(self.books) - 1
        if kind == EXTERNNAME:
            require(len(payload) >= 6, 'Truncated ExternName header')
            self._book_group_end = index + 1
            return index + 1, ('ExternNameOpaque', {'supbook_index': book_index})
        require(kind == XCT and len(payload) in (2, 4), 'Malformed XCT header')
        count = abs(struct.unpack_from('<h', payload)[0])
        if count >= 4096:
            raise ResourceLimitError('External cache record count exceeds budget')
        if len(payload) == 4:
            sheet = struct.unpack_from('<H', payload, 2)[0]
            require(sheet < self.books[-1][1], 'XCT sheet index outside SupBook')
        stop = index + 1 + count
        require(stop <= len(records) and all(record[1] == CRN and len(record[2]) >= 4
                                             for record in records[index + 1:stop]),
                'Missing or malformed adjacent CRN records after XCT')
        self._book_group_end = stop
        return stop, ('ExternalCacheOpaque', {'supbook_index': book_index, 'record_count': count})

    def _supbook(self, payload):
        require(len(payload) >= 4 and not self.local_refs, 'Malformed or misplaced SupBook')
        tabs, units = struct.unpack_from('<HH', payload)
        is_self = units == 0x0401
        if is_self:
            require(len(payload) == 4 and all(book[0] != 'self' for book in self.books),
                    'Malformed or duplicate self-referencing SupBook')
        else:
            require(self.allow_opaque, 'Only one self-referencing SupBook supported; external/add-in links refused')
            if units == 0x3A01:
                require(len(payload) == 4 and tabs == 1, 'Malformed add-in SupBook')
            else:
                require(1 <= units <= 255, 'Invalid external SupBook path length')
                position = _string_end(payload, 4, units)
                require(tabs <= (len(payload) - position) // 3, 'Missing external SupBook sheet names')
                for _ in range(tabs):
                    require(position + 3 <= len(payload), 'Truncated SupBook sheet name')
                    name_units = struct.unpack_from('<H', payload, position)[0]
                    position = _string_end(payload, position + 2, name_units)
                require(position == len(payload), 'Unexpected trailing bytes in SupBook')
            self.external_book_count += 1
        if len(self.books) >= 128:
            raise ResourceLimitError('SupBook count exceeds 128')
        kind = 'self' if is_self else 'addin' if units == 0x3A01 else 'opaque'
        index = len(self.books)
        self.books.append((kind, tabs))
        return ('SupBookSelf' if is_self else 'SupBookOpaque', {'supbook_index': index, 'kind': kind})

    def _externsheet(self, records, index):
        payload = records[index][2]
        require(self.books and not self.local_refs and len(payload) >= 2,
                'Missing SupBook or duplicate/truncated ExternSheet')
        count = struct.unpack_from('<H', payload)[0]
        limit = 4096 if self.allow_opaque else 128
        require(1 <= count <= limit, 'Malformed/oversized ExternSheet')
        expected = 2 + 6 * count
        stop = index + 1
        data = bytearray(payload)
        while self.allow_opaque and len(data) < expected:
            require(stop < len(records) and records[stop][1] == CONTINUE and records[stop][2],
                    'Truncated ExternSheet without adjacent Continue')
            if stop - index > 128:
                raise ResourceLimitError('ExternSheet continuation count exceeds 128')
            data.extend(records[stop][2])
            stop += 1
        require(len(data) == expected, 'Malformed ExternSheet byte count')
        entries = []
        for index, (link, first, last) in enumerate(struct.iter_unpack('<Hhh', data[2:])):
            require(link < len(self.books), 'ExternSheet SupBook index outside supporting link table')
            kind, tabs = self.books[link]
            if not self.allow_opaque:
                require(link == 0 and kind == 'self' and 0 <= first <= last < 128,
                        'Only valid internal sheet references supported')
            else:
                self._validate_scope(kind, tabs, first, last)
            local = kind == 'self' and 0 <= first <= last
            self.local_refs.append((first, last) if local else UNRESOLVED_XTI)
            entries.append({'xti_index': index, 'supbook_index': link, 'first_sheet_raw': first,
                            'last_sheet_raw': last, 'internal': local})
        name = 'ExternSheetInternal' if all(entry['internal'] for entry in entries) else 'ExternSheetOpaque'
        return stop, (name, {'xti': entries})

    @staticmethod
    def _validate_scope(kind, tabs, first, last):
        if kind == 'addin' or (kind == 'opaque' and tabs == 0) or -2 in (first, last):
            require(first == last == -2, 'Invalid workbook-level ExternSheet scope')
            return
        limit = 128 if kind == 'self' else tabs
        require(-1 <= first < limit and -1 <= last < limit and (min(first, last) < 0 or first <= last),
                'ExternSheet scope outside its supporting workbook')
