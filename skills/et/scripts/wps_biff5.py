# SPDX-License-Identifier: MIT
"""Independent BIFF5/7 saved-cell subset; no BIFF8 string/style decoding.

BOF 0x0500 identifies both Excel 5 and 95, not a verified producer. Reuse only
bounded framing, diagnostic retention and output assembly from the BIFF engine.
Old byte strings require an explicit, unambiguous supported CodePage. Unknown
records remain opaque with uncertain coverage. No macros, links or execution.
"""
import struct

from wps_biff8 import Reader
from wps_biff8_errors import ResourceLimitError, require
from wps_biff8_headers import column_name
from wps_biff8_records import ERRORS
from wps_biff8_sheet import skeleton_sheet
from wps_biff8_values import number, rk_value

# Explicit Windows/OEM encodings only; no locale, replacement or chardet fallback.
CODECS = {437: 'cp437', 850: 'cp850', 852: 'cp852', 866: 'cp866',
          932: 'cp932', 936: 'gbk', 949: 'cp949', 950: 'cp950',
          10000: 'mac_roman', **{n: 'cp' + str(n) for n in range(1250, 1259)}}
LEGACY_ERRORS = {key: value for key, value in ERRORS.items() if key != 43}
CELL_KINDS = {0x0201, 0x0203, 0x0204, 0x0205, 0x027E, 0x00BD, 0x00BE, 0x0006}


def legacy_bof(record, expected):
    """Require the old fixed header, not a patched BIFF8 header."""
    _offset, kind, payload = record
    require(kind == 0x0809 and len(payload) == 8, 'Unsupported BIFF version/header: expected 8-byte BIFF5/7 BOF')
    version, substream = struct.unpack_from('<HH', payload)
    # Excel 8+ may write 0600 in sheet BOFs when saving a BIFF5 workbook.
    # The globals BOF alone selects this parser; never switch sheet layouts.
    versions = (0x0500, 0x0600) if expected == 16 else (0x0500,)
    require(version in versions and substream == expected,
            'Unsupported BIFF5/7 version/substream')
    return payload.hex()


class LegacyReader(Reader):
    """Strict byte decoding and conservative per-record saved-value recovery."""

    def problem(self, record, scope, message, code='biff5_record_omitted'):
        self.omissions_affect_cell_values = True
        self.issue({'code': code, 'record_offset': record[0], 'scope': scope,
                    'message': message})
        self.salvage_record(record, scope, name='BIFF5Opaque', code=code)

    def byte_string(self, payload, offset, short=False):
        """Return exactly one length-prefixed byte string, never guess encoding."""
        width = 1 if short else 2
        require(len(payload) >= offset + width, 'Truncated legacy string header')
        count = payload[offset] if short else struct.unpack_from('<H', payload, offset)[0]
        require(count <= 32767 and len(payload) == offset + width + count,
                'Legacy string length mismatch; continuation not supported')
        require(self.codec is not None, 'Missing, conflicting or unsupported BIFF5/7 CodePage')
        return payload[offset + width:].decode(self.codec, errors='strict')

    def parse(self):
        legacy_bof(self.records[0], 5)
        # Pre-BIFF8 record payload limit. Framing has already checked encryption
        # and hard file/record budgets, including records in skipped streams.
        require(all(len(p) <= 2080 for _o, _k, p in self.records),
                'BIFF5/7 record payload exceeds 2080 bytes')
        self.note_options()
        self.issue({'code': 'biff5_core_subset', 'message':
                    'BIFF5/7 core saved-cell subset; old metadata and formula '
                    'tokens are not interpreted. Not complete format acceptance.'})
        stop = next((i for i, r in enumerate(self.records[1:], 1) if r[1] in (10, 0x0809)), len(self.records))
        require(stop < len(self.records) and self.records[stop][1:] == (10, b''),
                'BIFF5/7 globals require an unambiguous EOF')
        declarations = [p for _o, k, p in self.records[1:stop] if k == 0x42]
        codes = {struct.unpack('<H', p)[0] for p in declarations if len(p) == 2}
        self.code_page = next(iter(codes)) if len(codes) == 1 and all(len(p) == 2 for p in declarations) else None
        self.codec = CODECS.get(self.code_page)
        if self.codec is None:
            self.issue({'code': 'biff5_codepage_unavailable', 'message':
                        'No unique supported CodePage; text is omitted, never guessed.'})
        bounds = []
        date_values = []
        for record in self.records[1:stop]:
            offset, kind, payload = record
            try:
                if kind == 0x85:
                    if len(bounds) >= self.limits['max_sheets']:
                        raise ResourceLimitError('Legacy sheet budget exceeded')
                    require(len(payload) >= 7 and len(payload) == 7 + payload[6], 'Malformed legacy BoundSheet')
                    require(payload[4] in (0, 1, 2) and 1 <= payload[6] <= 31, 'Invalid legacy sheet state/name')
                    try:
                        name = self.byte_string(payload, 6, True)
                        require(name and name not in {b['name'] for b in bounds}, 'Duplicate legacy sheet name')
                        extra = {}
                    except ValueError as exc:
                        ordinal = len(bounds) + 1
                        name = '[undecoded %d]' % ordinal
                        while name in {b['name'] for b in bounds}:
                            ordinal += 1
                            name = '[undecoded %d]' % ordinal
                        extra = {'name_unavailable': True, 'name_raw_hex': payload[7:].hex()}
                        self.problem(record, 'globals', str(exc), 'biff5_sheet_name_unavailable')
                    bounds.append({'name': name, 'offset': struct.unpack_from('<I', payload)[0],
                                   'hidden_state': payload[4],
                                   'sheet_type_raw': payload[5], 'substream_type': 'worksheet' if payload[5] == 0 else 'unsupported',
                                   **extra})
                elif kind == 0xE0:
                    if len(self.styles) >= self.limits['max_xf_styles']:
                        raise ResourceLimitError('Legacy XF budget exceeded')
                    valid = len(payload) == 16
                    self.styles.append({'xf_index': len(self.styles), 'valid': valid,
                                        'number_format_id_raw': struct.unpack_from('<H', payload, 2)[0] if valid else 0,
                                        'raw_hex': payload.hex(), 'biff_version': '5/7'})
                    if not valid:
                        self.problem(record, 'globals', 'Invalid legacy XF; slot retained without interpreting layout')
                elif kind == 0x22:
                    require(len(payload) == 2 and payload in (b'\0\0', b'\1\0'), 'Invalid DateMode')
                    date_values.append(int.from_bytes(payload, 'little'))
                elif kind == 0x42:
                    self._append_metadata('globals', offset, kind, 'CodePage', payload)
                else:
                    self.problem(record, 'globals', 'Legacy global metadata not decoded', 'biff5_metadata_opaque')
            except ResourceLimitError:
                raise
            except ValueError as exc:
                self.problem(record, 'globals', str(exc))
        date_system = str(1904 if date_values[0] else 1900) if date_values and len(set(date_values)) == 1 else 'unknown'
        if not self.styles:
            self.styles = [{'xf_index': 0, 'number_format_id_raw': 0, 'raw_hex': '', 'synthetic_fallback': True}]
        if not bounds:
            self.omissions_affect_cell_values = True
        sheets = self.read_legacy_sheets(bounds, stop + 1)
        result = self._result(bounds, sheets, date_system)
        result.update(schema_version='biff5_readonly_v1', biff_version='5/7',
                      support_level='biff5_7_core_stored_data_subset', text_encoding=self.codec,
                      format='biff5_subset')
        result['policies']['scope'] = 'BIFF5/7 core subset; no old-style layout, formula-token or full ET/ETT acceptance'
        return result

    def read_legacy_sheets(self, bounds, global_end):
        """Bind declared offsets without scanning for values after lost framing."""
        positions = {r[0]: i for i, r in enumerate(self.records)}
        sheets = {}
        used = set(range(global_end))
        counts = {b['offset']: sum(x['offset'] == b['offset'] for x in bounds) for b in bounds}
        for bound in sorted(bounds, key=lambda b: b['offset']):
            sheet = skeleton_sheet(bound, 'unverified')
            sheets[bound['name']] = sheet
            target = positions.get(bound['offset'])
            if target is None or target in used or counts[bound['offset']] != 1 or bound['sheet_type_raw'] != 0:
                self.omissions_affect_cell_values = True
                self.issue({'code': 'biff5_sheet_not_read', 'sheet': bound['name'],
                            'message': 'Non-worksheet, invalid, duplicate or overlapping sheet offset'})
                continue
            try:
                sheet['bof_raw_hex'] = legacy_bof(self.records[target], 16)
                version = struct.unpack_from('<H', self.records[target][2])[0]
                if version != 0x0500:
                    self.issue({'code': 'biff5_sheet_bof_version_mismatch',
                                'sheet': bound['name'], 'record_offset': self.records[target][0],
                                'version_raw': version, 'cell_value_impact': 'metadata_only',
                                'message': 'Sheet BOF version differs; verified BIFF5/7 globals govern decoding.'})
            except ValueError as exc:
                self.problem(self.records[target], bound['name'], str(exc))
                continue
            used.add(target)
            index = target + 1
            ended = False
            while index < len(self.records):
                record = self.records[index]
                _offset, kind, payload = record
                if kind == 0x0809:
                    # Nested chart/macro substreams are not cell tables. Stop;
                    # never read their records as values belonging to this sheet.
                    break
                used.add(index)
                index += 1
                if kind == 10:
                    ended = not payload
                    break
                start_index = index
                try:
                    if kind in CELL_KINDS:
                        index = self.read_cell(sheet, record, index)
                    else:
                        self.problem(record, bound['name'], 'Legacy sheet metadata not decoded', 'biff5_metadata_opaque')
                except ResourceLimitError:
                    raise
                except ValueError as exc:
                    self.omitted_cell_records += int(kind in CELL_KINDS)
                    self.problem(record, bound['name'], str(exc))
                used.update(range(start_index, index))
            if not ended:
                self.omissions_affect_cell_values = True
                self.issue({'code': 'biff5_sheet_incomplete', 'sheet': bound['name'], 'message': 'No unambiguous worksheet EOF'})
            sheet['read_status'] = 'partial'
            sheet.pop('substream_skipped', None)
        for index, record in enumerate(self.records):
            if index not in used:
                self.problem(record, 'unbound', 'Record outside a verified legacy sheet', 'biff5_unbound_record')
        if self.records.tail:
            self.salvage_bytes(self.records.tail, self.records.tail_offset, 'unbound', code='biff5_unframed_tail')
        return sheets

    def put(self, sheet, record, row, col, xf, kind, value, **extra):
        require(0 <= row < 16384 and 0 <= col < 256, 'Coordinate outside BIFF5/7 grid')
        address = column_name(col + 1) + str(row + 1)
        require(address not in sheet['cells'], 'Duplicate cell omitted')
        if self.cell_count >= self.limits['max_cells']:
            raise ResourceLimitError('Legacy cell budget exceeded')
        self.reserve_output_text(value)
        valid_xf = 0 <= xf < len(self.styles) and self.styles[xf].get('valid', True)
        if not valid_xf:
            self.issue({'code': 'biff5_xf_unavailable', 'sheet': sheet['name'], 'cell': address,
                        'message': 'Stored value kept without interpreting invalid style reference'})
        sheet['cells'][address] = {'type': kind, 'value': value, 'xf_index': xf,
                                  'number_format_id_raw': self.styles[xf]['number_format_id_raw'] if valid_xf else None,
                                  'custom_format_raw': None, 'record_offset': record[0], 'record_id': hex(record[1]),
                                  'cache_status': 'not_applicable', **extra}
        self.cell_count += 1

    def read_cell(self, sheet, record, index):
        _offset, kind, payload = record
        require(len(payload) >= 6, 'Short legacy cell header')
        row, col, xf = struct.unpack_from('<HHH', payload)
        if kind in (0xBD, 0xBE):
            width = 6 if kind == 0xBD else 2
            require(len(payload) >= 6 + width and (len(payload) - 6) % width == 0, 'Invalid legacy multi-cell size')
            last = struct.unpack_from('<H', payload, len(payload) - 2)[0]
            require(col <= last < 256 and last - col + 1 == (len(payload) - 6) // width, 'Invalid legacy multi-cell range')
            for at, column in zip(range(4, len(payload) - 2, width), range(col, last + 1)):
                style = struct.unpack_from('<H', payload, at)[0]
                value = rk_value(payload[at + 2:at + 6]) if kind == 0xBD else None
                self.put(sheet, record, row, column, style, 'number' if kind == 0xBD else 'blank', value)
        elif kind == 0x204:
            self.put(sheet, record, row, col, xf, 'text', self.byte_string(payload, 6), code_page_raw=self.code_page)
        elif kind == 0x201:
            require(len(payload) == 6, 'Invalid legacy Blank size')
            self.put(sheet, record, row, col, xf, 'blank', None)
        elif kind in (0x203, 0x27E):
            require(len(payload) == (14 if kind == 0x203 else 10), 'Invalid legacy numeric size')
            value = number(payload[6:]) if kind == 0x203 else rk_value(payload[6:])
            self.put(sheet, record, row, col, xf, 'number', value, raw_numeric_hex=payload[6:].hex())
        elif kind == 0x205:
            require(len(payload) == 8 and payload[7] in (0, 1), 'Invalid legacy BoolErr')
            value = payload[6]
            require(value in LEGACY_ERRORS if payload[7] else value in (0, 1), 'Invalid legacy bool/error value')
            self.put(sheet, record, row, col, xf, 'error' if payload[7] else 'boolean', LEGACY_ERRORS[value] if payload[7] else bool(value))
        else:
            index = self.read_formula(sheet, record, index, row, col, xf)
        return index

    def read_formula(self, sheet, record, index, row, col, xf):
        payload = record[2]
        require(len(payload) >= 22, 'Truncated legacy Formula fixed header')
        count = struct.unpack_from('<H', payload, 20)[0]
        tokens_complete = len(payload) >= 22 + count
        if not tokens_complete:
            self.issue({'code': 'biff5_formula_tokens_truncated', 'sheet': sheet['name'],
                        'record_offset': record[0], 'declared_token_bytes': count,
                        'available_token_bytes': len(payload) - 22,
                        'cell_value_impact': 'none_within_verified_saved_cache',
                        'message': 'Formula expression incomplete; independently verified '
                                   'fixed-header cache retained without executing or guessing tokens.'})
        cache = payload[6:14]
        extra = {'formula': {'tokens_hex': payload[22:22 + count].hex(), 'biff_version': '5/7',
                             'flags_raw': struct.unpack_from('<H', payload, 14)[0],
                             'tokens_complete': tokens_complete, 'declared_token_bytes': count,
                             'ancillary_hex': payload[22 + count:].hex(),
                             'not_executed': True}, 'cache_status': 'unverified', 'raw_cache_hex': cache.hex()}
        if cache[6:] != b'\xff\xff':
            kind, value = 'number', number(cache)
        elif cache[0] == 0:
            require(index < len(self.records) and self.records[index][1] == 0x207,
                    'Missing immediately adjacent legacy formula String; cache not guessed')
            kind, value = 'text', self.byte_string(self.records[index][2], 0)
            index += 1
        elif cache[0] == 1:
            require(cache[2] in (0, 1), 'Invalid legacy Boolean formula cache')
            kind, value = 'boolean', bool(cache[2])
        elif cache[0] == 2:
            require(cache[2] in LEGACY_ERRORS, 'Invalid legacy error formula cache')
            kind, value = 'error', LEGACY_ERRORS[cache[2]]
            extra['cache_status'] = 'error_unverified'
        else:
            raise ValueError('Unknown legacy formula cache marker; BIFF8 empty marker not assumed')
        self.put(sheet, record, row, col, xf, kind, value, **extra)
        return index
