"""Public-entry tests for bounded binary drawing descriptions."""
import json
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from read_wps import ParseError, read_bytes
from test_wps import document
from test_wps_binary_officeart import fopt, sp
from test_wps_msdoc_maximum import cfb_payload, set_pair, shape_plc, textbox_plc


def record(kind, payload, *, version=15, instance=0):
    """Construct a literal bounded OfficeArt record."""
    return struct.pack('<HHI', instance << 4 | version, kind, len(payload)) + payload


def shape(shape_id, shape_type, fields, *, flags=0x0A00):
    """Construct a shape with an FSP and primary FOPT complex strings."""
    fsp = record(0xF00A, struct.pack('<II', shape_id, flags), version=2, instance=shape_type)
    entries, values = [], []
    for key, value in fields:
        raw = (value + '\0').encode('utf-16le') if isinstance(value, str) else value
        entries.append(struct.pack('<HI', 0x8000 | key, len(raw)))
        values.append(raw)
    fopt = record(0xF00B, b''.join(entries + values), version=3, instance=len(entries))
    return record(0xF004, fsp + fopt)


def drawing(*groups):
    """Wrap literal shape containers in the Word OfficeArt drawing structure."""
    return record(0xF000, b'') + b''.join(
        bytes([label]) + record(0xF002, record(0xF003, b''.join(shapes))) for label, shapes in groups
    )


def drawing_document():
    """Include matched anchors in the body and header and a textbox reference."""
    streams = document([('正文\x08\r'.encode('utf-16le'), False)], {
        84: '页眉\x08\r'.encode('utf-16le'),
        100: '正文框\rG'.encode('utf-16le'),
    })
    set_pair(streams, 40, shape_plc((2,), (1200,)))
    set_pair(streams, 41, shape_plc((2,), (1201,)))
    set_pair(streams, 50, drawing(
        (0, (shape(1200, 202, [(0x0380, '形状一'), (0x0381, '核心指标说明😀')]),)),
        (1, (shape(1201, 136, [(0x00C0, '页眉水印')]),)),
    ))
    set_pair(streams, 56, textbox_plc('正文框\r', 1200))
    return streams


class DrawingDescriptionsTests(unittest.TestCase):
    def test_grouped_textbox_requires_matching_unique_stored_shape_and_index(self):
        cases = ((1, 1, False, True), (2, 1, False, False), (1, 2, False, False), (1, 1, True, False))
        for text_index, copies, blocked_parent, expected in cases:
            with self.subTest(text_index=text_index, copies=copies, blocked_parent=blocked_parent):
                body = b'\x08\x13\x08\x14A\x15\r' if blocked_parent else b'\x08\r'
                streams = document([(body, True)], {100: '框\rG'.encode('utf-16le')})
                plc_spa = shape_plc((0, 2), (1001, 1002)) if blocked_parent else shape_plc((0,), (1001,))
                set_pair(streams, 40, plc_spa)
                plc = bytearray(textbox_plc('框\r', 1003))
                struct.pack_into('<I', plc, 8, 0xFFFFFFFF)  # final ignored CP
                set_pair(streams, 56, plc)
                child = sp(1003, 2, 202, options=(fopt(((0x80, text_index << 16),)),))
                group = record(0xF003, sp(1001, 1) + record(0xF003, sp(1002, 3) + child * copies))
                set_pair(streams, 50, drawing((0, (sp(999, 5), group))))
                result = read_bytes(cfb_payload(streams), reading_mode='maximum')
                ref = result['related_stories'][4]['references'][0]
                self.assertEqual(ref['text'], '框\n')
                self.assertEqual(result['drawing_objects']['objects'][0]['officeart']['shape_type'], 'group')
                if expected:
                    self.assertEqual(ref['anchor_status'], 'matched_officeart_group_shape_id')
                    self.assertEqual(ref['anchor_cp'], 0)
                    self.assertEqual(ref['anchor_shape_id'], 1001)
                    self.assertEqual(ref['group_shape_path'], [1001, 1002])
                else:
                    self.assertEqual(ref['anchor_status'], 'unknown')
                    self.assertNotIn('anchor_cp', ref)

    """Verify exact matches, useful fallback, and no field instruction disclosure."""

    def test_body_and_header_objects_are_linked_with_verified_sources(self):
        streams = drawing_document()
        data = cfb_payload(streams)
        standard = read_bytes(data)
        self.assertNotIn('drawing_objects', standard)
        result = read_bytes(data, reading_mode='maximum')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['objects_decoded'])
        self.assertEqual(result['text'], standard['text'])
        report = result['drawing_objects']
        self.assertEqual(report['status'], 'stored_metadata_partial')
        self.assertEqual((report['anchor_count'], report['officeart_matched_count']), (2, 2))
        body, header = report['objects']
        self.assertEqual((body['story'], body['anchor']['cp'], body['anchor']['shape_id']), ('main', 2, 1200))
        self.assertEqual((header['story'], header['anchor']['cp'], header['anchor']['shape_id']), ('headers_footers', 6, 1201))
        self.assertEqual(body['officeart']['shape_type'], 'text_box')
        self.assertEqual(body['officeart']['name'], '形状一')
        self.assertEqual(body['officeart']['description'], '核心指标说明😀')
        self.assertEqual(header['officeart']['wordart_text'], '页眉水印')
        self.assertEqual(header['officeart']['shape_type'], 'plain_text')
        location = body['officeart']['text_sources']['description']
        self.assertEqual(location['stream'], '0Table')
        stored = ('核心指标说明😀' + '\0').encode('utf-16le')
        self.assertEqual(streams[('0Table',)][location['offset']:location['offset'] + len(stored)], stored)
        self.assertEqual(result['related_stories'][4]['references'][0]['anchor_cp'], 2)
        self.assertNotIn('核心指标说明', json.dumps(result['reading_assessment'], ensure_ascii=False))

    def test_duplicate_anchor_instructions_do_not_gain_metadata(self):
        streams = document([('\x08\x13\x08\x14结果\x15\r'.encode('utf-16le'), False)])
        set_pair(streams, 40, shape_plc((0, 2), (1200, 1200)))
        set_pair(streams, 50, drawing((0, (shape(1200, 1, [(0x0381, '字段对象说明')]),))))
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        self.assertEqual(result['text'], '\ufffc结果\n')
        report = result['drawing_objects']
        self.assertEqual(report['status'], 'no_unique_shape_match')
        self.assertEqual(report['anchor_count'], 1)
        self.assertEqual(report['officeart_matched_count'], 0)
        self.assertNotIn('字段对象说明', json.dumps(result, ensure_ascii=False))

    def test_repeated_anchor_cannot_reuse_match_from_the_other_story(self):
        streams = document([('\x08\x08\r'.encode('utf-16le'), False)], {84: '\x08\r'.encode('utf-16le')})
        set_pair(streams, 40, shape_plc((0, 1), (1200, 1200)))
        set_pair(streams, 41, shape_plc((0,), (1200,)))
        set_pair(streams, 50, drawing(
            (0, (shape(1200, 1, [(0x0381, '正文歧义')]),)),
            (1, (shape(1200, 3, [(0x0381, '页眉说明')]),)),
        ))
        entries = read_bytes(cfb_payload(streams), reading_mode='maximum')['drawing_objects']['objects']
        self.assertEqual([entry['story'] for entry in entries], ['main', 'main', 'headers_footers'])
        self.assertNotIn('officeart', entries[0])
        self.assertNotIn('officeart', entries[1])
        self.assertEqual(entries[2]['officeart']['description'], '页眉说明')

    def test_absent_or_malformed_officeart_keeps_anchor_and_text(self):
        streams = document([('正文\x08\r'.encode('utf-16le'), False)])
        set_pair(streams, 40, shape_plc((2,), (1200,)))
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        self.assertEqual(result['drawing_objects']['status'], 'officeart_not_present')
        set_pair(streams, 50, record(0xF000, b'') + b'\x00\x0f')
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        self.assertEqual(result['text'], '正文\ufffc\n')
        self.assertEqual(result['drawing_objects']['status'], 'officeart_metadata_invalid')
        self.assertIn('officeart_metadata_invalid', [issue['code'] for issue in result['issues']])

    def test_officeart_span_cannot_alias_clx_or_shape_table(self):
        streams = drawing_document()
        original = streams[('WordDocument',)]
        for source_pair in (33, 40):
            with self.subTest(source_pair=source_pair):
                word = bytearray(original)
                word[554:562] = word[154 + source_pair * 8:162 + source_pair * 8]
                streams[('WordDocument',)] = bytes(word)
                data = cfb_payload(streams)
                self.assertEqual(read_bytes(data)['status'], 'partial')
                with self.assertRaisesRegex(ParseError, 'OfficeArtContent aliases'):
                    read_bytes(data, reading_mode='maximum')

    def test_cli_partial_json_and_untouched_source(self):
        data = cfb_payload(drawing_document())
        script = str(Path(__file__).resolve().parents[1] / 'read_wps.py')
        with TemporaryDirectory() as task:
            source = Path(task) / 'report.wps'
            source.write_bytes(data)
            receipt = subprocess.run([sys.executable, '-I', '-S', '-B', script, source.name, '--reading-mode', 'maximum'],
                                     cwd=task, capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(receipt.returncode, 3, receipt.stderr)
            report = json.loads(receipt.stdout)['drawing_objects']
            self.assertEqual(report['objects'][0]['officeart']['description'], '核心指标说明😀')
            self.assertEqual(source.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
