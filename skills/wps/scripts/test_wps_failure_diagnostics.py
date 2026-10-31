"""Synthetic CFB diagnostics: classify errors without automatic recovery."""
import struct
import unittest

from read_wps import ParseError, read_bytes
from test_wps import document, synthetic_cfb


def fixture():
    streams = document()
    return synthetic_cfb({path[0]: data for path, data in streams.items()})


class CfbDiagnosticTests(unittest.TestCase):
    def test_non_aligned_bytes_preserve_failure_status_and_location(self):
        data = fixture() + b'\x12\x34\x56'
        with self.assertRaises(ParseError) as caught:
            read_bytes(data)
        failure = caught.exception
        self.assertEqual(failure.status, 'invalid_or_unsupported')
        self.assertEqual(failure.location['reason_code'], 'cfb_unaligned_physical_length')
        self.assertIn('remainder 3 bytes', str(failure))

    def test_fat_claim_beyond_physical_size_is_not_accepted(self):
        data = bytearray(fixture())
        sector_count = len(data) // 512 - 1
        # FAT is the first sector in test_wps.synthetic_cfb; the test-only
        # mutation deliberately marks a nonexistent sector as allocated.
        struct.pack_into('<I', data, 512 + sector_count * 4, 0xfffffffe)
        with self.assertRaises(ParseError) as caught:
            read_bytes(bytes(data))
        failure = caught.exception
        self.assertEqual(failure.status, 'invalid_or_unsupported')
        self.assertEqual(failure.location['reason_code'], 'cfb_fat_unbacked_allocation')
        self.assertIn('first unbacked sector', str(failure))

    def test_valid_file_does_not_emit_structural_reasons(self):
        result = read_bytes(fixture())
        self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
        self.assertEqual(result['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
