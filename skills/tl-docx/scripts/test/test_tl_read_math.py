"""Independent OMML semantics and default revision diagnostics."""
from io import BytesIO
from pathlib import Path
import sys
import unittest

from docx import Document
from docx.oxml import parse_xml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_read_math import MATH_NS, math_expression
from tl_package import Package
from tl_read import report
from tl_read_revisions import revision_issues


def xml(body):
    """Wrap an OMML expression with its actual namespace."""
    return parse_xml(f'<m:oMath xmlns:m="{MATH_NS}">{body}</m:oMath>'.encode())


def run(value):
    """Create independent literal OMML input."""
    return '<m:r><m:t>' + value + '</m:t></m:r>'


class MathTests(unittest.TestCase):
    """Do not flatten mathematical relationships to adjacent digits."""

    def test_common_expressions(self):
        """Fraction, scripts and roots retain grouping relationships."""
        examples = [
            ('<m:f><m:num>' + run('1') + '</m:num><m:den>' + run('2') + '</m:den></m:f>', '(1)/(2)'),
            ('<m:sSubSup><m:e>' + run('x') + '</m:e><m:sub>' + run('i') + '</m:sub><m:sup>' + run('2') + '</m:sup></m:sSubSup>', '(x)_(i)^(2)'),
            ('<m:rad><m:deg/>' + '<m:e>' + run('x') + '</m:e></m:rad>', 'sqrt(x)'),
            ('<m:rad><m:deg>' + run('3') + '</m:deg><m:e>' + run('x') + '</m:e></m:rad>', 'root(3, x)'),
            ('<m:func><m:fName>' + run('sin') + '</m:fName><m:e>' + run('x') + '</m:e></m:func>', 'sin(x)'),
        ]
        for source, expected in examples:
            with self.subTest(expected=expected):
                result = math_expression(xml(source))
                self.assertEqual(expected, result['text'])
                self.assertEqual([], result['issues'])

    def test_operators_delimiters_and_approximation(self):
        """Keep operators and separators rather than concatenating operands."""
        source = ('<m:nary><m:naryPr><m:chr m:val="sum"/></m:naryPr><m:sub>' + run('i=1') +
                  '</m:sub><m:sup>' + run('n') + '</m:sup><m:e>' + run('x') + '</m:e></m:nary>')
        self.assertEqual('sum_(i=1)^(n)(x)', math_expression(xml(source))['text'])
        source = '<m:d><m:e>' + run('a') + '</m:e><m:e>' + run('b') + '</m:e></m:d>'
        self.assertEqual('(a|b)', math_expression(xml(source))['text'])
        source = '<m:eqArr><m:e>' + run('a=1') + '</m:e><m:e>' + run('b=2') + '</m:e></m:eqArr>'
        self.assertEqual('equations[a=1; b=2]', math_expression(xml(source))['text'])
        result = math_expression(xml('<m:bar><m:e>' + run('x') + '</m:e></m:bar>'))
        self.assertEqual('bar(x)', result['text'])
        self.assertEqual('math_notation_approximate', result['issues'][0]['code'])

    def test_unknown_and_missing_parts(self):
        """Unknown or malformed expressions are explicitly partial."""
        for source in ('<m:m>' + run('1') + '</m:m>', '<m:f><m:num>' + run('1') + '</m:num></m:f>',
                       '<m:f><m:num/><m:den/><m:unknown/></m:f>'):
            self.assertTrue(math_expression(xml(source))['issues'])

    def test_formula_in_paragraph_text(self):
        """Paragraph text includes inline formulas rather than dropping them."""
        doc = Document()
        para = doc.add_paragraph('ratio=')
        para._p.append(xml('<m:f><m:num>' + run('1') + '</m:num><m:den>' + run('2') + '</m:den></m:f>'))
        stream = BytesIO()
        doc.save(stream)
        records, _, _ = report(Package(stream.getvalue()))
        paragraphs = [item for item in records if item.get('type') == 'paragraph']
        self.assertEqual('ratio=(1)/(2)', paragraphs[0]['text'])

    def test_unknown_formula_report_is_partial(self):
        """Report status cannot claim complete semantics for unknown formulas."""
        doc = Document()
        para = doc.add_paragraph('formula=')
        para._p.append(xml('<m:m>' + run('1') + '</m:m>'))
        stream = BytesIO()
        doc.save(stream)
        records, issues, coverage = report(Package(stream.getvalue()))
        self.assertEqual('partial', coverage['status'])
        self.assertIn('math_structure_unsupported', [issue['code'] for issue in issues])
        paragraphs = [item for item in records if item.get('type') == 'paragraph']
        self.assertEqual('formula=[m: 1]', paragraphs[0]['text'])

    def test_default_complex_revision_diagnostics(self):
        """Default legacy text does not imply complex revisions are resolved."""
        class Reader:
            revisions = None

            def __init__(self):
                self.issues = []

            def _path(self, node):
                return node.getroottree().getpath(node)

            def _issue(self, code, *args, **kwargs):
                self.issues.append(code)
        root = parse_xml(b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                         b'<w:pPrChange/><w:trPr><w:del/></w:trPr><w:ins><w:del/></w:ins></w:document>')
        reader = Reader()
        revision_issues(reader, root, 'word/document.xml')
        self.assertEqual({'revision_property_change_unresolved', 'revision_row_flag_unresolved',
                          'revision_nested_view_conflict'}, set(reader.issues))


if __name__ == '__main__':
    unittest.main()
