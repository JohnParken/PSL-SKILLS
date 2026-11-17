"""Native read and editable authoring roundtrip library regression tests."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches
from pptx_to_svg.converter import ConvertOptions, convert_pptx_to_svg
from authoring_roundtrip import materialize_flat_authoring_roundtrip


class ImportRoundtripTests(unittest.TestCase):
    """A generated fixture never touches user presentations or external paths."""

    def test_native_read_and_unchanged_materialization(self):
        """Retain text, notes and native restoration identities across import."""
        with tempfile.TemporaryDirectory(prefix='tl-pptx-import-') as temporary:
            root = Path(temporary)
            source = root / 'fixture.pptx'
            workspace = root / 'workspace'
            deck = Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            shape = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(3), Inches(2),
            )
            shape.text = 'Native roundtrip'
            slide.notes_slide.notes_text_frame.text = 'Speaker note retained'
            deck.save(source)
            plain = convert_pptx_to_svg(source)
            self.assertEqual(len(plain.slides), 1)
            result = convert_pptx_to_svg(source, workspace, ConvertOptions(roundtrip=True))
            self.assertFalse(result.diagnostics)
            self.assertEqual(len(result.speaker_notes), 1)
            self.assertIn('Native', result.flat_slides[0].svg)
            materialized = materialize_flat_authoring_roundtrip(
                workspace, workspace / 'authoring-svg-flat', workspace / 'materialized',
            )
            self.assertEqual(len(materialized.svg_files), 1)
            self.assertIn('native-restore-placeholder', materialized.svg_files[0].read_text())

    def test_roundtrip_requires_both_inheritance_views(self):
        """Reject incompatible mode before reading a source file."""
        with self.assertRaisesRegex(ValueError, 'both'):
            convert_pptx_to_svg(Path('not-opened.pptx'), options=ConvertOptions(roundtrip=True, inheritance_mode='flat'))


if __name__ == '__main__':
    unittest.main()
