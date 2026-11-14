"""Edit addressed Word objects and retain unrelated package members verbatim."""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import math
from pathlib import PurePosixPath
import re

from docx.image.exceptions import InvalidImageStreamError, UnexpectedEndOfFileError, UnrecognizedImageError
from docx.image.image import Image
from docx.opc.oxml import serialize_part_xml
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.text.paragraph import Paragraph

from tl_package import OFFICE_REL, REL, TYPES, Package, parse, rels_member
from tl_revisions import tracked_replace

COMMENT_REF = qn('w:commentReference')
COMMENT_MARKERS = (COMMENT_REF, qn('w:commentRangeStart'), qn('w:commentRangeEnd'))
FORMAT_KEYS = {'font_east_asia', 'font_ascii', 'size_pt', 'bold', 'italic', 'underline', 'color'}


def fields(mapping, required, optional=()):
    """Require the exact documented keys for a specification object."""
    if not isinstance(mapping, dict) or not set(required) <= mapping.keys() or mapping.keys() - set(required) - set(optional):
        raise ValueError('Missing or unknown fields in edit specification')


def text(value, *, multiline=False):
    """Validate Word text before passing it to python-docx."""
    if not isinstance(value, str) or any(ord(char) < 32 and char not in ('\n\t' if multiline else '') for char in value):
        raise ValueError('Expected Word text with supported whitespace')
    return value


def index(items, position):
    """Select an explicit zero-based index without Python negative indexing."""
    if type(position) is not int or not 0 <= position < len(items):
        raise ValueError('Edit index is outside the supported target range')
    return items[position]


def expect(actual, expected):
    """Require an exact guard value at the selected target."""
    if not isinstance(expected, str) or actual != expected:
        raise ValueError('Edit target no longer matches its expected value')


def format_run(run, formatting):
    """Set only declared run properties without resetting inherited formatting."""
    fields(formatting, (), FORMAT_KEYS)
    if not formatting:
        raise ValueError('Run formatting must specify at least one property')
    for key, value in formatting.items():
        if key in ('bold', 'italic', 'underline'):
            if type(value) is not bool:
                raise ValueError('Run style flags must be booleans')
            setattr(run.font, key, value)
        elif key == 'color':
            if not re.fullmatch('[0-9a-fA-F]{6}', text(value)):
                raise ValueError('Run color must use six RGB digits')
            run.font.color.rgb = RGBColor.from_string(value)
        elif key == 'size_pt':
            if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 400:
                raise ValueError('Run size must be between 1 and 400 points')
            run.font.size = Pt(value)
            properties = run._r.get_or_add_rPr()  # pylint: disable=protected-access
            complex_size = properties.find(qn('w:szCs'))
            if complex_size is not None:
                complex_size.set(qn('w:val'), str(round(value * 2)))
        else:
            if not text(value).strip():
                raise ValueError('Font name must be nonempty')
            fonts = run._r.get_or_add_rPr().get_or_add_rFonts()  # pylint: disable=protected-access
            attributes = ('eastAsia',) if key == 'font_east_asia' else ('ascii', 'hAnsi')
            for attribute in attributes:
                fonts.set(qn('w:' + attribute), value)
                fonts.attrib.pop(qn('w:' + attribute + 'Theme'), None)


def plain_run(run):
    """Reject edits that would discard fields, pictures or annotation anchors."""
    element = run._r  # pylint: disable=protected-access
    if any(child.tag not in {qn('w:rPr'), qn('w:t'), qn('w:tab'), qn('w:br'), qn('w:cr')} or
           child.tag == qn('w:br') and child.get(qn('w:type'), 'textWrapping') != 'textWrapping'
           for child in element):
        raise ValueError('Target run contains unsupported non-text content')


def cell_run(cell, table):
    """Select the only simple run in an unmerged physical table cell."""
    props = cell.find(qn('w:tcPr'))
    if props is not None and (props.find(qn('w:vMerge')) is not None or
                              props.find(qn('w:gridSpan')) is not None):
        raise ValueError('Editing text in merged table cells is unsupported')
    paras = cell.findall(qn('w:p'))
    if len(paras) != 1 or len(cell) != 1 + (props is not None):
        raise ValueError('Table cell must contain one ordinary paragraph')
    paragraph = Paragraph(paras[0], table)
    if any(node.tag not in (qn('w:pPr'), qn('w:r')) for node in paras[0]) or len(paragraph.runs) != 1:
        raise ValueError('Table cell must contain one ordinary text run')
    return paragraph.runs[0]


def edit_row(document, operation):
    """Insert or delete a guarded, unmerged data row in a rectangular table."""
    inserting = operation['type'] == 'insert_row'
    required = ('type', 'table_index', 'row_index', 'expected_rows', 'expected_text')
    fields(operation, required + ('position', 'cells') if inserting else required)
    table = index(document.tables, operation['table_index'])
    rows = list(table.rows)
    row = index(rows, operation['row_index'])
    width = len(table.columns)
    if not width or type(operation['expected_rows']) is not int or len(rows) != operation['expected_rows']:
        raise ValueError('Table no longer matches its expected row count')
    header = qn('w:tblHeader')
    unsafe_row_props = (qn('w:gridBefore'), qn('w:gridAfter'), qn('w:ins'), qn('w:del'))
    all_runs = []
    for candidate in rows:
        element = candidate._tr  # pylint: disable=protected-access
        props = element.find(qn('w:trPr'))
        if len(element.tc_lst) != width or (props is not None and any(child.tag in unsafe_row_props for child in props)):
            raise ValueError('Row edits require a rectangular unmerged table')
        runs = [cell_run(cell, table) for cell in element.tc_lst]
        for run in runs:
            plain_run(run)
        all_runs.append(runs)
    element = row._tr  # pylint: disable=protected-access
    headers = [candidate._tr.find(f'./{qn("w:trPr")}/{header}') for candidate in rows]  # pylint: disable=protected-access
    repeated = [item is not None and item.get(qn('w:val'), '1') not in ('0', 'false', 'off') for item in headers]
    if repeated[operation['row_index']] or not inserting and repeated.count(False) < 2:
        raise ValueError('Cannot edit a repeating header or delete the last data row')
    if [run.text for run in all_runs[operation['row_index']]] != operation['expected_text']:
        raise ValueError('Table row no longer matches the expected cells')
    if not inserting:
        element.getparent().remove(element)
        return
    position, values = operation['position'], operation['cells']
    if position not in ('before', 'after') or not isinstance(values, list) or len(values) != width:
        raise ValueError('Invalid insertion position or row width')
    new_row = deepcopy(element)
    for cell, value in zip(new_row.tc_lst, values):
        cell_run(cell, table).text = text(value, multiline=True)
    (element.addprevious if position == 'before' else element.addnext)(new_row)


def replace_image(package, operation, read_image, replacements):
    """Replace a known binary part while preserving its shapes and relationships."""
    fields(operation, ('type', 'part', 'expected_sha256', 'path'), ('allow_shared',))
    name = text(operation['part'])
    allow_shared = operation.get('allow_shared', False)
    if type(allow_shared) is not bool:
        raise ValueError('allow_shared must be boolean')
    kind = package.types.get(name, '')
    if not kind.startswith('image/') or name in replacements:
        raise ValueError('Image must identify one unmodified internal image part')
    expect(sha256(package.read(name)).hexdigest(), operation['expected_sha256'])
    references = 0
    for source in package.names - {TYPES}:
        rel_ids = [rid for rid, relation in package.relations(source).items()
                   if relation['target'] == name and not relation['external']]
        if rel_ids:
            references += sum(value in rel_ids for node in package.xml(source).iter() for key, value in node.attrib.items()
                              if key.startswith('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'))
    if references < 1 or (references > 1 and not allow_shared):
        raise ValueError('Image must have one reference or explicit approval to replace a shared part')
    blob = read_image(text(operation['path']))
    try:
        image = Image.from_file(BytesIO(blob))
    except (InvalidImageStreamError, UnexpectedEndOfFileError, UnrecognizedImageError, ValueError) as exc:
        raise ValueError('Unsupported replacement image') from exc
    if image.content_type != kind:
        raise ValueError('Replacement image must have the original media type')
    replacements[name] = blob


def merge_runs(paragraph, operation):
    """Merge an explicitly selected contiguous group of identical plain runs."""
    fields(operation, ('type', 'paragraph_index', 'expected_text', 'start_run', 'expected_runs'))
    expected = operation['expected_runs']
    if not isinstance(expected, list) or len(expected) < 2 or any(not isinstance(item, str) for item in expected):
        raise ValueError('Merge requires at least two expected run texts')
    start = operation['start_run']
    runs = paragraph.runs
    index(runs, start)
    chosen = runs[start:start + len(expected)]
    if len(chosen) != len(expected) or [run.text for run in chosen] != expected:
        raise ValueError('Merge targets no longer match expected run texts')
    element = paragraph._p  # pylint: disable=protected-access
    if any(child.tag not in (qn('w:pPr'), qn('w:r')) for child in element):
        raise ValueError('Merge cannot cross fields, ranges, links or revision anchors')
    if any(node.tag in (qn('w:sectPr'), qn('w:pPrChange'), qn('w:rPrChange')) for node in element.iter()):
        raise ValueError('Merge cannot alter paragraphs with section or revision properties')
    first = chosen[0]._r  # pylint: disable=protected-access
    first_props = first.find(qn('w:rPr'))
    signature = None if first_props is None else serialize_part_xml(first_props)
    for run in chosen:
        node = run._r  # pylint: disable=protected-access
        if dict(node.attrib) != dict(first.attrib) or not node.findall(qn('w:t')):
            raise ValueError('Merge requires identical run attributes and ordinary text')
        if any(child.tag not in (qn('w:rPr'), qn('w:t')) for child in node):
            raise ValueError('Merge supports text-only runs without fields or drawings')
        if len(node.findall(qn('w:rPr'))) > 1:
            raise ValueError('Merge rejects duplicate run properties')
        props = node.find(qn('w:rPr'))
        if (None if props is None else serialize_part_xml(props)) != signature:
            raise ValueError('Merge requires identical explicit formatting')
    # Keep the original text elements, including xml:space, rather than reinterpreting whitespace.
    for run in chosen[1:]:
        node = run._r  # pylint: disable=protected-access
        for child in list(node.findall(qn('w:t'))):
            first.append(child)
        element.remove(node)


def edit_body(document, operation):
    """Apply an exactly anchored edit through python-docx objects."""
    kind = operation['type']
    target_keys = ('type', 'paragraph_index', 'expected_text')
    if kind in ('insert_row', 'delete_row'):
        edit_row(document, operation)
        return
    if kind == 'replace_cell_text':
        fields(operation, ('type', 'table_index', 'row_index', 'cell_index', 'expected_text', 'text'))
        table = index(document.tables, operation['table_index'])
        row = index(table.rows, operation['row_index'])
        run = cell_run(index(row._tr.tc_lst, operation['cell_index']), table)  # pylint: disable=protected-access
        expect(run.text, operation['expected_text'])
        plain_run(run)
        run.text = text(operation['text'], multiline=True)
        return
    paragraph = index(document.paragraphs, operation.get('paragraph_index'))
    expect(paragraph.text, operation.get('expected_text'))
    if kind == 'merge_runs':
        merge_runs(paragraph, operation)
        return
    if kind == 'insert_paragraph':
        fields(operation, target_keys + ('text',), ('style', 'position'))
        position = operation.get('position', 'before')
        if position not in ('before', 'after'):
            raise ValueError('Paragraph insertion position must be before or after')
        style = operation.get('style')
        if style is not None:
            style = text(style)
        inserted = paragraph.insert_paragraph_before(text(operation['text'], multiline=True), style)
        if position == 'after':
            paragraph._p.addnext(inserted._p)  # pylint: disable=protected-access
        return
    if kind not in ('format_run', 'replace_run_text', 'add_comment'):
        raise ValueError('Unknown edit operation')
    run_keys = target_keys + ('run_index', 'expected_run_text')
    run = index(paragraph.runs, operation.get('run_index'))
    expect(run.text, operation.get('expected_run_text'))
    if kind == 'format_run':
        fields(operation, run_keys + ('format',))
        format_run(run, operation['format'])
    elif kind == 'replace_run_text':
        fields(operation, run_keys + ('text',))
        plain_run(run)
        run.text = text(operation['text'], multiline=True)
    else:
        fields(operation, run_keys + ('text', 'author'), ('initials',))
        if not run.text:
            raise ValueError('A comment requires a nonempty target run')
        document.add_comment(run, text(operation['text'], multiline=True), text(operation['author']), text(operation.get('initials', '')))


def comments_member(package):
    """Locate a single classic comment part and protect modern threads."""
    relations = package.relations(package.main)
    if any('comments' in relation['type'].rsplit('/', 1)[-1].lower() and relation['type'] != OFFICE_REL + 'comments'
           for relation in relations.values()):
        raise ValueError('Editing threaded comments is unsupported')
    comments = [value['target'] for value in relations.values() if value['type'] == OFFICE_REL + 'comments' and not value['external']]
    if len(comments) > 1:
        raise ValueError('Multiple classic comments parts are unsupported')
    return comments[0] if comments else None


def change_comment(root, main, operation):
    """Edit simple classic comments or remove their exact classic anchors."""
    required = ('type', 'comment_id', 'expected_text')
    delete = operation['type'] == 'delete_comment'
    fields(operation, required if delete else required + ('text',))
    identifier = operation['comment_id']
    if type(identifier) is not int or identifier < 0 or root is None:
        raise ValueError('Expected an existing nonnegative classic comment ID')
    matching = [comment for comment in root.findall(qn('w:comment')) if comment.get(qn('w:id')) == str(identifier)]
    comment = index(matching, 0)
    if len(matching) != 1:
        raise ValueError('Duplicate comment identifiers')
    para = comment.find(qn('w:p'))
    if len(comment) != 1 or para is None or any(child.tag not in (qn('w:pPr'), qn('w:r')) for child in para):
        raise ValueError('Target comment must contain one ordinary text run')
    all_runs = Paragraph(para, None).runs
    marker = qn('w:annotationRef')
    candidates = []
    for run in all_runs:
        element = run._r  # pylint: disable=protected-access
        if element.find(marker) is None:
            candidates.append(run)
        elif any(child.tag not in (marker, qn('w:rPr')) for child in element):
            raise ValueError('Unsupported comment annotation run')
    if len(candidates) != 1:
        raise ValueError('Target comment must contain one ordinary text run')
    word_run = candidates[0]
    plain_run(word_run)
    expect(word_run.text, operation['expected_text'])
    if not delete:
        word_run.text = text(operation['text'], multiline=True)
        return
    anchors = [node for node in main.iter(*COMMENT_MARKERS) if node.get(qn('w:id')) == str(identifier)]
    if len(anchors) != 3 or {node.tag for node in anchors} != set(COMMENT_MARKERS):
        raise ValueError('Classic comment does not have one complete body anchor')
    for node in anchors:
        parent = node.getparent()
        parent.remove(node)
        if node.tag == COMMENT_REF and parent.tag == qn('w:r') and all(child.tag == qn('w:rPr') for child in parent):
            parent.getparent().remove(parent)
    root.remove(comment)


def merge_new_comment_part(package, doc, replacements):
    """Add only the new part, content-type override and relationship."""
    output = BytesIO()
    doc.save(output)
    saved = Package(output.getvalue())
    generated = comments_member(saved)
    if not generated:
        raise ValueError('New comments part could not be located')
    name = str(PurePosixPath(package.main).with_name('comments.xml'))
    if name in package.names:
        raise ValueError('New classic comments part conflicts with an existing member')
    replacements[name] = saved.read(generated)
    types = deepcopy(package.xml(TYPES))
    entry = deepcopy(next(item for item in saved.xml(TYPES) if item.get('PartName') == '/' + generated))
    entry.set('PartName', '/' + name)
    types.append(entry)
    rels_name = rels_member(package.main)
    relations = (deepcopy(package.xml(rels_name)) if rels_name in package.names else
                 parse(f'<Relationships xmlns="{REL[1:-1]}"/>'.encode()))
    relation = deepcopy(next(item for item in saved.xml(rels_member(saved.main)) if item.get('Type') == OFFICE_REL + 'comments'))
    if relation.get('Id') in package.relations(package.main):
        raise ValueError('New classic comments relationship conflicts with an existing ID')
    relation.set('Target', 'comments.xml')
    relations.append(relation)
    replacements[TYPES] = serialize_part_xml(types)
    replacements[rels_name] = serialize_part_xml(relations)


def edit_document(package, spec, read_image):
    """Apply all requested operations in memory and validate the resulting DOCX."""
    fields(spec, ('operations',))
    operations = spec['operations']
    if not isinstance(operations, list) or not 0 < len(operations) <= 1000:
        raise ValueError('Expected 1–1000 edit operations')
    doc = package.document()
    replacements = {}
    kinds = []
    for operation in operations:
        if not isinstance(operation, dict) or not isinstance(operation.get('type'), str):
            raise ValueError('Each edit operation must have a type')
        kinds.append(operation['type'])
    comment_kinds = {'add_comment', 'update_comment', 'delete_comment'} & set(kinds)
    if 'add_comment' in comment_kinds and len(comment_kinds) > 1:
        raise ValueError('Submit additions and updates/deletions of comments separately')
    comment_name = comments_member(package) if comment_kinds else None
    comment_xml = deepcopy(package.xml(comment_name)) if comment_name else None
    for operation in operations:
        kind = operation['type']
        if kind == 'tracked_replace_run':
            tracked_replace(doc, operation)
        elif kind == 'replace_image':
            replace_image(package, operation, read_image, replacements)
        elif kind in ('update_comment', 'delete_comment'):
            change_comment(comment_xml, doc.element, operation)
        else:
            edit_body(doc, operation)
    if set(kinds) - {'replace_image', 'update_comment'}:
        replacements[package.main] = doc.part.blob
    if 'add_comment' in comment_kinds:
        if comment_name:
            replacements[comment_name] = doc.part._comments_part.blob  # pylint: disable=protected-access
        else:
            merge_new_comment_part(package, doc, replacements)
    elif comment_xml is not None:
        replacements[comment_name] = serialize_part_xml(comment_xml)
    candidate = package.overlay(replacements)
    verified = Package(candidate)
    verified.document()
    for name in verified.names | package.names:
        wanted = replacements[name] if name in replacements else package.read(name)
        if verified.read(name) != wanted:
            raise ValueError('Edited document failed the part preservation check')
    return candidate, {'operations': len(operations), 'changed_parts': sorted(
        name for name, data in replacements.items() if name not in package.names or data != package.read(name)
    )}
