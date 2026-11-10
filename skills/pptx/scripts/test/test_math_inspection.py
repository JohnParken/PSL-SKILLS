"""Math flattening is observable without changing stored-text or structural results."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import test_workflow
from pptx_core.package import ConvertedSlide, build_presentation
from pptx_core.validation import inspect_presentation, validate_presentation
from test_package import SHAPE, read_parts, slide_xml
from test_validation import zipped
from test_v680_inspection import alternate, choice


MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
FRACTION = (
    f'<m:oMath xmlns:m="{MATH_NS}"><m:f><m:num><m:r><m:t>12</m:t></m:r></m:num>'
    '<m:den><m:r><m:t>15</m:t></m:r></m:den></m:f></m:oMath>'
)
SUPERSCRIPT = (
    f'<m:oMath xmlns:m="{MATH_NS}"><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e>'
    '<m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:oMath>'
)
SUBSCRIPT = SUPERSCRIPT.replace('sSup', 'sSub').replace('m:sup', 'm:sub')


def shape_with_paragraph(content: str) -> str:
    """Use literal OOXML fixtures, independent of the SVG formula converter."""
    return re.sub(r'<a:p>.*?</a:p>', '<a:p>' + content + '</a:p>', SHAPE)


def math_deck(count: int = 1) -> bytes:
    """Build a synthetic input solely in memory."""
    return build_presentation([ConvertedSlide(slide_xml(shape_with_paragraph(FRACTION)))] * count)


class MathInspectionTests(unittest.TestCase):
    """Compare known mathematical semantics with unchanged flattened output."""

    def test_fraction_super_and_subscript_report_loss(self):
        for expression, flattened in ((FRACTION, '1215'), (SUPERSCRIPT, 'x2'), (SUBSCRIPT, 'x2')):
            with self.subTest(flattened=flattened, expression=expression):
                payload = build_presentation([ConvertedSlide(slide_xml(shape_with_paragraph(expression)))])
                result = inspect_presentation(payload)
                slide = result['slides'][0]
                self.assertEqual(slide['text'], [flattened])
                self.assertEqual(slide['text_assessment'], {
                    'status': 'math_structure_not_preserved',
                    'scope': 'slide_text_and_notes_before_projection',
                    'math_paragraphs': {'text': 1, 'notes': 0},
                    'diagnostics': ['math_text_flattened'],
                })
                self.assertEqual(result['structural_validation'], 'passed')
                self.assertEqual(result['visual_validation'], 'not_run')
                self.assertNotIn('text_assessment', validate_presentation(payload))

    def test_plain_literal_formula_has_no_omml_claim(self):
        payload = build_presentation([ConvertedSlide(slide_xml(shape_with_paragraph('<a:r><a:t>x^2 = 12/15</a:t></a:r>')))])
        slide = inspect_presentation(payload)['slides'][0]
        self.assertEqual(slide['text'], ['x^2 = 12/15'])
        self.assertNotIn('text_assessment', slide)

    def test_count_is_per_paragraph_and_includes_empty_math(self):
        shape = shape_with_paragraph(FRACTION + SUPERSCRIPT)
        shape = shape.replace('</p:txBody>', f'<a:p><m:oMath xmlns:m="{MATH_NS}"/></a:p></p:txBody>')
        slide = inspect_presentation(build_presentation([ConvertedSlide(slide_xml(shape))]))['slides'][0]
        self.assertEqual(slide['text'], ['1215x2'])
        self.assertEqual(slide['text_assessment']['math_paragraphs'], {'text': 2, 'notes': 0})

    def test_unselected_mce_math_does_not_create_diagnostic(self):
        plain = shape_with_paragraph('<a:r><a:t>readable fallback</a:t></a:r>').replace('id="2"', 'id="3"')
        math_shape = shape_with_paragraph(FRACTION)
        for branches in (alternate(choice(plain), math_shape), alternate(choice(math_shape, 'urn:future'), plain)):
            payload = build_presentation([ConvertedSlide(slide_xml(branches))])
            slide = inspect_presentation(payload)['slides'][0]
            self.assertEqual(slide['text'], ['readable fallback'])
            self.assertNotIn('text_assessment', slide)
        selected = alternate(choice(math_shape), plain)
        slide = inspect_presentation(build_presentation([ConvertedSlide(slide_xml(selected))]))['slides'][0]
        self.assertEqual(slide['text_assessment']['math_paragraphs']['text'], 1)

    def test_notes_math_counts_exclude_footer_placeholders(self):
        payload = build_presentation([ConvertedSlide(slide_xml(), notes='speaker')])
        members = read_parts(payload)
        name = 'ppt/notesSlides/notesSlide1.xml'
        notes = members[name].decode('utf-8')
        notes, changed = re.subn(r'<a:r>.*?<a:t[^>]*>speaker</a:t></a:r>', FRACTION, notes)
        self.assertEqual(changed, 1)
        footer = shape_with_paragraph(SUPERSCRIPT).replace('id="2"', 'id="9"')
        footer = footer.replace('<p:nvPr/>', '<p:nvPr><p:ph type="ftr"/></p:nvPr>')
        members[name] = notes.replace('</p:spTree>', footer + '</p:spTree>').encode('utf-8')
        slide = inspect_presentation(zipped(members))['slides'][0]
        self.assertEqual(slide['notes'], ['1215'])
        self.assertEqual(slide['text_assessment']['math_paragraphs'], {'text': 0, 'notes': 1})


class MathInspectionCliTests(unittest.TestCase):
    """Exercise real stdout, status and budgets in isolated task directories."""
    root: Path
    setUp = test_workflow.WorkflowTests.setUp
    tearDown = test_workflow.WorkflowTests.tearDown
    invoke = test_workflow.WorkflowTests.invoke

    def test_summary_omit_and_paging_retain_math_diagnostics(self):
        (self.root / 'math.pptx').write_bytes(math_deck(3))
        process, full = self.invoke('inspect', 'math.pptx')
        self.assertEqual(process.returncode, 0)
        for options in (('--summary',), ('--notes', 'omit'), ('--slides', '2')):
            process, result = self.invoke('inspect', 'math.pptx', *options)
            self.assertEqual(process.returncode, 0)
            for slide in result['slides']:
                self.assertEqual(slide['text_assessment'], full['slides'][0]['text_assessment'])
        collected, cursor = [], None
        for _ in range(3):
            arguments = ['inspect', 'math.pptx', '--max-output-bytes', '1300']
            if cursor:
                arguments.extend(['--cursor', cursor])
            process, page = self.invoke(*arguments)
            self.assertEqual(process.returncode, 0, page)
            self.assertLessEqual(len(process.stdout.encode('utf-8')), 1300)
            collected.extend(page['slides'])
            cursor = page['pagination']['next_cursor']
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(collected, full['slides'])
        process, failure = self.invoke('inspect', 'math.pptx', '--max-output-bytes', '512')
        self.assertEqual(process.returncode, 2)
        self.assertEqual(failure['error']['code'], 'output_budget_too_small')

    def test_math_and_png_partial_are_independent(self):
        members = read_parts(math_deck())
        types = ET.fromstring(members['[Content_Types].xml'])
        ET.SubElement(types, '{http://schemas.openxmlformats.org/package/2006/content-types}Default',
                      {'Extension': 'png', 'ContentType': 'image/png'})
        members['[Content_Types].xml'] = ET.tostring(types)
        members['ppt/media/padded.png'] = test_workflow.image_png() + bytes(8)
        (self.root / 'partial.pptx').write_bytes(zipped(members))
        process, result = self.invoke('inspect', 'partial.pptx', '--summary', '--allow-png-zero-padding')
        self.assertEqual(process.returncode, 3)
        self.assertEqual(result['structural_validation'], 'partial')
        self.assertEqual(result['diagnostics'][0]['bytes'], 8)
        self.assertEqual(result['slides'][0]['text_assessment']['status'], 'math_structure_not_preserved')


if __name__ == '__main__':
    unittest.main()
