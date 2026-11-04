"""Expansion budgets reject large reference graphs before cloning occurs."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


SCRIPT_ROOT = Path(__file__).resolve().parents[2] / 'skills' / 'pptx' / 'scripts'
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(SCRIPT_ROOT / '_engine'))

from pptx_core.svg_limits import check_expansion_budget


SVG_NS = 'http://www.w3.org/2000/svg'
BYTE_BUDGET = 128 * 1024 * 1024


def svg(content: str) -> ET.Element:
    return ET.fromstring(f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">{content}</svg>')


class SvgExpansionBudgetTests(unittest.TestCase):
    def test_ordinary_input_is_unchanged(self) -> None:
        root = svg('<rect width="100" height="100"/><text x="10" y="20">中文 &amp; &lt;XML&gt;</text>')
        before = ET.tostring(root)
        nodes, byte_count = check_expansion_budget(root, max_bytes=BYTE_BUDGET)
        self.assertEqual(nodes, 3)
        self.assertGreaterEqual(byte_count, len(before))
        self.assertEqual(ET.tostring(root), before)

    def test_repeated_subtrees_are_counted_for_each_use(self) -> None:
        root = svg(
            '<defs><g id="glyph"><rect width="10" height="10"/><text>A</text></g></defs>'
            '<use href="#glyph"/><use href="#glyph"/><use href="#glyph"/>'
        )
        nodes, _ = check_expansion_budget(root, max_bytes=BYTE_BUDGET)
        self.assertEqual(nodes, 17)
        with self.assertRaisesRegex(ValueError, 'budget'):
            check_expansion_budget(root, max_nodes=16, max_bytes=BYTE_BUDGET)

    def test_large_referenced_text_hits_byte_limit_without_cloning(self) -> None:
        root = svg('<defs><text id="large">' + 'A' * 4096 + '</text></defs>' + '<use href="#large"/>' * 20)
        self.assertLess(len(ET.tostring(root)), 10000)
        with self.assertRaisesRegex(ValueError, 'byte budget'):
            check_expansion_budget(root, max_bytes=50000)

    def test_exponential_sharing_is_rejected_as_a_graph(self) -> None:
        definitions = ['<g id="s0"><rect width="1" height="1"/></g>']
        for index in range(1, 18):
            definitions.append(f'<g id="s{index}"><use href="#s{index - 1}"/><use href="#s{index - 1}"/></g>')
        root = svg('<defs>' + ''.join(definitions) + '</defs><use href="#s17"/>')
        self.assertLess(len(list(root.iter())), 100)
        with self.assertRaisesRegex(ValueError, 'budget'):
            check_expansion_budget(root, max_bytes=BYTE_BUDGET)

    def test_missing_duplicate_and_nonlocal_references_are_rejected(self) -> None:
        cases = (
            '<use href="#missing"/>',
            '<rect id="x"/><circle id="x"/><use href="#x"/>',
            '<use href="icons.svg#x"/>',
        )
        for content in cases:
            with self.subTest(content=content), self.assertRaises(ValueError):
                check_expansion_budget(svg(content), max_bytes=BYTE_BUDGET)

    def test_self_and_indirect_cycles_are_rejected(self) -> None:
        cases = (
            '<defs><use id="a" href="#a"/></defs>',
            '<defs><g id="a"><use href="#b"/></g><g id="b"><use href="#a"/></g></defs><use href="#a"/>',
            '<g id="a"><use href="#a"/></g>',
        )
        for content in cases:
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, 'cycle'):
                check_expansion_budget(svg(content), max_bytes=BYTE_BUDGET)

    def test_deep_source_tree_is_rejected(self) -> None:
        root = svg('')
        cursor = root
        for _ in range(128):
            cursor = ET.SubElement(cursor, f'{{{SVG_NS}}}g')
        with self.assertRaisesRegex(ValueError, 'depth'):
            check_expansion_budget(root, max_bytes=BYTE_BUDGET)

    def test_cached_reference_height_still_obeys_caller_depth_limit(self) -> None:
        definitions = ['<rect id="s0" width="1" height="1"/>']
        for index in range(1, 100):
            definitions.append(f'<use id="s{index}" href="#s{index - 1}"/>')
        content = '<defs>' + ''.join(definitions) + '</defs>'
        content += '<g>' * 40 + '<use href="#s99"/>' + '</g>' * 40
        with self.assertRaisesRegex(ValueError, 'depth'):
            check_expansion_budget(svg(content), max_bytes=BYTE_BUDGET)

    def test_estimate_exceeds_real_native_expansion(self) -> None:
        from svg_to_pptx.use_expander import expand_local_use_references

        root = svg(
            '<defs><g id="glyph"><rect id="box" width="10" height="10"/>'
            '<use href="#box"/></g></defs><use href="#glyph"/><use href="#glyph"/>'
        )
        nodes, byte_count = check_expansion_budget(root, max_bytes=BYTE_BUDGET)
        expanded = copy.deepcopy(root)
        expand_local_use_references(expanded)
        self.assertGreaterEqual(nodes, len(list(expanded.iter())))
        self.assertGreaterEqual(byte_count, len(ET.tostring(expanded)))


if __name__ == '__main__':
    unittest.main()
