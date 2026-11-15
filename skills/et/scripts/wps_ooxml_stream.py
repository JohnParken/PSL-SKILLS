"""Checked worksheet row streaming with metadata retained for later passes.

The full bounded byte input is security-validated before any callback. This
reduces retained row XML, not the size of the caller's cell result or SST.
"""
import io
from xml.etree import ElementTree as ET

from wps_ooxml import _checked, split_tag
from wps_ooxml_text import MAIN_NAMESPACES


def worksheet_rows(data, consume, *, budget=None):
    """Validate fully before consuming rows; retain the metadata tree."""
    return prepare_worksheet(data, budget=budget)(consume)


def prepare_worksheet(data, *, budget=None):
    """Validate once and return a parser over the same immutable bytes."""
    data = _checked(data, budget=budget)
    return lambda consume: _consume_rows(data, consume)


def _consume_rows(data, consume):
    """Return the metadata tree after consuming direct sheetData children.

    Foreign wrappers and metadata remain intact, matching the existing direct
    child routing. Callbacks must not retain elements; processed row subtrees
    are removed and cleared. No result is exposed before whole-input checking.
    """
    stack = []
    root = None
    valid_root = False
    for event, element in ET.iterparse(io.BytesIO(data), events=("start", "end")):
        if event == "start":
            stack.append(element)
            if root is None:
                root = element
                namespace, local = split_tag(root.tag)
                valid_root = namespace in MAIN_NAMESPACES and local == "worksheet"
            continue
        if valid_root and len(stack) == 3:
            namespace, local = split_tag(stack[-2].tag)
            if namespace in MAIN_NAMESPACES and local == "sheetData":
                consume(element)
                stack[-2].remove(element)
                element.clear()
        stack.pop()
    return root
