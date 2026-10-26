"""Synthetic D9/112 layouts from MS-DOC tables, not real WPS provenance."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import ParseError, parse_streams
from test_wps import document, replace_word, synthetic_cfb
from read_wps import read_bytes


def fixture(version, parts=None, **kwargs):
    streams = document(parts, **kwargs)
    # Literal specification values, deliberately independent of FIB_LAYOUTS.
    pairs, offset, words = (108, 1018, 2) if version == 0xD9 else (183, 1618, 5)
    for at, value in ((152, pairs), (898, 0), (offset, words),
                      (offset+2, version), (offset+4, 15)):
        streams = replace_word(streams, at, value, 2)
    if version == 0x112:
        for at, value in ((1624, 0xFFFF), (1626, 0x1234), (1628, 0x8000)):
            streams = replace_word(streams, at, value, 2)
    return streams


class DocVersionTests(unittest.TestCase):
    def error(self, streams, status, offset=None, **options):
        with self.assertRaises(ParseError) as caught:
            parse_streams(streams, **options)
        self.assertEqual(caught.exception.status, status)
        if offset is not None:
            self.assertEqual(caught.exception.location['offset'], offset)
        return caught.exception

    def test_literal_layouts_mixed_order_surrogates_and_both_tables(self):
        for version in (0xD9, 0x112):
            for table_one in (False, True):
                for base in (0xC1, version):
                    streams = fixture(version, [(b'A', True), (b'\x3d\xd8', False),
                                                (b'\x00\xde'+ '中\rB\r'.encode('utf-16le'), False)],
                                      table_one=table_one,
                                      other_stories={84: '页眉不应出现'.encode('utf-16le')})
                    streams = replace_word(streams, 2, base, 2)
                    result = parse_streams(streams)
                    self.assertEqual(result['text'], 'A😀中\nB\n')
                    self.assertEqual(result['paragraphs'], ['A😀中', 'B'])
                    self.assertEqual(result['main_cp_units'], 7)
                    self.assertEqual(result['fib_version'], hex(version))
                    self.assertEqual(result['fib_base_version'], hex(base))

    def test_empty_body_and_container_entry(self):
        for version in (0xD9, 0x112):
            streams = fixture(version, [])
            result = parse_streams(streams)
            self.assertEqual((result['status'], result['text'], result['paragraphs']), ('ok', '', []))
            data = synthetic_cfb({name[0]: value for name, value in streams.items()})
            self.assertEqual(read_bytes(data)['fib_version'], hex(version))
            self.error(replace_word(streams, 422, 0), 'corrupt')

    def test_extensions_and_array_counts_must_match_effective_version(self):
        for version, offset in ((0xD9, 1018), (0x112, 1618)):
            for count in (0, 93, 107, 109, 182, 184, 65535):
                self.error(replace_word(replace_word(fixture(version), 2, version, 2), 152, count, 2), 'unsupported')
            for length in (0, 1, 3, 4, 6, 65535):
                self.error(replace_word(fixture(version), offset, length, 2), 'unsupported')
            for wrong in (0xC1, 0x101, 0x10C, 0xFFFF):
                self.error(replace_word(fixture(version), offset+2, wrong, 2), 'unsupported')
            self.error(replace_word(fixture(version), offset+4, 16, 2), 'corrupt', offset+4)

    def test_every_extension_truncation_is_corrupt(self):
        for version, offset, end in ((0xD9, 1018, 1024), (0x112, 1618, 1630)):
            for stop in range(offset, end):
                streams = fixture(version)
                streams[('WordDocument',)] = streams[('WordDocument',)][:stop]
                self.error(streams, 'corrupt')

    def test_112_theme_words_are_ignored_but_included_in_fib_extent(self):
        for at in (1624, 1626, 1628):
            for value in (0, 0x0409, 65535):
                result = parse_streams(replace_word(fixture(0x112), at, value, 2))
                self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
        for version, end in ((0xD9, 1024), (0x112, 1630)):
            streams = fixture(version, [('Z\r'.encode('utf-16le'), False)])
            table = bytearray(streams[('0Table',)])
            struct.pack_into('<I', table, 15, end-2)
            streams[('0Table',)] = table
            self.error(streams, 'corrupt', 15)
            word = bytearray(streams[('WordDocument',)])
            word[end:end+4] = b'Z\0\r\0'
            streams[('WordDocument',)] = word
            struct.pack_into('<I', table, 15, end)
            streams[('0Table',)] = table
            self.assertEqual(parse_streams(streams)['text'], 'Z\n')
            self.error(replace_word(streams, 64, end-1), 'corrupt', 64)

    def test_clx_index_and_prc_rules_are_unchanged(self):
        for version in (0xD9, 0x112):
            streams = fixture(version)
            streams = replace_word(streams, 410, 0xFFFFFFFF)
            streams = replace_word(streams, 426, 0xFFFFFFFF)
            self.assertEqual(parse_streams(streams)['text'], 'Hello 中文\n第二段\n')
            self.error(replace_word(streams, 418, 0xFFFFFFFF), 'corrupt', 0xFFFFFFFF)
            self.error(replace_word(streams, 422, 0xFFFFFFFF), 'resource_limit', 422)

    def test_encryption_back_version_budget_and_unknowns(self):
        for version in (0xD9, 0x112):
            streams = fixture(version)
            for flags in (0x1100, 0x9100):
                self.error(replace_word(streams, 10, flags, 2), 'encrypted', 10)
            self.error(replace_word(streams, 12, version, 2), 'corrupt', 12)
            self.error(replace_word(streams, 14, 1), 'corrupt', 14)
            with patch('read_wps.MAX_UNITS', 2):
                self.error(streams, 'resource_limit', 76)
            for base in (0, 0xBF, 0x113, 0xFFFF):
                self.error(replace_word(streams, 2, base, 2), 'unsupported', 2)


if __name__ == '__main__':
    unittest.main()
