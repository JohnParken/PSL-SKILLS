# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Semantic shape text subset of the upstream authoring module."""
from __future__ import annotations
from xml.etree import ElementTree as ET
SVG_NS = "http://www.w3.org/2000/svg"

SEMANTIC_OBJECT_ATTRIBUTE = "data-pptx-semantic-object"


SEMANTIC_SHAPE_KIND = "shape"


def semantic_shape_text_component(
    shape: ET.Element,
    *,
    included_text_ids: set[int] | None = None,
) -> ET.Element | None:
    """Validate text compiled into one semantic shape, excluding native restores."""
    def included(element: ET.Element) -> bool:
        return (
            element.tag.replace(f'{{{SVG_NS}}}', '') == 'text'
            and (included_text_ids is None or id(element) in included_text_ids)
        )

    texts = [child for child in shape if included(child)]
    nested_texts = [child for child in shape.iter() if included(child)]
    if nested_texts != texts:
        raise ValueError('Semantic shape text must be one direct SVG text component')
    if not texts:
        return None
    if len(texts) != 1:
        raise ValueError('Semantic shape requires at most one paragraph-based text component')
    if shape.get('data-pptx-frame') is None:
        raise ValueError('Semantic shape text requires data-pptx-frame on its owner')
    return texts[0]
