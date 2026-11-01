"""Synthetic-only CHPX/PAPX-to-paragraph CP association regression tests.

A source intersection is not proof of effective formatting or WPS provenance.
"""
import unittest
from unittest.mock import patch

from read_wps import parse_streams
from test_wps import document
import wps_paragraph_evidence as evidence


class ParagraphEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {'index': 0, 'cp_start': 0, 'cp_end': 4, 'text_cp_end': 3},
            {'index': 1, 'cp_start': 4, 'cp_end': 6, 'text_cp_end': 5},
        ]

    def test_cross_paragraph_chpx_link_observed_only(self):
        item = evidence.link_evidence(
            self.sources, character_requested=True,
            character_runs=[{'cp_start': 1, 'cp_end': 5}])
        self.assertEqual(item['character_status'], 'direct_chpx_overlaps_only')
        self.assertEqual(item['character_links'], [
            {'paragraph_index': 0, 'run_index': 0, 'cp_start': 1, 'cp_end': 4},
            {'paragraph_index': 1, 'run_index': 0, 'cp_start': 4, 'cp_end': 5},
        ])
        self.assertEqual(item['effective_formatting'], 'unknown')
        self.assertEqual(item['table_grid'], 'unknown')

    def test_direct_row_end_not_promoted_to_full_papx(self):
        item = evidence.link_evidence(
            self.sources, table_rows=[
                {'source': {'row_mark_cp': 3}},
                {'source': {'row_mark_cp': 5}},
            ])
        self.assertEqual(item['papx_row_end_links'], [
            {'paragraph_index': 0, 'row_index': 0, 'row_mark_cp': 3},
            {'paragraph_index': 1, 'row_index': 1, 'row_mark_cp': 5},
        ])
        self.assertEqual(item['papx_row_status'], 'direct_row_end_markers_only')

    def test_unrequested_chpx_remains_unknown(self):
        item = evidence.link_evidence(
            self.sources, character_runs=[{'cp_start': 0, 'cp_end': 4}])
        self.assertEqual(item['character_status'], 'not_requested')
        self.assertEqual(item['character_links'], [])

    def test_ambiguous_chpx_is_not_associated(self):
        item = evidence.link_evidence(
            self.sources, character_requested=True,
            character_runs=[{'cp_start': 0, 'cp_end': 4},
                            {'cp_start': 3, 'cp_end': 5}])
        self.assertEqual(item['character_links'], [])
        self.assertEqual(item['character_status'], 'ambiguous_source_ranges')

    def test_row_end_outside_source_does_not_claim_link(self):
        item = evidence.link_evidence(
            self.sources, table_rows=[{'source': {'row_mark_cp': 7}}])
        self.assertEqual(item['papx_row_end_links'], [])

    def test_invalid_row_end_and_bounded_result(self):
        item = evidence.link_evidence(
            self.sources, table_rows=[{'source': {}}])
        self.assertEqual(item['papx_row_end_links'], [])
        self.assertEqual(item['papx_row_status'], 'ambiguous_row_end_source')
        with patch.object(evidence, 'MAX_LINKS', 1):
            item = evidence.link_evidence(self.sources)
        self.assertEqual(item['character_status'], 'resource_limited')

    def test_chpx_reader_integration_keeps_original_run_evidence(self):
        raw = '甲😀\r乙\r'.encode('utf-16le')
        stored_runs = [{
            'cp_start': 1, 'cp_end': 5,
            'source': {'stream': 'WordDocument', 'piece': 0},
            'modifiers': [], 'effective_visibility': 'unknown',
        }]
        with patch('wps_msdoc_charprops.inspect_character_properties',
                   return_value={'runs': stored_runs, 'fkp_ranges': []}):
            item = parse_streams(document([(raw, False)]),
                                 hidden_text='annotate',
                                 binary_paragraph_sources=True)
        self.assertEqual(item['paragraph_evidence']['character_status'],
                         'direct_chpx_overlaps_only')
        self.assertEqual(item['paragraph_evidence']['character_links'], [
            {'paragraph_index': 0, 'run_index': 0, 'cp_start': 1, 'cp_end': 4},
            {'paragraph_index': 1, 'run_index': 0, 'cp_start': 4, 'cp_end': 5},
        ])
        self.assertEqual(item['character_properties']['runs'], stored_runs)
        self.assertEqual(item['status'], 'partial')

    def test_papx_row_marker_integration_is_partial(self):
        raw = '甲\x07乙\x07\x07\r'.encode('utf-16le')
        stub = {
            'rows': [{'source': {'row_mark_cp': 4}}],
            'issues': [{'code': 'binary_table_structure_incomplete'}],
            'status': 'rows_partial', 'fkp_ranges': [],
        }
        with patch('wps_binary_tables.inspect_tables', return_value=stub):
            item = parse_streams(document([(raw, False)]),
                                 binary_paragraph_sources=True)
        self.assertEqual(item['paragraph_evidence']['papx_row_end_links'],
                         [{'paragraph_index': 0, 'row_index': 0, 'row_mark_cp': 4}])
        self.assertEqual(item['paragraph_evidence']['character_status'], 'not_requested')
        self.assertEqual(item['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
