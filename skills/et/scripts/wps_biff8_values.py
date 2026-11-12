"""Bounded BIFF8 string continuation and numeric value decoders."""
import math
import struct
from decimal import Decimal

from wps_biff8_errors import EncryptedError, ResourceLimitError, UnsupportedError, require

class ContinuedStrings:
    """MS-XLS Unicode characters and raw rich SST runs across Continue records.

    A fixed string header must stay in one record. A continuation of character
    data carries an encoding byte; a new string at a record boundary does not.
    UTF-16 units are assembled before decoding, so surrogate pairs may span records.
    """
    def __init__(self, records, index, position=0, *, metadata_limit=1024*1024):
        self.metadata_limit = metadata_limit
        self.records = records
        self.index = index
        self.position = position

    def advance(self):
        self.index += 1
        require(self.index < len(self.records) and self.records[self.index][1] == 0x003C,
                'Missing Continue record after string data')
        self.position = 0
        require(bool(self.records[self.index][2]), 'Empty Continue record')

    def string(self, allow_rich=False, metadata_budget=None, allow_phonetic=False,
               metadata_entries_remaining=None, ignore_reserved_flags=False,
               retain_metadata=True):
        try:
            return self._string(allow_rich, metadata_budget, allow_phonetic,
                                metadata_entries_remaining, ignore_reserved_flags,
                                retain_metadata)
        except ValueError as exc:
            # Add physical context without changing the expected failure class.
            # Programming errors (IndexError, TypeError, etc.) are not swallowed.
            index = min(self.index, len(self.records)-1)
            offset, kind, _ = self.records[index]
            wrapped = type(exc)(f'{exc}; Workbook record_offset={offset} '
                                f'payload_offset={self.position}')
            wrapped.location = {'record_offset': offset,
                                'payload_offset': self.position,
                                'record_id': hex(kind)}
            raise wrapped from exc

    def _skip_bytes(self, needed):
        """Advance over retained-metadata bytes without decoding them."""
        while needed:
            payload = self.records[self.index][2]
            if self.position == len(payload):
                self.advance()
                payload = self.records[self.index][2]
            take = min(needed, len(payload)-self.position)
            self.position += take
            needed -= take

    def _string(self, allow_rich=False, metadata_budget=None, allow_phonetic=False,
                metadata_entries_remaining=None, ignore_reserved_flags=False,
                retain_metadata=True):
        self.last_rich = None
        self.last_phonetic = None
        payload = self.records[self.index][2]
        if self.position == len(payload):
            self.advance()
            payload = self.records[self.index][2]
        require(len(payload)-self.position >= 3, 'String fixed header crosses record boundary')
        count, flag = struct.unpack_from('<HB', payload, self.position)
        self.last_flags_raw = flag
        if ignore_reserved_flags:
            require(not allow_rich and not allow_phonetic, 'Reserved masking applies only to plain XLUnicodeString')
            flag &= 1
        require(count <= 32767, 'String count exceeds 32767')
        allowed_flags = {0, 1}
        if allow_rich:
            allowed_flags.update((8, 9))
        if allow_phonetic:
            allowed_flags.update((4, 5))
        if allow_rich and allow_phonetic:
            allowed_flags.update((12, 13))
        if flag not in allowed_flags:
            raise UnsupportedError('Phonetic/rich-cache/unknown string flags unsupported')
        start = self.position
        record_offset = self.records[self.index][0]
        self.position += 3
        run_count = 0
        if flag & 8:
            require(len(payload)-self.position >= 2, 'Rich string fixed header crosses record boundary')
            run_count = struct.unpack_from('<H', payload, self.position)[0]
            self.position += 2
            require(run_count > 0, 'Rich string has no format runs')
        ext_length = 0
        if flag & 4:
            require(len(payload)-self.position >= 4, 'Phonetic string fixed header crosses record boundary')
            ext_length = struct.unpack_from('<i', payload, self.position)[0]
            self.position += 4
            require(ext_length >= 0, 'Negative phonetic string cbExtRst')
        metadata_entries = int(bool(flag & 8)) + int(bool(flag & 4))
        if (retain_metadata and metadata_entries_remaining is not None
                and metadata_entries > metadata_entries_remaining):
            raise ResourceLimitError('Rich/phonetic string metadata entry limit exceeded before read')
        metadata_size = run_count * 4 + ext_length
        budget = self.metadata_limit if metadata_budget is None else metadata_budget
        if retain_metadata and metadata_size > min(self.metadata_limit, budget):
            raise ResourceLimitError('Rich/phonetic string metadata budget exceeded before read')
        character_flag = flag & 1
        remaining = count
        units = bytearray()
        while remaining:
            payload = self.records[self.index][2]
            if self.position == len(payload):
                self.advance()
                payload = self.records[self.index][2]
                character_flag = payload[0]
                require(character_flag in (0, 1), 'Invalid Continue encoding flag')
                self.position = 1
                require(self.position < len(payload), 'Continue has no character data')
            width = 2 if character_flag else 1
            available = len(payload)-self.position
            take = min(remaining, available//width)
            require(take > 0, 'UTF-16 character split within a Continue boundary')
            raw = payload[self.position:self.position+take*width]
            if character_flag:
                units.extend(raw)
            else:
                for byte in raw:
                    units.extend((byte, 0))
            self.position += take*width
            remaining -= take
            if remaining:
                require(self.position == len(payload), 'UTF-16 character split within a Continue boundary')
        try:
            value = units.decode('utf-16le')
        except UnicodeDecodeError as exc:
            raise ValueError('Malformed UTF-16 string across Continue records') from exc
        if run_count and not retain_metadata:
            self._skip_bytes(run_count * 4)
        elif run_count:
            # Unlike a continuation of characters, raw FormatRun bytes carry no
            # encoding flag. Never interpret a run byte as a character option.
            needed = run_count * 4
            runs = bytearray()
            while needed:
                payload = self.records[self.index][2]
                if self.position == len(payload):
                    self.advance()
                    payload = self.records[self.index][2]
                take = min(needed, len(payload)-self.position)
                runs.extend(payload[self.position:self.position+take])
                self.position += take
                needed -= take
            previous = -1
            for at in range(0, len(runs), 4):
                character, font = struct.unpack_from('<HH', runs, at)
                require(previous < character <= count, 'Rich FormatRun indexes must increase within string bounds')
                if character != count:
                    require(font != 4 and font <= 1022, 'Invalid rich string FontIndex')
                previous = character
            self.last_rich = {'record_offset': record_offset,
                              'payload_offset': start, 'utf16_units': count,
                              'run_count': run_count, 'runs_bytes': bytes(runs)}
        if flag & 4 and not retain_metadata:
            self._skip_bytes(ext_length)
        elif flag & 4:
            # ExtRst is opaque metadata. Continue records here do not carry the
            # character-encoding byte used while string characters remain.
            needed = ext_length
            ext_bytes = bytearray()
            while needed:
                payload = self.records[self.index][2]
                if self.position == len(payload):
                    self.advance()
                    payload = self.records[self.index][2]
                take = min(needed, len(payload)-self.position)
                ext_bytes.extend(payload[self.position:self.position+take])
                self.position += take
                needed -= take
            self.last_phonetic = {'record_offset': record_offset,
                                  'payload_offset': start, 'utf16_units': count,
                                  'ext_bytes': bytes(ext_bytes)}
        return value

    def finish(self):
        require(self.position == len(self.records[self.index][2]), 'Unparsed continued string tail')
        return self.index + 1


def unicode_string(data,offset=0,short=False,ignore_reserved_flags=False):
    prefix=2 if short else 3
    require(len(data)>=offset+prefix,'Truncated Unicode string header')
    count=data[offset] if short else struct.unpack_from('<H',data,offset)[0]
    flag=data[offset+prefix-1]
    if ignore_reserved_flags:
        require(not short, 'Short string reserved flags have a separate contract')
        flag &= 1
    require(count<=32767 and flag in (0,1),'Rich/phonetic/unknown string flags unsupported')
    start=offset+prefix
    end=start+count*(2 if flag else 1)
    require(end<=len(data),'Truncated string; Continue records unsupported')
    try:
        value=data[start:end].decode('utf-16le' if flag else 'latin1')
    except UnicodeDecodeError as exc:
        raise ValueError('Malformed UTF-16 string') from exc
    return value,end


def number(raw):
    require(len(raw)==8,'Xnum must contain eight bytes')
    value=struct.unpack('<d',raw)[0]
    require(math.isfinite(value),'Nonfinite Xnum unsupported')
    return Decimal.from_float(value)


def rk_value(raw):
    require(len(raw)==4,'RK value must contain four bytes')
    bits=struct.unpack('<I',raw)[0]
    if bits&2:
        value=Decimal(struct.unpack('<i',raw)[0]>>2)
    else:
        value=number(struct.pack('<Q',(bits&0xFFFFFFFC)<<32))
    if bits&1:
        sign,digits,exponent=value.as_tuple()
        value=Decimal((sign,digits,exponent-2))
    return value
