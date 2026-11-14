"""Independent expectations for native Word numbering labels and limitations."""
from pathlib import Path
import sys
import unittest
from docx.oxml import parse_xml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
                                     (9, 'lowerRoman', 'ix'), (4, 'decimalZero', '04')):
            self.assertEqual(formatted(value, fmt), expected)
        resolver = reader(level(0, 'bullet', '•'))
        self.assertEqual(resolver.paragraph_info(paragraph(), 'document')[0]['label'], '•')

    def test_unsupported_formats_and_links_are_partial(self):
        for resolver in (reader(level(0, 'chineseCounting')), reader(abstract_extra='<w:numStyleLink w:val="Other"/>')):
            result, issues = resolver.paragraph_info(paragraph(), 'document')
            self.assertNotIn('label', result)
            self.assertTrue(issues)

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
