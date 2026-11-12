"""Compatibility and budget checks for the BIFF8 value decoder split."""
import sys
from pathlib import Path
import struct
from decimal import Decimal
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wps_biff8
import wps_biff8_errors
import wps_biff8_values


class ValueModuleBoundaries(unittest.TestCase):
    def test_global_mixin_counters_are_initialized_per_reader(self):
        """Declared counters belong to each reader, not the mixin class."""
        mixin = wps_biff8.GlobalSubstreamMixin
        stream = struct.pack('<HH', 0x0809, 0)
        first = wps_biff8.Reader(stream)
        second = wps_biff8.Reader(stream)
        for name in ('metadata_bytes', 'dxf_property_count', 'dxf_record_count'):
            self.assertNotIn(name, mixin.__dict__)
            self.assertEqual(first.__dict__[name], 0)
            self.assertEqual(second.__dict__[name], 0)
            setattr(first, name, 7)
            self.assertEqual(getattr(second, name), 0)

    def test_error_and_numeric_exports_preserve_identity(self):
        for symbol in ('EncryptedError', 'ResourceLimitError', 'UnsupportedError', 'require'):
            self.assertIs(getattr(wps_biff8, symbol), getattr(wps_biff8_errors, symbol))
        for symbol in ('unicode_string', 'number', 'rk_value'):
            self.assertIs(getattr(wps_biff8, symbol), getattr(wps_biff8_values, symbol))
        self.assertEqual(wps_biff8.number(struct.pack('<d', 1.25)), Decimal('1.25'))

    def test_direct_continued_string_respects_reader_budget(self):
        payload = struct.pack('<HBH', 1, 8, 1) + b'A' + struct.pack('<HH', 0, 0)
        records = [(0, 0x00FC, payload)]
        with patch('wps_biff8.MAX_METADATA_BYTES', 3):
            reader = wps_biff8.ContinuedStrings(records, 0)
            with self.assertRaises(wps_biff8.ResourceLimitError):
                reader.string(allow_rich=True)
        reader = wps_biff8.ContinuedStrings(records, 0)
        self.assertEqual(reader.string(allow_rich=True), 'A')
        self.assertEqual(reader.finish(), 1)


if __name__ == '__main__':
    unittest.main()
