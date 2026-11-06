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

    def test_truncated_or_unknown_container_is_rejected(self):
        for drawing in (DRAWING[:-1], art(0xF002, art(0xF123, b'x', 15))):
            with self.subTest(drawing=drawing), self.assertRaises(ParseError):
                self.read(chart_workbook(drawing=drawing))


if __name__ == '__main__':
    unittest.main()
