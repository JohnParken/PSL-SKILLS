"""Synthetic embedded-chart framing tests; no redistributed template bytes.

Charts are always retained as bounded opaque evidence. Malformed, unknown or
link-bearing chart records degrade to diagnostics and later worksheet cells are
still read; nothing is decoded, followed or executed.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_biff8 import workbook, rec, bof, cell
from test_wps_cfb import synthetic_cfb
import json
import subprocess
import tempfile
from wps_biff8 import parse_stream
from wps_biff8_charts import drawing_frame


def art(kind, payload, flags=15):
    return struct.pack('<HHI', flags, kind, len(payload)) + payload


GROUP = art(0xF000, art(0xF006, bytes(24), 0))
DRAWING = art(0xF002, art(0xF008, bytes(8), 0))
OBJECT = struct.pack('<4H', 0x15, 0x12, 5, 1) + b'\x11\x60' + bytes(16)
CHART = (bof(0x20) + rec(0x1001, bytes(2)) + rec(0x1002, bytes(16)) +
         rec(0x1033) + rec(0x0200, bytes(14)) + rec(0x1034) + rec(0xA))


def chart_workbook(chart=CHART, drawing=DRAWING, obj=OBJECT, extra=b''):
    after = rec(0x203, cell(1, 0) + struct.pack('<d', 42))
    body = rec(0xEC, drawing) + rec(0x5D, obj) + chart + after + extra
    return workbook(sheet_records=[body], extra_global=rec(0xEB, GROUP))


def issue_codes(result):
    return [item.get('code') for item in result.get('issues', ())]


class EmbeddedChartTests(unittest.TestCase):
    def read(self, data, **options):
        return parse_stream(data, **options)

    def test_bad_chart_payload_is_retained_and_cells_survive(self):
        malformed = CHART.replace(rec(0x1001, bytes(2)), rec(0x1001, b'x'))
        result = self.read(chart_workbook(chart=malformed))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        self.assertEqual(result['embedded_charts'][0]['payload_issues'][0]['message'],
                         'Malformed chart Units')
        self.assertIn('embedded_chart_retained_opaque', issue_codes(result))
        retained = [x for x in result['uninterpreted_metadata']
                    if x['name'] == 'EmbeddedChartRecordOpaque']
        rebuilt = b''.join(rec(int(x['record_id'], 16), bytes.fromhex(x['payload_hex']))
                           for x in retained)
        self.assertEqual(rebuilt, malformed)

    def test_truncated_or_unbalanced_chart_does_not_swallow_later_cell(self):
        for damaged in (CHART[:-4], CHART.replace(rec(0x1034), b'')):
            with self.subTest(chart=damaged[-8:].hex()):
                result = self.read(chart_workbook(chart=damaged))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
                self.assertIn('embedded_chart_invalid', issue_codes(result))
                retained = [x for x in result['uninterpreted_metadata']
                            if x['name'] == 'EmbeddedChartRecordOpaque']
                self.assertTrue(retained)

    def test_unknown_link_and_executable_chart_records_are_not_followed(self):
        for replacement in (rec(0x1999), bof(0x40), rec(0x1AE, b'link')):
            with self.subTest(record=replacement[:4].hex()):
                chart = CHART.replace(rec(0x0200, bytes(14)), replacement)
                result = self.read(chart_workbook(chart=chart))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
                self.assertFalse(result['external_links_followed'])
                self.assertFalse(result['formulas_executed'])
                codes = issue_codes(result)
                self.assertTrue(any(code in ('embedded_chart_unknown_record',
                                             'embedded_chart_link_not_followed',
                                             'embedded_chart_invalid')
                                    for code in codes))
        # FILEPASS is a workbook-level encryption marker; it is a hard refusal
        # wherever it appears in the framed stream and is never decrypted.
        chart = CHART.replace(rec(0x0200, bytes(14)), rec(0x2F, b'password'))
        with self.assertRaises(ValueError):
            self.read(chart_workbook(chart=chart))

    def test_bad_drawing_object_or_duplicate_ids_degrade(self):
        for obj in (OBJECT[:-1], OBJECT[:4] + b'\x08\x00' + OBJECT[6:],
                    OBJECT[:6] + bytes(2) + OBJECT[8:], OBJECT[:-1] + b'x'):
            with self.subTest(obj=obj.hex()):
                result = self.read(chart_workbook(obj=obj))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        duplicate = rec(0xEC, DRAWING) + rec(0x5D, OBJECT) + CHART
        result = self.read(chart_workbook(extra=duplicate))
        self.assertEqual(result['status'], 'partial')
        self.assertIn('duplicate_drawing_object_id', issue_codes(result))
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        for drawing in (DRAWING[:-1], art(0xF002, art(0xF123, b'x', 0))):
            with self.subTest(drawing=drawing.hex()):
                result = self.read(chart_workbook(drawing=drawing))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
                self.assertTrue(any(code in ('drawing_frame_invalid',
                                             'drawing_fragments_incomplete',
                                             'unknown_officeart_atoms')
                                    for code in issue_codes(result)))

    def test_generic_fragmented_drawing_is_concatenated_before_obj_and_chart(self):
        half = len(DRAWING) // 2
        body = (rec(0xEC, DRAWING[:half]) + rec(0xEC, DRAWING[half:])
                + rec(0x5D, OBJECT) + CHART
                + rec(0x203, cell(1, 0) + struct.pack('<d', 42)))
        stream = workbook(sheet_records=[body], extra_global=rec(0xEB, GROUP))
        result = self.read(stream)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        names = [item['name'] for item in result['uninterpreted_metadata']]
        self.assertIn('MsoDrawingOpaque', names)
        self.assertIn('MsoDrawingContinuationOpaque', names)
        self.assertEqual(len(result['embedded_charts']), 1)
        self.assertFalse(result['formulas_executed'])


    def test_budget_and_nested_drawing_frames(self):
        with patch('wps_biff8.MAX_METADATA_BYTES', 10):
            result = self.read(chart_workbook())
            self.assertEqual(result['status'], 'partial')
            self.assertIn('metadata_budget_truncated', issue_codes(result))
            self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        nested = art(0xF004, b'')
        for _ in range(18):
            nested = art(0xF004, nested)
        with self.assertRaisesRegex(ValueError, 'nesting'):
            drawing_frame(art(0xF002, nested), 0xF002)

    def test_chart_policy_reject_is_a_no_op_diagnostic(self):
        result = self.read(chart_workbook(), chart_policy='reject')
        self.assertEqual(result['chart_policy'], 'opaque')
        self.assertIn('chart_policy_option_ignored', issue_codes(result))
        self.assertEqual(len(result['embedded_charts']), 1)

    def test_default_cli_is_partial_and_input_unchanged(self):
        data = synthetic_cfb({'Workbook': chart_workbook()})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chart.et'
            path.write_bytes(data)
            process = subprocess.run([sys.executable, '-I', '-S', '-B',
                                      str(Path(__file__).resolve().parents[1] / 'read_et.py'),
                                      str(path)],
                                     capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, 3, process.stdout + process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['sheets'][0]['cells']['A2']['value'], '42')
            self.assertEqual(path.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
