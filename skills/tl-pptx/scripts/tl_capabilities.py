"""Read-only capability inventory; no imports, installs, network or credentials."""
from __future__ import annotations

import importlib.metadata
import platform
import shutil


def _distribution(name):
    try:
        return {'installed': True, 'version': importlib.metadata.version(name)}
    except importlib.metadata.PackageNotFoundError:
        return {'installed': False, 'version': None}


def capability_status():
    """Report prerequisites separately from execution or Office validation."""
    packages = {name: _distribution(name) for name in (
        'PyYAML', 'skia-pathops', 'uharfbuzz', 'edge-tts', 'aiohttp', 'numpy',
        'stable-ts', 'CairoSVG', 'svglib', 'reportlab', 'PyMuPDF',
    )}
    executables = {name: bool(shutil.which(name))
                   for name in ('ffmpeg', 'ffprobe', 'powershell', 'pwsh')}
    return {
        'optional_python_distributions': packages,
        'optional_executables': executables,
        'native_video_platform': platform.system(),
        'native_video_platform_supported': platform.system() == 'Windows',
        'powerpoint_installation_and_license': 'not_verified',
        'font_licenses_and_readability': 'operation_specific',
        'service_credentials_content_and_cost_authorization': 'operation_specific_not_inspected',
        'pdf_input_license': 'AGPL-3.0_or_commercial_approval_required',
        'optional_imports_and_functional_validation': 'not_run',
        'automatic_installation_or_model_download': False,
    }
