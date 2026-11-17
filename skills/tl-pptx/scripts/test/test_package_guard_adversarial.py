"""Encoding, archive-alias and cumulative-budget adversarial regressions."""
import base64
import hashlib
import io
from pathlib import Path
import stat
import struct
import sys
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pptx import Presentation
from tl_guard import ContractError
import tl_package_guard as guard
from svg_to_pptx.native_objects.source_package_guard import validate_source_chart_package

DTD = '<!DOCTYPE a [<!ENTITY x "forbidden">]><a>&x;</a>'


def zipped(items):
    """Create a local byte-only ZIP fixture."""
    stream = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as output:
        warnings.simplefilter('ignore', UserWarning)
        for name, content in items:
            output.writestr(name, content)
    return stream.getvalue()


def presentation_parts():
    """Read a genuine python-pptx package without extracting it."""
    stream = io.BytesIO()
    Presentation().save(stream)
    with zipfile.ZipFile(stream) as source:
        return {name: source.read(name) for name in source.namelist()}


def blob(payload):
    """Wrap a source chart part using upstream's integrity envelope."""
    return {'encoding': 'base64', 'payload': base64.b64encode(payload).decode('ascii'),
            'sha256': hashlib.sha256(payload).hexdigest()}


class PackageGuardAdversarialTests(unittest.TestCase):
    """Reject hostile representations while preserving ordinary native assets."""

    def test_safe_empty_directory_entries_are_supported(self):
        parts = presentation_parts()
        parts['ppt/'] = b''
        parts['ppt/slides/'] = b''
        result = guard.inspect_package(zipped(parts.items()))
        self.assertNotIn('ppt/', result['entries'])
        self.assertEqual(result['part_count'], len(parts) - 2)

    def test_directory_file_and_unicode_parent_collisions_rejected(self):
        for items in ([('folder', b'data'), ('folder/child.bin', b'data')],
                      [('folder/', b''), ('folder', b'data')],
                      [('caf\u00e9', b'data'), ('cafe\u0301/child.bin', b'data')]):
            with self.subTest(items=items), self.assertRaises(ContractError):
                guard.inspect_package(zipped(items), require_presentation=False)

    def test_special_files_and_nonempty_directory_rejected(self):
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK):
            info = zipfile.ZipInfo('special')
            info.create_system = 3
            info.external_attr = (mode | 0o600) << 16
            with self.subTest(mode=mode), self.assertRaises(ContractError):
                guard.inspect_package(zipped([(info, b'x')]), require_presentation=False)
        with self.assertRaises(ContractError):
            guard.inspect_package(zipped([('folder/', b'not empty')]), require_presentation=False)

    def test_encoded_traversal_and_canonical_duplicates_rejected(self):
        for name in ('ppt/charts/%2e%2e/evil.xml', 'ppt/%2E/evil.xml'):
            with self.subTest(name=name), self.assertRaises(ContractError):
                guard.inspect_package(zipped([(name, b'<data/>')]), require_presentation=False)
        for names in (('chart.xml', 'chart.%78ml'), ('chart.xml', 'CHART.XML')):
            with self.subTest(names=names), self.assertRaises(ContractError):
                guard.inspect_package(zipped([(name, b'<data/>') for name in names]), require_presentation=False)

    def test_xml_suffix_and_encoding_aliases_cannot_hide_entities(self):
        for name in ('item.XML', 'item.%78ml', 'opaque.bin'):
            for encoding in ('utf-8', 'utf-16', 'utf-32', 'utf-16-be', 'utf-32-be'):
                payload = (' ' * 1024 + DTD).encode(encoding)
                with self.subTest(name=name, encoding=encoding), self.assertRaises(ContractError):
                    guard.inspect_package(zipped([(name, payload)]), require_presentation=False)

    def test_content_type_alias_and_xml_mime_are_enforced(self):
        content_types = (f'<Types xmlns="{guard.CT_NS}"><Override PartName="/opaque.dat" '
                         'ContentType="application/custom+xml"/></Types>').encode()
        for content_name in ('[Content_Types].xml', '[CONTENT_TYPES].XML'):
            with self.subTest(content_name=content_name), self.assertRaises(ContractError):
                guard.inspect_package(zipped([(content_name, content_types), ('opaque.dat', b'not valid XML')]),
                                      require_presentation=False)

    def test_relationship_case_alias_still_checks_external_resources(self):
        relationship = (f'<Relationships xmlns="{guard.REL_NS}"><Relationship Id="rId1" '
                        'Type="image" Target="https://example.com/pixel.png" '
                        'TargetMode="External"/></Relationships>').encode()
        with self.assertRaises(ContractError):
            guard.inspect_package(zipped([('data.xml', b'<data/>'), ('_rels/data.xml.RELS', relationship)]),
                                  require_presentation=False)

    def test_prefixed_and_empty_nested_zip_are_not_skipped(self):
        nested = zipped([('huge.bin', b'0' * 8192)])
        with patch.object(guard, 'MAX_MEMBER_BYTES', 4096), self.assertRaisesRegex(ContractError, 'size budget'):
            guard.inspect_package(zipped([('attachment.bin', b'prefix' + nested)]), require_presentation=False)
        result = guard.inspect_package(zipped([('empty.zip', zipped([]))]), require_presentation=False)
        self.assertEqual(result['nested_archives'], 1)

    def test_cumulative_nested_budget_is_shared(self):
        nested = zipped([('data.bin', b'0' * 2048)])
        with patch.object(guard, 'MAX_EXPANDED_BYTES', 3000), self.assertRaisesRegex(ContractError, 'Cumulative'):
            guard.inspect_package(zipped([('first.zip', nested), ('second.zip', nested)]), require_presentation=False)

    def test_declared_expansion_is_checked_before_opening_member(self):
        payload = bytearray(zipped([('huge.bin', b'x')]))
        central = payload.index(b'PK\x01\x02')
        struct.pack_into('<I', payload, central + 24, guard.MAX_MEMBER_BYTES + 1)
        with self.assertRaisesRegex(ContractError, 'member size'):
            guard.inspect_package(bytes(payload), require_presentation=False)

    def test_central_metadata_budget_precedes_zipinfo_allocation(self):
        """Even a forged small EOCD count cannot hide a huge entry roster."""
        payload = bytearray(zipped([(f'item{number}.bin', b'x') for number in range(12)]))
        footer = payload.rfind(b'PK\x05\x06')
        struct.pack_into('<2H', payload, footer + 8, 1, 1)
        with patch.object(guard, 'MAX_MEMBERS', 10):
            with patch.object(guard.zipfile, 'ZipFile', side_effect=AssertionError('must not allocate entries')):
                with self.assertRaisesRegex(ContractError, 'before directory allocation'):
                    guard.inspect_package(bytes(payload), require_presentation=False)

    def test_small_zip64_and_prefixed_zip64_remain_supported(self):
        """ZIP64 records are not forbidden merely to simplify bomb protection."""
        with patch.object(zipfile, 'ZIP64_LIMIT', 1):
            payload = zipped([('data.bin', b'ordinary binary')])
        self.assertIn(b'PK\x06\x06', payload)
        for prefix in (b'', b'prefix'):
            with self.subTest(prefix=prefix):
                result = guard.inspect_package(prefix + payload, require_presentation=False)
                self.assertEqual(result['entries']['data.bin'], b'ordinary binary')

    def test_xml_node_budget_stops_during_tree_construction(self):
        """The counter must stop after the first over-budget start callback."""
        budget = guard.PackageBudget()
        payload = b'<root>' + b'<node/>' * 10000 + b'</root>'
        with patch.object(guard, 'MAX_XML_NODES', 10), self.assertRaises(ContractError):
            guard._safe_xml(payload, budget)
        self.assertEqual(budget.xml_nodes, 11)

    def test_xml_depth_and_comment_budget_enforced_during_parse(self):
        for payload in (b'<a>' * 20 + b'</a>' * 20,
                        b'<a>' + b'<!-- comment -->' * 20 + b'</a>'):
            budget = guard.PackageBudget()
            with patch.object(guard, 'MAX_XML_NODES', 10), patch.object(guard, 'MAX_XML_DEPTH', 5):
                with self.assertRaises(ContractError):
                    guard._safe_xml(payload, budget)
            self.assertLessEqual(budget.xml_nodes, 11)

    def test_literal_doctype_text_in_xml_comment_remains_valid(self):
        """Reject real DTD declarations, not harmless text by regex scanning."""
        payload = b'<root><!-- example: <!DOCTYPE example> -->text</root>'
        result = guard.inspect_package(zipped([('data.xml', payload)]), require_presentation=False)
        self.assertEqual(result['entries']['data.xml'], payload)

    def test_streaming_parser_preserves_default_and_prefixed_namespaces(self):
        """Normal namespace declarations survive the bounded parser target."""
        root = guard._safe_xml(
            b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:q="urn:extra"><q:child/></svg>',
            guard.PackageBudget(),
        )
        self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
        self.assertEqual(root[0].tag, '{urn:extra}child')
        self.assertEqual(root.nsmap[None], 'http://www.w3.org/2000/svg')

    def test_slide_count_uses_presentation_roster_not_orphan_part_names(self):
        """Report real deck cardinality without imposing a caller-specific cap."""
        stream = io.BytesIO()
        presentation = Presentation()
        for _ in range(2):
            presentation.slides.add_slide(presentation.slide_layouts[6])
        presentation.save(stream)
        with zipfile.ZipFile(stream) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries['ppt/slides/slide999.xml'] = entries['ppt/slides/slide1.xml']
        self.assertEqual(guard.inspect_package(zipped(entries.items()))['slide_count'], 2)
        self.assertEqual(guard.inspect_package(zipped(presentation_parts().items()))['slide_count'], 0)
        self.assertIsNone(guard.inspect_package(zipped([('plain.bin', b'bytes')]),
                                               require_presentation=False)['slide_count'])

    def test_source_chart_alias_and_opaque_xml_payloads_rejected(self):
        for name in ('ppt/charts/chart1.XML', 'ppt/charts/chart1.%78ml',
                     'ppt/charts/chart1.bin', 'ppt/charts/%2e%2e/evil.xml'):
            source = {'chart_part': name, 'frame': blob(b'<frame/>'),
                      'parts': [{'name': name, **blob(DTD.encode('utf-16'))}]}
            with self.subTest(name=name), self.assertRaises((RuntimeError, ValueError)):
                validate_source_chart_package(source)

    def test_source_chart_prefixed_workbook_zip_shares_budget(self):
        nested = b'prefix' + zipped([('huge.bin', b'0' * 8192)])
        source = {'chart_part': 'ppt/charts/chart1.xml', 'frame': blob(b'<frame/>'),
                  'parts': [{'name': 'ppt/charts/chart1.xml', **blob(b'<chart/>')},
                            {'name': 'ppt/embeddings/workbook.xlsx', **blob(nested)}]}
        with patch.object(guard, 'MAX_MEMBER_BYTES', 4096), self.assertRaises(ContractError):
            validate_source_chart_package(source)


if __name__ == '__main__':
    unittest.main()
