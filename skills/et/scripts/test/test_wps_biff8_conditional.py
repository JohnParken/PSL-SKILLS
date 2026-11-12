"""Known conditional-format/validation groups are retained opaquely.

Valid groups keep bounded structural evidence. Malformed groups, orphan rules and
unsupported semantics degrade to issues and raw retention so later cell values
are never discarded; nothing is evaluated or applied.
"""
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
from test_wps_cfb import synthetic_cfb
from read_et import read_bytes
from wps_biff8 import parse_stream
from wps_biff8_conditional import validation_group


def header(count=1, bounds=(0, 9, 0, 2), regions=None):
    regions = [bounds] if regions is None else regions
    return (struct.pack('<2H4HH', count, 1, *bounds, len(regions)) +
            b''.join(struct.pack('<4H', *region) for region in regions))


def rule(condition=1, comparison=3, first=b'\x1e\x05\0', second=b'', style=bytes(6)):
    return struct.pack('<BBHH', condition, comparison, len(first), len(second)) + style + first + second


def issue_codes(result):
    return [item.get('code') for item in result.get('issues', ())]


class ConditionalGroupTests(unittest.TestCase):
    def test_numeric_comparison_and_fill_are_observed_not_applied(self):
        def literal(value):
            return b'\x1e' + struct.pack('<H', value)

        fill = struct.pack('<I3H', 0x20040000, 0, 1 << 10, 10 | (40 << 7))
        first = rule(comparison=5, first=literal(100), style=fill)
        second = rule(comparison=1, first=literal(10), second=literal(20))
        third = rule(comparison=5, first=b'\x44\0\0\0\0')
        tail = rec(0x1B0, header(3)) + b''.join(rec(0x1B1, item) for item in (first, second, third))
        result = parse_stream(template_workbook(extra_sheet=tail))
        rules = [item['structure'] for item in result['uninterpreted_metadata'] if item['name'] == 'CFOpaque']
        self.assertEqual(rules[0]['cell_value_comparison'], {'operator': 'greaterThan', 'operands': ['100']})
        self.assertEqual(rules[0]['differential_fill'], {'pattern_id': 1, 'foreground_color_index': 10})
        self.assertEqual(rules[1]['cell_value_comparison'], {'operator': 'between', 'operands': ['10', '20']})
        self.assertNotIn('cell_value_comparison', rules[2])
        self.assertFalse(rules[0]['formatting_applied'])
        self.assertEqual(values(result), values(parse_stream(template_workbook())))

    def test_opaque_groups_preserve_saved_values_caches_and_source_bytes(self):
        for condition, comparison, second in ((1, 1, b'\x1e\x09\0'), (1, 3, b''), (2, 255, b'')):
            source_header = header()
            source_rule = rule(condition, comparison, second=second, style=bytes(124))
            raw = template_workbook(extra_sheet=rec(0x1B0, source_header) + rec(0x1B1, source_rule))
            result = parse_stream(raw)
            self.assertEqual(values(result), values(parse_stream(template_workbook())))
            self.assertEqual(result['status'], 'partial')
            sources = [item for item in result['uninterpreted_metadata'] if item['record_id'] in ('0x1b0', '0x1b1')]
            self.assertEqual([item['payload_hex'] for item in sources], [source_header.hex(), source_rule.hex()])
            self.assertFalse(sources[1]['structure']['inner_payload_validated'])
            self.assertFalse(sources[1]['structure']['formulas_executed'])
            self.assertFalse(sources[1]['structure']['formatting_applied'])

    def test_semantic_range_problems_are_reported_not_aborted(self):
        # Rule ranges outside the bounding rectangle keep every saved cell.
        for source_header in (header(bounds=(9, 0, 0, 2)),
                              header(bounds=(0, 9, 0, 256)),
                              header(regions=[(0, 10, 0, 2)]),
                              header(regions=[(0, 1, 1, 0)])):
            with self.subTest(header=source_header.hex()):
                tail = rec(0x1B0, source_header) + rec(0x1B1, rule()) + \
                    rec(0x0203, cell(10, 0) + struct.pack('<d', 7))
                result = parse_stream(template_workbook(extra_sheet=tail))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A11']['value'], 7)
                self.assertIn('bounded_rule_group_not_applied', issue_codes(result))

    def test_missing_or_orphan_rule_records_retain_later_cells(self):
        tails = (rec(0x1B1, rule()),
                 rec(0x1B0, header()) + rec(0x203, cell(10, 0) + struct.pack('<d', 1)),
                 rec(0x1B0, header(2)) + rec(0x1B1, rule()),
                 rec(0x1B0, header()) + rec(0x1B1, rule()) * 2)
        for tail in tails:
            with self.subTest(tail=tail.hex()):
                result = parse_stream(template_workbook(extra_sheet=tail))
                self.assertEqual(result['status'], 'partial')
                self.assertTrue(any(code in ('bounded_rule_group_validation_failed',
                                             'orphan_rule_record',
                                             'bounded_rule_group_not_applied')
                                    for code in issue_codes(result)))
                self.assertIn(result['sheets'][0]['cells'].get('A11', {}).get('value'), (None, 1))

    def test_invalid_cf_headers_are_retained_with_diagnostic(self):
        invalid = (bytes(11), rule(condition=3), rule(comparison=0),
                   rule(condition=2, second=b'x'), rule(comparison=3, second=b'x'),
                   struct.pack('<BBHH', 1, 1, 200, 1) + bytes(10))
        for payload in invalid:
            raw = template_workbook(extra_sheet=rec(0x1B0, header()) + rec(0x1B1, payload))
            with self.subTest(payload=payload.hex()):
                result = parse_stream(raw)
                self.assertEqual(result['status'], 'partial')
                self.assertTrue(any(code in ('bounded_rule_group_validation_failed',
                                             'bounded_rule_group_not_applied')
                                    for code in issue_codes(result)))
                retained = [item for item in result['uninterpreted_metadata']
                            if item['record_id'] in ('0x1b0', '0x1b1')]
                self.assertTrue(retained)

    def test_three_rules_and_subsequent_cells_are_read(self):
        tail = (rec(0x1B0, header(3)) + rec(0x1B1, rule()) * 3 +
                rec(0x203, cell(10, 0) + struct.pack('<d', 123.5)))
        result = parse_stream(template_workbook(extra_sheet=tail))
        self.assertEqual(result['sheets'][0]['cells']['A11']['value'], 123.5)
        self.assertEqual(sum(item['name'] == 'CFOpaque' for item in result['uninterpreted_metadata']), 3)

    def test_metadata_budget_and_unknown_records_do_not_abort(self):
        tail = rec(0x1B0, header()) + rec(0x1B1, rule())
        with patch('wps_biff8.MAX_METADATA_BYTES', 16):
            result = parse_stream(template_workbook(extra_sheet=tail))
            self.assertEqual(result['status'], 'partial')
            self.assertIn('metadata_budget_truncated', issue_codes(result))
        result = parse_stream(template_workbook(extra_sheet=tail + rec(0x7777, b'unknown')))
        self.assertEqual(result['status'], 'partial')
        self.assertIn('unknown_biff_record_salvaged', issue_codes(result))


def validation_header(count=1):
    return bytes(10) + struct.pack('<iI', -1, count)


def validation_rule(*, strings=('标题', '', '提示', ''), first=b'\x1e\x01\0', second=b'', ranges=((0, 9, 0, 2),)):
    text = b''.join(struct.pack('<HB', len(value.encode('utf-16le')) // 2, 1) + value.encode('utf-16le') for value in strings)
    formulas = b''.join(struct.pack('<HH', len(value), 0) + value for value in (first, second))
    return bytes(4) + text + formulas + struct.pack('<H', len(ranges)) + b''.join(struct.pack('<4H', *region) for region in ranges)


class RuleGroupFileTests(unittest.TestCase):
    def test_bounded_rule_semantic_errors_keep_later_cells(self):
        cf = rule(condition=3)
        dv = validation_rule(ranges=((9, 0, 0, 1),))
        tail = rec(0x203, cell(10, 0) + struct.pack('<d', 42))
        for prefix, member, payload in ((rec(0x1B0, header()), 0x1B1, cf),
                                        (rec(0x1B2, validation_header()), 0x1BE, dv)):
            with self.subTest(member=member):
                stream = template_workbook(extra_sheet=prefix + rec(member, payload) + tail)
                result = read_bytes(synthetic_cfb({'Workbook': stream}))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A11']['value'], 42)
                self.assertEqual(result['cell_value_coverage'], 'complete_within_declared_sheets')
                source = next(item for item in result['uninterpreted_metadata'] if item['record_id'] == hex(member))
                self.assertEqual(source['payload_hex'], payload.hex())
                self.assertIn('semantic_issue', source['structure'])

    def test_malformed_rule_members_are_retained_not_fatal(self):
        cf = rule(condition=3)
        dv = validation_rule(ranges=((9, 0, 0, 1),))
        tail = rec(0x203, cell(10, 0) + struct.pack('<d', 42))
        for prefix, member in ((rec(0x1B0, header()), 0x1B1),
                               (rec(0x1B2, validation_header()), 0x1BE)):
            with self.subTest(member=member):
                broken = prefix + rec(member, bytes(3)) + tail
                result = read_bytes(synthetic_cfb({'Workbook': template_workbook(extra_sheet=broken)}))
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['sheets'][0]['cells']['A11']['value'], 42)
                self.assertIn('bounded_rule_group_validation_failed', issue_codes(result))


class ValidationGroupTests(unittest.TestCase):
    def read(self, header_payload=None, rules=None, after=b'', **options):
        header_payload = validation_header() if header_payload is None else header_payload
        rules = [validation_rule()] if rules is None else rules
        tail = rec(0x1B2, header_payload) + b''.join(rec(0x1BE, raw) for raw in rules) + after
        return parse_stream(template_workbook(extra_sheet=tail), **options)

    def test_saved_values_and_raw_opaque_formulas_preserved(self):
        raw = validation_rule(first=b'\xffopaque', second=b'\x39link')
        after = rec(0x203, cell(10, 0) + struct.pack('<d', 123.5))
        result = self.read(rules=[raw], after=after)
        base = parse_stream(template_workbook(extra_sheet=after))
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
            with self.subTest(header=payload.hex()):
                result = self.read(payload)
                self.assertEqual(result['status'], 'partial')
                self.assertIn('bounded_rule_group_validation_failed', issue_codes(result))
        with self.assertRaises(ValueError) as failure:
            validation_group([(0, 0x1B2, validation_header(1)), (22, 0x1BE, validation_rule())], 0, 0)
        self.assertEqual(failure.exception.status, 'resource_limit')
        with patch('wps_biff8.MAX_METADATA_BYTES', 18):
            result = self.read()
            self.assertEqual(result['status'], 'partial')

    def test_interrupted_and_orphan_rules_keep_saved_cells(self):
        for tail in (rec(0x1BE, validation_rule()), rec(0x1B2, validation_header()),
                     rec(0x1B2, validation_header()) + rec(0x203, cell(5, 0) + struct.pack('<d', 1)),
                     rec(0x1B2, validation_header()) + rec(0x1BE, validation_rule()) * 2):
            with self.subTest(tail=tail.hex()):
                result = parse_stream(template_workbook(extra_sheet=tail))
                self.assertEqual(result['status'], 'partial')
                self.assertIn(result['sheets'][0]['cells'].get('A6', {}).get('value'), (None, 1))

    def test_string_formula_and_range_boundary_failures_degrade(self):
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
            with self.subTest(payload=payload.hex()):
                result = self.read(rules=[payload])
                self.assertEqual(result['status'], 'partial')
                self.assertTrue(any(code in ('bounded_rule_group_validation_failed',
                                             'bounded_rule_group_not_applied')
                                    for code in issue_codes(result)))


if __name__ == '__main__':
    unittest.main()
