"""Explicit dependency-boundary behavior, not a claim that unavailable features work."""
import builtins
import importlib.metadata
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_capabilities import capability_status, DECLARED
from svg_to_pptx.shape_boolean import _load_pathops
from svg_to_pptx.text_outline import _load_harfbuzz
from svg_to_pptx.pptx_package import media
from pptx_to_svg.pic_to_svg import _convert_office_vector_to_png


class DeclaredCapabilitiesTests(unittest.TestCase):
    """Excluded optional libraries must not become implicit runtime dependencies."""

    def test_inventory_does_not_probe_excluded_distributions(self):
        def metadata(name):
            self.assertIn(name, DECLARED)
            raise importlib.metadata.PackageNotFoundError(name)
        with patch("tl_capabilities.importlib.metadata.version", side_effect=metadata):
            result = capability_status()
        self.assertEqual(result["optional_executables"], {})
        self.assertEqual(result["optional_python_distributions"], {})
        self.assertEqual(result["speaker_notes"], "retained")

    def test_boolean_and_outline_refuse_without_importing(self):
        with patch.object(builtins, "__import__", side_effect=AssertionError("unexpected import")):
            for load in (_load_pathops, _load_harfbuzz):
                with self.assertRaisesRegex(RuntimeError, "declared dependency set"):
                    load()

    def test_unverified_raster_does_not_write_output(self):
        self.assertIsNone(media.PNG_RENDERER)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            media.convert_svg_to_png(Path("missing.svg"), Path("must-not-exist.png"))
        self.assertFalse(Path("must-not-exist.png").exists())

    def test_office_vector_keeps_source_without_external_conversion(self):
        self.assertIsNone(_convert_office_vector_to_png("drawing.emf", b"source"))
