"""Known conditional-format groups remain opaque and cannot swallow cell records."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_biff8 import rec, cell
from test_wps_biff8_salvage import values
from test_wps_template_compatibility import template_workbook
from wps_biff8 import parse_stream, ResourceLimitError
from wps_biff8_conditional import validation_group


def header(count=1, bounds=(0, 9, 0, 2), regions=None):
    regions = [bounds] if regions is None else regions
    return (struct.pack('<2H4HH', count, 1, *bounds, len(regions)) +
            b''.join(struct.pack('<4H', *region) for region in regions))


def rule(condition=1, comparison=3, first=b'\x1e\x05\0', second=b'', style=bytes(6)):
    return struct.pack('<BBHH', condition, comparison, len(first), len(second)) + style + first + second


class ConditionalGroupTests(unittest.TestCase):
    def test_opaque_groups_preserve_saved_values_caches_and_source_bytes(self):
        for condition, comparison, second in ((1, 1, b'\x1e\x09\0'), (1, 3, b''), (2, 255, b'')):
            source_header = header()
            source_rule = rule(condition, comparison, second=second, style=bytes(124))
            raw = template_workbook(extra_sheet=rec(0x1B0, source_header) + rec(0x1B1, source_rule))
            result = parse_stream(raw, compatibility='data')
            self.assertEqual(values(result), values(parse_stream(template_workbook(), compatibility='data')))
            self.assertEqual(result['status'], 'partial')
            sources = [item for item in result['uninterpreted_metadata'] if item['record_id'] in ('0x1b0', '0x1b1')]
            self.assertEqual([item['payload_hex'] for item in sources], [source_header.hex(), source_rule.hex()])
            self.assertFalse(sources[1]['structure']['inner_payload_validated'])
            self.assertFalse(sources[1]['structure']['formulas_executed'])
            self.assertFalse(sources[1]['structure']['formatting_applied'])

    def test_counts_and_range_list_boundaries(self):
        malformed = (header(0), header(4), header(regions=[]), header()[:-1], header() + b'\0',
                     header(bounds=(9, 0, 0, 2)), header(bounds=(0, 9, 0, 256)),
                     header(regions=[(0, 10, 0, 2)]), header(regions=[(0, 1, 1, 0)]))
        for payload in malformed:
            with self.subTest(payload=payload.hex()), self.assertRaises(ValueError):
                parse_stream(template_workbook(extra_sheet=rec(0x1B0, payload) + rec(0x1B1, rule())), compatibility='data')

    def test_missing_interrupted_and_orphan_rules_refused_even_in_salvage(self):
        tails = (rec(0x1B0, header()), rec(0x1B1, rule()),
                 rec(0x1B0, header()) + rec(0x203, cell(10, 0) + struct.pack('<d', 1)),
                 rec(0x1B0, header(2)) + rec(0x1B1, rule()),
                 rec(0x1B0, header()) + rec(0x1B1, rule()) * 2)
        for salvage in (False, True):
            for tail in tails:
                with self.subTest(salvage=salvage, tail=tail.hex()), self.assertRaises(ValueError):
                    parse_stream(template_workbook(extra_sheet=tail), compatibility='data', salvage_unknown_metadata=salvage)

    def test_invalid_cf_headers_and_lengths_refused_at_the_rule_record(self):
        invalid = (bytes(11), rule(condition=3), rule(comparison=0), rule(condition=2, second=b'x'),
                   rule(comparison=3, second=b'x'), struct.pack('<BBHH', 1, 1, 200, 1) + bytes(10))
        for payload in invalid:
            raw = template_workbook(extra_sheet=rec(0x1B0, header()) + rec(0x1B1, payload))
            with self.subTest(payload=payload.hex()), self.assertRaises(ValueError) as failure:
                parse_stream(raw, compatibility='data')
            self.assertEqual(failure.exception.location['record_id'], '0x1b1')

    def test_three_rules_and_subsequent_cells_are_read(self):
        tail = (rec(0x1B0, header(3)) + rec(0x1B1, rule()) * 3 +
                rec(0x203, cell(10, 0) + struct.pack('<d', 123.5)))
        result = parse_stream(template_workbook(extra_sheet=tail), compatibility='data')
        self.assertEqual(result['sheets'][0]['cells']['A11']['value'], 123.5)
        self.assertEqual(sum(item['name'] == 'CFOpaque' for item in result['uninterpreted_metadata']), 3)

    def test_strict_legacy_budget_and_unknown_records_are_unchanged(self):
        tail = rec(0x1B0, header()) + rec(0x1B1, rule())
        for mode in ('strict', 'legacy_excel_clsid'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                parse_stream(template_workbook(extra_sheet=tail), compatibility=mode)
        with patch('wps_biff8.MAX_METADATA_BYTES', 16), self.assertRaises(ResourceLimitError):
            parse_stream(template_workbook(extra_sheet=tail), compatibility='data')
        with self.assertRaises(ValueError):
            parse_stream(template_workbook(extra_sheet=tail + rec(0x7777, b'unknown')), compatibility='data')


def validation_header(count=1):
    return bytes(10) + struct.pack('<iI', -1, count)


def validation_rule(*, strings=('标题', '', '提示', ''), first=b'\x1e\x01\0', second=b'', ranges=((0, 9, 0, 2),)):
    text = b''.join(struct.pack('<HB', len(value.encode('utf-16le')) // 2, 1) + value.encode('utf-16le') for value in strings)
    formulas = b''.join(struct.pack('<HH', len(value), 0) + value for value in (first, second))
    return bytes(4) + text + formulas + struct.pack('<H', len(ranges)) + b''.join(struct.pack('<4H', *region) for region in ranges)


class ValidationGroupTests(unittest.TestCase):
    def read(self, header_payload=None, rules=None, after=b'', **options):
        header_payload = validation_header() if header_payload is None else header_payload
        rules = [validation_rule()] if rules is None else rules
        tail = rec(0x1B2, header_payload) + b''.join(rec(0x1BE, raw) for raw in rules) + after
        return parse_stream(template_workbook(extra_sheet=tail), compatibility='data', **options)

    def test_saved_values_and_raw_opaque_formulas_preserved(self):
        # Arbitrary rule bytes are not claimed to be parsed formula semantics.
        raw = validation_rule(first=b'\xffopaque', second=b'\x39link')
        after = rec(0x203, cell(10, 0) + struct.pack('<d', 123.5))
        result = self.read(rules=[raw], after=after)
        base = parse_stream(template_workbook(extra_sheet=after), compatibility='data')
        self.assertEqual(values(result), values(base))
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['external_links_followed'])
        source = next(item for item in result['uninterpreted_metadata'] if item['name'] == 'DvOpaque')
        self.assertEqual(source['payload_hex'], raw.hex())
        self.assertEqual(source['structure']['formula_bytes'], [7, 5])
        self.assertFalse(source['structure']['formula_semantics_validated'])
        self.assertFalse(source['structure']['validation_applied'])
        self.assertFalse(source['structure']['formulas_executed'])

    def test_empty_and_multiple_validation_groups(self):
        self.assertEqual(self.read(validation_header(0), [])['cell_count'], 4)
        result = self.read(validation_header(2), [validation_rule(), validation_rule()])
        self.assertEqual(sum(item['name'] == 'DvOpaque' for item in result['uninterpreted_metadata']), 2)

    def test_malformed_header_count_truncation_and_record_budget(self):
        for payload in (bytes(17), bytes(19), validation_header(65535), validation_header(2)):
            with self.subTest(header=payload.hex()), self.assertRaises(ValueError):
                self.read(payload)
        with self.assertRaises(ValueError) as failure:
            validation_group([(0, 0x1B2, validation_header(1)), (22, 0x1BE, validation_rule())], 0, 1)
        self.assertEqual(failure.exception.status, 'resource_limit')
        with patch('wps_biff8.MAX_METADATA_BYTES', 18), self.assertRaises(ResourceLimitError):
            self.read()

    def test_interrupted_and_orphan_rules_fail_even_in_salvage(self):
        for salvage in (False, True):
            for tail in (rec(0x1BE, validation_rule()), rec(0x1B2, validation_header()),
                         rec(0x1B2, validation_header()) + rec(0x203, cell(5, 0) + struct.pack('<d', 1)),
                         rec(0x1B2, validation_header()) + rec(0x1BE, validation_rule()) * 2):
                with self.subTest(salvage=salvage, tail=tail.hex()), self.assertRaises(ValueError):
                    parse_stream(template_workbook(extra_sheet=tail), compatibility='data', salvage_unknown_metadata=salvage)

    def test_string_formula_and_range_boundary_failures(self):
        raw = validation_rule(strings=('', '', '', ''))
        invalid = [raw[:-1], raw + b'x', bytes(3), validation_rule(strings=('x' * 33, '', '', '')),
                   validation_rule(ranges=()), validation_rule(ranges=((9, 0, 0, 1),)),
                   validation_rule(ranges=((0, 9, 0, 256),))]
        for offset, value in ((4, 65535), (16, 65535), (23, 65535)):
            changed = bytearray(raw)
            struct.pack_into('<H', changed, offset, value)
            invalid.append(bytes(changed))
        invalid.append(raw[:6] + b'\x80' + raw[7:])
        for payload in invalid:
            with self.subTest(payload=payload.hex()), self.assertRaises(ValueError):
                self.read(rules=[payload])

    def test_strict_and_legacy_do_not_gain_opaque_validation(self):
        raw = template_workbook(extra_sheet=rec(0x1B2, validation_header()) + rec(0x1BE, validation_rule()))
        for mode in ('strict', 'legacy_excel_clsid'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                parse_stream(raw, compatibility=mode)


if __name__ == '__main__':
    unittest.main()
