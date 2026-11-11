"""Independent synthetic note fragments and hostile group boundaries."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_biff8 import rec, cell
from test_wps_biff8_salvage import values
from test_wps_cfb import synthetic_cfb
from test_wps_embedded_charts import art, GROUP
from test_wps_template_compatibility import template_workbook
from wps_biff8 import parse_stream
from wps_biff8_drawing_notes import note_drawing_group, DrawingResourceLimit, _escher_markers


def note_records(count=1, text='备注', terminal=False):
    """Construct the logical drawing first, then split at its client markers."""
    shape = art(0xF004, art(0xF00A, bytes(8), 2) + art(0xF011, b'', 0) + art(0xF00D, b'', 0))
    drawing = art(0xF002, art(0xF003, shape * count))
    output = []
    cursor = 0
    for number in range(count):
        identity = number + 1
        data_end = 16 + (number + 1) * len(shape) - 8
        output.append((0xEC, drawing[cursor:data_end]))
        obj = struct.pack('<4H', 0x15, 18, 25, identity) + bytes(14) + struct.pack('<2H', 0xD, 22) + bytes(26)
        output.append((0x5D, obj))
        output.append((0xEC, drawing[data_end:data_end + 8]))
        units = len(text.encode('utf-16le')) // 2
        output.append((0x1B6, bytes(10) + struct.pack('<2H', units, 16 if units else 0) + bytes(4)))
        if units:
            output.extend(((0x3C, b'\1' + text.encode('utf-16le')), (0x3C, bytes(16))))
        cursor = data_end + 8
    for number in range(count):
        note = struct.pack('<5HB', number, 0, 0, number + 1, 1, 1) + '作'.encode('utf-16le')
        output.append((0x1C, note + (b'\0' if terminal else b'')))
    return output


def replace_payload(records, index, payload):
    changed = records.copy()
    changed[index] = (changed[index][0], payload)
    return changed


def framed(records):
    offset = 0
    output = []
    for kind, payload in records:
        output.append((offset, kind, payload))
        offset += 4 + len(payload)
    return output


def note_workbook(records, after=b'', with_group=True):
    body = b''.join(rec(kind, payload) for kind, payload in records) + after
    return template_workbook(extra_sheet=body, extra_global=rec(0xEB, GROUP) if with_group else b'')


class NoteDrawingTests(unittest.TestCase):
    def read(self, records, **options):
        return parse_stream(note_workbook(records, **options), compatibility='data', chart_policy='opaque')

    def test_multiple_notes_preserve_all_bytes_and_cells_before_and_after(self):
        records = note_records(2)
        after = rec(0x203, cell(4, 0) + struct.pack('<d', 42.5))
        result = self.read(records, after=after)
        base = parse_stream(template_workbook(extra_sheet=after), compatibility='data')
        self.assertEqual(values(result), values(base))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')
        self.assertFalse(result['omissions_affect_cell_values'])
        self.assertFalse(result['formulas_executed'])
        self.assertFalse(result['external_links_followed'])
        sources = [item for item in result['uninterpreted_metadata'] if item['name'] in
                   ('MsoDrawingNoteFragmentOpaque', 'ObjNoteOpaque', 'TxONoteOpaque', 'NoteTextOrRunsOpaque', 'NoteLinkOpaque')]
        self.assertEqual([(int(item['record_id'], 16), bytes.fromhex(item['payload_hex'])) for item in sources], records)
        report = result['note_drawings'][0]
        drawing = b''.join(payload for kind, payload in records if kind == 0xEC)
        self.assertEqual(report['drawing_sha256'], hashlib.sha256(drawing).hexdigest())
        self.assertEqual(report['object_count'], 2)
        self.assertFalse(report['semantics_validated'])
        self.assertFalse(report['text_decoded_for_output'])
        self.assertEqual(result['embedded_charts'], [])

    def test_empty_text_and_note_unused_terminal_byte_variants(self):
        for text in ('', '备注', 'A😀B'):
            for terminal in (False, True):
                with self.subTest(text=text, terminal=terminal):
                    result = self.read(note_records(text=text, terminal=terminal))
                    source = next(item for item in result['uninterpreted_metadata'] if item['name'] == 'NoteLinkOpaque')
                    self.assertEqual(source['structure']['unused_terminal_byte_present'], terminal)

    def test_text_can_continue_with_encoding_changes_and_surrogate_pair(self):
        records = note_records(text='A😀B')
        records[4:5] = [(0x3C, b'\0A'), (0x3C, b'\1\x3d\xd8'), (0x3C, b'\1\0\xde'), (0x3C, b'\0B')]
        records[8:9] = [(0x3C, bytes(5)), (0x3C, bytes(11))]
        self.assertEqual(self.read(records)['note_drawings'][0]['object_count'], 1)

    def test_root_and_nested_truncation_overflow_and_fake_marker_refused(self):
        source = note_records()
        first = source[0][1]
        bad = []
        for offset, value in ((4, len(first) - 8), (4, 0xFFFFFFFF), (12, 1), (20, 0xFFFFFFFF)):
            changed = bytearray(first)
            struct.pack_into('<I', changed, offset, value)
            bad.append(bytes(changed))
        # A marker-looking byte sequence inside an opaque atom is not a real leaf.
        fake = bytearray(first)
        struct.pack_into('<I', fake, 28, 16)
        bad.append(bytes(fake))
        bad.extend((first[:-1], first + b'x'))
        for payload in bad:
            with self.subTest(payload=payload.hex()), self.assertRaises(ValueError):
                self.read(replace_payload(source, 0, payload))

    def test_interrupted_missing_and_orphan_chains_do_not_swallow_cells(self):
        source = note_records()
        extra = (0x203, cell(9, 0) + struct.pack('<d', 9))
        for index in range(len(source)):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.read(source[:index] + [extra] + source[index + 1:])
            with self.subTest(truncated=index), self.assertRaises(ValueError):
                self.read(source[:index])
        with self.assertRaises(ValueError):
            self.read(source + [(0x3C, b'unknown')])
        with self.assertRaises(ValueError):
            self.read(source, with_group=False)

    def test_object_macro_link_type_and_id_mismatch_refused(self):
        source = note_records()
        obj = source[1][1]
        wrong_type = obj[:4] + struct.pack('<H', 5) + obj[6:]
        for payload in (obj[:-1], obj + struct.pack('<2H', 4, 0), wrong_type,
                        obj[:6] + bytes(2) + obj[8:], obj[:22] + struct.pack('<2H', 9, 22) + obj[26:]):
            with self.subTest(payload=payload.hex()), self.assertRaises(ValueError):
                self.read(replace_payload(source, 1, payload))
        duplicated = note_records(2)
        duplicated[7] = (0x5D, obj)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.read(duplicated)

    def test_text_run_unicode_and_link_formula_lengths_refused(self):
        source = note_records()
        for payload in (source[3][1][:-1], source[3][1][:-2] + b'\1\0',
                        bytes(10) + struct.pack('<2H', 2, 8) + bytes(4),
                        bytes(10) + struct.pack('<2H', 0, 16) + bytes(4)):
            with self.subTest(txo=payload.hex()), self.assertRaises(ValueError):
                self.read(replace_payload(source, 3, payload))
        for payload in (b'\2xx', b'\1x', b'\1\0\xd8\0\xd8', b'\0abc', b'\0'):
            with self.subTest(text=payload.hex()), self.assertRaises(ValueError):
                self.read(replace_payload(source, 4, payload))
        for payload in (b'', bytes(15), bytes(17)):
            with self.subTest(runs=len(payload)), self.assertRaises(ValueError):
                self.read(replace_payload(source, 5, payload))

    def test_note_cell_author_and_object_links_refused(self):
        source = note_records()
        note = source[-1][1]
        invalid = [note[:-1], note + bytes(2), note[:10] + b'\2' + note[11:]]
        for offset, value in ((2, 256), (6, 2), (8, 0), (8, 55)):
            payload = bytearray(note)
            struct.pack_into('<H', payload, offset, value)
            invalid.append(bytes(payload))
        for payload in invalid:
            with self.subTest(note=payload.hex()), self.assertRaises(ValueError):
                self.read(replace_payload(source, len(source) - 1, payload))
        duplicated = note_records(2)
        duplicated[-1] = duplicated[-2]
        with self.assertRaises(ValueError):
            self.read(duplicated)

    def test_metadata_limits_and_existing_id_collisions(self):
        records = framed(note_records())
        size = sum(len(item[2]) for item in records)
        for byte_budget, record_budget in ((size - 1, len(records)), (size, len(records) - 1), (7, len(records))):
            with self.subTest(bytes=byte_budget, records=record_budget), self.assertRaises(DrawingResourceLimit):
                note_drawing_group(records, 0, byte_budget=byte_budget, record_budget=record_budget, existing_ids=set())
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            note_drawing_group(records, 0, byte_budget=size, record_budget=len(records), existing_ids={1})
        with patch('wps_biff8.MAX_METADATA_BYTES', 100), self.assertRaises(ValueError):
            self.read(note_records())

    def test_nested_frame_limits_and_marker_parent_order_remain_refused(self):
        nested = art(0xF004, b'')
        for _ in range(17):
            nested = art(0xF003, nested)
        malformed = (art(0xF002, nested), art(0xF002, art(0xF00A, b'', 2) * 4097),
                     art(0xF002, art(0xF011, b'', 0)),
                     art(0xF002, art(0xF004, art(0xF00D, b'', 0) + art(0xF011, b'', 0))))
        for raw in malformed:
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                _escher_markers(raw, (0, 0xEC, raw))
        with self.assertRaisesRegex(ValueError, 'excessive'):
            self.read(note_records(129))

    def test_strict_legacy_and_reject_modes_keep_their_previous_subset(self):
        raw = note_workbook(note_records())
        for mode, policy in (('strict', 'reject'), ('legacy_excel_clsid', 'opaque'), ('data', 'reject')):
            with self.subTest(mode=mode, policy=policy), self.assertRaises(ValueError):
                parse_stream(raw, compatibility=mode, chart_policy=policy)

    def test_default_cli_partial_json_and_readonly_input(self):
        data = synthetic_cfb({'Workbook': note_workbook(note_records())})
        with tempfile.TemporaryDirectory(prefix='et-notes-', dir='.') as directory:
            source = Path(directory) / 'synthetic.ett'
            with source.open('xb') as handle:
                handle.write(data)
            process = subprocess.run([sys.executable, '-I', '-S', '-B', str((Path(__file__).resolve().parents[1] / 'read_et.py')), str(source)],
                                     capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, 3, process.stderr)
            self.assertEqual(process.stderr, '')
            result = json.loads(process.stdout)
            self.assertEqual(result['status'], 'partial')
            self.assertFalse(result['reading_assessment']['full_format_verified'])
            self.assertEqual(source.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
