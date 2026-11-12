"""Synthetic internal and external hyperlink metadata, not WPS provenance."""
import sys
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import patch
from xml.parsers import expat
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_wps_ooxml_sheet import (
    add_sheet_relationships, codes, package, result, P, R,
)


@unittest.skipUnless(expat.version_info >= (2, 6, 0), 'requires Expat >=2.6')
class InternalLinks(unittest.TestCase):
    def test_metadata_not_executed_or_resolved(self):
        for strict in (False, True):
            value = result(package(strict=strict, sheet_extra=(
                '<hyperlinks><hyperlink ref="A1" location="Data!B2" display="跳转" '
                'tooltip="提示"/></hyperlinks>')))
            self.assertEqual(value['status'], 'partial')
            self.assertEqual(str(value['sheets'][0]['cells']['A1']['value']), '12.5')
            link = value['sheets'][0]['hyperlinks'][0]
            self.assertEqual((link['cell'], link['location_raw'], link['display_raw']),
                             ('A1', 'Data!B2', '跳转'))
            self.assertFalse(link['followed'])
            self.assertFalse(link['target_resolved'])
            self.assertFalse(value['external_links_followed'])

    def test_invalid_and_duplicate_links_are_recorded_without_losing_cells(self):
        for body, code in (
                ('<hyperlink ref="XFE1" location="x"/>', 'hyperlink_reference_not_interpreted'),
                ('<hyperlink ref="A1"/>', 'hyperlink_target_not_followed'),
                ('<hyperlink ref="A1" location="x"/><hyperlink ref="A1" location="y"/>',
                 'duplicate_hyperlink_ignored'),
                ('<hyperlink ref="A1:B2" location="x"/>', None)):
            with self.subTest(body=body):
                value = result(package(sheet_extra='<hyperlinks>' + body + '</hyperlinks>'))
                self.assertEqual(value['status'], 'partial')
                self.assertEqual(value['sheets'][0]['cells']['A1']['value'], 12.5)
                if code:
                    self.assertIn(code, codes(value))
        duplicate = result(package(sheet_extra=(
            '<hyperlinks><hyperlink ref="A1" location="x"/>'
            '<hyperlink ref="A1" location="y"/></hyperlinks>')))
        self.assertEqual(len(duplicate['sheets'][0]['hyperlinks']), 1)

    def test_external_hyperlink_relationship_is_recorded_not_followed(self):
        rels = ('<Relationships xmlns="' + P + '"><Relationship Id="h1" Type="' + R +
                '/hyperlink" Target="https://example.invalid/page" '
                'TargetMode="External"/></Relationships>')
        value = result(add_sheet_relationships(package(sheet_extra=(
            '<hyperlinks><hyperlink ref="A1" r:id="h1"/></hyperlinks>')), rels))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['sheets'][0]['cells']['A1']['value'], 12.5)
        link = value['sheets'][0]['hyperlinks'][0]
        self.assertEqual(link['relationship_id_raw'], 'h1')
        self.assertTrue(link['relationship_external'])
        self.assertFalse(link['target_resolved'])
        self.assertFalse(link['followed'])
        self.assertIn('external_relationship_not_followed', codes(value))

    def test_count_budget_degrades_instead_of_failing(self):
        links = ''.join(f'<hyperlink ref="A{index}" location="x{index}"/>'
                        for index in range(1, 8))
        with patch('wps_ooxml_worksheet.MAX_HYPERLINKS', 3):
            value = result(package(sheet_extra='<hyperlinks>' + links + '</hyperlinks>'))
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(len(value['sheets'][0]['hyperlinks']), 3)
        self.assertIn('hyperlink_count_limit_reached', codes(value))


if __name__ == '__main__':
    unittest.main()
