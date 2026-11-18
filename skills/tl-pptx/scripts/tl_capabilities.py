"""Read-only declared capability inventory, without probing excluded components."""
from __future__ import annotations

import importlib.metadata

DECLARED = ("python-pptx", "Pillow", "XlsxWriter", "typing-extensions",
            "lxml", "PyYAML", "PyMuPDF")


def _distribution(name):
    """Read installed metadata only; this is not functional verification."""
    try:
        return {"installed": True, "version": importlib.metadata.version(name)}
    except importlib.metadata.PackageNotFoundError:
        return {"installed": False, "version": None}


def capability_status():
    """Do not import, discover or activate undeclared optional libraries."""
    return {
        "declared_python_distributions": {name: _distribution(name) for name in DECLARED},
        "optional_python_distributions": {},
        "optional_executables": {},
        "shape_boolean": "limited_axis_aligned_solid_rectangles",
        "text_outline": "explicit_static_font_simple_ascii_basic_cjk_paths",
        "svg_png_compatibility": "unavailable_no_equivalent_renderer_verified",
        "office_vector_preview": "unavailable_original_vector_preserved",
        "narration_subtitles_video": "removed",
        "speaker_notes": "retained",
        "pdf_input_license": "AGPL-3.0_or_commercial_approval_required",
        "pdf_binary_dependency": "PyMuPDFb==1.24.1_for_split_PyMuPDF_1.24.2_wheels",
        "optional_imports_and_functional_validation": "not_run",
        "automatic_installation_or_model_download": False,
    }
