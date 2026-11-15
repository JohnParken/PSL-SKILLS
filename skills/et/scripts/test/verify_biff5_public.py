#!/usr/bin/env python3
"""Opt-in maintenance probe of two public OpenOffice fixtures, never runtime.

Fetch immutable, hash-pinned originals in memory. Compare old BIFF values to an
independent stdlib XML/ZIP oracle for the official same-topic XLSX companion.
No package installation, office execution, external links, private files or saves.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.request
import zipfile
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_et import read_bytes

BASE = 'https://www.openoffice.org/sc/testdocs/'
FIXTURES = {
    'cells_import_biff5.xls': 'ac35c2d56d583eca5a6b22b4d3102f9b8df479547e9cd5d2512f29e355a10081',
    'cells_import_xml_11.xlsx': 'd4b07efeabedfb6b95ce69bb7640cb128faf695a0f6c4d803080157155724662',
}
# Manual structural alignment, verified against raw old LABEL row headers:
# old A25/A29/A33 mark Boolean/Error/Formula; modern A23/A27/A31 do so.
# Do not automatically align by matching values or claim whole-sheet equality.
CORE_MAP = {key: key for key in ('A1', 'B7', 'C7', 'B10', 'D10', 'F10', 'H10',
                                 'B13', 'D13', 'F13', 'H13')}
CORE_MAP.update({col + '27': col + '25' for col in 'AB'})
CORE_MAP.update({col + '31': col + '29' for col in 'ABCDEFG'})
CORE_MAP.update({col + '35': col + '33' for col in 'ABCDE'})
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def fetch(name):
    with urllib.request.urlopen(BASE + name, timeout=30) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != FIXTURES[name]:
        raise ValueError('Public fixture size/hash changed: ' + name)
    return data


def xml_oracle(data):
    """Independent same-topic fixture value oracle, not the ET OOXML reader."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        raw_strings = archive.read('xl/sharedStrings.xml')
        raw_sheet = archive.read('xl/worksheets/sheet1.xml')
    if any(b'<!DOCTYPE' in raw or b'<!ENTITY' in raw for raw in (raw_strings, raw_sheet)):
        raise ValueError('Unexpected XML declarations in pinned fixture')
    strings = [''.join(n.text or '' for n in node.findall('.//m:t', NS))
               for node in ElementTree.fromstring(raw_strings).findall('m:si', NS)]
    values = {}
    for node in ElementTree.fromstring(raw_sheet).findall('.//m:sheetData/m:row/m:c', NS):
        saved = node.find('m:v', NS)
        if saved is None:
            continue
        kind, value = node.get('t'), saved.text or ''
        if kind == 's':
            kind, value = 'text', strings[int(value)]
        elif kind == 'str':
            kind = 'text'
        elif kind == 'b':
            kind, value = 'boolean', value == '1'
        elif kind == 'e':
            kind = 'error'
        else:
            kind, value = 'number', float(value)
        values[node.get('r')] = (kind, value)
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true', required=True,
                        help='Explicitly fetch only the two hash-pinned public fixtures')
    parser.parse_args()
    old, modern = (fetch(name) for name in FIXTURES)
    result = read_bytes(old)
    actual = result['sheets'][0]['cells']
    expected = xml_oracle(modern)
    mismatches = []
    for coordinate, (kind, value) in expected.items():
        cell = actual.get(coordinate)
        observed = cell.get('value') if cell else None
        if cell and cell['type'] == 'number':
            observed = float(observed)
        if not cell or cell['type'] != kind or observed != value:
            mismatches.append({'cell': coordinate, 'expected_type': kind,
                               'expected': value, 'actual': str(observed)})
    core_mismatches = []
    for old_coordinate, modern_coordinate in CORE_MAP.items():
        kind, value = expected[modern_coordinate]
        cell = actual.get(old_coordinate)
        observed = cell.get('value') if cell else None
        if cell and cell['type'] == 'number':
            observed = float(observed)
        if not cell or cell['type'] != kind or observed != value:
            core_mismatches.append(old_coordinate)
    output = {'source_page': BASE + 'index.html', 'fixture_sha256': FIXTURES,
              'source_format': result['biff_version'], 'codepage': result['code_page_raw'],
              'schema': result['schema_version'], 'cells_including_blanks': len(actual),
              'whole_coordinate_comparisons': len(expected), 'whole_coordinate_differences': len(mismatches),
              'whole_sheet_equality_claimed': False, 'explicit_core_map': CORE_MAP,
              'core_comparisons': len(CORE_MAP), 'core_mismatches': core_mismatches,
              'status': result['status'], 'coverage': result['cell_value_coverage'],
              'limits': 'Only this public Excel95/BIFF5 fixture and saved values; '
                        'not full BIFF5/7 conformance, real WPS ET acceptance or layout validation.'}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if core_mismatches else 0


if __name__ == '__main__':
    raise SystemExit(main())
