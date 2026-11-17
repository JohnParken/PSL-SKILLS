"""Read-only verification of one externally produced, addressed format change."""
import math
import re

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from tl_edit import (FORMAT_KEYS, addressed_cell_run, expect, fields, index,
                     ordinary_text_paragraphs, story_part, text)
from tl_format_guard import verify_format_change, verify_xml_preserved


def validate_format(value):
    """Validate request values without executing any formatting setter."""
    fields(value, (), FORMAT_KEYS)
    if not value:
        raise ValueError('A format request must not be empty')
    for key, item in value.items():
        if key in ('bold', 'italic', 'underline'):
            if type(item) is not bool:
                raise ValueError('Style flags require booleans')
        elif key == 'size_pt':
            if type(item) not in (int, float) or not math.isfinite(item) or not 1 <= item <= 400:
                raise ValueError('Size is outside the supported range')
        elif key == 'color':
            if not re.fullmatch('[0-9a-fA-F]{6}', text(item)):
                raise ValueError('Color requires six RGB digits')
        elif not text(item).strip():
            raise ValueError('Font name must be nonempty')


def check_format(original, candidate, specification):
    """Verify a single requested format change, never saving either package."""
    fields(specification, ('operations',))
    operations = specification['operations']
    if not isinstance(operations, list) or len(operations) != 1:
        raise ValueError('Format checking requires exactly one operation')
    operation = operations[0]
    base = ('type', 'paragraph_index', 'run_index', 'expected_text', 'expected_run_text', 'format')
    if not isinstance(operation, dict):
        raise ValueError('Format operation must be an object')
    kind = operation.get('type')
    if kind == 'format_run':
        fields(operation, base)
    elif kind == 'format_cell_run':
        fields(operation, base + ('table_index', 'row_index', 'cell_index'), ('expected_merge',))
    elif kind == 'format_story_run':
        fields(operation, base + ('part',))
    else:
        raise ValueError('Only addressed formatting operations can be checked')
    validate_format(operation['format'])
    if original.sha256 == candidate.sha256:
        raise ValueError('Original and candidate must contain different bytes')
    if original.names != candidate.names or original.main != candidate.main:
        raise ValueError('Package member sets must be identical')
    document = original.document()
    candidate.document()
    verify_xml_preserved(original.xml(original.main), document.element)
    target = operation.get('part', original.main)
    for name in original.names - {target}:
        if original.read(name) != candidate.read(name):
            raise ValueError('An unrelated package member changed')
    if kind == 'format_story_run':
        before = story_part(original, target)
        after = story_part(candidate, target)
        paragraph = Paragraph(index(before.findall(qn('w:p')), operation['paragraph_index']), None)
        expect(paragraph.text, operation['expected_text'])
        expect(index(paragraph.runs, operation['run_index']).text, operation['expected_run_text'])
    else:
        before, after = original.xml(original.main), candidate.xml(candidate.main)
        if kind == 'format_cell_run':
            addressed_cell_run(document, operation)
        else:
            paragraph = index(document.paragraphs, operation['paragraph_index'])
            ordinary_text_paragraphs([paragraph._p])  # pylint: disable=protected-access
            expect(paragraph.text, operation['expected_text'])
            expect(index(paragraph.runs, operation['run_index']).text, operation['expected_run_text'])
    verify_format_change(before, after, operation)
    return {'status': 'format_checked', 'verification': 'passed', 'operations': 1,
            'original_sha256': original.sha256, 'candidate_sha256': candidate.sha256,
            'checked': ['requested_run_properties', 'parsed_xml_preservation', 'other_part_bytes'],
            'not_checked': ['visual_layout', 'field_recalculation', 'numbering_labels',
                            'xml_lexical_representation', 'zip_metadata'],
            'read_only': True, 'scope': 'captured_input_bytes'}
