"""Only synthetic PlcfSpa shape fixtures; never claim WPS producer compliance."""
import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from read_wps import ParseError, parse_streams, read_bytes
from test_wps import document, synthetic_cfb

PAIR_OFFSET = 154 + 40 * 8


def shaped_document(text='甲\x08乙\r', anchors=(1,), terminal=None):
    raw = text.encode('utf-16le')
    streams = document([(raw, False)])
    original_word = bytearray(streams[('WordDocument',)])
    original_table = bytearray(streams[('0Table',)])
    end = len(raw) // 2 if terminal is None else terminal
    cp_values = tuple(anchors) + (end,)
    plc = struct.pack('<' + 'I' * len(cp_values), *cp_values)
    plc += bytes(26 * len(anchors))
    struct.pack_into('<II', original_word, PAIR_OFFSET, len(original_table), len(plc))
    streams[('WordDocument',)] = bytes(original_word)
    streams[('0Table',)] = bytes(original_table + plc)
    return streams


class BinaryShapesTests(unittest.TestCase):
    def assert_error(self, streams, status, **opts):
        with self.assertRaises(ParseError) as caught:
            parse_streams(streams, **opts)
        self.assertEqual(caught.exception.status, status)

    def test_default_rejects_shape_and_optin_preserves_text(self):
        sample = shaped_document()
        self.assert_error(sample, 'unsupported')
        result = parse_streams(sample, object_policy='placeholder')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['text'], '甲\ufffc乙\n')
        self.assertEqual(result['paragraphs'], ['甲\ufffc乙'])
        issue = next(i for i in result['issues'] if i['code'] == 'drawn_shape_anchor_not_rendered')
        self.assertEqual(issue['count'], 1)
        self.assertEqual(issue['locations'][0]['cp'], 1)
        self.assertEqual(issue['locations'][0]['shape_index'], 0)
        self.assertFalse(result['objects_decoded'])

    def test_absent_shape_anchor_not_allowed(self):
        self.assert_error(document([('甲\x08乙\r'.encode('utf-16le'), False)]),
                          'unsupported', object_policy='placeholder')
        self.assert_error(shaped_document(anchors=(0,)), 'corrupt',
                          object_policy='placeholder')
        self.assert_error(shaped_document(text='甲乙\r', anchors=(1,)), 'corrupt',
                          object_policy='placeholder')
        self.assert_error(shaped_document(text='甲\x08\x08\r', anchors=(1,)), 'unsupported',
                          object_policy='placeholder')

    def test_multiple_shapes_and_invalid_cp(self):
        sample = shaped_document('甲\x08乙\x08\r', anchors=(1, 3))
        result = parse_streams(sample, object_policy='placeholder')
        self.assertEqual(result['text'], '甲\ufffc乙\ufffc\n')
        self.assertEqual(next(i['count'] for i in result['issues']
                              if i['code'] == 'drawn_shape_anchor_not_rendered'), 2)
        self.assert_error(shaped_document('甲\x08乙\x08\r', anchors=(1, 1)),
                          'corrupt', object_policy='placeholder')
        # The final PlcfSpa CP is undefined by MS-DOC: ignore its magnitude.
        self.assertEqual(parse_streams(shaped_document(terminal=100),
                                       object_policy='placeholder')['status'], 'partial')
        # Real anchor CPs, unlike the final sentinel, must belong to the story.
        self.assert_error(shaped_document(anchors=(4,), terminal=100),
                          'corrupt', object_policy='placeholder')

    def test_bad_plcf_size_and_alias(self):
        sample = shaped_document()
        word = bytearray(sample[('WordDocument',)])
        struct.pack_into('<I', word, PAIR_OFFSET + 4, 33)
        sample[('WordDocument',)] = bytes(word)
        self.assert_error(sample, 'corrupt', object_policy='placeholder')
        sample = shaped_document()
        word = bytearray(sample[('WordDocument',)])
        struct.pack_into('<I', word, PAIR_OFFSET, 0)
        sample[('WordDocument',)] = bytes(word)
        self.assert_error(sample, 'corrupt', object_policy='placeholder')

    def test_public_api_is_binary_and_partial(self):
        sample = shaped_document()
        data = synthetic_cfb({path[0]: value for path, value in sample.items()})
        with TemporaryDirectory() as temp:
            file = Path(temp) / 'synthetic.wps'
            file.write_bytes(data)
            from read_wps import read
            result = read(file, object_policy='placeholder', binary_paragraph_sources=True)
            self.assertEqual(result['status'], 'partial')
            self.assertIn('drawn_shape_anchor_not_rendered',
                          [issue['code'] for issue in result['issues']])
            self.assertEqual(result['paragraph_source_status'],
                             'unavailable_after_text_transform')
            self.assertEqual(file.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
