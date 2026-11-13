"""Exercise stored MS-DOC table groups and merges through the public reader."""

from pathlib import Path
import struct
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from read_wps import read_bytes
from test_wps_msdoc_maximum import IN_TABLE, ROW_END, cfb_payload, papx_document, set_pair


def definition(edges, flags):
    """Serialize the literal TDefTable operand with one TC80 per cell."""
    cells = b''.join(struct.pack('<H', flag) + bytes(18) for flag in flags)
    data = bytes((len(flags),)) + struct.pack('<' + 'h' * len(edges), *edges) + cells
    return struct.pack('<HH', 0xD608, len(data) + 1) + data


def table_row(texts, *, edges=None, flags=None, before=b'', after=b'', first_paragraph=b''):
    """Return literal text/PAPX segments for a depth-one row."""
    count = len(texts)
    if edges is None:
        edges = tuple(index * 600 for index in range(count + 1))
    flags = (0,) * count if flags is None else flags
    cells = [(text + '\x07', IN_TABLE + (first_paragraph if index == 0 else b''))
             for index, text in enumerate(texts)]
    return [*cells, ('\x07', ROW_END + before + definition(edges, flags) + after)]


def load_structure(*segments):
    payload = cfb_payload(papx_document(*segments))
    return read_bytes(payload, reading_mode='maximum')['binary_table_structure']


def grouped_fixture():
    """One two-row table, an outside paragraph and a separate one-row table."""
    return cfb_payload(papx_document(
        *table_row(('Group', 'ignored continuation'), flags=(2 | 0x60, 1)),
        *table_row(('',), edges=(0, 1200), flags=(0x20,)),
        ('Gap\r', b''),
        *table_row(('Separate',), edges=(0, 600)),
        ('\r', b''),
    ))


class StoredTableGroupsTests(unittest.TestCase):
    def test_groups_index_physical_rows_and_return_stored_merges(self):
        payload = grouped_fixture()
        original = read_bytes(payload)
        result = read_bytes(payload, reading_mode='maximum')
        self.assertEqual(result['text'], original['text'])
        self.assertNotIn('tables', original['binary_table_structure'])
        structure = result['binary_table_structure']
        self.assertEqual((structure['table_grouping'], structure['grid_and_merges']), ('stored', 'stored'))
        self.assertEqual(structure['unresolved_marker_count'], 0)
        self.assertEqual([table['row_count'] for table in structure['tables']], [2, 1])
        first, second = structure['tables']
        self.assertEqual((first['first_row_index'], second['first_row_index']), (0, 2))
        self.assertEqual((first['start_boundary'], first['end_boundary']), ('story_start', 'outside_table_paragraph'))
        self.assertEqual(first['column_edges_twips'], [0, 600, 1200])
        self.assertEqual(first['horizontal_merges'], [
            {'row_index': 0, 'first_cell': 0, 'cell_count': 2, 'left_twips': 0, 'right_twips': 1200},
        ])
        self.assertEqual(first['vertical_merges'], [
            {'row_index': 0, 'row_count': 2, 'left_twips': 0, 'right_twips': 1200,
             'cells': [{'first_cell': 0, 'cell_count': 2}, {'first_cell': 0, 'cell_count': 1}], 'status': 'stored'},
        ])
        self.assertEqual(structure['rows'][0]['cells'][1]['text'], 'ignored continuation')
        self.assertEqual(result['status'], 'partial')

    def test_adjacent_rows_split_on_style_direction_placement_or_frame(self):
        style = b'\x3a\x56\x13\0'
        direct = ((style, 'different_row_properties'),
                  (b'\x0b\x56\1\0', 'different_row_properties'),
                  (b'\x0f\x94\xfc\xff', 'different_row_properties'),
                  (b'\x69\x74\x01\x00\x00\x00', 'different_row_properties'))
        for modifier, reason in direct:
            with self.subTest(modifier=modifier):
                structure = load_structure(*table_row(('A',)), *table_row(('B',), before=modifier), ('\r', b''))
                self.assertEqual([table['row_count'] for table in structure['tables']], [1, 1])
                self.assertEqual([structure['tables'][0]['end_boundary'], structure['tables'][1]['start_boundary']], [reason, reason])
                self.assertEqual(structure['table_grouping'], 'stored')

        frame1 = b'\x1a\x84\x64\x00'
        frame2 = b'\x1a\x84\xc8\x00'
        structure = load_structure(*table_row(('A',), first_paragraph=frame1),
                                   *table_row(('B',), first_paragraph=frame2), ('\r', b''))
        self.assertEqual(structure['tables'][0]['end_boundary'], 'different_paragraph_frame')
        self.assertEqual(structure['table_grouping'], 'stored')

    def test_equivalent_bidi_flags_and_width_differences_do_not_split_rows(self):
        structure = load_structure(*table_row(('A',), before=b'\x0b\x56\x01\x00'),
                                   *table_row(('B', 'C'), edges=(0, 250, 600), before=b'\x64\x56\x01\x00'), ('\r', b''))
        self.assertEqual([table['row_count'] for table in structure['tables']], [2])
        self.assertEqual(structure['tables'][0]['column_edges_twips'], [0, 250, 600])
        self.assertEqual(structure['tables'][0]['horizontal_merges'], [])

    def test_unknown_boundary_and_unknown_cell_operation_keep_evidence_separate(self):
        left = b'\xff\xd5\x04\x00\x00\x00\x00'
        right = b'\xff\xd5\x04\x00\x00\x00\x01'
        structure = load_structure(*table_row(('A',), after=left), *table_row(('B',), after=right), ('\r', b''))
        self.assertEqual([table['row_count'] for table in structure['tables']], [1, 1])
        self.assertEqual(structure['tables'][0]['end_boundary'], 'unresolved_opaque_table_property')
        self.assertEqual(structure['table_grouping'], 'partial')
        self.assertEqual(structure['grid_and_merges'], 'partial')

        structure = load_structure(*table_row(('A',), after=b'\x22\x56\x00\x01'), ('\r', b''))
        self.assertEqual(structure['rows'][0]['cells'][0]['text'], 'A')
        self.assertIsNone(structure['rows'][0]['stored_cell_layout'])
        self.assertEqual(structure['tables'][0]['merge_status'], 'unknown')

    def test_bookmark_metadata_prevents_confirming_an_unexamined_join(self):
        streams = papx_document(*table_row(('A',)), *table_row(('B',)), ('\r', b''))
        word = bytearray(streams[('WordDocument',)])
        struct.pack_into('<H', word, 152, 164)
        struct.pack_into('<3H', word, 154 + 164 * 8, 2, 0x10C, 0)
        streams[('WordDocument',)] = bytes(word)
        set_pair(streams, 138, b'\0' * 8)
        structure = read_bytes(cfb_payload(streams), reading_mode='maximum')['binary_table_structure']
        self.assertEqual(structure['table_grouping'], 'partial')
        self.assertEqual(structure['tables'][0]['end_boundary'], 'unresolved_table_bookmarks')
        self.assertEqual([table['row_count'] for table in structure['tables']], [1, 1])

    def test_merge_and_split_apply_in_order_with_supported_row_decorations(self):
        merge = b'\x24\x56\x00\x02'
        split = b'\x25\x56\x00\x02'
        before = b'\x3a\x56\x13\x00'  # Table style selected before TDefTable.
        after = b'\x05\xd6\x18' + bytes(24)  # Borders.
        after += b'\x07\x94\x00\x00\x04\x34\x01\x15\x36\x01'  # Height, header, autofit.
        after += b'\x01\x96\x00\x00\x35\xd6\x05\x00\x01\x03\x58\x02'  # Origin and preferred width.
        after += b'\x25\x56\x00\x00'  # Empty ItcFirstLim is a no-op.
        structure = load_structure(*table_row(('A', 'B'), before=before, after=after + merge + split), ('\r', b''))
        self.assertEqual(structure['tables'][0]['horizontal_merges'], [])
        self.assertEqual(structure['tables'][0]['merge_status'], 'stored')
        structure = load_structure(*table_row(('A', 'B'), flags=(2, 1), after=after + split + merge), ('\r', b''))
        self.assertEqual(len(structure['tables'][0]['horizontal_merges']), 1)

    def test_vertical_chain_does_not_cross_an_unresolved_gap(self):
        streams = [
            *table_row(('Lead',), flags=(0x60,)),
            *table_row(('',), flags=(0x20,)),
            ('\x07', ROW_END),  # row marker without a verified cell
            *table_row(('',), flags=(0x20,)),
            ('\r', b''),
        ]
        structure = load_structure(*streams)
        self.assertEqual(structure['table_grouping'], 'partial')
        self.assertEqual(structure['tables'][0]['end_boundary'], 'unresolved_row_gap')
        self.assertEqual(structure['tables'][0]['vertical_merges'][0]['status'], 'partial')
        self.assertEqual(structure['tables'][0]['vertical_merges'][0]['row_count'], 2)
        self.assertEqual(structure['tables'][1]['vertical_merges'], [])


if __name__ == '__main__':
    unittest.main()
