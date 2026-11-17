"""Allocation-budget regressions for the independent local-use expander."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from svg_to_pptx import use_expander as module


def document(instances=1):
    """Return a small reusable shape and a caller-selected instance count."""
    return ET.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg"><defs><g id="shape">'
        '<rect width="10" height="10"/><circle r="2"/></g></defs>'
        + '<use href="#shape"/>' * instances + '</svg>')


class UseBudgetTests(unittest.TestCase):
    """Budget rejection must happen before the expensive subtree clone."""

    def test_over_budget_rejected_before_deepcopy(self):
        """A referenced subtree cannot be copied even once past the cap."""
        root = document()
        size = sum(1 for _ in root.iter())
        with patch.object(module, '_MAX_LOCAL_USE_NODES', size + 3):
            with patch.object(module.copy, 'deepcopy') as clone:
                with self.assertRaisesRegex(module.UseExpansionError, 'node safety budget'):
                    module.expand_local_use_references(root)
                clone.assert_not_called()

    def test_cumulative_clones_are_charged(self):
        """Repeating a legal subtree still consumes one shared allocation budget."""
        root = document(3)
        size = sum(1 for _ in root.iter())
        original_clone = module.copy.deepcopy
        with patch.object(module, '_MAX_LOCAL_USE_NODES', size + 4):
            with patch.object(module.copy, 'deepcopy', wraps=original_clone) as clone:
                with self.assertRaises(module.UseExpansionError):
                    module.expand_local_use_references(root)
                self.assertEqual(clone.call_count, 1)

    def test_source_budget_precedes_cloning(self):
        """Even callers outside the CLI cannot expand an oversized source tree."""
        root = document()
        with patch.object(module, '_MAX_LOCAL_USE_NODES', 2):
            with patch.object(module.copy, 'deepcopy') as clone:
                with self.assertRaises(module.UseExpansionError):
                    module.expand_local_use_references(root)
                clone.assert_not_called()

    def test_large_attribute_repeat_is_rejected_before_second_clone(self):
        """Few nodes cannot amplify a large embedded payload beyond 32MiB."""
        root = document(3)
        target = root.find('.//{http://www.w3.org/2000/svg}rect')
        target.set('data-payload', 'x' * (12 * 1024 * 1024))
        original_clone = module.copy.deepcopy
        with patch.object(module.copy, 'deepcopy', wraps=original_clone) as clone:
            with self.assertRaisesRegex(module.UseExpansionError, 'byte safety budget'):
                module.expand_local_use_references(root)
            self.assertEqual(clone.call_count, 1)

    def test_utf8_text_tail_and_wrapper_are_charged(self):
        """Multibyte text/tail and use attributes count, not just target attrs."""
        root = document()
        root.text = '字' * 10
        root.tail = '尾' * 10
        use = root.find('{http://www.w3.org/2000/svg}use')
        use.set('data-extra', '字' * 100)
        source_bytes = sum(module._element_bytes(elem) for elem in root.iter())
        self.assertGreaterEqual(source_bytes, 360)
        with patch.object(module, '_MAX_LOCAL_USE_BYTES', source_bytes + 200):
            with patch.object(module.copy, 'deepcopy') as clone:
                with self.assertRaisesRegex(module.UseExpansionError, 'byte safety budget'):
                    module.expand_local_use_references(root)
                clone.assert_not_called()

    def test_source_byte_budget_precedes_cloning(self):
        """Large source text is rejected without any copy allocation."""
        root = document()
        root.text = '字' * 100
        with patch.object(module, '_MAX_LOCAL_USE_BYTES', 200):
            with patch.object(module.copy, 'deepcopy') as clone:
                with self.assertRaisesRegex(module.UseExpansionError, 'byte safety budget'):
                    module.expand_local_use_references(root)
                clone.assert_not_called()

    def test_small_reuse_keeps_conversion(self):
        """The added safety bound does not ban the ordinary reuse feature."""
        root = document(2)
        self.assertEqual(module.expand_local_use_references(root), 2)
        self.assertFalse(root.findall('.//{http://www.w3.org/2000/svg}use'))
        self.assertEqual(len(root.findall('.//{http://www.w3.org/2000/svg}rect')), 3)


if __name__ == '__main__':
    unittest.main()
