"""Resource policy boundaries using synthetic inputs only, never user files."""
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_STORED

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docx import Document
import tl_resource_limits as limits
from tl_package import Package, parse
from tl_read import report
from tl_read_cache import cached_report, load, release, store


def sample(count=3):
    output = BytesIO()
    Document().save(output)
    with ZipFile(BytesIO(output.getvalue())) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    parts['word/document.xml'] = ('<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>' + '<w:p><w:r><w:t>中文🙂</w:t></w:r></w:p>' * count
        + '</w:body></w:document>').encode()
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_STORED) as target:
        for name, data in parts.items():
            target.writestr(name, data)
    return output.getvalue()


class ResourceLimitsTests(unittest.TestCase):
    def assert_resource(self, resource, callback):
        with self.assertRaises(limits.ResourceLimitError) as error:
            callback()
        self.assertEqual(error.exception.code, 'resource_limit_exceeded')
        self.assertEqual(error.exception.next_action['resource'], resource)

    def test_xml_node_boundaries_and_before_dom(self):
        for maximum in (2, 3):
            with patch.dict(limits.LIMITS, xml_nodes=maximum):
                parse(b'<r><a/></r>')
        with patch.dict(limits.LIMITS, xml_nodes=1), patch('tl_package.parse_xml') as dom:
            self.assert_resource('xml_nodes', lambda: parse(b'<r><a/></r>'))
            dom.assert_not_called()

    def test_xml_depth_and_characters(self):
        with patch.dict(limits.LIMITS, xml_depth=2):
            parse(b'<r><a/></r>')
            self.assert_resource('xml_depth', lambda: parse(b'<r><a><b/></a></r>'))
        # Names r/a, attribute a/value, text and trailing text = 6 characters.
        value = '<r a="中">🙂<a/>尾</r>'.encode()
        with patch.dict(limits.LIMITS, xml_characters=6):
            parse(value)
        with patch.dict(limits.LIMITS, xml_characters=5):
            self.assert_resource('xml_characters', lambda: parse(value))

    def test_comments_and_processing_instructions_count(self):
        with patch.dict(limits.LIMITS, xml_nodes=2):
            self.assert_resource('xml_nodes', lambda: parse(b'<r><!--x--><?x y?></r>'))

    def test_dtd_and_malformed_are_distinct(self):
        with self.assertRaisesRegex(ValueError, 'DTD is not supported'):
            parse(b'<!DOCTYPE r [<!ENTITY a "x">]><r>&a;</r>')
        with self.assertRaisesRegex(ValueError, '^Malformed XML part$'):
            parse(b'<r>')

    def test_external_and_parameter_entities_rejected(self):
        payloads = [
            b'<!DOCTYPE r SYSTEM "https://example.invalid/input"><r/>',
            b'<!DOCTYPE r [<!ENTITY % p SYSTEM "file:///never-read">%p;]><r/>',
            b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///never-read">]><r>&x;</r>',
        ]
        for payload in payloads:
            with self.subTest(payload=payload), patch('tl_package.parse_xml') as dom:
                with self.assertRaisesRegex(ValueError, 'DTD is not supported'):
                    parse(payload)
                dom.assert_not_called()

    def test_optional_part_resource_error_is_not_partial(self):
        package = Package(sample())
        from tl_read_parts import PartCatalog
        issues = []
        catalog = PartCatalog(package, lambda *args, **kwargs: issues.append(args))
        with patch.object(package, 'xml', side_effect=limits.ResourceLimitError('xml_nodes')):
            self.assert_resource('xml_nodes', lambda: catalog.xml('word/header.xml', 'header'))
        self.assertEqual(issues, [])

    def test_old_expat_rejected_before_dom(self):
        with patch.object(limits.expat, 'version_info', (2, 5, 0)), patch('tl_package.parse_xml') as dom:
            with self.assertRaisesRegex(ValueError, 'Expat >=2.6.0'):
                parse(b'<r/>')
            dom.assert_not_called()

    def test_aggregate_and_package_cache_not_double_counted(self):
        budget = limits.XMLBudget()
        with patch.dict(limits.LIMITS, xml_nodes=2):
            budget.screen(b'<r/>')
            budget.screen(b'<r/>')
            self.assert_resource('xml_nodes', lambda: budget.screen(b'<r/>'))
        package = Package(sample())
        before = (package._xml_budget.nodes, package._xml_budget.characters)
        self.assertIs(package.xml(package.main), package.xml(package.main))
        self.assertEqual(before, (package._xml_budget.nodes, package._xml_budget.characters))
        other = Package(sample())
        self.assertEqual(before, (other._xml_budget.nodes, other._xml_budget.characters))

    def test_report_boundaries_and_unicode(self):
        value = ([{'text': '中🙂'}], [], {})
        with patch.dict(limits.LIMITS, report_records=1, value_characters=6):
            limits.validate_report(value)
        with patch.dict(limits.LIMITS, report_records=0):
            self.assert_resource('report_records', lambda: limits.validate_report(value))
        with patch.dict(limits.LIMITS, value_characters=5):
            self.assert_resource('value_characters', lambda: limits.validate_report(value))
        with patch.dict(limits.LIMITS, report_issues=0):
            self.assert_resource('report_issues', lambda: limits.validate_report(([], [{}], {})))

    def test_json_preflight_quotes_and_depth(self):
        raw = json.dumps({'text': '[\\"{}]中🙂', 'values': [1, True, None]}).encode()
        limits.screen_json(raw)
        with patch.dict(limits.LIMITS, value_depth=2):
            limits.screen_json(b'[[0]]')
            self.assert_resource('value_depth', lambda: limits.screen_json(b'[[[0]]]'))
            self.assert_resource('value_depth', lambda: limits.validate_value([[[0]]]))
        with patch.dict(limits.LIMITS, value_nodes=3):
            limits.screen_json(b'[1,2]')
            self.assert_resource('value_nodes', lambda: limits.screen_json(b'[1,2,3]'))

    def test_cold_reader_is_bounded(self):
        package = Package(sample())
        with patch.dict(limits.LIMITS, report_records=2):
            self.assert_resource('report_records', lambda: report(package))

    def test_cache_hit_revalidates_and_release_still_works(self):
        with tempfile.TemporaryDirectory() as folder:
            root = os.open(folder, os.O_RDONLY)
            try:
                package = Package(sample())
                options = {'revisions': None, 'include_related': True}
                data, meta = cached_report(root, package)
                token = meta['token']
                self.assertEqual(load(root, token, package.sha256, options), data)
                with patch('tl_read_cache.validate_report', side_effect=limits.ResourceLimitError('report_records')):
                    self.assert_resource('report_records', lambda: load(root, token, package.sha256, options))
                    self.assertEqual(release(root, token)['status'], 'released')
            finally:
                os.close(root)

    def test_cache_policy_change_rejected_but_releasable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = os.open(folder, os.O_RDONLY)
            try:
                token = store(root, 'a' * 64, {}, ([], [], {}))
                with patch.dict(limits.LIMITS, report_records=1):
                    with self.assertRaisesRegex(ValueError, 'reading options changed'):
                        load(root, token, 'a' * 64, {})
                    self.assertEqual(release(root, token)['status'], 'released')
            finally:
                os.close(root)

    def test_cache_envelope_depth_failure_cleans_new_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = os.open(folder, os.O_RDONLY)
            try:
                data = ([{}], [], {})
                with patch.dict(limits.LIMITS, value_depth=3):
                    limits.validate_report(data)  # Report fits; envelope does not.
                    self.assert_resource('value_depth', lambda: store(root, 'a' * 64, {}, data))
                self.assertEqual(os.listdir(folder), [])
                with patch.dict(limits.LIMITS, value_depth=4):
                    token = store(root, 'a' * 64, {}, data)
                    self.assertEqual(load(root, token, 'a' * 64, {}), data)
                    self.assertEqual(release(root, token)['status'], 'released')
                self.assertEqual(os.listdir(folder), [])
            finally:
                os.close(root)

    def test_cache_envelope_node_boundary_and_neighbor_preservation(self):
        def nodes(value):
            if isinstance(value, dict):
                return 1 + sum(nodes(key) + nodes(item) for key, item in value.items())
            if isinstance(value, list):
                return 1 + sum(nodes(item) for item in value)
            return 1

        with tempfile.TemporaryDirectory() as folder:
            root = os.open(folder, os.O_RDONLY)
            try:
                data = ([], [], {})
                token = store(root, 'a' * 64, {}, data)
                cache_file = next(Path(folder).iterdir())
                envelope_nodes = nodes(json.loads(cache_file.read_bytes()))
                release(root, token)
                neighbor = Path(folder) / 'user-input.docx'
                neighbor.write_bytes(b'protected input')
                with patch.dict(limits.LIMITS, value_nodes=envelope_nodes - 1):
                    limits.validate_report(data)
                    self.assert_resource('value_nodes', lambda: store(root, 'a' * 64, {}, data))
                self.assertEqual(os.listdir(folder), ['user-input.docx'])
                self.assertEqual(neighbor.read_bytes(), b'protected input')
                with patch.dict(limits.LIMITS, value_nodes=envelope_nodes):
                    token = store(root, 'a' * 64, {}, data)
                    self.assertEqual(load(root, token, 'a' * 64, {}), data)
                    self.assertEqual(release(root, token)['status'], 'released')
                self.assertEqual(os.listdir(folder), ['user-input.docx'])
            finally:
                os.close(root)

    def test_reasonable_scale_10000_paragraphs(self):
        package = Package(sample(10_000))
        records, issues, coverage = report(package)
        self.assertEqual(sum(item['type'] == 'paragraph' for item in records), 10_000)
        self.assertTrue(coverage)
        self.assertIsInstance(issues, list)


if __name__ == '__main__':
    unittest.main()
