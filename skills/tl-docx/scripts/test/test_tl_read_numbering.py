"""Independent expectations for native Word numbering labels and limitations."""
from pathlib import Path
import sys
import unittest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_create import build_document
from tl_create_numbering import create_heading_numbering, create_multilevel_list_numbering, set_paragraph_numbering
from tl_read_numbering import NumberingReader, formatted

NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


class MemoryPackage:
    """Minimal package containing independent synthetic OOXML fixtures."""
    def __init__(self, numbering, styles=''):
        self.parts = {'word/numbering.xml': f'<w:numbering xmlns:w="{NS}">{numbering}</w:numbering>',
                      'word/styles.xml': f'<w:styles xmlns:w="{NS}">{styles}</w:styles>'}
        self.names = set(self.parts)

    def xml(self, part):
        """Parse test data with the same XML element API as production."""
        return parse_xml(self.parts[part])


def level(index, fmt='decimal', pattern=None, extra=''):
    """Build a small explicit numbering level."""
    pattern = pattern or '%' + str(index + 1) + '.'
    return (f'<w:lvl w:ilvl="{index}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
            f'<w:lvlText w:val="{pattern}"/>{extra}</w:lvl>')


def reader(levels=None, override='', styles='', abstract_extra=''):
    """Create one list instance without relying on generation implementation."""
    return NumberingReader(MemoryPackage('<w:abstractNum w:abstractNumId="0">' +
                                        (levels or level(0)) + abstract_extra + '</w:abstractNum>' +
                                        '<w:num w:numId="1"><w:abstractNumId w:val="0"/>' + override + '</w:num>', styles))


def paragraph(index=0, num='1', style=None, direct=True):
    """Create a paragraph reference independent of the creator skill."""
    props = f'<w:pStyle w:val="{style}"/>' if style else ''
    if direct:
        props += f'<w:numPr><w:ilvl w:val="{index}"/><w:numId w:val="{num}"/></w:numPr>'
    return parse_xml(f'<w:p xmlns:w="{NS}"><w:pPr>{props}</w:pPr><w:r><w:t>Text</w:t></w:r></w:p>')


class NumberingTests(unittest.TestCase):
    """Read supported formats and explicitly report unsupported definitions."""
    def test_numbering_is_inserted_in_schema_order_on_formatted_paragraph(self):
        from docx.shared import Pt
        p = Document().add_paragraph('办理事项')
        p.paragraph_format.keep_together = True
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.left_indent = Pt(16)
        set_paragraph_numbering(p, 10)
        set_paragraph_numbering(p, 11, 1)
        self.assertEqual([child.tag for child in p._p.pPr],
                         [qn('w:keepLines'), qn('w:numPr'), qn('w:spacing'), qn('w:ind')])
        self.assertEqual(p._p.pPr.numPr.numId.val, 11)
        self.assertEqual(p._p.pPr.numPr.ilvl.val, 1)

    def test_multilevel_default_restart(self):
        resolver = reader(level(0) + level(1, pattern='%1.%2'))
        actual = [resolver.paragraph_info(paragraph(index), 'document')[0]['label'] for index in (0, 1, 1, 0, 1)]
        self.assertEqual(actual, ['1.', '1.1', '1.2', '2.', '2.1'])

    def test_never_restart(self):
        resolver = reader(level(0) + level(1, extra='<w:lvlRestart w:val="0"/>'))
        actual = [resolver.paragraph_info(paragraph(index), 'document')[0]['label'] for index in (0, 1, 0, 1)]
        self.assertEqual(actual, ['1.', '1.', '2.', '2.'])

    def test_start_and_level_override(self):
        override = '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="4"/>' + level(0, 'upperRoman') + '</w:lvlOverride>'
        resolver = reader(override=override)
        self.assertEqual(resolver.paragraph_info(paragraph(), 'document')[0]['label'], 'IV.')
        self.assertEqual(resolver.paragraph_info(paragraph(), 'document')[0]['label'], 'V.')
        self.assertEqual(resolver.paragraph_info(paragraph(), 'header')[0]['label'], 'IV.')

    def test_style_chain_and_suppression(self):
        styles = ('<w:style w:styleId="Base"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
                  '<w:style w:styleId="Child"><w:basedOn w:val="Base"/></w:style>')
        resolver = reader(styles=styles)
        result, issues = resolver.paragraph_info(paragraph(style='Child', direct=False), 'document')
        self.assertEqual(result['label'], '1.')
        self.assertFalse(issues)
        result, issues = resolver.paragraph_info(paragraph(style='Child', num='0'), 'document')
        self.assertTrue(result['suppressed'])
        self.assertNotIn('label', result)

    def test_format_subset(self):
        for value, fmt, expected in ((27, 'upperLetter', 'AA'), (28, 'lowerLetter', 'bb'),
                                     (9, 'lowerRoman', 'ix'), (4, 'decimalZero', '04'),
                                     (1, 'chineseCounting', '一'), (10, 'chineseCounting', '十'),
                                     (11, 'chineseCounting', '十一'), (20, 'chineseCounting', '二十'),
                                     (99, 'chineseCounting', '九十九')):
            self.assertEqual(formatted(value, fmt), expected)
        resolver = reader(level(0, 'bullet', '•'))
        self.assertEqual(resolver.paragraph_info(paragraph(), 'document')[0]['label'], '•')

    def test_unsupported_formats_and_links_are_partial(self):
        for resolver in (reader(level(0, 'chineseLegalSimplified')), reader(abstract_extra='<w:numStyleLink w:val="Other"/>')):
            result, issues = resolver.paragraph_info(paragraph(), 'document')
            self.assertNotIn('label', result)
            self.assertTrue(issues)

    def test_chinese_override_outside_supported_range_is_partial(self):
        override = '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="100"/></w:lvlOverride>'
        result, issues = reader(level(0, 'chineseCounting'), override=override).paragraph_info(paragraph(), 'document')
        self.assertNotIn('label', result)
        self.assertEqual(issues, ['numbering_counter_range_unsupported'])

    def test_generated_chinese_headings_and_independent_list_start(self):
        document = Document()
        heading_id = create_heading_numbering(document, 'chinese')
        custom_id = create_multilevel_list_numbering(document, True, start=7)
        normal_id = create_multilevel_list_numbering(document, True)
        numbering = document.part.numbering_part.element
        override = numbering.find(f"{{{NS}}}num[@{{{NS}}}numId='{custom_id}']/{{{NS}}}lvlOverride")
        self.assertEqual(override.get(qn('w:ilvl')), '0')
        self.assertEqual(override.find(qn('w:startOverride')).get(qn('w:val')), '7')


        def abstract_levels(num_id):
            instance = numbering.find(f"{{{NS}}}num[@{{{NS}}}numId='{num_id}']")
            abstract_id = instance.find(qn('w:abstractNumId')).get(qn('w:val'))
            abstract = numbering.find(f"{{{NS}}}abstractNum[@{{{NS}}}abstractNumId='{abstract_id}']")
            return abstract.findall(qn('w:lvl'))

        self.assertTrue(all(entry.find(qn('w:pPr')) is None for entry in abstract_levels(heading_id)))
        self.assertTrue(all(entry.find(f"{{{NS}}}pPr/{{{NS}}}ind") is not None for entry in abstract_levels(normal_id)))
        self.assertTrue(all(entry.find(f"{{{NS}}}pPr/{{{NS}}}tabs") is not None for entry in abstract_levels(normal_id)))
        self.assertEqual(abstract_levels(custom_id)[1].find(qn('w:lvlRestart')).get(qn('w:val')), '1')

        package = MemoryPackage('')
        package.parts['word/numbering.xml'] = numbering.xml
        resolver = NumberingReader(package)
        actual = []
        for num_id, index in ((heading_id, 0), (heading_id, 1), (heading_id, 2), (heading_id, 3),
                              (custom_id, 0), (custom_id, 1), (normal_id, 0), (custom_id, 0),
                              (custom_id, 1), (heading_id, 0), (heading_id, 1)):
            item = document.add_paragraph('不包含手工编号')
            set_paragraph_numbering(item, num_id, index)
            info, issues = resolver.paragraph_info(item._p, 'document')
            self.assertFalse(issues)
            self.assertEqual(item.text, '不包含手工编号')
            actual.append(info['label'])
        self.assertEqual(actual, ['一、', '（一）', '1.', '（1）', '7.', '7.1.', '1.', '8.', '8.1.', '二、', '（一）'])

    def test_build_document_custom_start_continuation_and_validation(self):
        root = {'type': 'list', 'ordered': True, 'list_id': 'work', 'items': ['条目']}
        blocks = [dict(root, start=2147483647), {'type': 'paragraph', 'text': '说明'},
                  dict(root, items=['续号']), dict(root, list_id='new')]
        document = build_document({'blocks': blocks}, lambda _path: b'')
        package = MemoryPackage('')
        package.parts['word/numbering.xml'] = document.part.numbering_part.element.xml
        resolver = NumberingReader(package)
        labels = [info['label'] if info else None for info, issues in
                  (resolver.paragraph_info(item._p, 'document') for item in document.paragraphs)
                  if not issues]
        self.assertEqual(labels, ['2147483647.', None, '2147483648.', '1.'])
        invalid = [
            [dict(root, start=True)], [dict(root, start=2147483648)],
            [{'type': 'list', 'ordered': True, 'items': ['条目'], 'start': 3}],
            [dict(root, ordered=False, start=3)], [root, dict(root, level=1, start=3)],
            [root, dict(root, start=3)],
        ]
        for items in invalid:
            with self.subTest(blocks=items), self.assertRaisesRegex(ValueError, r'\.start'):
                build_document({'blocks': items}, lambda _path: b'')

    def test_invalid_generated_list_starts_do_not_modify_numbering(self):
        document = Document()
        before = document.part.numbering_part.element.xml
        for ordered, start in ((True, False), (True, 0), (True, 2147483648), (False, 2)):
            with self.assertRaises(ValueError):
                create_multilevel_list_numbering(document, ordered, start=start)
        self.assertEqual(document.part.numbering_part.element.xml, before)

    def test_style_cycles_are_partial(self):
        resolver = reader(styles='<w:style w:styleId="A"><w:basedOn w:val="A"/></w:style>')
        result, issues = resolver.paragraph_info(paragraph(style='A', direct=False), 'document')
        self.assertIsNone(result)
        self.assertEqual(issues, ['numbering_style_inheritance_cycle_or_limit'])

    def test_missing_and_duplicate_levels(self):
        for resolver, node in ((reader(), paragraph(2)), (reader(level(0) + level(0)), paragraph())):
            result, issues = resolver.paragraph_info(node, 'document')
            self.assertTrue(issues)
            self.assertNotIn('label', result)

    def test_default_style_and_level_style_binding(self):
        """Resolve omitted pStyle via default and level association via pStyle."""
        styles = ('<w:style w:styleId="Default" w:type="paragraph" w:default="1"><w:pPr>'
                  '<w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>')
        resolver = reader(level(0) + level(1, pattern='%1.%2', extra='<w:pStyle w:val="Default"/>'), styles=styles)
        result, issues = resolver.paragraph_info(paragraph(direct=False), 'document')
        self.assertFalse(issues)
        self.assertEqual(result['level'], '1')
        self.assertEqual(result['label'], '1.1')

    def test_catalog_reads_nonstandard_paths(self):
        """An injected catalog locates parts; no conventional path is assumed."""
        package = MemoryPackage('<w:abstractNum w:abstractNumId="0">' + level(0) + '</w:abstractNum>'
                                '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>')
        class Catalog:
            """Fixture catalog representing relationship-based part location."""
            def locate(self, kind):
                """Return a nonconventional location."""
                return ['custom/' + kind + '.xml']
            def xml(self, part, kind):
                """Verify the catalog location was used before parsing."""
                self.assert_part(part, kind)
                return package.xml('word/' + kind + '.xml')
            @staticmethod
            def assert_part(part, kind):
                """Reject any accidental fixed-path lookup."""
                assert part == 'custom/' + kind + '.xml'
        resolver = NumberingReader(package, Catalog())
        self.assertEqual(resolver.paragraph_info(paragraph(), 'document')[0]['label'], '1.')

    def test_unsafe_xml_failure_not_swallowed(self):
        """Package safety exceptions remain blocking, not a partial label issue."""
        package = MemoryPackage('')
        def unsafe(_part):
            raise ValueError('DTD forbidden')
        package.xml = unsafe
        with self.assertRaisesRegex(ValueError, 'DTD forbidden'):
            NumberingReader(package)


    def test_legal_numbering_uses_decimal_parents(self):
        resolver = reader(level(0, 'upperRoman') + level(1, pattern='%1.%2', extra='<w:isLgl/>'))
        resolver.paragraph_info(paragraph(), 'document')
        self.assertEqual(resolver.paragraph_info(paragraph(1), 'document')[0]['label'], '1.1')


if __name__ == '__main__':
    unittest.main()
