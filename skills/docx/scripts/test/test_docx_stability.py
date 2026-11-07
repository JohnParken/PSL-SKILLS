"""Stability regressions for generated OPC packages and targeted corruptions.

Only Python standard library is needed. LibreOffice integration is an
optional manual compatibility check documented separately.
"""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from fixture_delivery import create_fixture


import json
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import create_docx
from audit_package import audit_package

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'stable.docx'

    def create(self, spec=None):
        if spec is None:
            spec = {'blocks': [{'type': 'paragraph', 'text': '基础稳定性测试'}]}
        create_fixture(create_docx.create_document,spec, self.path, verify=True)
        return self.path

    def edit_part(self, name, editor):
        with zipfile.ZipFile(self.path) as archive:
            files = {n: archive.read(n) for n in archive.namelist()}
        files[name] = editor(files[name])
        with zipfile.ZipFile(self.path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for n, content in files.items():
                archive.writestr(n, content)

    def test_generator_uses_default_opc_relationship_namespace(self):
        self.create({'header': '页眉', 'page_numbers': True,
                     'blocks': [{'type': 'paragraph', 'text': '正文'}]})
        with zipfile.ZipFile(self.path) as archive:
            for name in ('_rels/.rels', 'word/_rels/document.xml.rels'):
                data = archive.read(name)
                self.assertIn(b'<Relationships xmlns="' + REL[1:REL.index('}')].encode() + b'">', data)
                self.assertNotIn(b'ns0:Relationships', data)
                self.assertEqual(ET.fromstring(data).tag, REL+'Relationships')
        self.assertEqual(audit_package(self.path)['warnings'], [])

    def test_escaping_in_relationship_writer(self):
        xml = create_docx._relationship_xml([('r"&', R+'/hyperlink', 'https://example.org/?a=1&b="<x>')])
        relationship = ET.fromstring(xml).find(REL+'Relationship')
        self.assertEqual(relationship.get('Id'), 'r"&')
        self.assertEqual(relationship.get('Target'), 'https://example.org/?a=1&b="<x>')

    def test_old_prefixed_opc_relation_is_warned_not_rejected(self):
        self.create()
        def to_prefixed(data):
            return (data.replace(b'<Relationships xmlns=', b'<ns0:Relationships xmlns:ns0=')
                    .replace(b'<Relationship ', b'<ns0:Relationship ')
                    .replace(b'</Relationships>', b'</ns0:Relationships>'))
        self.edit_part('_rels/.rels', to_prefixed)
        report = audit_package(self.path)
        self.assertTrue(report['structure_valid'], report['errors'])
        self.assertTrue(any('prefixed Relationships root' in x for x in report['warnings']))

    def test_invalid_part_content_type_is_rejected(self):
        self.create()
        def corrupt(data):
            root = ET.fromstring(data)
            root[:] = [node for node in root if node.get('PartName') != '/word/styles.xml']
            return ET.tostring(root, encoding='utf-8', xml_declaration=True)
        self.edit_part('[Content_Types].xml', corrupt)
        report = audit_package(self.path)
        self.assertFalse(report['structure_valid'])
        self.assertTrue(any('Incorrect content type for word/styles.xml' in x for x in report['errors']))

    def test_broken_package_relationship_is_rejected(self):
        self.create()
        self.edit_part('_rels/.rels', lambda data: data.replace(
            b'Target="word/document.xml"', b'Target="word/missing.xml"'))
        report = audit_package(self.path)
        self.assertFalse(report['structure_valid'])
        self.assertTrue(any('Missing relationship target' in x for x in report['errors']))

    def test_cyclic_style_inheritance_is_rejected(self):
        self.create()
        def corrupt(data):
            styles = ET.fromstring(data)
            normal = next(style for style in styles.findall(W+'style')
                          if style.get(W+'styleId') == 'Normal')
            ET.SubElement(normal, W+'basedOn', {W+'val': 'Heading1'})
            return ET.tostring(styles, encoding='utf-8', xml_declaration=True)
        self.edit_part('word/styles.xml', corrupt)
        report = audit_package(self.path)
        self.assertFalse(report['structure_valid'])
        self.assertTrue(any('Cyclic style inheritance' in x for x in report['errors']))

    def test_section_header_reference_wrong_type_is_rejected(self):
        self.create({'header': '页眉', 'blocks': [{'type': 'paragraph', 'text': '正文'}]})
        def corrupt(data):
            root = ET.fromstring(data)
            for rel in root.findall(REL+'Relationship'):
                if rel.get('Type') == R+'/header':
                    rel.set('Type', R+'/image')
            return ET.tostring(root, encoding='utf-8', xml_declaration=True)
        self.edit_part('word/_rels/document.xml.rels', corrupt)
        report = audit_package(self.path)
        self.assertFalse(report['structure_valid'])
        self.assertTrue(any('wrong relationship type' in x for x in report['errors']))

    def test_section_properties_must_be_last(self):
        self.create({'blocks': [{'type': 'paragraph', 'text': '甲'}, {'type': 'section_break'},
                                {'type': 'paragraph', 'text': '乙'}]})
        def corrupt(data):
            doc = ET.fromstring(data)
            section_props = next(p for p in doc.iter(W+'pPr') if p.find(W+'sectPr') is not None)
            self.assertIsNotNone(section_props.find(W+'sectPr'))
            ET.SubElement(section_props, W+'pStyle', {W+'val': 'Normal'})
            return ET.tostring(doc, encoding='utf-8', xml_declaration=True)
        self.edit_part('word/document.xml', corrupt)
        report = audit_package(self.path)
        self.assertFalse(report['structure_valid'])
        self.assertTrue(any('sectPr must be last' in x for x in report['errors']))

    def test_machine_report_does_not_modify_input(self):
        self.create()
        before = self.path.read_bytes()
        result = subprocess.run([sys.executable, '-I', '-S', '-B',
                                 str((Path(__file__).parents[1] / 'audit_package.py')),
                                 str(self.path), '--json'], text=True,
                                encoding='utf8', capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['structure_valid'], True)
        self.assertFalse(payload['xsd_validated'])
        self.assertFalse(payload['visual_validated'])
        self.assertEqual(before, self.path.read_bytes())

    def test_cli_fails_and_reports_bad_input(self):
        self.path.write_bytes(b'not a zip')
        result = subprocess.run([sys.executable, '-I', '-S', '-B',
                                 str((Path(__file__).parents[1] / 'audit_package.py')),
                                 str(self.path), '--json'], text=True,
                                encoding='utf8', capture_output=True)
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload['structure_valid'])
        self.assertTrue(payload['errors'])

    def test_independent_libreoffice_control_file_passes_audit(self):
        # Fixture was created separately by LibreOffice from plain text;
        # the automated test itself invokes no office software.
        path = Path(__file__).resolve().parents[2] / 'assets' / 'compatibility' / 'libreoffice-control.docx'
        report = audit_package(path)
        self.assertTrue(report['structure_valid'], report)
        self.assertFalse(report['warnings'], report)

    def test_complex_sections_fields_numbering_still_validate(self):
        self.create({
            'heading_numbering': 'chinese', 'odd_even_headers': True,
            'header': '正文', 'headers': {'even': '偶数页'},
            'page_numbers': True, 'blocks': [
                {'type': 'toc', 'depth': 2},
                {'type': 'heading', 'level': 1, 'text': '报告', 'bookmark': 'report'},
                {'type': 'reference', 'kind': 'page', 'bookmark': 'report'},
                {'type': 'section_break', 'page': {'orientation': 'landscape'},
                 'page_number_start': 1},
                {'type': 'table', 'header_rows': 2, 'caption': '附录表格', 'rows': [
                    [{'text': '地区', 'row_span': 2}, {'text': '存款', 'col_span': 2}, None],
                    [None, '期初', '期末'], ['地区甲', '100', '120']]}]})
        report = audit_package(self.path)
        self.assertTrue(report['structure_valid'], report)
        self.assertFalse(report['warnings'], report)


if __name__ == '__main__':
    unittest.main()
