"""Normalized structural coverage and storage/occurrence separation, stdlib only."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import importlib.util
import tempfile
import unittest
from unittest import mock
import uuid
from xml.etree import ElementTree as ET

BASE_SPEC = importlib.util.spec_from_file_location('ofd_structure_helpers', Path(__file__).with_name('test_ofd.py'))
BASE = importlib.util.module_from_spec(BASE_SPEC)
BASE_SPEC.loader.exec_module(BASE)
READER = BASE.READER


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / ('ofd-structure-test-' + uuid.uuid4().hex)
        self.path = self.root / 'input.ofd'
        self.registered = {self.path}
        self.assertFalse(self.root.exists())
        self.root.mkdir()

    def tearDown(self):
        for path in self.root.iterdir():
            self.assertIn(path, self.registered)
            path.unlink()
        self.root.rmdir()

    def read(self, files=None):
        BASE.write_package(self.path, BASE.fixture() if files is None else files)
        return READER.read_ofd(self.path)

    def part(self, result, source):
        return next(part for part in result['structure']['xml_parts'] if part['source'] == source)

    def test_all_three_versions_same_structure_and_literal_contract(self):
        for version in ('1.0', '1.1', '1.2'):
            files = BASE.fixture()
            files['OFD.xml'] = files['OFD.xml'].replace(b'Version="1.0"', f'Version="{version}"'.encode())
            result = self.read(files)
            self.assertEqual(result['schema_version'], 'ofd-read-3')
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['structure']['status'], 'complete')
            self.assertEqual(result['text_assessment']['status'], 'complete')
            self.assertEqual(result['text_assessment']['stored_textcode_count'], 1)
            self.assertEqual(result['text_sources'][0]['occurrences'], [{'document': 0, 'page': 0, 'run': 0}])

    def test_unknown_elements_attributes_mixed_text_and_tail_are_preserved(self):
        files = BASE.fixture()
        files['Doc/Document.xml'] = files['Doc/Document.xml'].replace(
            b'</ofd:Document>', b'<ofd:Future Flag="raw">before<ofd:Inner/>after</ofd:Future></ofd:Document>')
        result = self.read(files)
        nodes = self.part(result, 'Doc/Document.xml')['nodes']
        future = next(node for node in nodes if node['tag'] == READER.q('Future'))
        inner = next(node for node in nodes if node['tag'] == READER.q('Inner'))
        self.assertEqual(future['attributes'], {'Flag': 'raw'})
        self.assertEqual(future['text'], 'before')
        self.assertEqual(inner['tail'], 'after')
        self.assertEqual(inner['parent'], future['index'])
        self.assertEqual(result['structure']['status'], 'complete')
        self.assertEqual(result['status'], 'partial')

    def test_declaration_comments_pi_and_namespace_events(self):
        files = BASE.fixture()
        files['Doc/Pages/P.xml'] = (
            b'<?xml version="1.0" encoding="UTF-8"?><?xml-stylesheet href="https://example.invalid/style"?>'
            + BASE.xml('Page', '<ofd:Content><ofd:Layer><ofd:TextObject><ofd:TextCode>'
                       'A<!--comment-->B<?display ignored?>C</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>'))
        result = self.read(files)
        events = self.part(result, 'Doc/Pages/P.xml')['events']
        self.assertIn('declaration', [event['kind'] for event in events])
        self.assertIn('namespace_start', [event['kind'] for event in events])
        self.assertIn('namespace_end', [event['kind'] for event in events])
        self.assertEqual(next(event['value'] for event in events if event['kind'] == 'comment'), 'comment')
        self.assertEqual(result['text_sources'][0]['text'], 'ABC')
        self.assertTrue(any(event.get('target') == 'xml-stylesheet' for event in events))

    def test_unreferenced_foreign_xml_is_catalogued_without_body_injection(self):
        files = BASE.fixture(extra={'Extra.xml': b'<x:Data xmlns:x="urn:custom" key="v">metadata<x:Child/>tail</x:Data>'})
        result = self.read(files)
        part = self.part(result, 'Extra.xml')
        self.assertEqual(part['nodes'][0]['tag'], '{urn:custom}Data')
        self.assertFalse(part['active_route'])
        self.assertEqual(len(result['documents'][0]['pages'][0]['text_runs']), 1)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['structure']['status'], 'complete')

    def test_annotation_and_unreferenced_storage_text_are_not_page_text(self):
        annotation = BASE.xml('PageAnnot', '<ofd:Annot><ofd:Appearance><ofd:TextObject ID="20">'
                              '<ofd:TextCode>注释字</ofd:TextCode></ofd:TextObject></ofd:Appearance></ofd:Annot>')
        files = BASE.fixture(extra={'Doc/Notes.xml': annotation})
        result = self.read(files)
        stored = next(record for record in result['text_sources'] if record['source'] == 'Doc/Notes.xml')
        self.assertEqual(stored['text'], '注释字')
        self.assertEqual(stored['occurrences'], [])
        self.assertEqual(result['text_assessment']['stored_textcode_count'], 2)
        self.assertEqual(result['text_assessment']['page_occurrence_count'], 1)

    def test_xml_attachment_never_opened_or_parsed(self):
        files = BASE.fixture(extra={
            'Doc/Attachments.xml': BASE.xml('Attachments', '<ofd:Attachment ID="5"><ofd:FileLoc>payload.xml</ofd:FileLoc></ofd:Attachment>'),
            'Doc/payload.xml': b'<!DOCTYPE x SYSTEM "https://example.invalid/evil"><x/>',
        })
        files['Doc/Document.xml'] = files['Doc/Document.xml'].replace(
            b'</ofd:Document>', b'<ofd:Attachments>Attachments.xml</ofd:Attachments></ofd:Document>')
        BASE.write_package(self.path, files)
        original = READER.zipfile.ZipFile.open
        opened = []

        def track(archive, name, *args, **kwargs):
            opened.append(name if isinstance(name, str) else name.filename)
            return original(archive, name, *args, **kwargs)

        with mock.patch.object(READER.zipfile.ZipFile, 'open', new=track):
            result = READER.read_ofd(self.path)
        self.assertNotIn('Doc/payload.xml', opened)
        self.assertNotIn('Doc/payload.xml', result['parsed_xml'])
        self.assertIn({'source': 'Doc/payload.xml', 'role': 'attachment', 'bytes': len(files['Doc/payload.xml'])},
                      result['structure']['opaque_payloads'])

    def test_xml_named_font_payload_is_opaque(self):
        files = BASE.fixture(common_extra='<ofd:PublicRes>Res.xml</ofd:PublicRes>', extra={
            'Doc/Res.xml': BASE.xml('Res', '<ofd:Fonts><ofd:Font ID="9"><ofd:FontFile>font.xml</ofd:FontFile></ofd:Font></ofd:Fonts>'),
            'Doc/font.xml': b'not an XML document; opaque font bytes',
        })
        result = self.read(files)
        self.assertNotIn('Doc/font.xml', result['parsed_xml'])
        self.assertEqual(result['structure']['status'], 'complete')

    def test_xml_header_in_unknown_extension_is_not_lost(self):
        files = BASE.fixture(extra={'Extra.data': b'<?xml version="1.0"?><data>value</data>'})
        result = self.read(files)
        self.assertEqual(self.part(result, 'Extra.data')['nodes'][0]['text'], 'value')

    def test_unknown_nonxml_payload_is_explicit_partial(self):
        result = self.read(BASE.fixture(extra={'unknown.data': b'<not XML at all'}))
        self.assertEqual(result['structure']['status'], 'partial')
        self.assertEqual(result['structure']['unclassified_members'], ['unknown.data'])
        self.assertIn('unclassified_payload', [diagnostic['code'] for diagnostic in result['diagnostics']])

    def test_unreferenced_xml_security_failure_is_not_partial_success(self):
        with self.assertRaisesRegex(READER.ReadError, 'DTD/entity'):
            self.read(BASE.fixture(extra={'unreferenced.xml': b'<!DOCTYPE x [<!ENTITY e "evil">]><x>&e;</x>'}))
        with self.assertRaisesRegex(READER.ReadError, 'invalid XML part: unreferenced.xml'):
            self.read(BASE.fixture(extra={'unreferenced.xml': b'<broken>'}))

    def test_event_limit_stops_instead_of_truncating_structure(self):
        original = READER.LIMITS['xml_events']
        READER.LIMITS['xml_events'] = 2
        try:
            with self.assertRaisesRegex(READER.ReadError, 'XML events'):
                self.read()
        finally:
            READER.LIMITS['xml_events'] = original

    def test_orphan_and_foreign_context_text_are_preserved_but_not_interpreted(self):
        files = BASE.fixture()
        files['Doc/Pages/P.xml'] = BASE.xml(
            'Page', '<ofd:TextCode>orphan</ofd:TextCode><foreign:TextObject xmlns:foreign="urn:x">'
            '<ofd:TextCode>foreign</ofd:TextCode></foreign:TextObject>')
        result = self.read(files)
        self.assertEqual([record['raw_text'] for record in result['text_sources']], ['orphan', 'foreign'])
        self.assertTrue(all(record['text'] is None for record in result['text_sources']))
        self.assertEqual(result['text_assessment']['status'], 'partial')
        self.assertEqual(result['documents'][0]['pages'][0]['text_runs'], [])

    def test_unreferenced_xml_cannot_declare_opaque_roles(self):
        files = BASE.fixture(extra={
            'Extra.xml': BASE.xml('Unknown', '<ofd:SignedValue>Hidden.xml</ofd:SignedValue>'),
            'Hidden.xml': BASE.xml('Page', '<ofd:Content><ofd:Layer><ofd:TextObject>'
                                   '<ofd:TextCode>HIDDEN</ofd:TextCode></ofd:TextObject></ofd:Layer></ofd:Content>'),
        })
        result = self.read(files)
        self.assertIn('Hidden.xml', result['parsed_xml'])
        self.assertTrue(any(record['text'] == 'HIDDEN' for record in result['text_sources']))
        self.assertEqual(result['structure']['opaque_payloads'], [])

    def test_stray_attachment_index_cannot_change_scan_order(self):
        extra_index = BASE.xml('Attachments', '<ofd:Attachment ID="7"><ofd:FileLoc>payload.xml</ofd:FileLoc></ofd:Attachment>')
        for order in (('Extra.xml', 'payload.xml'), ('payload.xml', 'Extra.xml')):
            values = {'Extra.xml': extra_index, 'payload.xml': b'<!DOCTYPE x [<!ENTITY e "data">]><x>&e;</x>'}
            files = BASE.fixture(extra={name: values[name] for name in order})
            with self.assertRaisesRegex(READER.ReadError, 'DTD/entity'):
                self.read(files)

    def test_active_xml_payload_role_conflict_fails_before_page_open(self):
        files = BASE.fixture(extra={
            'Doc/Attachments.xml': BASE.xml('Attachments', '<ofd:Attachment ID="8">'
                                           '<ofd:FileLoc>Pages/P.xml</ofd:FileLoc></ofd:Attachment>'),
        })
        files['Doc/Document.xml'] = files['Doc/Document.xml'].replace(
            b'</ofd:Document>', b'<ofd:Attachments>Attachments.xml</ofd:Attachments></ofd:Document>')
        BASE.write_package(self.path, files)
        original = READER.zipfile.ZipFile.open
        opened = []

        def track(archive, name, *args, **kwargs):
            opened.append(name if isinstance(name, str) else name.filename)
            return original(archive, name, *args, **kwargs)

        with mock.patch.object(READER.zipfile.ZipFile, 'open', new=track):
            with self.assertRaisesRegex(READER.ReadError, 'conflicting XML part'):
                READER.read_ofd(self.path)
        self.assertNotIn('Doc/Pages/P.xml', opened)

    def test_revision_only_storage_is_not_rejected_for_absent_active_docroot(self):
        files = BASE.fixture(extra={
            'Versions/V.xml': BASE.xml('DocVersion', '<ofd:FileList/>'
                                       '<ofd:DocRoot>/Doc/Document.xml</ofd:DocRoot>', 'ID="v1" Version="1.1"'),
        })
        files['OFD.xml'] = BASE.xml('OFD', '<ofd:DocBody><ofd:Versions>'
                                    '<ofd:Version ID="v1" Index="1" Current="true" BaseLoc="Versions/V.xml"/>'
                                    '</ofd:Versions></ofd:DocBody>', 'Version="1.1" DocType="OFD"')
        result = self.read(files)
        self.assertEqual(result['documents'][0]['pages'], [])
        self.assertEqual(result['documents'][0]['revision_entries'][0]['source'], 'Versions/V.xml')
        self.assertEqual(result['text_sources'][0]['text'], '甲乙')
        self.assertEqual(result['structure']['status'], 'complete')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('no_active_docroot', [diagnostic['code'] for diagnostic in result['diagnostics']])

    def test_duplicate_revision_identifiers_are_not_silently_merged(self):
        files = BASE.fixture(extra={'V.xml': BASE.xml('DocVersion', '<ofd:FileList/>', 'ID="v"')})
        files['OFD.xml'] = BASE.xml('OFD', '<ofd:DocBody><ofd:Versions>'
                                    '<ofd:Version ID="v" BaseLoc="V.xml"/><ofd:Version ID="v" BaseLoc="V.xml"/>'
                                    '</ofd:Versions></ofd:DocBody>', 'Version="1.2" DocType="OFD"')
        with self.assertRaisesRegex(READER.ReadError, 'duplicate/missing revision ID'):
            self.read(files)

    def test_all_character_fields_remain_unicode_and_keep_whitespace(self):
        files = BASE.fixture()
        files['Doc/Document.xml'] = files['Doc/Document.xml'].replace(
            b'</ofd:Document>', '<ofd:Extra> 文字 &amp; 数据 <ofd:Inner/> 尾部 </ofd:Extra></ofd:Document>'.encode())
        result = self.read(files)
        nodes = self.part(result, 'Doc/Document.xml')['nodes']
        extra = next(node for node in nodes if node['tag'] == READER.q('Extra'))
        inner = next(node for node in nodes if node['tag'] == READER.q('Inner'))
        self.assertEqual(extra['text'], ' 文字 & 数据 ')
        self.assertEqual(inner['tail'], ' 尾部 ')
        self.assertGreater(result['structure']['character_field_count'], 0)

    def test_untyped_namespace_irregularity_keeps_raw_names_and_text(self):
        extra = b'<?xml version="1.0"?><:Extra key="raw"><:Value>text</:Value></:Extra>'
        result = self.read(BASE.fixture(extra={'Extra.xml': extra}))
        part = self.part(result, 'Extra.xml')
        self.assertEqual(part['namespace_mode'], 'raw_names')
        self.assertEqual(part['nodes'][0]['tag'], ':Extra')
        self.assertEqual(part['nodes'][1]['text'], 'text')
        self.assertEqual(result['structure']['namespace_resolution'], 'partial')
        self.assertIn('xml_namespace_unresolved', [diagnostic['code'] for diagnostic in result['diagnostics']])

    def test_active_ofd_parts_never_use_raw_namespace_mode(self):
        files = BASE.fixture()
        files['Doc/Pages/P.xml'] = b'<:Page><:Content/></:Page>'
        with self.assertRaisesRegex(READER.ReadError, 'invalid XML part: Doc/Pages/P.xml'):
            self.read(files)

    def test_raw_name_pass_keeps_security_limits_and_counts_single_input(self):
        extra = b'<?xml version="1.0"?><root xmlns:p="urn:p"><p:Good/><:Raw/></root>'
        BASE.write_package(self.path, BASE.fixture(extra={'Extra.xml': extra}))
        package = READER.Package(self.path.read_bytes())
        root = package.xml('Extra.xml', require_ofd=False)
        self.assertEqual(package.nodes, len(list(root.iter())))
        self.assertEqual(package.xml_total, len(extra))
        self.assertEqual(package.event_count, len(package.xml_events['Extra.xml']))
        package.archive.close()
        with self.assertRaisesRegex(READER.ReadError, 'DTD/entity'):
            self.read(BASE.fixture(extra={'Extra.xml': b'<!DOCTYPE x [<!ENTITY e "data">]><:x>&e;</:x>'}))

    def test_declared_custom_xml_is_read_independently_of_suffix(self):
        files = BASE.fixture(extra={
            'Doc/Tags.xml': BASE.xml('CustomTags', '<ofd:CustomTag TypeID="t"><ofd:FileLoc>custom.data</ofd:FileLoc>'
                                     '<ofd:SchemaLoc>custom.schema</ofd:SchemaLoc></ofd:CustomTag>'),
            'Doc/custom.data': '<Business>业务内容</Business>'.encode(),
            'Doc/custom.schema': b'<schema/>',
        })
        files['Doc/Document.xml'] = files['Doc/Document.xml'].replace(
            b'</ofd:Document>', b'<ofd:CustomTags>Tags.xml</ofd:CustomTags></ofd:Document>')
        result = self.read(files)
        self.assertEqual(self.part(result, 'Doc/custom.data')['nodes'][0]['text'], '业务内容')
        self.assertIn('Doc/custom.schema', result['parsed_xml'])
        self.assertEqual(result['structure']['status'], 'complete')

    def test_external_custom_schema_is_data_and_not_a_file_access(self):
        files = BASE.fixture(extra={
            'Doc/Tags.xml': BASE.xml('CustomTags', '<ofd:CustomTag TypeID="t"><ofd:FileLoc>custom.xml</ofd:FileLoc>'
                                     '<ofd:SchemaLoc>https://example.invalid/schema.xsd</ofd:SchemaLoc></ofd:CustomTag>'),
            'Doc/custom.xml': b'<Business/>',
        })
        files['Doc/Document.xml'] = files['Doc/Document.xml'].replace(
            b'</ofd:Document>', b'<ofd:CustomTags>Tags.xml</ofd:CustomTags></ofd:Document>')
        result = self.read(files)
        self.assertIn('external_schema_not_followed', [diagnostic['code'] for diagnostic in result['diagnostics']])
        self.assertTrue(all(not part['source'].startswith('https:') for part in result['structure']['xml_parts']))

    def test_identifier_and_reference_fields_are_indexed_without_global_id_guessing(self):
        result = self.read()
        ident = next(item for item in result['structure']['identifiers'] if item['tag'] == READER.q('TextObject'))
        self.assertEqual(ident['value'], '3')
        reference = next(item for item in result['structure']['reference_fields']
                         if item['source'] == 'Doc/Document.xml' and item['field'] == 'BaseLoc')
        self.assertEqual(reference['matching_file_targets'], ['Doc/Pages/P.xml'])
        self.assertEqual(reference['interpretation'], 'matching_file_value_only')

    def test_namespace_uri_size_is_bounded_in_both_name_modes(self):
        uri = 'u' * (READER.LIMITS['xml_namespace_chars'] + 1)
        for tag in ('p:Data', ':Data'):
            extra = f'<?xml version="1.0"?><{tag} xmlns:p="{uri}"/>'.encode()
            with self.assertRaisesRegex(READER.ReadError, 'XML namespace declaration'):
                self.read(BASE.fixture(extra={'Extra.xml': extra}))

    def test_expanded_namespace_names_have_a_cumulative_budget(self):
        uri = 'u' * 2000
        extra = (f'<p:Data xmlns:p="{uri}">' + ''.join(f'<p:N{i}/>' for i in range(40)) + '</p:Data>').encode()
        original = READER.LIMITS['xml_name_chars']
        READER.LIMITS['xml_name_chars'] = 30000
        try:
            with self.assertRaisesRegex(READER.ReadError, 'expanded XML names'):
                self.read(BASE.fixture(extra={'Extra.xml': extra}))
        finally:
            READER.LIMITS['xml_name_chars'] = original

    def test_extreme_xml_names_are_not_retained_after_limit_failure(self):
        name = 'N' * (READER.LIMITS['xml_name_length'] + 1)
        with self.assertRaisesRegex(READER.ReadError, 'XML name length'):
            self.read(BASE.fixture(extra={'Extra.xml': f'<{name}/>'.encode()}))

    def test_namespace_identity_is_exact_not_a_uri_prefix(self):
        self.assertTrue(READER.is_ofd(READER.q('TextObject')))
        self.assertFalse(READER.is_ofd('{' + READER.NS + '}suffix}TextObject'))
        files = BASE.fixture()
        files['Doc/Pages/P.xml'] = BASE.xml('Page', '<ofd:Content><ofd:Layer>'
            '<other:TextObject xmlns:other="' + READER.NS + '}suffix">'
            '<ofd:TextCode>other namespace</ofd:TextCode></other:TextObject></ofd:Layer></ofd:Content>')
        try:
            result = self.read(files)
        except READER.ReadError as exc:
            self.assertIn('invalid XML part', str(exc))
        else:
            self.assertEqual(result['documents'][0]['pages'][0]['text_runs'], [])
            self.assertIsNone(result['text_sources'][0]['text'])
            self.assertIn('foreign_namespace', [diagnostic['code'] for diagnostic in result['diagnostics']])

    def test_logical_structure_matches_independent_standard_xml_read(self):
        files = BASE.fixture(extra={'Extra.xml': b'<data k="a&amp;b">leading<child mode="raw"/>trailing</data>'})
        result = self.read(files)
        for source, payload in files.items():
            expected_root = ET.fromstring(payload)
            expected_nodes = list(expected_root.iter())
            expected_parents = {child: parent for parent in expected_nodes for child in parent}
            expected_indexes = {node: index for index, node in enumerate(expected_nodes)}
            actual = self.part(result, source)['nodes']
            self.assertEqual(len(actual), len(expected_nodes))
            for record, expected in zip(actual, expected_nodes):
                parent = expected_parents.get(expected)
                self.assertEqual(record['parent'], expected_indexes[parent] if parent is not None else None)
                self.assertEqual(record['tag'], expected.tag)
                self.assertEqual(record['attributes'], expected.attrib)
                self.assertEqual(record['text'], expected.text or '')
                self.assertEqual(record['tail'], expected.tail or '')

    def test_blank_page_text_assessment_is_empty(self):
        files = BASE.fixture()
        files['Doc/Pages/P.xml'] = BASE.xml('Page')
        result = self.read(files)
        self.assertEqual(result['text_assessment']['status'], 'empty')
        self.assertEqual(result['structure']['status'], 'complete')
        self.assertEqual(result['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
