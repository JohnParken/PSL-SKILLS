"""Synthetic coverage for MS-DOC table merge pairing."""
import copy
from pathlib import Path
import sys
import unittest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from wps_binary_table_merges import analyze_table_merges


def make_row(edges, roles, horizontal_merges=(), texts=None):
    if texts is None:
        texts = [''] * len(roles)
    return {
        'cells': [{'text': text} for text in texts],
        'stored_cell_layout': {
            'cell_edges_twips': list(edges),
            'horizontal_merges': list(horizontal_merges),
            'vertical_merge_roles': list(roles),
        },
    }


class BinaryTableMergeTests(unittest.TestCase):
    def test_exact_edges_pair_when_physical_indices_differ(self):
        rows = [
            make_row([0, 20, 70, 100], ['none', 'start', 'none']),
            make_row([0, 10, 20, 70, 90, 100],
                     ['none', 'none', 'continuation', 'none', 'none']),
        ]

        result = analyze_table_merges(rows)

        self.assertEqual(result['merge_status'], 'stored')
        self.assertEqual(result['column_edges_twips'], [0, 10, 20, 70, 90, 100])
        self.assertEqual(result['vertical_merges'], [{
            'row_index': 0,
            'row_count': 2,
            'left_twips': 20,
            'right_twips': 70,
            'cells': [
                {'first_cell': 1, 'cell_count': 1},
                {'first_cell': 2, 'cell_count': 1},
            ],
            'status': 'stored',
        }])

    def test_horizontal_and_vertical_merge_preserve_physical_coverage(self):
        rows = [
            make_row([0, 20, 40, 60, 80], ['none', 'start', 'start', 'none'],
                     [{'first_cell': 1, 'cell_count': 2}]),
            make_row([0, 20, 50, 60, 80], ['none', 'continuation', 'none', 'none'],
                     [{'first_cell': 1, 'cell_count': 2}]),
        ]

        result = analyze_table_merges(rows)

        self.assertEqual(len(result['horizontal_merges']), 2)
        self.assertEqual(result['horizontal_merges'][0], {
            'row_index': 0, 'first_cell': 1, 'cell_count': 2,
            'left_twips': 20, 'right_twips': 60,
        })
        self.assertEqual(result['vertical_merges'][0]['cells'], [
            {'first_cell': 1, 'cell_count': 2},
            {'first_cell': 1, 'cell_count': 2},
        ])
        self.assertEqual(result['vertical_merges'][0]['status'], 'stored')

    def test_incompatible_follower_role_keeps_horizontal_merge_only(self):
        row = make_row([0, 25, 50], ['start', 'continuation'],
                       [{'first_cell': 0, 'cell_count': 2}])

        result = analyze_table_merges([row])

        self.assertEqual(len(result['horizontal_merges']), 1)
        self.assertEqual(result['vertical_merges'], [])
        self.assertEqual(result['merge_status'], 'partial')
        self.assertIn('incompatible_horizontal_vertical_roles',
                      [issue['code'] for issue in result['merge_issues']])

    def test_missing_layout_breaks_a_chain_and_later_continuation_is_orphaned(self):
        rows = [
            make_row([0, 50], ['start']),
            {'cells': [{'text': ''}], 'stored_cell_layout': None},
            make_row([0, 50], ['continuation']),
        ]

        result = analyze_table_merges(rows)

        self.assertEqual(result['merge_status'], 'partial')
        self.assertEqual(result['vertical_merges'], [])
        self.assertIn('unknown_row_layout', [issue['code'] for issue in result['merge_issues']])
        self.assertIn('orphan_vertical_continuation',
                      [issue['code'] for issue in result['merge_issues']])

    def test_no_usable_layout_returns_unknown(self):
        result = analyze_table_merges([
            {'cells': [{'text': ''}], 'stored_cell_layout': None},
            {'cells': [{'text': ''}], 'stored_cell_layout': None},
        ])

        self.assertEqual(result['merge_status'], 'unknown')
        self.assertIsNone(result['column_edges_twips'])
        self.assertEqual(result['vertical_merges'], [])

    def test_overlapping_but_different_edges_never_pair(self):
        rows = [
            make_row([0, 30, 100], ['start', 'none']),
            make_row([0, 40, 100], ['continuation', 'none']),
        ]

        result = analyze_table_merges(rows)

        self.assertEqual(result['vertical_merges'], [])
        self.assertEqual(result['merge_status'], 'partial')
        self.assertIn('conflicting_vertical_span',
                      [issue['code'] for issue in result['merge_issues']])

    def test_verified_prefix_is_partial_after_overlapping_continuations(self):
        rows = [
            make_row([0, 50, 100], ['start', 'none']),
            make_row([0, 50, 100], ['continuation', 'none']),
            make_row([0, 20, 50, 100], ['continuation', 'continuation', 'none']),
        ]

        result = analyze_table_merges(rows)

        self.assertEqual(result['vertical_merges'], [{
            'row_index': 0,
            'row_count': 2,
            'left_twips': 0,
            'right_twips': 50,
            'cells': [
                {'first_cell': 0, 'cell_count': 1},
                {'first_cell': 0, 'cell_count': 1},
            ],
            'status': 'partial',
        }])
        self.assertIn('conflicting_vertical_span',
                      [issue['code'] for issue in result['merge_issues']])

    def test_nonempty_horizontal_member_of_continuation_is_rejected(self):
        horizontal = [{'first_cell': 0, 'cell_count': 2}]
        rows = [
            make_row([0, 20, 40], ['start', 'none'], horizontal),
            make_row([0, 20, 40], ['continuation', 'none'], horizontal,
                     texts=['', 'stored']),
        ]

        result = analyze_table_merges(rows)

        self.assertEqual(len(result['horizontal_merges']), 2)
        self.assertEqual(result['vertical_merges'], [])
        self.assertIn('nonempty_vertical_continuation',
                      [issue['code'] for issue in result['merge_issues']])

    def test_invalid_horizontal_bounds_and_vertical_roles_are_partial(self):
        bad_bounds = make_row([0, 50, 100], ['none', 'none'],
                              [{'first_cell': 1, 'cell_count': 2}])
        bad_roles = make_row([0, 50], ['unknown'])

        bounds_result = analyze_table_merges([
            bad_bounds,
            make_row([0, 50], ['none']),
        ])
        roles_result = analyze_table_merges([
            bad_roles,
            make_row([0, 50], ['none']),
        ])

        self.assertEqual(bounds_result['merge_status'], 'partial')
        self.assertIn('horizontal_merge_out_of_bounds',
                      [issue['code'] for issue in bounds_result['merge_issues']])
        self.assertEqual(roles_result['merge_status'], 'partial')
        self.assertIn('invalid_vertical_merge_roles',
                      [issue['code'] for issue in roles_result['merge_issues']])

    def test_invalid_edges_and_row_width_mismatch_block_pairing(self):
        duplicate_edges = make_row([0, 0], ['start'])
        valid_continuation = make_row([0, 40, 100], ['continuation', 'none'])

        result = analyze_table_merges([duplicate_edges, valid_continuation])

        self.assertEqual(result['vertical_merges'], [])
        self.assertEqual(result['merge_status'], 'partial')
        self.assertIn('invalid_cell_edges', [issue['code'] for issue in result['merge_issues']])

    def test_orphan_and_nonempty_continuations_are_reported(self):
        orphan = make_row([0, 50], ['continuation'])
        nonempty_pair = [
            make_row([0, 50], ['start']),
            make_row([0, 50], ['continuation'], texts=['stored text']),
        ]

        orphan_result = analyze_table_merges([orphan])
        nonempty_result = analyze_table_merges(nonempty_pair)

        self.assertIn('orphan_vertical_continuation',
                      [issue['code'] for issue in orphan_result['merge_issues']])
        self.assertIn('nonempty_vertical_continuation',
                      [issue['code'] for issue in nonempty_result['merge_issues']])
        self.assertEqual(nonempty_result['vertical_merges'], [])

    def test_valid_restart_closes_previous_chain(self):
        rows = [
            make_row([0, 50], ['start']),
            make_row([0, 50], ['continuation']),
            make_row([0, 50], ['start']),
            make_row([0, 50], ['continuation']),
        ]

        result = analyze_table_merges(rows)

        self.assertEqual(result['merge_status'], 'stored')
        self.assertEqual([merge['row_index'] for merge in result['vertical_merges']], [0, 2])
        self.assertEqual([merge['status'] for merge in result['vertical_merges']],
                         ['stored', 'stored'])

    def test_shifted_valid_restart_finishes_previous_chain_as_stored(self):
        rows = [
            make_row([0, 50, 100], ['start', 'none']),
            make_row([0, 50, 100], ['continuation', 'none']),
            make_row([0, 60, 100], ['start', 'none']),
            make_row([0, 60, 100], ['continuation', 'none']),
        ]

        result = analyze_table_merges(rows)

        self.assertEqual(result['merge_status'], 'stored')
        self.assertEqual([merge['row_index'] for merge in result['vertical_merges']], [0, 2])
        self.assertEqual([merge['status'] for merge in result['vertical_merges']],
                         ['stored', 'stored'])

    def test_invalid_horizontal_follower_role_taints_overlapping_chain(self):
        rows = [
            make_row([0, 50, 100], ['start', 'none']),
            make_row([0, 50, 100], ['continuation', 'none']),
            make_row([0, 30, 60, 100], ['none', 'start', 'none'],
                     [{'first_cell': 0, 'cell_count': 2}]),
        ]

        result = analyze_table_merges(rows)

        self.assertEqual(result['vertical_merges'][0]['status'], 'partial')
        self.assertEqual(result['vertical_merges'][0]['row_count'], 2)
        self.assertEqual(result['horizontal_merges'][0]['row_index'], 2)
        codes = [issue['code'] for issue in result['merge_issues']]
        self.assertIn('incompatible_horizontal_vertical_roles', codes)
        self.assertIn('conflicting_vertical_span', codes)

    def test_uncertain_end_marks_only_active_chain_partial(self):
        rows = [
            make_row([0, 50], ['start']),
            make_row([0, 50], ['continuation']),
            make_row([0, 50], ['start']),
            make_row([0, 50], ['continuation']),
        ]

        result = analyze_table_merges(rows, uncertain_end=True)

        self.assertEqual([merge['status'] for merge in result['vertical_merges']],
                         ['stored', 'partial'])
        self.assertEqual(result['merge_status'], 'partial')
        self.assertIn('uncertain_vertical_merge_end',
                      [issue['code'] for issue in result['merge_issues']])

    def test_grid_requires_same_outer_edges_and_at_most_256_columns(self):
        differing_outer = [
            make_row([0, 50, 100], ['none', 'none']),
            make_row([1, 50, 101], ['none', 'none']),
        ]
        too_many_columns = make_row(list(range(258)), ['none'] * 257)

        self.assertIsNone(analyze_table_merges(differing_outer)['column_edges_twips'])
        self.assertIsNone(analyze_table_merges([too_many_columns])['column_edges_twips'])

    def test_diagnostic_cap_preserves_exact_unresolved_count(self):
        rows = [make_row([0, 50], ['continuation']) for _ in range(105)]

        result = analyze_table_merges(rows)

        self.assertEqual(len(result['merge_issues']), 100)
        self.assertEqual(result['merge_issues_omitted'], 5)
        self.assertEqual(result['unresolved_merge_count'], 105)

    def test_result_has_fixed_fields_and_does_not_mutate_rows(self):
        rows = [make_row([0, 50], ['start']), make_row([0, 50], ['continuation'])]
        original = copy.deepcopy(rows)

        result = analyze_table_merges(rows)

        self.assertEqual(set(result), {
            'column_edges_twips', 'horizontal_merges', 'vertical_merges', 'merge_status',
            'unresolved_merge_count', 'merge_issues', 'merge_issues_omitted',
        })
        self.assertEqual(rows, original)


if __name__ == '__main__':
    unittest.main()
