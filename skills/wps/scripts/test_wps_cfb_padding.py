"""Missing physical padding must never synthesize declared stream content."""
import struct
import unittest

from test_wps_cfb import synthetic_cfb
from wps_cfb import CompoundFile, END


class FinalPaddingTests(unittest.TestCase):
    def test_default_api_keeps_text_and_partial_assessment(self):
        from read_wps import read_bytes, ParseError
        from test_wps import document
        streams = {path[0]: content for path, content in document().items()}
        word = streams['WordDocument']
        streams['WordDocument'] = word + bytes(8192 + 136 - len(word))
        full = synthetic_cfb(streams)
        short = full[:-376]
        result = read_bytes(short)
        self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['partial'])
        self.assertIn('cfb_missing_final_sector_padding', [item['code'] for item in result['container_issues']])
        with self.assertRaises(ParseError):
            read_bytes(short, compatibility='strict')

    def fixture(self, remainder=73):
        content = b'\xec\xa5' + b'x' * (4096 + remainder - 2)
        return synthetic_cfb({'WordDocument': content}), content

    def test_normal_and_missing_padding_preserve_every_stream_byte(self):
        for remainder in (1, 73, 136, 511):
            with self.subTest(remainder=remainder):
                full, content = self.fixture(remainder)
                original = full[:-(512 - remainder)]
                normal = CompoundFile(full, compatibility='legacy_word_clsid')
                recovered = CompoundFile(original, compatibility='legacy_word_clsid')
                self.assertEqual(normal.streams, recovered.streams)
                self.assertEqual(recovered.streams[('WordDocument',)], content)
                self.assertEqual(recovered.issues[-1]['code'], 'cfb_missing_final_sector_padding')
                self.assertEqual(recovered.issues[-1]['missing_padding_bytes'], 512 - remainder)
                self.assertEqual(original, full[:len(original)])

    def test_strict_still_rejects_unaligned_physical_file(self):
        full, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, 'not sector aligned'):
            CompoundFile(full[:-439])

    def test_one_declared_byte_missing_rejected(self):
        full, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, 'declared content is physically truncated'):
            CompoundFile(full[:-440], compatibility='legacy_word_clsid')

    def test_cycle_not_hidden_by_padding(self):
        full, _ = self.fixture()
        data = bytearray(full[:-439])
        # Last physical sector points to itself instead of END.
        last = (len(full) // 512) - 2
        struct.pack_into('<I', data, 512 + 4 * last, last)
        with self.assertRaises(ValueError):
            CompoundFile(bytes(data), compatibility='legacy_word_clsid')

    def test_free_partial_tail_not_claimed_as_padding(self):
        full, _ = self.fixture(0)
        with self.assertRaises(ValueError):
            CompoundFile(full + b'x', compatibility='legacy_word_clsid')

    def test_missing_metadata_rejected(self):
        full, _ = self.fixture()
        data = bytearray(full[:-439])
        last = (len(full) // 512) - 2
        struct.pack_into('<I', data, 48, last)
        struct.pack_into('<I', data, 512 + 4 * last, END)
        with self.assertRaises(ValueError):
            CompoundFile(bytes(data), compatibility='legacy_word_clsid')


if __name__ == '__main__':
    unittest.main()
