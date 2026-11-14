"""Guarded text-only revision writing without accepting existing revisions."""
from copy import deepcopy
from datetime import datetime
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

REVISION_TAGS = {qn('w:' + name) for name in (
    'ins', 'del', 'moveFrom', 'moveTo', 'pPrChange', 'rPrChange', 'sectPrChange')}


def _text(value, label, *, nonempty=False):
    """Require XML-safe single-line text; never interpret markup or whitespace."""
    if (not isinstance(value, str) or (nonempty and not value.strip()) or
            any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in (0xFFFE, 0xFFFF) for char in value)):
        raise ValueError('Invalid revision ' + label)
    return value


def _identifier(root):
    """Find a fresh main-story identifier without reusing existing range IDs."""
    used = set()
    for node in root.iter():
        value = node.get(qn('w:id'))
        if value is not None and re.fullmatch(r'[0-9]+', value):
            used.add(int(value))
    identifier = 0
    while identifier in used:
        identifier += 1
    return identifier


def tracked_replace(document, operation):
    """Replace one guarded ordinary run with deletion/insertion revision nodes.

    A blank replacement is a tracked deletion; an empty original is a tracked
    insertion. Neither views nor settings are globally changed. Existing complex
    content in the target paragraph is rejected rather than silently discarded.
    """
    required = {'type', 'paragraph_index', 'expected_text', 'run_index',
                'expected_run_text', 'text', 'author', 'date'}
    if not isinstance(operation, dict) or set(operation) != required:
        raise ValueError('Missing or unknown fields in tracked replacement')
    author = _text(operation['author'], 'author', nonempty=True)
    date = operation['date']
    if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', date):
        raise ValueError('Revision date must be UTC YYYY-MM-DDTHH:MM:SSZ')
    try:
        datetime.strptime(date, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError as exc:
        raise ValueError('Invalid revision date') from exc
    replacement = _text(operation['text'], 'text')
    position = operation['paragraph_index']
    paragraphs = document.paragraphs
    if type(position) is not int or not 0 <= position < len(paragraphs):
        raise ValueError('Revision paragraph index is outside the target range')
    paragraph = paragraphs[position]
    if paragraph.text != operation['expected_text'] or not isinstance(operation['expected_text'], str):
        raise ValueError('Revision paragraph no longer matches expected text')
    element = paragraph._p  # pylint: disable=protected-access
    if any(child.tag not in {qn('w:pPr'), qn('w:r')} for child in element):
        raise ValueError('Revision target contains range anchors, links or existing revisions')
    if any(node.tag in REVISION_TAGS or node.tag == qn('w:sectPr') for node in element.iter()):
        raise ValueError('Revision target contains section or revision properties')
    run_index = operation['run_index']
    runs = paragraph.runs
    if type(run_index) is not int or not 0 <= run_index < len(runs):
        raise ValueError('Revision run index is outside the target range')
    run = runs[run_index]
    original = run.text
    if not isinstance(operation['expected_run_text'], str) or original != operation['expected_run_text']:
        raise ValueError('Revision run no longer matches expected text')
    _text(original, 'original text')
    if original == replacement:
        raise ValueError('Tracked replacement must change the target text')
    source = run._r  # pylint: disable=protected-access
    if any(child.tag not in {qn('w:rPr'), qn('w:t')} for child in source) or len(source.findall(qn('w:rPr'))) > 1:
        raise ValueError('Tracked replacement requires an ordinary text-only run')
    identifier = _identifier(document.element)
    nodes = []
    for value, kind, text_kind in ((original, 'del', 'delText'), (replacement, 'ins', 't')):
        if not value:
            continue
        wrapper = OxmlElement('w:' + kind)
        wrapper.set(qn('w:id'), str(identifier))
        wrapper.set(qn('w:author'), author)
        wrapper.set(qn('w:date'), date)
        identifier += 1
        # A consecutive ID may already belong to another range; recompute using
        # the staged wrapper before selecting the next ID.
        revised_run = deepcopy(source)
        for child in list(revised_run):
            if child.tag != qn('w:rPr'):
                revised_run.remove(child)
        content = OxmlElement('w:' + text_kind)
        content.set(qn('xml:space'), 'preserve')
        content.text = value
        revised_run.append(content)
        wrapper.append(revised_run)
        nodes.append(wrapper)
        source.addprevious(wrapper)
        identifier = _identifier(document.element)
    element.remove(source)
    return nodes
