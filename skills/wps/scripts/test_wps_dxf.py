import json
import unittest

from wps_dxf import validate_dxf


ZERO_PROPERTIES = bytes.fromhex(
    "8d 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00"
)

MULTIPLE_PROPERTIES = bytes.fromhex(
    "8d 08 00 00 00 00 00 00 00 00 00 00 02 00 00 00 02 00 "
    "01 00 06 00 aa bb 14 00 05 00 cc"
)


class DxfParserTests(unittest.TestCase):
    @staticmethod
    def record_with_property_ids(property_ids, dxf_flags=0):
        property_frames = b"".join(
            property_id.to_bytes(2, "little") + b"\x04\x00"
            for property_id in property_ids
        )
        return (
            ZERO_PROPERTIES[:12]
            + dxf_flags.to_bytes(2, "little")
            + ZERO_PROPERTIES[14:16]
            + len(property_ids).to_bytes(2, "little")
            + property_frames
        )

    def test_zero_properties(self):
        result = validate_dxf(ZERO_PROPERTIES)

        self.assertEqual(result["record_type"], 0x088D)
        self.assertFalse(result["f_new_border"])
        self.assertEqual(result["property_count"], 0)
        self.assertEqual(result["properties"], [])

    def test_multiple_properties_preserve_bounded_raw_payloads(self):
        result = validate_dxf(MULTIPLE_PROPERTIES)

        self.assertTrue(result["f_new_border"])
        self.assertEqual(result["property_count"], 2)
        self.assertEqual(result["properties"], [
            {"property_id": 1, "payload_hex": "aabb", "offset": 18, "size": 6},
            {"property_id": 0x14, "payload_hex": "cc", "offset": 24, "size": 5},
        ])
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_unused_and_reserved_fields_are_ignored(self):
        payload = bytearray(ZERO_PROPERTIES)
        payload[2:4] = b"\xfc\xff"  # Reserved FrtFlags bits; scope flags stay clear.
        payload[4:12] = bytes.fromhex("01 02 03 04 05 06 07 08")
        payload[12:14] = b"\xfd\xff"  # Unused bits and reserved DXF flag bits set.
        payload[14:16] = b"\x34\x12"  # Reserved XFProps field is ignored.

        result = validate_dxf(payload)

        self.assertFalse(result["f_new_border"])
        self.assertEqual(result["frt_flags"], 0xFFFC)
        self.assertEqual(result["frt_reserved_hex"], "0102030405060708")
        self.assertEqual(result["dxf_flags"], 0xFFFD)
        self.assertEqual(result["xfprops_reserved"], 0x1234)

    def test_truncated_record_headers_are_rejected(self):
        for length in (0, 11, 12, 13, 14, 15, 16, 17):
            with self.subTest(length=length):
                with self.assertRaises(ValueError):
                    validate_dxf(ZERO_PROPERTIES[:length])

    def test_record_type_must_match_dxf(self):
        payload = bytearray(ZERO_PROPERTIES)
        payload[0:2] = b"\x8c\x08"

        with self.assertRaises(ValueError):
            validate_dxf(payload)

    def test_frtheader_scope_flags_must_be_clear(self):
        for flags in (0x0001, 0x0002, 0x0003):
            with self.subTest(flags=flags):
                payload = bytearray(ZERO_PROPERTIES)
                payload[2:4] = flags.to_bytes(2, "little")
                with self.assertRaises(ValueError):
                    validate_dxf(payload)

    def test_property_count_is_bounded_by_available_headers(self):
        payload = bytearray(ZERO_PROPERTIES)
        payload[16:18] = b"\xff\xff"

        with self.assertRaises(ValueError):
            validate_dxf(payload)

    def test_per_record_property_count_limit(self):
        property_count = 1025
        payload = (
            ZERO_PROPERTIES[:16]
            + property_count.to_bytes(2, "little")
            + b"\x01\x00\x04\x00" * property_count
        )

        with self.assertRaises(ValueError):
            validate_dxf(payload)

    def test_truncated_property_header_and_data_are_rejected(self):
        truncated_header = bytes.fromhex(
            "8d 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 01 00"
        )
        truncated_data = bytes.fromhex(
            "8d 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 "
            "01 00 06 00 aa"
        )

        for payload in (truncated_header, truncated_data):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_dxf(payload)

    def test_invalid_property_size_and_type_are_rejected(self):
        short_property = bytes.fromhex(
            "8d 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 "
            "01 00 03 00"
        )
        invalid_type = bytes.fromhex(
            "8d 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 "
            "27 00 04 00"
        )

        for payload in (short_property, invalid_type):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_dxf(payload)

    def test_count_and_declared_sizes_must_consume_payload_exactly(self):
        count_mismatch = bytearray(MULTIPLE_PROPERTIES)
        count_mismatch[16:18] = b"\x03\x00"
        trailing = ZERO_PROPERTIES + b"\x00"

        for payload in (count_mismatch, trailing):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_dxf(payload)

    def test_fill_pattern_and_gradient_properties_are_mutually_exclusive(self):
        for property_ids in ((0x0000, 0x0003), (0x0000, 0x0004)):
            with self.subTest(property_ids=property_ids):
                with self.assertRaises(ValueError):
                    validate_dxf(self.record_with_property_ids(property_ids))

    def test_internal_borders_require_f_new_border(self):
        for property_id in (0x000B, 0x000C):
            with self.subTest(property_id=property_id):
                payload = self.record_with_property_ids((property_id,))
                with self.assertRaises(ValueError):
                    validate_dxf(payload)

        allowed = self.record_with_property_ids((0x000B, 0x000C), dxf_flags=0x0002)
        self.assertEqual(validate_dxf(allowed)["property_count"], 2)

    def test_non_bytes_like_input_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_dxf(None)

    def test_dxf_payload_size_limit(self):
        at_limit = bytearray(8224)
        at_limit[0:2] = b"\x8d\x08"
        with self.assertRaisesRegex(ValueError, "trailing bytes"):
            validate_dxf(at_limit)

        payload = bytearray(8225)
        payload[0:2] = b"\x8d\x08"

        with self.assertRaisesRegex(ValueError, "8224-byte"):
            validate_dxf(payload)


if __name__ == "__main__":
    unittest.main()
