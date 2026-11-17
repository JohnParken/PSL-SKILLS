"""Guard indexed XML locations against the uncached path contract."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_read
from test_tl_read import MemoryPackage, report_fixture
from test_tl_read_parts import fixture


def nested_fixture():
    """Include excluded branches and nested tables in more than one story."""
    paragraph = '<w:p><w:r><w:t>Visible</w:t></w:r></w:p>'
    cell = '<w:tc>' + paragraph + '</w:tc>'
    table = '<w:tbl><w:tr>' + cell + cell + '</w:tr></w:tbl>'
    body = (
        '<w:del><w:p><w:r><w:delText>Removed</w:delText></w:r></w:p></w:del>'
        '<w:del><w:p><w:r><w:delText>Also removed</w:delText></w:r></w:p></w:del>'
        '<w:ins>' + paragraph + '</w:ins>'
        '<w:ins>' + paragraph + '</w:ins>'
        '<w:tbl><w:tr><w:tc>' + table + paragraph + '</w:tc>' + cell + '</w:tr></w:tbl>'
        '<mc:AlternateContent><mc:Choice Requires="unknown">' + paragraph + '</mc:Choice>'
        '<mc:Choice Requires="wps">' + paragraph + '</mc:Choice>'
        '<mc:Fallback>' + paragraph + '</mc:Fallback></mc:AlternateContent>'
    )
    namespaces = f'xmlns:w="{tl_read.W_NS}" xmlns:mc="{tl_read.MC_NS}" xmlns:wps="{tl_read.WPS_NS}"'
    return MemoryPackage({
        'word/document.xml': f'<w:document {namespaces}><w:body>{body}</w:body></w:document>',
        'word/header1.xml': f'<w:hdr {namespaces}>{body}</w:hdr>',
    })


class CountingNode:
    """Minimal immutable tree with observable sibling iteration work."""

    def __init__(self, tag, parent=None):
        self.tag = tag
        self.parent = parent
        self.children = []
        self.visits = 0
        if parent is not None:
            parent.children.append(self)

    def getparent(self):
        """Return the fixed parent."""
        return self.parent

    def __iter__(self):
        for child in self.children:
            self.visits += 1
            yield child


class PathIndexTests(unittest.TestCase):
    """Preserve full records and exact structural paths, not just visible text."""

    def test_namespaces_comments_and_query_order(self):
        """Count all original same-QName siblings, independent of prefix/order."""
        root = etree.fromstring(b'<w:root xmlns:w="urn:w" xmlns:a="urn:w" xmlns:x="urn:x">'
                                b'<w:p/><!--comment--><x:p/><a:p/><?pi test?><p/>'
                                b'<w:p><w:p/><x:p/><w:p/></w:p></w:root>')
        nodes = list(root.iter())
        indexes = {}
        for node in reversed(nodes):
            self.assertEqual(tl_read._path(node), tl_read._path(node, indexes))  # pylint: disable=protected-access
        self.assertEqual('/{urn:w}root[1]/{urn:w}p[3]/{urn:w}p[2]',
                         tl_read._path(root[-1][-1], indexes))  # pylint: disable=protected-access
        self.assertEqual('/', tl_read._path(root[1], indexes))  # pylint: disable=protected-access
        self.assertEqual('/', tl_read._path(root[4], indexes))  # pylint: disable=protected-access
        self.assertEqual('/{urn:w}root[1]/p[1]', tl_read._path(root[-2], indexes))  # pylint: disable=protected-access

    def test_one_scan_per_parent(self):
        """The number of sibling visits grows linearly without timing thresholds."""
        for count in (250, 2500, 10000):
            root = CountingNode('root')
            nodes = [CountingNode('p', root) for _ in range(count)]
            indexes = {}
            for index, node in reversed(list(enumerate(nodes, 1))):
                self.assertEqual(f'/root[1]/p[{index}]', tl_read._path(node, indexes))  # pylint: disable=protected-access
            for node in nodes:
                tl_read._path(node, indexes)  # pylint: disable=protected-access
            self.assertEqual(count, root.visits)
            self.assertEqual(count, len(indexes))

    def test_deep_path_is_iterative(self):
        """Path construction itself does not consume one Python frame per level."""
        root = CountingNode('root')
        leaf = root
        for _ in range(1500):
            leaf = CountingNode('p', leaf)
        path = tl_read._path(leaf, {})  # pylint: disable=protected-access
        self.assertEqual('/root[1]' + '/p[1]' * 1500, path)

    def test_distinct_trees_and_readers(self):
        """Identical trees do not share indexes or depend on recycled object ids."""
        first = etree.fromstring(b'<root><p/><p/></root>')
        second = etree.fromstring(b'<root><p/></root>')
        indexes = {}
        self.assertEqual('/root[1]/p[2]', tl_read._path(first[-1], indexes))  # pylint: disable=protected-access
        self.assertEqual('/root[1]/p[1]', tl_read._path(second[-1], indexes))  # pylint: disable=protected-access
        package = fixture()
        reader_a = tl_read.ReportReader(package)
        reader_b = tl_read.ReportReader(package)
        self.assertIsNot(reader_a._path_indexes, reader_b._path_indexes)  # pylint: disable=protected-access

    def test_complete_report_equivalence(self):
        """Tables, fields, drawings, choices, revisions and stories retain paths."""
        for package in (report_fixture(), fixture(rename=True), nested_fixture()):
            for revisions in (None, 'accepted', 'original', 'all'):
                for related in (False, True):
                    with self.subTest(revisions=revisions, related=related):
                        with patch.object(tl_read.ReportReader, '_path', lambda self, node: tl_read._path(node)):
                            expected = tl_read.report(package, revisions=revisions, include_related=related)
                        actual = tl_read.report(package, revisions=revisions, include_related=related)
                        self.assertEqual(expected, actual)


if __name__ == '__main__':
    unittest.main()
