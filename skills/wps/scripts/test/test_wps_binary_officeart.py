"""Literal synthetic OfficeArt records for the bounded MS-DOC reader."""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parents[1])]

import struct
import unittest

from read_wps import ParseError, Region, require
from wps_binary_officeart import read_officeart


DGG = 0xF000
DG = 0xF002
SPGR = 0xF003
SP = 0xF004
FSP = 0xF00A
FOPT = 0xF00B
HAVE_SPT = 1 << 11
DELETED = 1 << 3
PATRIARCH = 1 << 2


def record(record_type, version, instance, payload=b''):
    """Encode one literal 8-byte OfficeArt header and its payload."""
    return struct.pack('<HHI', (instance << 4) | version,
                       record_type, len(payload)) + payload


def complex_property(opid, value):
    encoded = value.encode('utf-16le') + b'\x00\x00'
    return opid | 0x8000, encoded


def fopt(properties, *, version=3, tail_override=None):
    """Encode FOPT rows and complex values in property order."""
    entries = bytearray()
    tail = bytearray()
    for opid_word, value in properties:
        if opid_word & 0x8000:
            entries.extend(struct.pack('<HI', opid_word, len(value)))
            tail.extend(value)
        else:
            entries.extend(struct.pack('<HI', opid_word, value))
    if tail_override is not None:
        tail = bytearray(tail_override)
    payload = bytes(entries + tail)
    return record(FOPT, version, len(properties), payload)


def fsp(shape_id, flags=0, shape_type=1):
    return record(FSP, 2, shape_type, struct.pack('<II', shape_id, flags))


def sp(shape_id, flags=0, shape_type=1, options=(), extras=(),
       fsp_record=None):
    children = [fsp(shape_id, flags, shape_type) if fsp_record is None
                else fsp_record]
    children.extend(extras)
    children.extend(options)
    return record(SP, 15, 0, b''.join(children))


def drawing(*children):
    return record(DG, 15, 0, b''.join(children))


def officeart(drawings, *, base=4096, dgg_payload=b'opaque drawing group'):
    parts = [record(DGG, 15, 0, dgg_payload)]
    for label, content in drawings:
        parts.extend((bytes((label,)), content))
    return Region(b''.join(parts), '1Table', base=base)


def requested(*ids):
    return {0: set(ids)}


class OfficeArtReaderTests(unittest.TestCase):
    def parse(self, region, wanted):
        return read_officeart(region, wanted, require)

    def assert_status(self, region, wanted, status):
        with self.assertRaises(ParseError) as caught:
            self.parse(region, wanted)
        self.assertEqual(caught.exception.status, status)

    def test_grouped_shapes_and_story_labels_route_independently(self):
        patriarch = sp(90, PATRIARCH | 1)
        body_shape = sp(101, HAVE_SPT | 1, 2)
        nested = record(SPGR, 15, 0, sp(102, HAVE_SPT, 3))
        group = record(SPGR, 15, 0, patriarch + body_shape + nested)
        header_shape = sp(201, HAVE_SPT | (1 << 4) | (1 << 8) | (1 << 9), 75)
        anchor_only_shape = sp(202, 1 << 9)
        region = officeart(((0, drawing(group)),
                            (1, drawing(header_shape, anchor_only_shape))))

        result = self.parse(region, {0: {90, 101, 102, 201},
                                     1: {101, 201, 202}})

        self.assertEqual(set(result), {(0, 101), (0, 102), (1, 201), (1, 202)})
        self.assertEqual(result[(0, 101)]['shape_type'], 'rounded_rectangle')
        self.assertTrue(result[(0, 101)]['is_group'])
        self.assertEqual(result[(1, 201)]['shape_type'], 'picture_frame')
        self.assertTrue(result[(1, 201)]['is_ole_shape'])
        self.assertTrue(result[(1, 201)]['is_connector'])
        self.assertNotIn('is_ole_shape', result[(1, 202)])
        self.assertNotIn('is_connector', result[(1, 202)])
        patriarch_size = len(patriarch)
        body_fsp_offset = (len(record(DGG, 15, 0, b'opaque drawing group')) +
                           1 + 8 + 8 + patriarch_size + 8)
        self.assertEqual(result[(0, 101)]['source'], region.location(body_fsp_offset))
        self.assertNotIn((0, 201), result)
        self.assertNotIn((1, 101), result)

    def test_duplicate_ids_are_ambiguous_before_deleted_filtering(self):
        region = officeart(((0, drawing(sp(17), sp(17, DELETED))),))
        self.assertEqual(self.parse(region, requested(17)), {})

        deleted = officeart(((0, drawing(sp(18, DELETED))),))
        patriarch = officeart(((0, drawing(sp(19, PATRIARCH))),))
        self.assertEqual(self.parse(deleted, requested(18)), {})
        self.assertEqual(self.parse(patriarch, requested(19)), {})

    def test_opaque_records_are_skipped_and_unknown_types_stay_numeric(self):
        apparent_fsp = fsp(88, HAVE_SPT, 1)
        opaque = record(0xF777, 7, 12, apparent_fsp + b'other data')
        unknown = sp(89, HAVE_SPT, 0xABC, extras=(opaque,))
        no_type = sp(90, 0, 202)
        region = officeart(((0, drawing(opaque, unknown, no_type)),))

        result = self.parse(region, requested(88, 89, 90))

        self.assertEqual(set(result), {(0, 89), (0, 90)})
        self.assertEqual(result[(0, 89)]['shape_type_code'], 0xABC)
        self.assertNotIn('shape_type', result[(0, 89)])
        self.assertNotIn('shape_type_code', result[(0, 90)])

    def test_primary_fopt_text_and_absolute_payload_sources(self):
        prefix_complex = (0x03A0 | 0x8000, b'unparsed binary')
        properties = [
            prefix_complex,
            complex_property(0x00C0, 'DRAFT 合同'),
            complex_property(0x0380, '标题 Ω'),
            complex_property(0x0381, '说明 🚀'),
        ]
        option = fopt(properties)
        shape = sp(31, HAVE_SPT, 202, options=(option,))
        region = officeart(((0, drawing(shape)),), base=12000)

        result = self.parse(region, requested(31))[(0, 31)]

        self.assertEqual(result['wordart_text'], 'DRAFT 合同')
        self.assertEqual(result['name'], '标题 Ω')
        self.assertEqual(result['description'], '说明 🚀')
        fsp_offset = (len(record(DGG, 15, 0, b'opaque drawing group')) + 1 + 8 + 8)
        self.assertEqual(result['source'], region.location(fsp_offset))
        fopt_offset = fsp_offset + 16
        payload_start = fopt_offset + 8 + 6 * len(properties)
        self.assertEqual(result['text_sources'], {
            'wordart_text': region.location(payload_start + len(b'unparsed binary')),
            'name': region.location(payload_start + len(b'unparsed binary') +
                                    len('DRAFT 合同'.encode('utf-16le') + b'\x00\x00')),
            'description': region.location(payload_start + len(b'unparsed binary') +
                                           len('DRAFT 合同'.encode('utf-16le') + b'\x00\x00') +
                                           len('标题 Ω'.encode('utf-16le') + b'\x00\x00')),
        })

    def test_optional_text_failure_keeps_shape_metadata(self):
        malformed_rows = record(FOPT, 3, 1, b'')
        bad_tail = record(FOPT, 3, 1,
                          struct.pack('<HI', 0x0380 | 0x8000, 4) + b'\x00\x00')
        duplicate_name = fopt([complex_property(0x0380, 'A'),
                               complex_property(0x0380, 'B')])
        malformed_unicode = record(FOPT, 3, 1,
                                   struct.pack('<HI', 0x0380 | 0x8000, 2) +
                                   b'\x00\xd8')
        malformed_version = fopt([complex_property(0x0380, 'A')], version=2)
        repeated_fopt = fopt([complex_property(0x0380, 'A')])

        for options in ((malformed_rows,), (bad_tail,), (duplicate_name,),
                        (malformed_unicode,), (malformed_version,),
                        (repeated_fopt, repeated_fopt)):
            with self.subTest(options=options):
                region = officeart(((0, drawing(
                    sp(44, HAVE_SPT, 1, options=options))),))
                result = self.parse(region, requested(44))[(0, 44)]
                self.assertEqual(result['shape_type'], 'rectangle')
                self.assertEqual(result['text_status'], 'invalid_or_unsupported')
                self.assertNotIn('name', result)
                self.assertNotIn('description', result)
                self.assertNotIn('wordart_text', result)
                self.assertNotIn('text_sources', result)

    def test_noncomplex_zero_is_absent_and_nonzero_is_unsupported(self):
        absent = fopt([(0x0380, 0)])
        unsupported = fopt([(0x0380, 4)])
        valid_region = officeart(((0, drawing(sp(51, options=(absent,)))),))
        invalid_region = officeart(((0, drawing(sp(52, options=(unsupported,)))),))
        self.assertNotIn('text_status', self.parse(valid_region, requested(51))[(0, 51)])
        self.assertEqual(self.parse(invalid_region, requested(52))[(0, 52)]['text_status'],
                         'invalid_or_unsupported')

    def test_structural_record_container_and_fsp_corruption(self):
        valid = officeart(((0, drawing(sp(61))),))
        cases = []
        cases.append(officeart(((0, drawing(sp(61))),), dgg_payload=b'' )
                     .region(0, 7))

        invalid_container = record(DG, 14, 0, sp(61))
        cases.append(officeart(((0, invalid_container),)))
        wrong_fsp = record(FSP, 1, 1, struct.pack('<II', 61, 0))
        cases.append(officeart(((0, drawing(sp(61, fsp_record=wrong_fsp))),)))
        truncated = record(DGG, 15, 0) + b'\x00' + b'\x01\x02'
        cases.append(Region(truncated, '1Table'))
        bad_parent = record(DGG, 15, 0) + b'\x00' + struct.pack(
            '<HHI', 0xF, DG, 100)
        cases.append(Region(bad_parent, '1Table'))
        for region in cases:
            with self.subTest(data_length=len(region.data)):
                self.assert_status(region, requested(61), 'corrupt')
        self.assertEqual(self.parse(valid, requested(61))[(0, 61)]['source'],
                         valid.location(len(record(DGG, 15, 0,
                                                    b'opaque drawing group')) + 17))

    def test_drawing_labels_allow_reversed_order_but_must_be_unique(self):
        body = drawing(sp(70))
        header = drawing(sp(71))
        reversed_order = self.parse(officeart(((1, header), (0, body))), {0: {70}, 1: {71}})
        self.assertEqual(set(reversed_order), {(0, 70), (1, 71)})
        for drawings in (((0, body), (0, body)), ((1, header), (1, header)), ((2, header),)):
            self.assert_status(officeart(drawings), {0: {70}, 1: {71}}, 'corrupt')

    def test_record_count_and_container_depth_are_bounded(self):
        many_records = b''.join(record(0xF777, 0, 0) for _ in range(20000))
        record_limited = officeart(((0, drawing(
            record(SPGR, 15, 0, many_records))),))
        self.assert_status(record_limited, {}, 'resource_limit')

        nested = b''
        for _ in range(32):
            nested = record(SPGR, 15, 0, nested)
        depth_limited = officeart(((0, drawing(nested)),))
        self.assert_status(depth_limited, {}, 'resource_limit')

    def test_output_text_budget_is_bounded(self):
        text_value = 'x' * 4095
        option = fopt([complex_property(0x0380, text_value)])
        shapes = [sp(shape_id, options=(option,)) for shape_id in range(100, 133)]
        region = officeart(((0, drawing(*shapes)),))
        self.assert_status(region, requested(*range(100, 133)), 'resource_limit')


if __name__ == '__main__':
    unittest.main()
