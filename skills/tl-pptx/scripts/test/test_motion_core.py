"""Synthetic motion regressions against pinned upstream 7e0df670.

Golden hashes were independently generated from its unmodified source for
all 203 animation presets and all 48 transitions, in registry order. No
PowerPoint process or user project is involved.
"""
from pathlib import Path
import hashlib
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pptx_animations as animations
import pptx_transitions as transitions


def slide(fragment):
    """Wrap a motion fragment with one named synthetic target shape."""
    return (f'<p:sld xmlns:p="{transitions.PML_NS}"><p:cSld><p:spTree>'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="item"/></p:nvSpPr>'
            '</p:sp></p:spTree></p:cSld>' + fragment + '</p:sld>')


def digest(rows):
    """Match the independently sampled upstream serialization digest."""
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


class MotionCoreTests(unittest.TestCase):
    """Retained registries must generate and read back native OOXML."""

    def test_all_203_animations_upstream_and_readback(self):
        """Every preset, including required concrete font, retains semantics."""
        rows = []
        self.assertEqual(len(animations.NATIVE_ANIMATION_KEYS), 203)
        for effect in animations.NATIVE_ANIMATION_KEYS:
            with self.subTest(effect=effect):
                options = {'font_name': 'Arial'} if effect == 'emphasis_change_font' else {}
                targets = [{'shape_id': 2, 'effect': effect, 'duration': 1.0,
                            'effect_options': options}]
                xml = animations.create_sequence_timing_xml(targets, duration=1.0)
                rows.append((effect, xml))
                summary = animations.validate_generated_animation_xml(slide(xml), targets, duration=1.0)
                self.assertEqual(len(summary.rows), 1)
        self.assertEqual(digest(rows), 'cc88bca0bf0bb6efc72e9aaec5db801ad23a19c345a8368140cee3f5c853fa47')

    def test_all_48_transitions_upstream_and_readback(self):
        """MCE choices/fallbacks and Morph remain native, not replaced by Fade."""
        rows = []
        self.assertEqual(len(transitions.NATIVE_TRANSITION_KEYS), 48)
        for effect in transitions.NATIVE_TRANSITION_KEYS:
            with self.subTest(effect=effect):
                xml = transitions.create_transition_xml(effect)
                rows.append((effect, xml))
                summary = transitions.validate_generated_transition_xml(
                    slide(xml), effect=effect, duration=0.5,
                    advance_on_click=None, advance_after=None)
                self.assertEqual(summary.canonical_effect, effect)
        self.assertEqual(digest(rows), '88a136bec0c9b9676d6864d6dafc4f1fcff9a6c2a5c12a922d916b7a8f3b1fdd')

    def test_mixed_beats_wait_for_effective_previous_duration(self):
        """Issue-331 upstream beat offsets survive the responsibility split."""
        targets = [
            {'shape_id': 2, 'effect': 'entrance_fade', 'duration': 0.75},
            {'shape_id': 2, 'effect': 'entrance_zoom', 'duration': 0.28,
             'effect_options': {'direction': 'out'}, 'accelerate': 1},
            {'shape_id': 2, 'effect': 'entrance_fade', 'duration': 0.12,
             'trigger': 'with-previous'},
            {'shape_id': 2, 'effect': 'emphasis_grow_shrink', 'duration': 0.09,
             'effect_options': {'size': 110}, 'auto_reverse': True},
        ]
        xml = animations.create_sequence_timing_xml(targets)
        summary = animations.validate_generated_animation_xml(slide(xml), targets)
        self.assertEqual([row.offset_ms for row in summary.rows], [0, 750, 750, 1030])
        self.assertEqual(max(row.offset_ms + row.playback_duration_ms for row in summary.rows), 1210)

    def test_transition_advancement_and_morph_options(self):
        """Native Morph words mode preserves independent automatic advancement."""
        options = {'morph_by': 'word'}
        xml = transitions.create_transition_xml('morph', duration=1.2,
                                                advance_after=3, advance_on_click=False,
                                                effect_options=options)
        transitions.validate_generated_transition_xml(
            slide(xml), effect='morph', duration=1.2, advance_after=3,
            advance_on_click=False, effect_options=options)

    def test_invalid_motion_is_rejected(self):
        """No silent fallback is introduced for unsupported effects or NaN."""
        for value in (float('nan'), float('inf'), -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    animations.create_timing_xml(duration=value)
                with self.assertRaises(ValueError):
                    transitions.create_transition_xml(duration=value)
        with self.assertRaises(ValueError):
            animations.create_timing_xml('not-a-preset')


if __name__ == '__main__':
    unittest.main()
