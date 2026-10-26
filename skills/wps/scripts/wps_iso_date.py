"""Limited ISO 8601 saved dates; preserve lexical precision and timezone."""
from datetime import date
import re

ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(\.[0-9]{1,9})?)?)?(Z|[+-][0-9]{2}:[0-9]{2})?")


def parse_iso_date(raw):
    if not raw:
        raise ValueError('Empty ISO date value')
    if len(raw) > 40:
        raise ValueError('Unsupported ISO date lexical subset')
    match = ISO_DATE.fullmatch(raw)
    if match is None:
        raise ValueError('Unsupported ISO date lexical subset')
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    try:
        date(int(year), int(month), int(day))
    except ValueError as exc:
        raise ValueError('Invalid ISO calendar date') from exc
    if hour is not None and (int(hour) > 23 or int(minute) > 59 or (second is not None and int(second) > 59)):
        raise ValueError('Invalid ISO clock time')
    offset = None
    if zone == 'Z':
        offset = 0
    elif zone:
        hours, minutes = map(int, zone[1:].split(':'))
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            raise ValueError('Invalid ISO timezone offset')
        offset = (hours * 60 + minutes) * (-1 if zone[0] == '-' else 1)
    return {'encoding': 'iso8601_saved_date', 'lexical': raw,
            'kind': 'datetime' if hour is not None else 'date',
            'timezone': 'unspecified' if zone is None else 'explicit_offset',
            'timezone_raw': zone, 'offset_minutes': offset,
            'fractional_second_raw': fraction,
            'precision': 'day' if hour is None else 'minute' if second is None else 'fractional_second' if fraction else 'second',
            'serial_conversion_performed': False, 'source_value_unchanged': True}
