#!/usr/bin/env python3
"""OOXML saved-text escape decoding and evidence regression tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from read_et import read_bytes
from test_wps_ooxml_sheet import T, build_package, package, text_parts_of
from wps_ooxml_escape import decode_ooxml_text


class EscapeDecoderTests(unittest.TestCase):
    def test_control_characters_decode_once_with_raw_evidence(self):
        value, evidence = decode_ooxml_text('A_x000D_B')
        self.assertEqual(value, 'A\rB')
        self.assertEqual(evidence['escape_sequences'], 1)
        self.assertEqual(evidence['raw_text'], 'A_x000D_B')
        value, evidence = decode_ooxml_text('A_x0001_B')
        self.assertEqual(value, 'A\x01B')
        self.assertEqual(evidence['escape_sequences'], 1)

    def test_literal_escape_sequence_is_protected_and_not_redecoded(self):
        value, evidence = decode_ooxml_text('_x005F_x000D_')
        self.assertEqual(value, '_x000D_')
        self.assertEqual(evidence['literal_escapes'], 1)
        self.assertEqual(evidence['escape_sequences'], 0)
        self.assertEqual(decode_ooxml_text('__x005F_x000D_')[0], '__x000D_')

    def test_surrogate_pair_combines_and_lone_surrogate_stays_literal(self):
        self.assertEqual(decode_ooxml_text('_xD83D__xDE00_')[0], '😀')
        value, evidence = decode_ooxml_text('_xD800_')
        self.assertEqual(value, '_xD800_')
        self.assertEqual(evidence['invalid_sequences'], 1)

    def test_invalid_or_case_variant_sequences_are_bounded(self):
        self.assertEqual(decode_ooxml_text('_xzzzz_')[0], '_xzzzz_')
        self.assertEqual(decode_ooxml_text('_x000d_')[0], '\r')
        self.assertEqual(decode_ooxml_text('plain')[0], 'plain')
        self.assertIsNone(decode_ooxml_text('plain')[1])


class EscapeIntegrationTests(unittest.TestCase):
    def shared_result(self, saved):
        sst = (f'<sst xmlns="{T}" count="1" uniqueCount="1"><si><t>{saved}</t>'
               '</si></sst>')
        parts = text_parts_of(package(cells='<c r="A1" t="s"><v>0</v></c>'))
        parts['xl/sharedStrings.xml'] = sst
        return read_bytes(build_package(parts))

    def test_shared_string_control_is_decoded_and_evidence_is_linked(self):
        result = self.shared_result('A_x000D_B')
        cell = result['sheets'][0]['cells']['A1']
        self.assertEqual(cell['value'], 'A\rB')
        self.assertEqual(cell['text_encoding']['source'], 'shared_string')
        self.assertEqual(cell['text_encoding']['sst_index'], 0)
        self.assertNotIn('raw_text', cell['text_encoding'])
        self.assertEqual(result['shared_string_encoding'][0]['raw_text'],
                         'A_x000D_B')
        self.assertEqual(result['cell_value_coverage'],
                         'complete_within_declared_sheets')

    def test_literal_shared_string_keeps_its_text(self):
        result = self.shared_result('_x005F_x000D_')
        cell = result['sheets'][0]['cells']['A1']
        self.assertEqual(cell['value'], '_x000D_')
        self.assertEqual(cell['text_encoding']['literal_escapes'], 1)
        self.assertNotIn('\r', cell['value'])

    def test_inline_and_rich_text_are_decoded_with_cell_local_evidence(self):
        cells = (
            f'<c r="A1" t="inlineStr"><is><t>A_x0001_B</t></is></c>'
            f'<c r="B1" t="inlineStr"><is><r><t>C_x000D_D</t></r>'
            f'<r><t>E</t></r></is></c>')
        result = read_bytes(package(cells=cells))
        cells = result['sheets'][0]['cells']
        self.assertEqual(cells['A1']['value'], 'A\x01B')
        self.assertEqual(cells['A1']['text_encoding']['raw_text'], 'A_x0001_B')
        self.assertEqual(cells['B1']['value'], 'C\rDE')
        self.assertEqual([item['text'] for item in
                          cells['B1']['rich_text']['segments']], ['C\rD', 'E'])
        self.assertEqual(cells['B1']['text_encoding']['raw_text'],
                         'C_x000D_D')


if __name__ == '__main__':
    unittest.main()
