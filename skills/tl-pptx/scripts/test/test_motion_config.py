"""Synthetic tests for complete motion-sidecar and Morph configuration."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from svg_to_pptx import animation_config as config


def morph_config():
    """Return one deterministic object pair across consecutive slides."""
    return {'version': 1, 'slides': {'slide_02': {
        'transition': {'effect': 'morph'},
        'morph': {'from': 'slide_01', 'pairs': {
            'card': {'from': 'before', 'to': 'after'},
        }},
    }}}


class MotionConfigTests(unittest.TestCase):
    """Prior grouping helpers and full sidecar behavior remain available."""

    def test_explicit_morph_identity(self):
        """Morph retains the forced Selection Pane !! identity."""
        pairs = config.resolve_morph_pairs(['slide_01', 'slide_02'], morph_config())
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].shape_name, '!!card')
        self.assertEqual(pairs[0].source_group_id, 'before')
        self.assertEqual(pairs[0].destination_group_id, 'after')

    def test_morph_nonadjacent_slide_rejected(self):
        """Explicit pairs cannot silently span unrelated intervening slides."""
        with self.assertRaises(ValueError):
            config.resolve_morph_pairs(['slide_01', 'middle', 'slide_02'], morph_config())

    def test_morph_requires_object_transition(self):
        """Word Morph remains a transition, not an object-matching sidecar."""
        source = morph_config()
        source['slides']['slide_02']['transition']['effect_options'] = {'morph_by': 'word'}
        with self.assertRaisesRegex(ValueError, 'Morph by object'):
            config.resolve_morph_pairs(['slide_01', 'slide_02'], source)

    def test_group_scan_preserves_hidden_and_chrome_policy(self):
        """In-memory synthetic project exercises scanner and old helper APIs."""
        svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
               '<g id="background"><rect width="10" height="10"/></g>'
               '<g id="content"><text font-size="20">Visible</text></g>'
               '<g id="hidden" display="none"><rect width="10" height="10"/></g>'
               '<g><circle r="2"/></g></svg>')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'slide_01.svg'
            path.write_text(svg, encoding='utf-8')
            targets, anonymous = config.scan_svg_targets(path)
            all_targets, _ = config.scan_svg_targets(path, include_hidden=True)
        self.assertEqual([target.group_id for target in targets], ['background', 'content'])
        self.assertEqual([target.chrome for target in targets], [True, False])
        self.assertEqual(len(anonymous), 1)
        self.assertEqual(len(all_targets), 3)
        self.assertTrue(config.is_chrome_id('footer'))
        self.assertEqual(config.usable_animation_group_id('content'), 'content')
        self.assertIsNone(config.usable_animation_group_id('   '))

    def test_sidecar_rejects_invalid_effect_and_unknown_fields(self):
        """Strict validation is retained rather than silently ignoring intent."""
        source = {'defaults': {'animation': {'effect': 'not-a-preset'}}}
        self.assertTrue(config.validate_animation_config_errors(source))
        self.assertTrue(config.validate_animation_config_errors({'unknown': True}))
        self.assertEqual(config.validate_animation_config_errors({}), [])


if __name__ == '__main__':
    unittest.main()
