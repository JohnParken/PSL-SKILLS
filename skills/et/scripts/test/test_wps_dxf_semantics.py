"""MS-XLS selected DXF semantics and layered diagnostics, synthetic only."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wps_dxf import validate_dxf, BOOLEAN_PROPERTIES, SCALAR_PROPERTIES
from read_wps import read_bytes, read, ParseError
from test_wps_dxf_integration import EMPTY_DXF
from test_wps_biff8 import workbook, rec
from test_wps_cfb import synthetic_cfb


def dxf(properties):
    return EMPTY_DXF[:16] + len(properties).to_bytes(2, 'little') + b''.join(
        kind.to_bytes(2, 'little') + (4 + len(value)).to_bytes(2, 'little') + value for kind, value in properties)


class DxfSemanticTests(unittest.TestCase):
    def test_all_boolean_flags_and_fixed_width_scalar_edges(self):
        for kind in BOOLEAN_PROPERTIES:
            for value in (0, 1):
                props = [(kind, bytes([value]))]
                if kind == 0x15 and value == 1:
                    props.append((0x0F, b'\x07'))
                self.assertEqual(validate_dxf(dxf(props))['property_interpretations'][0]['value'], value)
            for value in (b'', b'\x02', b'\x00\x00'):
                with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                    validate_dxf(dxf([(kind, value)]))
        for kind, (width, _, allowed) in SCALAR_PROPERTIES.items():
            values = [0, 255] if allowed is None else [min(allowed), max(allowed)]
            for value in values:
                raw = value.to_bytes(width, 'little', signed=kind == 0x2A)
                self.assertEqual(validate_dxf(dxf([(kind, raw)]))['property_interpretations'][0]['value'], value)
            with self.assertRaises(ValueError):
                validate_dxf(dxf([(kind, bytes(width + 1))]))

    def test_out_of_range_values_refused_not_interpreted_as_style(self):
        for kind, raw in [(0x0F, b'\x08'), (0x10, b'\x05'), (0x12, b'\x10\x00'), (0x13, b'\x03'),
                          (0x23, b'\x06'), (0x24, (19).to_bytes(4, 'little')), (0x24, (8192).to_bytes(4, 'little')),
                          (0x2A, (-16).to_bytes(2, 'little', signed=True)), (0x2A, (16).to_bytes(2, 'little'))]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_dxf(dxf([(kind, raw)]))

    def test_distributed_alignment_dependency_does_not_guess_duplicate_precedence(self):
        for props in [[(0x15, b'\x01')], [(0x15, b'\x01'), (0x0F, b'\x02')]]:
            with self.assertRaises(ValueError):
                validate_dxf(dxf(props))
        self.assertEqual(len(validate_dxf(dxf([(0x15, b'\x01'), (0x0F, b'\x07')]))['property_interpretations']), 2)
        duplicated = validate_dxf(dxf([(0x0F, b'\x07'), (0x15, b'\x01'), (0x0F, b'\x00')]))
        self.assertEqual(len(duplicated['property_interpretations']), 3)
        self.assertEqual(duplicated['effective_formatting'], 'unknown')

    def test_font_name_utf16_length_and_raw_retention(self):
        for text in ('宋体', 'A' * 32, '😀字体', ''):
            encoded = text.encode('utf-16le')
            raw = (len(encoded) // 2).to_bytes(2, 'little') + encoded
            result = validate_dxf(dxf([(0x18, raw)]))
            self.assertEqual(result['property_interpretations'][0]['value'], text)
            self.assertEqual(result['properties'][0]['payload_hex'], raw.hex())
        for raw in (b'', b'\x01', b'\x01\x00', b'\x01\x00\x00\xd8', b'\x21\x00' + b'A\x00' * 33):
            with self.assertRaises(ValueError):
                validate_dxf(dxf([(0x18, raw)]))

    def test_legal_uninterpreted_property_preserves_cells_partial_and_raw(self):
        # XFPropColor is outside this selected scalar interpreter, not a guessed color.
        payload = dxf([(0x01, bytes(8)), (0x14, b'\x01')])
        baseline = read_bytes(synthetic_cfb({'Workbook': workbook()}))
        result = read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0x088D, payload))}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], baseline['sheets'][0]['cells']['A1']['value'])
        meta = next(item for item in result['uninterpreted_metadata'] if item['record_id'] == '0x88d')
        self.assertEqual(meta['payload_hex'], payload.hex())
        self.assertEqual(meta['structure']['uninterpreted_property_count'], 1)
        self.assertEqual(meta['structure']['effective_formatting'], 'unknown')

    def test_invalid_semantics_retain_cells_and_precise_diagnostics(self):
        payload = dxf([(0x14, b'\xcc')])
        result = read_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0x088D, payload))}))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['sheets'][0]['cells']['A1']['value'], '部门')
        meta = next(item for item in result['uninterpreted_metadata'] if item['record_id'] == '0x88d')
        self.assertEqual(meta['payload_hex'], payload.hex())
        self.assertEqual(meta['structure']['property_interpretations'], [])
        self.assertEqual(meta['structure']['formatting_issues'][0]['property_id'], 0x14)
        self.assertEqual(meta['structure']['formatting_issues'][0]['offset'], 18)
        self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')
        self.assertFalse(result['omissions_affect_cell_values'])

    def test_layered_errors_distinguish_container_and_record_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'input.et'
            path.write_bytes(synthetic_cfb({'Workbook': workbook(extra_global=rec(0x088D, EMPTY_DXF))}))
            for script in ('read_wps.py', 'wps_biff8.py'):
                child = subprocess.run([sys.executable, '-I', '-S', '-B', str((Path(__file__).resolve().parents[1] / script)), './input.et',
                                        '--compatibility', 'strict'], cwd=temporary, capture_output=True, text=True)
                self.assertEqual(child.returncode, 3)
                result = json.loads(child.stdout)
                self.assertEqual(result['status'], 'partial')
                self.assertIn('compatibility_option_ignored',
                              [item['code'] for item in result['issues']])
            # Damaged CFB minor version is a container diagnostic; readable
            # workbook values are still delivered under the tolerant policy.
            damaged = bytearray(path.read_bytes())
            damaged[24:26] = bytes(2)
            path.write_bytes(damaged)
            result = read(path)
            self.assertIn(result['status'], ('ok', 'partial'))
            self.assertFalse(result.get('producer_verified', True))


if __name__ == '__main__':
    unittest.main()
