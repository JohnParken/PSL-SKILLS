# SPDX-License-Identifier: MIT
"""Validate a bounded WPS spreadsheet JSON result for existing read-only analysis.
No binary parser, conversion, formula evaluation, or file writing occurs here.
"""
from decimal import Decimal, InvalidOperation
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

MAX_RESULT_BYTES = 16 * 1024 * 1024


def load_result(path, address):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('WPS JSON input must be a regular file')
        if before.st_size > MAX_RESULT_BYTES:
            raise ValueError('WPS JSON result exceeds 16 MiB before reading')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(before.st_size + 1)
        after = os.fstat(descriptor)
        if (len(raw) != before.st_size or
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError('WPS JSON input changed during read')
    finally:
        os.close(descriptor)
    def reject_constant(value):
        raise ValueError('Nonfinite JSON constant refused: ' + value)
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('Duplicate JSON member: ' + key)
            obj[key] = value
        return obj
    try:
        result = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except RecursionError as exc:
        raise ValueError('WPS JSON nesting exceeds supported depth') from exc
    pending = [(result, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 64:
            raise ValueError('WPS JSON nesting exceeds 64')
        if isinstance(value, dict):
            pending.extend((item, depth+1) for item in value.values() if isinstance(item, (dict, list)))
        elif isinstance(value, list):
            pending.extend((item, depth+1) for item in value if isinstance(item, (dict, list)))
    if not isinstance(result, dict):
        raise ValueError('Require a WPS spreadsheet JSON object')
    schema = result.get('schema_version')
    if schema == 'biff8_readonly_v1' and result.get('biff_parsed') is True:
        max_row, max_col = 65536, 256
    elif schema == 'ooxml_spreadsheet_readonly_v1' and result.get('spreadsheet_parsed') is True:
        max_row, max_col = 1048576, 16384
    else:
        raise ValueError('Require a successful supported WPS spreadsheet JSON schema')
    if result.get('status') not in ('ok', 'partial', 'read_biff8_subset', 'read_biff8_subset_with_diagnostics'):
        raise ValueError('Failed WPS extraction cannot be analyzed')
    if result.get('date_system') not in ('1900', '1904'):
        raise ValueError('Missing explicit WPS date system')
    sheets = result.get('sheets')
    if not isinstance(sheets, list) or not 1 <= len(sheets) <= 128:
        raise ValueError('Invalid WPS result sheet list')
    cells, merges = {}, {}
    count = 0
    for sheet in sheets:
        if not isinstance(sheet, dict) or not isinstance(sheet.get('name'), str) or not 1 <= len(sheet['name']) <= 31 or sheet['name'] in cells:
            raise ValueError('Invalid or duplicate WPS sheet name')
        name = sheet['name']; source = sheet.get('cells')
        if not isinstance(source, dict):
            raise ValueError('Invalid WPS cells object')
        count += len(source)
        if count > 200000:
            raise ValueError('WPS result exceeds 200000 cells')
        cells[name] = {}
        for coordinate, cell in source.items():
            row, col = address(coordinate)
            if row > max_row or col > max_col or coordinate != coordinate.upper() or not isinstance(cell, dict):
                raise ValueError('Invalid WPS result cell')
            kind, value = cell.get('type'), cell.get('value')
            if kind == 'number':
                if not isinstance(value, str) or len(value) > 1000:
                    raise ValueError('WPS numeric JSON values must be bounded decimal strings')
                try:
                    value = Decimal(value)
                except InvalidOperation as exc:
                    raise ValueError('Invalid WPS numeric value') from exc
                if not value.is_finite() or abs(value.adjusted()) > 1000:
                    raise ValueError('Nonfinite or oversized WPS number')
            elif kind == 'boolean':
                if type(value) is not bool: raise ValueError('Invalid WPS boolean')
            elif kind in ('text', 'error'):
                if not isinstance(value, str) or len(value) > 32767: raise ValueError('Invalid WPS text/error')
            elif kind == 'blank':
                if value is not None: raise ValueError('Invalid WPS blank')
            else:
                raise ValueError('Unknown WPS cell type')
            mapped = {**cell, 'value': value, 'type': 'e' if kind == 'error' else kind}
            if 'formula' in cell:
                if not isinstance(cell['formula'], dict): raise ValueError('Invalid opaque formula metadata')
                # Existing analysis requires explicit cached opt-in. Preserve missing/error.
                if cell.get('cache_status') == 'unverified_empty_string': mapped['cache_status'] = 'unverified'
            cells[name][coordinate] = mapped
        values = sheet.get('merges', [])
        if not isinstance(values, list) or len(values) > 256:
            raise ValueError('Invalid WPS merge list')
        merges[name] = []
        for merge in values:
            if not isinstance(merge, dict) or not isinstance(merge.get('range'), str):
                raise ValueError('Invalid WPS merge')
            ends = merge['range'].split(':')
            if len(ends) != 2: raise ValueError('Invalid WPS merge range')
            first, last = address(ends[0]), address(ends[1])
            if not (1 <= first[0] <= last[0] <= max_row and 1 <= first[1] <= last[1] <= max_col):
                raise ValueError('WPS merge out of range')
            merges[name].append({'ref': merge['range']})
    return SimpleNamespace(cells=cells, date1904=result['date_system'] == '1904',
                           wps_merges=merges, wps_input_schema=schema, wps_source_status=result['status'],
                           wps_issues=result.get('issues', []))
