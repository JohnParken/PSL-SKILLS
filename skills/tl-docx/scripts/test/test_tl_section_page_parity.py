"""Compatibility regressions for DOCX-style nested section geometries."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_docx import create_verified_bytes

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def spec_with(section):
    return {
        'profile': 'official-basic',
        'page': {'margin_mm': 20},
        'blocks': [
            {'type': 'paragraph', 'text': '正文'},
            dict(section, type='section_break'),
            {'type': 'paragraph', 'text': '横向附件'},
            {'type': 'section_break', 'page': {'orientation': 'portrait'}},
            {'type': 'paragraph', 'text': '后续正文'},
        ],
    }


class SectionPageParityTests(unittest.TestCase):
    def test_mixed_orientations_and_inherited_margins_in_verified_package(self):
        spec = spec_with({
            'break_type': 'next_page', 'start_type': 'next_page',
            'margins_cm': {'left': 2.5, 'right': 2},
            'page': {'orientation': 'landscape', 'margin_left_mm': 25},
        })
        original = deepcopy(spec)
        content, receipt = create_verified_bytes(spec, lambda path: self.fail(path))
        self.assertEqual(spec, original)
        self.assertEqual(receipt['content_verification']['status'], 'verified')
        with ZipFile(BytesIO(content)) as archive:
            body = ET.fromstring(archive.read('word/document.xml'))
        sections = list(body.iter(W + 'sectPr'))
        self.assertEqual(len(sections), 3)
        sizes = [section.find(W + 'pgSz') for section in sections]
        self.assertEqual([(size.get(W + 'w'), size.get(W + 'h')) for size in sizes],
                         [('11906', '16838'), ('16838', '11906'), ('11906', '16838')])
        self.assertEqual(sizes[1].get(W + 'orient'), 'landscape')
        margins = [section.find(W + 'pgMar') for section in sections]
        self.assertEqual([margin.get(W + 'left') for margin in margins],
                         ['1134', '1417', '1417'])
        self.assertEqual([margin.get(W + 'right') for margin in margins],
                         ['1134', '1134', '1134'])
        self.assertEqual(len(list(body.iter(W + 'br'))), 0)

    def test_rejects_conflicts_unsupported_fields_and_invalid_values(self):
        for options, message in [
            ({'page': []}, 'section_break.page'),
            ({'page': {'page_start': 2}}, 'section_break.page'),
            ({'page': {'orientation': 'landscape'}, 'orientation': 'portrait'}, 'conflicts'),
            ({'page': {'margin_mm': 20}, 'margins_cm': {'left': 3}}, 'conflicts'),
            ({'break_type': 'continuous', 'start_type': 'next_page'}, 'conflict'),
            ({'break_type': 'invalid'}, 'start_type'),
            ({'page': {'margin_left_mm': 10}, 'margins_cm': {'left': True}}, 'margins_cm.left'),
            ({'page': {'width_mm': True}}, 'width_mm'),
            ({'page': {'width_mm': 1}}, 'positive usable page area'),
        ]:
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, message):
                create_verified_bytes(spec_with(options), lambda path: self.fail(path))


    def test_signature_uses_native_paragraph_properties_without_manual_page_break(self):
        spec = {'profile': 'official-basic', 'blocks': [
            {'type': 'paragraph', 'text': '正文'},
            {'type': 'official_signature', 'issuer': '某某机构', 'date': '2026年11月19日'},
        ]}
        content, receipt = create_verified_bytes(spec, lambda path: self.fail(path))
        self.assertEqual(receipt['blocks'], 3)
        with ZipFile(BytesIO(content)) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
        signature = root.find(W + 'body').findall(W + 'p')[-2:]
        self.assertEqual([''.join(t.text for t in p.iter(W + 't')) for p in signature],
                         ['某某机构', '2026年11月19日'])
        for p in signature:
            props = p.find(W + 'pPr')
            self.assertEqual(props.find(W + 'jc').get(W + 'val'), 'right')
            self.assertEqual(props.find(W + 'ind').get(W + 'firstLineChars'), '0')
            self.assertEqual(props.find(W + 'pageBreakBefore').get(W + 'val'), '0')
        self.assertIsNone(signature[0].find(W + 'pPr/' + W + 'keepNext').get(W + 'val'))
        self.assertEqual(signature[1].find(W + 'pPr/' + W + 'keepNext').get(W + 'val'), '0')

    def test_signature_rejects_missing_date_and_multiline_issuer(self):
        for entry in [
            {'type': 'official_signature', 'issuer': '某某机构'},
            {'type': 'official_signature', 'issuer': '某某\n机构', 'date': '2026年11月19日'},
        ]:
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, 'official_signature'):
                create_verified_bytes({'blocks': [entry]}, lambda path: self.fail(path))
