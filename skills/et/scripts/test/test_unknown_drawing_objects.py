"""Bounded unknown drawing data must not hide uncertain coverage."""
import json
import struct
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from test_wps_embedded_charts import art, chart_workbook, DRAWING, OBJECT
from test_wps_cfb import synthetic_cfb
from test_wps_biff8 import rec, cell
from test_wps_biff8_drawing_notes import note_records, note_workbook
from read_et import read_bytes
from wps_access import ParseError


class UnknownDrawingTests(unittest.TestCase):
    def read(self, stream, **options):
        return read_bytes(synthetic_cfb({'Workbook': stream}), **options)

    def test_unknown_atom_retains_cell_and_diagnostic(self):
        drawing = art(0xF002, art(0xF123, b'abc', 0))
        result = self.read(chart_workbook(drawing=drawing))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        self.assertEqual(result['cell_value_coverage'], 'unverified_due_to_best_effort_salvage')
        issue = next(item for item in result['issues'] if item['code'] == 'unknown_officeart_atoms')
        self.assertEqual(issue['records'][0]['record_type'], '0xf123')
        with self.assertRaises(ParseError):
            self.read(chart_workbook(drawing=drawing), compatibility='strict')

    def test_unknown_bounded_object_retains_later_cell(self):
        obj = OBJECT[:4] + struct.pack('<H', 8) + OBJECT[6:]
        result = self.read(chart_workbook(obj=obj, chart=b''))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        self.assertEqual(result['opaque_objects'][0]['object_type_raw'], 8)
        self.assertTrue(result['omissions_affect_cell_values'])

    def test_cli_partial_exit_and_budget(self):
        data = synthetic_cfb({'Workbook': chart_workbook(drawing=art(0xF002, art(0xF123, b'abc', 0)))})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.et'
            path.write_bytes(data)
            process = subprocess.run([sys.executable, '-I', '-S', '-B',
                                      str(Path(__file__).resolve().parents[1] / 'read_et.py'), 'input.et'],
                                     cwd=directory, capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, 3)
            self.assertEqual(json.loads(process.stdout)['sheets'][0]['cells']['A2']['value'], '42')
        with patch('wps_biff8.MAX_METADATA_BYTES', 10), self.assertRaises(ParseError):
            read_bytes(data)

    def test_bounded_unknown_container_preserves_before_and_after_cells(self):
        unknown = art(0xF123, art(0xF124, b'abc', 0))
        drawing = art(0xF002, unknown)
        before = rec(0x203, cell(0, 0) + struct.pack('<d', 17))
        stream = chart_workbook(drawing=drawing).replace(rec(0xEC, drawing), before + rec(0xEC, drawing), 1)
        result = self.read(stream)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], 17)
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        self.assertTrue(result['omissions_affect_cell_values'])
        issue = next(item for item in result['issues'] if item['code'] == 'unknown_officeart_atoms')
        self.assertEqual([x['record_type'] for x in issue['records']], ['0xf123', '0xf124'])
        with self.assertRaises(ParseError):
            self.read(stream, compatibility='strict')

    def test_unknown_note_container_in_fragmented_group(self):
        records = note_records()
        payload = bytearray(records[0][1])
        unknown = art(0xF123, art(0xF124, b'abc', 0))
        for offset in (4, 12, 20):
            size = struct.unpack_from('<I', payload, offset)[0]
            struct.pack_into('<I', payload, offset, size + len(unknown))
        payload[40:40] = unknown
        records[0] = (0xEC, bytes(payload))
        after = rec(0x203, cell(4, 0) + struct.pack('<d', 42.5))
        result = self.read(note_workbook(records, after=after))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '00123')
        self.assertEqual(result['sheets'][0]['cells']['A5']['value'], 42.5)
        self.assertTrue(result['omissions_affect_cell_values'])
        self.assertEqual(result['cell_value_coverage'], 'unverified_due_to_best_effort_salvage')
        self.assertEqual([x['record_type'] for x in result['note_drawings'][0]['unknown_officeart_records']],
                         ['0xf123', '0xf124'])
        retained = [x for x in result['uninterpreted_metadata'] if x['name'] == 'MsoDrawingNoteFragmentOpaque']
        self.assertEqual(bytes.fromhex(retained[0]['payload_hex']), bytes(payload))
        with patch('wps_biff8.MAX_METADATA_BYTES', 10), self.assertRaises(ParseError):
            self.read(note_workbook(records, after=after))
        with self.assertRaises(ParseError):
            self.read(note_workbook(records, after=after), compatibility='strict')
        interrupted = records[:1] + [(0x203, cell(9, 0) + struct.pack('<d', 9))] + records[1:]
        with self.assertRaises(ParseError):
            self.read(note_workbook(interrupted, after=after))

    def test_bad_lengths_container_depth_and_known_atom_type_remain_rejected(self):
        nested = art(0xF123, b'')
        for _ in range(17):
            nested = art(0xF123, nested)
        for drawing in (DRAWING[:-1], art(0xF002, art(0xF123, b'x', 15)),
                        art(0xF002, art(0xF008, b'', 15)), art(0xF002, nested)):
            with self.subTest(drawing=drawing), self.assertRaises(ParseError):
                self.read(chart_workbook(drawing=drawing))


if __name__ == '__main__':
    unittest.main()
