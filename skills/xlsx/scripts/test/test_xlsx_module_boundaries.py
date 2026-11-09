"""Compatibility checks for formula wildcard matching helpers."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import formula_engine
import formula_wildcards


class WildcardModuleBoundaries(unittest.TestCase):
    def test_private_compatibility_exports(self):
        self.assertIs(formula_engine._wildcard_tokens, formula_wildcards._wildcard_tokens)
        self.assertIs(formula_engine._wildcard_matches, formula_wildcards._wildcard_matches)

    def test_escaped_and_unicode_matching(self):
        for value, pattern, expected in (
            ('A*B', 'a~*b', True), ('AbB', 'a~*b', False),
            ('AbB', 'a?b', True), ('Straße', 'STRASSE', True),
            ('abc', '*c', True), ('abc', '*d', False),
        ):
            with self.subTest(value=value, pattern=pattern):
                tokens = formula_engine._wildcard_tokens(pattern)
                self.assertEqual(formula_engine._wildcard_matches(value, tokens), expected)


if __name__ == '__main__':
    unittest.main()
