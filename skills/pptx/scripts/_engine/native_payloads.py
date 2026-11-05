# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Reject upstream external payload-store references in the standalone engine."""
from __future__ import annotations
from pathlib import Path
from xml.etree import ElementTree as ET


class NativePayloadError(ValueError):
    """Reject native metadata that requires an upstream project workspace."""


def hydrate_native_payload_refs(root: ET.Element, svg_path: Path) -> int:
    """Keep supported inline metadata; never read external payload stores."""
    reference_names = {
        "data-pptx-native-ref", "data-pptx-ref", "data-pptx-shape-style-ref",
        "data-pptx-custgeom-ref",
    }
    for element in root.iter():
        if reference_names.intersection(element.attrib):
            raise NativePayloadError("External native payload references are unsupported")
    return 0
