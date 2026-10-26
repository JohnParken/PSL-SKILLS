"""Synthetic embedded-chart framing tests; no redistributed template bytes."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
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


class EmbeddedChartTests(unittest.TestCase):
    def read(self, data):
        return parse_stream(data, compatibility='legacy_excel_clsid', chart_policy='opaque')

    def test_opaque_chart_preserves_all_bytes_and_resumes_cells(self):
        data = chart_workbook()
        result = self.read(data)
        self.assertEqual(result['cell_count'], 1)
        self.assertEqual(result['sheets'][0]['cells']['A2']['value'], 42)
        self.assertEqual(len(result['embedded_charts']), 1)
        self.assertFalse(result['charts_decoded'])
        self.assertFalse(result['formulas_executed'])
        self.assertFalse(result['external_links_followed'])
        self.assertTrue(any(i['code'] == 'embedded_chart_retained_opaque' for i in result['issues']))
        retained = [x for x in result['uninterpreted_metadata'] if x['name'] == 'EmbeddedChartRecordOpaque']
        rebuilt = b''.join(rec(int(x['record_id'], 16), bytes.fromhex(x['payload_hex'])) for x in retained)
        self.assertEqual(rebuilt, CHART)
        with self.assertRaises(ValueError):
            parse_stream(data, compatibility='legacy_excel_clsid')
        with self.assertRaises(ValueError):
            parse_stream(data, chart_policy='opaque')

    def test_unknown_macro_nested_and_unbalanced_chart_records_fail(self):
        for chart in (CHART.replace(rec(0x1034), b''), CHART.replace(rec(0x1033), b''),
                      CHART[:-4], CHART.replace(rec(0x0200, bytes(14)), rec(0xFFFF)),
                      CHART.replace(rec(0x0200, bytes(14)), bof(0x40)),
                      CHART.replace(rec(0x0200, bytes(14)), rec(0x2F, b'password')),
                      CHART.replace(rec(0x0200, bytes(14)), rec(0x1AE, b'link'))):
            with self.subTest(chart=chart), self.assertRaises(ValueError):
                self.read(chart_workbook(chart=chart))

    def test_bad_drawing_object_or_duplicate_ids_fail(self):
        for obj in (OBJECT[:-1], OBJECT[:4] + b'\x08\x00' + OBJECT[6:],
                    OBJECT[:6] + bytes(2) + OBJECT[8:], OBJECT[:-1] + b'x'):
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                self.read(chart_workbook(obj=obj))
        duplicate = rec(0xEC, DRAWING) + rec(0x5D, OBJECT) + CHART
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.read(chart_workbook(extra=duplicate))
        for drawing in (DRAWING[:-1], art(0xF002, art(0xF123, b'x', 0))):
            with self.subTest(drawing=drawing), self.assertRaises(ValueError):
                self.read(chart_workbook(drawing=drawing))

    def test_budgets_and_nested_drawing_frames(self):
        with patch('wps_biff8.MAX_METADATA_BYTES', 10), self.assertRaises(ValueError):
            self.read(chart_workbook())
        nested = art(0xF004, b'')
        for _ in range(18):
            nested = art(0xF004, nested)
        with self.assertRaisesRegex(ValueError, 'nesting'):
            drawing_frame(art(0xF002, nested), 0xF002)

    def test_main_cli_requires_explicit_chart_policy_and_keeps_inputs(self):
        class Runner:
            def run_read(self, data, suffix, *options):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp)/('input'+suffix); path.write_bytes(data)
                    command = [sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name('read_wps.py')), str(path), *options]
                    result = subprocess.run(command, capture_output=True, text=True)
                    assert path.read_bytes() == data
                    return result.returncode, json.loads(result.stdout)
        runner = Runner()
        data = synthetic_cfb({'Workbook': chart_workbook()})
        self.assertEqual(runner.run_read(data, '.ett', '--compatibility', 'legacy_excel_clsid')[0], 1)
        code, result = runner.run_read(data, '.ett', '--compatibility', 'legacy_excel_clsid', '--chart-policy', 'opaque')
        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result['embedded_charts']), 1)
        self.assertEqual(runner.run_read(b'a,b', '.csv', '--chart-policy', 'opaque')[0], 1)


if __name__ == '__main__':
    unittest.main()
