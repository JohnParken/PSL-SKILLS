"""Literal MS-DOC fixtures for maximum-mode recovery and conservative fallbacks."""
import json
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from read_wps import ParseError, read_bytes
from test_wps import document, synthetic_cfb


def cfb_payload(streams):
    """Wrap fixture streams using the public binary container test fixture."""
    return synthetic_cfb({path[0]: value for path, value in streams.items()})


def set_pair(streams, index, blob):
    """Append one Table Stream block and update the fixture's FIB pair."""
    word = bytearray(streams[('WordDocument',)])
    table = ('0Table',)
    struct.pack_into('<II', word, 154 + index * 8, len(streams[table]), len(blob))
    streams[('WordDocument',)] = bytes(word)
    streams[table] += blob


def install_papx(streams, runs):
    """Install an independent, one-page PAPX fixture using given FC limits."""
    word = bytearray(streams[('WordDocument',)])
    page_offset = ((len(word) + 511) // 512) * 512
    page = bytearray(512)
    starts = [runs[0][0], *(right for _, right, _ in runs)]
    struct.pack_into('<' + 'I' * len(starts), page, 0, *starts)
    array_size = 4 * len(starts)
    cursor = array_size + 13 * len(runs)
    cursor += cursor % 2
    for index, (left, _, prls) in enumerate(runs):
        assert left == starts[index]
        group = b'\0\0' + prls
        if len(group) % 2:
            block = bytes(((len(group) + 1) // 2,)) + group
        else:
            block = bytes((0, len(group) // 2)) + group
        assert cursor + len(block) <= 511
        page[array_size + 13 * index] = cursor // 2
        page[cursor:cursor + len(block)] = block
        cursor += len(block)
        cursor += cursor % 2
    page[511] = len(runs)
    word.extend(bytes(page_offset - len(word)))
    streams[('WordDocument',)] = bytes(word + page)
    set_pair(streams, 13, struct.pack('<III', starts[0], starts[-1], page_offset // 512))


def papx_document(*segments):
    """Build literal text segments ending at stored paragraph or cell marks."""
    raw = b''.join(text.encode('utf-16le') for text, _ in segments)
    streams = document([(raw, False)])
    runs = []
    fc = 4096
    for text, prls in segments:
        end = fc + len(text.encode('utf-16le'))
        runs.append((fc, end, prls))
        fc = end
    install_papx(streams, runs)
    return streams


def shape_plc(cps, shape_ids):
    """Create a literal SPA array with signed stored coordinates and origins."""
    positions = (*cps, 0xFFFFFFFF)
    return struct.pack('<' + 'I' * len(positions), *positions) + b''.join(
        struct.pack('<I4iHI', shape_id, -10, 20, 100, 220, 2 | (2 << 3), 0)
        for shape_id in shape_ids
    )


def textbox_plc(text, shape_id, *, end=None):
    """One actual textbox followed by a one-CP final guard range."""
    units = len(text.encode('utf-16le')) // 2
    cps = struct.pack('<III', 0, units if end is None else end, units + 1)
    record = struct.pack('<8sHIII', bytes(8), 0, 0, shape_id, 0)
    return cps + record + bytes(22)


IN_TABLE = b'\x16\x24\x01\x49\x66\x01\0\0\0'
ROW_END = IN_TABLE + b'\x17\x24\x01'


class MsDocMaximumTests(unittest.TestCase):
    """Check the public reader and CLI without changing fixture input bytes."""

    def test_known_fixed_paragraph_properties_allow_verified_rows(self):
        # R/L layout, numbering and East Asian font alignment precede real table flags.
        leading = b'\x41\x24\x00\x0a\x26\x00\x0b\x46\x02\x00\x39\x44\x04\x00'
        streams = papx_document(
            ('甲\x07', leading + IN_TABLE), ('乙\x07', IN_TABLE), ('\x07', leading + ROW_END),
            ('间隔\r', leading), ('丙\x07', IN_TABLE), ('\x07', ROW_END), ('\r', b''),
        )
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        structure = result['binary_table_structure']
        self.assertEqual([[cell['text'] for cell in row['cells']] for row in structure['rows']], [['甲', '乙'], ['丙']])
        self.assertEqual(structure['unresolved_marker_count'], 0)
        self.assertEqual(structure['table_grouping'], 'stored')
        self.assertEqual(structure['grid_and_merges'], 'unknown')
        self.assertEqual([table['row_count'] for table in structure['tables']], [1, 1])
        self.assertEqual(structure['contiguous_row_runs'], [
            {'first_row_index': 0, 'row_count': 1, 'cp_start': 0, 'cp_end': 5},
            {'first_row_index': 1, 'row_count': 1, 'cp_start': 8, 'cp_end': 11},
        ])
        self.assertEqual(result['status'], 'partial')

    def test_truncated_known_paragraph_operand_leaves_readable_body(self):
        streams = papx_document(('甲\x07', IN_TABLE), ('\x07', ROW_END + b'\x39\x44\x04'), ('\r', b''))
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        self.assertEqual(result['text'], '甲\t\t\n')
        self.assertEqual(result['binary_table_structure']['rows'], [])
        self.assertIn('binary_table_metadata_invalid', [issue['code'] for issue in result['issues']])

    def test_field_row_is_skipped_without_losing_other_rows_or_source_positions(self):
        unsafe = '甲\x13秘密😀\x14显示值\x15\x07'
        streams = papx_document(
            ('编号\x07', IN_TABLE), ('人数\x07', IN_TABLE), ('\x07', ROW_END),
            (unsafe, IN_TABLE), ('60\x07', IN_TABLE), ('\x07', ROW_END),
            ('乙\x01\x07', IN_TABLE), ('77\x07', IN_TABLE), ('\x07', ROW_END),
            ('\r', b''),
        )
        payload = cfb_payload(streams)
        standard = read_bytes(payload)
        result = read_bytes(payload, reading_mode='maximum')
        self.assertNotIn('binary_table_structure', standard)
        self.assertEqual(result['text'], standard['text'])
        self.assertIn('显示值', result['text'])
        self.assertNotIn('秘密', json.dumps(result, ensure_ascii=False))
        structure = result['binary_table_structure']
        self.assertEqual([[cell['text'] for cell in row['cells']] for row in structure['rows']],
                         [['编号', '人数'], ['乙\ufffc', '77']])
        later_row_cp = len(('编号\x07人数\x07\x07' + unsafe + '60\x07\x07').encode('utf-16le')) // 2
        self.assertEqual(structure['rows'][1]['source']['cp_start'], later_row_cp)
        self.assertEqual(structure['unresolved_markers'][0]['reason'], 'field_overlap')
        self.assertEqual(result['status'], 'partial')

    def test_field_instruction_marks_cannot_create_rows_or_poison_a_later_table(self):
        streams = papx_document(
            ('\x13秘密\x07', IN_TABLE), ('\x07', ROW_END), ('\x14结果\x15\r', b''),
            ('A\x07', IN_TABLE), ('B\x07', IN_TABLE), ('\x07', ROW_END), ('\r', b''),
        )
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        self.assertEqual(result['text'], '结果\nA\tB\t\t\n')
        rows = result['binary_table_structure']['rows']
        self.assertEqual([[cell['text'] for cell in row['cells']] for row in rows], [['A', 'B']])

    def test_style_and_relative_depth_allow_rows_with_explicit_evidence(self):
        props = b'\x16\x24\x01\x00\x46\x05\0\x4a\x66\x01\0\0\0'
        streams = papx_document(('甲\x07', props), ('乙\x07', props),
                                ('\x07', props + b'\x17\x24\x01'), ('\r', b''))
        payload = cfb_payload(streams)
        self.assertEqual(read_bytes(payload)['binary_table_structure']['rows'], [])
        rows = read_bytes(payload, reading_mode='maximum')['binary_table_structure']['rows']
        self.assertEqual([cell['text'] for cell in rows[0]['cells']], ['甲', '乙'])

    def test_invalid_row_mark_does_not_promote_the_suffix_to_a_complete_row(self):
        streams = papx_document(
            ('A\x07', IN_TABLE), ('\r', ROW_END), ('B\x07', IN_TABLE), ('\x07', ROW_END),
            ('C\x07', IN_TABLE), ('\x07', ROW_END), ('\r', b''),
        )
        rows = read_bytes(cfb_payload(streams), reading_mode='maximum')['binary_table_structure']['rows']
        self.assertEqual([[cell['text'] for cell in row['cells']] for row in rows], [['C']])

    def test_unrelated_piece_prm_does_not_hide_a_complete_table(self):
        streams = document([(b'Lead\r', True), ('甲\x07乙\x07\x07\r'.encode('utf-16le'), False)])
        table = bytearray(streams[('0Table',)])
        struct.pack_into('<H', table, 5 + 12 + 6, 2)
        streams[('0Table',)] = bytes(table)
        install_papx(streams, [(4096, 4100, IN_TABLE), (4100, 4104, IN_TABLE),
                               (4104, 4106, ROW_END), (4106, 4108, b'')])
        payload = cfb_payload(streams)
        self.assertEqual(read_bytes(payload)['binary_table_structure']['rows'], [])
        result = read_bytes(payload, reading_mode='maximum')
        self.assertEqual([cell['text'] for cell in result['binary_table_structure']['rows'][0]['cells']], ['甲', '乙'])
        self.assertEqual(result['binary_table_structure']['rows'][0]['source']['cp_start'], 5)

    def test_piece_prm_inside_cell_cannot_be_ignored_by_clean_ending_piece(self):
        streams = document([(b'A', True), (b'\x07B\x07\x07\r', True)])
        table = bytearray(streams[('0Table',)])
        struct.pack_into('<H', table, 5 + 12 + 6, 2)
        streams[('0Table',)] = bytes(table)
        install_papx(streams, [(4096, 4097, IN_TABLE), (4097, 4099, IN_TABLE),
                               (4099, 4100, ROW_END), (4100, 4101, b'')])
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        self.assertEqual(result['binary_table_structure']['rows'], [])
        self.assertEqual(result['binary_table_structure']['unresolved_markers'][0]['cp'], 1)

    def test_cell_edges_and_merge_operands_require_a_consistent_row(self):
        definitions = bytes((3,)) + struct.pack('<4h', 0, 1000, 2000, 3000)
        table_definition = b'\x08\xd6' + struct.pack('<H', len(definitions) + 1) + definitions
        merge_operands = b'\x24\x56\x00\x02\x2b\xd6\x02\x02\x03'
        for extra in (b'', b'\x22\x56\x00\x01', b'\x00\x46\x05\0'):
            with self.subTest(extra=extra):
                streams = papx_document(
                    ('总计\x07', IN_TABLE), ('\x07', IN_TABLE), ('600\x07', IN_TABLE),
                    ('\x07', ROW_END + table_definition + merge_operands + extra), ('\r', b''),
                )
                row = read_bytes(cfb_payload(streams), reading_mode='maximum')['binary_table_structure']['rows'][0]
                self.assertEqual([cell['text'] for cell in row['cells']], ['总计', '', '600'])
                if extra:
                    self.assertIsNone(row['stored_cell_layout'])
                else:
                    self.assertEqual(row['stored_cell_layout']['cell_edges_twips'], [0, 1000, 2000, 3000])
                    self.assertEqual(row['stored_cell_layout']['horizontal_merges'], [{'first_cell': 0, 'cell_count': 2}])
                    self.assertEqual(row['stored_cell_layout']['vertical_merge_roles'], ['none', 'none', 'start'])
                self.assertEqual(row['merges'], 'unknown')

    def test_ordinary_paragraph_layout_preserves_order_and_raw_operands(self):
        props = b'\x61\x24\x01\x05\x24\x01\x31\x24\x00\x5e\x84\x9c\xff\x13\xa4\x78\0'
        streams = papx_document(('标题😀\r', props), ('第二段\r', b'\x61\x24\xff'))
        payload = cfb_payload(streams)
        self.assertNotIn('binary_paragraph_layout', read_bytes(payload))
        result = read_bytes(payload, reading_mode='maximum')
        self.assertNotIn('binary_table_structure', result)
        layout = result['binary_paragraph_layout']
        self.assertEqual(layout['effective_layout'], 'unknown')
        first, second = layout['paragraphs']
        self.assertEqual([first['mark_cp'], second['mark_cp']], [4, 8])
        self.assertEqual([item['value'] for item in first['modifiers']], ['center', True, False, -100, 120])
        self.assertEqual(first['modifiers'][3]['unit'], 'twips')
        self.assertEqual(second['modifiers'][0]['interpretation'], 'invalid_operand')
        self.assertEqual(second['modifiers'][0]['operand_hex'], 'ff')

    def test_layout_modifier_limit_counts_known_omissions(self):
        align_and_keep = b'\x61\x24\x01\x05\x24\x01'
        payload = cfb_payload(papx_document(('A\r', align_and_keep), ('B\r', align_and_keep)))
        with patch('wps_binary_tables.MAX_LAYOUT_MODIFIERS', 1):
            layout = read_bytes(payload, reading_mode='maximum')['binary_paragraph_layout']
        self.assertEqual(layout['modifier_count'], 4)
        self.assertEqual(layout['modifiers_omitted'], 3)
        self.assertEqual(layout['paragraphs'][0]['modifiers'][0]['value'], 'center')

    def test_unknown_marker_detail_is_bounded_with_omission_count(self):
        payload = cfb_payload(document([(b'A\x07B\x07\x07\r', True)]))
        with patch('wps_binary_tables.MAX_UNRESOLVED_MARKERS', 1):
            standard = read_bytes(payload)['binary_table_structure']
            structure = read_bytes(payload, reading_mode='maximum')['binary_table_structure']
        self.assertEqual(len(standard['unresolved_markers']), 3)
        self.assertEqual(len(structure['unresolved_markers']), 1)
        self.assertEqual(structure['unresolved_marker_count'], 3)
        self.assertEqual(structure['unresolved_markers_omitted'], 2)

    def test_binary_textboxes_link_to_correct_story_and_keep_guard_out_of_copy(self):
        streams = document([('前\x08后\r'.encode('utf-16le'), False)], {
            80: '脚\r'.encode('utf-16le'),
            84: '页\x08\r'.encode('utf-16le'),
            100: '框😀\rG'.encode('utf-16le'),
            104: '页框\rG'.encode('utf-16le'),
        })
        set_pair(streams, 40, shape_plc((1,), (1234,)))
        set_pair(streams, 41, shape_plc((1,), (4321,)))
        set_pair(streams, 56, textbox_plc('框😀\r', 1234))
        set_pair(streams, 58, textbox_plc('页框\r', 4321))
        payload = cfb_payload(streams)
        result = read_bytes(payload, reading_mode='maximum')
        self.assertEqual(result['text'], '前\ufffc后\n')
        self.assertEqual(result['related_stories'][1]['text'], '页\ufffc\n')
        body, header = result['related_stories'][4:6]
        self.assertEqual([body['references'][0]['text'], header['references'][0]['text']], ['框😀\n', '页框\n'])
        self.assertEqual([body['references'][0]['anchor_cp'], header['references'][0]['anchor_cp']], [1, 7])
        self.assertEqual(body['references'][0]['text_range']['cp_start'], 9)
        self.assertEqual(body['references'][0]['text_range']['cp_end'], 13)
        shape = next(issue['locations'][0] for issue in result['issues'] if issue['code'] == 'drawn_shape_anchor_not_rendered')
        self.assertEqual(shape['shape_id'], 1234)
        self.assertEqual(shape['stored_rectangle_twips']['left'], -10)
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['objects_decoded'])
        with patch('read_wps.MAX_TEXTBOX_COPY_UNITS', 1):
            region = read_bytes(payload, reading_mode='maximum')['related_stories'][4]
        self.assertEqual(region['textbox_text_status'], 'combined_story_with_utf16_ranges')
        self.assertEqual(region['references'][0]['story_text_utf16_range'], {'start': 0, 'end': 4})
        self.assertNotIn('text', region['references'][0])

    def test_bad_textbox_metadata_keeps_story_and_shared_stream_alias_fails(self):
        streams = document([(b'Main\r', True)], {100: '框\rG'.encode('utf-16le')})
        set_pair(streams, 56, textbox_plc('框\r', 1234, end=1))
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        box = result['related_stories'][4]
        self.assertEqual(box['text'], '框\nG')
        self.assertEqual(box['references'], [])
        self.assertIn('textbox_metadata_invalid', [item['code'] for item in box['issues']])
        word = bytearray(streams[('WordDocument',)])
        struct.pack_into('<I', word, 154 + 56 * 8, 0)
        streams[('WordDocument',)] = bytes(word)
        with self.assertRaisesRegex(ParseError, 'Overlapping decoded table'):
            read_bytes(cfb_payload(streams), reading_mode='maximum')

    def test_field_filtered_textbox_does_not_claim_rendered_text_range(self):
        box_text = '\x13私有指令\x14公开结果\x15\r'
        streams = document([(b'Main\r', True)], {100: (box_text + 'G').encode('utf-16le')})
        set_pair(streams, 56, textbox_plc(box_text, 1234))
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        box = result['related_stories'][4]
        self.assertEqual(box['text'], '公开结果\nG')
        self.assertEqual(box['textbox_text_status'], 'combined_story_only_after_field_filtering')
        self.assertNotIn('story_text_utf16_range', box['references'][0])
        self.assertNotIn('text', box['references'][0])
        self.assertNotIn('私有指令', json.dumps(result, ensure_ascii=False))

    def test_duplicate_shape_id_in_field_instructions_prevents_anchor_match(self):
        text = '\x08\x13\x08\x14结果\x15\r'
        streams = document([(text.encode('utf-16le'), False)], {100: '框\rG'.encode('utf-16le')})
        set_pair(streams, 40, shape_plc((0, 2), (1234, 1234)))
        set_pair(streams, 56, textbox_plc('框\r', 1234))
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        box = result['related_stories'][4]
        self.assertEqual(box['references'][0]['anchor_status'], 'unknown')
        self.assertNotIn('anchor_cp', box['references'][0])
        self.assertEqual(box['references'][0]['text'], '框\n')

    def test_cli_returns_partial_json_with_textbox_range_and_preserves_input(self):
        streams = document([(b'Main\r', True)], {100: '核心指标\rG'.encode('utf-16le')})
        set_pair(streams, 56, textbox_plc('核心指标\r', 1234))
        payload = cfb_payload(streams)
        with TemporaryDirectory() as task:
            source = Path(task) / 'report.wps'
            source.write_bytes(payload)
            proc = subprocess.run(
                [sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve().parents[1] / 'read_wps.py'),
                 source.name, '--reading-mode', 'maximum'],
                cwd=task, capture_output=True, text=True, check=False, timeout=10,
            )
            self.assertEqual(proc.returncode, 3, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result['related_stories'][4]['references'][0]['text'], '核心指标\n')
            self.assertEqual(source.read_bytes(), payload)


if __name__ == '__main__':
    unittest.main()
