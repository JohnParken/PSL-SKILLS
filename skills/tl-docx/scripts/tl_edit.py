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
from tl_format_guard import verify_format_change, verify_cell_text_change, verify_xml_preserved

COMMENT_REF = qn('w:commentReference')
COMMENT_MARKERS = (COMMENT_REF, qn('w:commentRangeStart'), qn('w:commentRangeEnd'))
FORMAT_KEYS = {'font_east_asia', 'font_ascii', 'size_pt', 'bold', 'italic', 'underline', 'color'}
CELL_RUN_KINDS = {'format_cell_run', 'replace_cell_run_text'}
STORY_RUN_KINDS = {'format_story_run', 'replace_story_run_text'}
GUARDED_KINDS = {'format_run'} | CELL_RUN_KINDS


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
            if key == 'underline':
                properties = run._r.get_or_add_rPr()  # pylint: disable=protected-access
                properties.get_or_add_u().set(qn('w:val'), 'single' if value else 'none')
            else:
                setattr(run.font, key, value)
        elif key == 'color':
            if not re.fullmatch('[0-9a-fA-F]{6}', text(value)):
                raise ValueError('Run color must use six RGB digits')
            properties = run._r.get_or_add_rPr()  # pylint: disable=protected-access
            color = properties.get_or_add_color()
            color.set(qn('w:val'), str(RGBColor.from_string(value)))
            for attribute in ('themeColor', 'themeTint', 'themeShade'):
                color.attrib.pop(qn('w:' + attribute), None)
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



def ordinary_text_paragraphs(paragraphs):
    """Reject destructive run editing around fields, ranges, shapes and revisions."""
    forbidden = {qn('w:sectPr'), qn('w:pPrChange'), qn('w:rPrChange'), qn('w:ins'), qn('w:del'),
                 qn('w:moveFrom'), qn('w:moveTo'), qn('w:numberingChange')}
    for element in paragraphs:
        if (len(element.findall(qn('w:pPr'))) > 1 or
                any(child.tag not in {qn('w:pPr'), qn('w:r')} for child in element) or
                any(node.tag in forbidden for node in element.iter())):
            raise ValueError('Run edits require ordinary paragraphs without fields, ranges or revisions')
        for run in Paragraph(element, None).runs:
            plain_run(run)
            if len(run._r.findall(qn('w:rPr'))) > 1:  # pylint: disable=protected-access
                raise ValueError('Run edits reject duplicate run properties')


def merge_layout(table):
    """Prove a canonical rectangular physical grid without normalizing any cells."""
    grids = table.findall(qn('w:tblGrid'))
    if (len(grids) != 1 or len(table.findall(qn('w:tblPr'))) > 1 or
            len(grids[0]) == 0 or any(node.tag != qn('w:gridCol') for node in grids[0]) or
            any(node.tag not in {qn('w:tblPr'), qn('w:tblGrid'), qn('w:tr')} for node in table)):
        raise ValueError('Merged-origin edits require one explicit ordinary table grid')
    width = len(grids[0])
    layout = []
    unsafe = {qn('w:gridBefore'), qn('w:gridAfter'), qn('w:ins'), qn('w:del'), qn('w:trPrChange')}
    for row in table.findall(qn('w:tr')):
        row_props = row.findall(qn('w:trPr'))
        if (len(row_props) > 1 or any(node.tag in unsafe for prop in row_props for node in prop.iter()) or
                any(node.tag not in {qn('w:trPr'), qn('w:tc')} for node in row)):
            raise ValueError('Merged-origin edits reject omitted-grid cells and ambiguous or revised rows')
        cells = []
        column = 0
        for cell in row.findall(qn('w:tc')):
            properties = cell.findall(qn('w:tcPr'))
            if len(properties) > 1:
                raise ValueError('Merged-origin edits reject duplicate cell properties')
            props = properties[0] if properties else None
            forbidden = {qn('w:hMerge'), qn('w:tcPrChange'), qn('w:cellIns'), qn('w:cellDel'), qn('w:cellMerge')}
            if props is not None and any(node.tag in forbidden for node in props.iter()):
                raise ValueError('Legacy horizontal merges and revised cell geometry are unsupported')
            spans = props.findall(qn('w:gridSpan')) if props is not None else []
            merges = props.findall(qn('w:vMerge')) if props is not None else []
            if len(spans) > 1 or len(merges) > 1:
                raise ValueError('Merged-origin edits reject duplicate merge properties')
            span = 1
            if spans:
                value = spans[0].get(qn('w:val'), '')
                if not re.fullmatch('[1-9][0-9]*', value) or len(spans[0]) or set(spans[0].attrib) != {qn('w:val')}:
                    raise ValueError('Merged-origin edits require an explicit positive gridSpan')
                span = int(value)
            merge = None
            if merges:
                merge = merges[0].get(qn('w:val'), 'continue')
                if merge not in ('restart', 'continue') or len(merges[0]) or set(merges[0].attrib) - {qn('w:val')}:
                    raise ValueError('Merged-origin edits require an unambiguous vertical merge marker')
            if column + span > width:
                raise ValueError('Merged cell exceeds the declared table grid')
            cells.append((cell, column, span, merge))
            column += span
        if column != width:
            raise ValueError('Physical rows must exactly cover the declared grid')
        layout.append(cells)
    return layout


def merged_cell_origin(table, row_index, cell_index, expected):
    """Require exact declared geometry and a true origin, never a continuation alias."""
    fields(expected, ('grid_column', 'colspan', 'rowspan'))
    if any(type(expected[key]) is not int or expected[key] < (0 if key == 'grid_column' else 1) for key in expected):
        raise ValueError('Expected merge geometry must use nonnegative column and positive integer spans')
    layout = merge_layout(table)
    selected = index(index(layout, row_index), cell_index)
    active = {}
    groups = {}
    for cells in layout:
        following = {}
        for cell, column, span, merge in cells:
            if merge == 'continue':
                prior = active.get(column)
                if prior is None or prior[0] != span:
                    raise ValueError('Vertical continuation must match the preceding origin width and column')
                origin = prior[1]
                groups[origin].append(cell)
                following[column] = prior
            elif merge == 'restart':
                groups[cell] = [cell]
                following[column] = (span, cell)
        active = following
    cell, column, span, merge = selected
    if merge == 'continue':
        raise ValueError('Merged-origin edits cannot target a continuation cell')
    group = groups.get(cell, [cell])
    actual = {'grid_column': column, 'colspan': span, 'rowspan': len(group)}
    if actual != expected or (span == 1 and len(group) == 1):
        raise ValueError('Target is not a merged origin matching expected_merge')
    for continuation in group[1:]:
        paragraphs = continuation.findall(qn('w:p'))
        if len(paragraphs) != 1 or any(node.tag not in {qn('w:tcPr'), qn('w:p')} for node in continuation):
            raise ValueError('Merged-origin edits require simple empty continuation cells')
        ordinary_text_paragraphs(paragraphs)
        if Paragraph(paragraphs[0], None).text or any(node.tag == qn('w:numPr') for node in paragraphs[0].iter()):
            raise ValueError('Merged-origin edits refuse hidden continuation content')


def addressed_cell_run(document, operation):
    """Locate a physical cell run; merged origins require explicit proven geometry."""
    table = index(document.tables, operation['table_index'])
    row = index(table._tbl.findall(qn('w:tr')), operation['row_index'])  # pylint: disable=protected-access
    revision_tags = {qn('w:ins'), qn('w:del'), qn('w:trPrChange'), qn('w:tblPrChange'), qn('w:tblGridChange')}
    table_element = table._tbl  # pylint: disable=protected-access
    properties = table_element.findall(qn('w:tblPr')) + table_element.findall(qn('w:tblGrid')) + row.findall(qn('w:trPr'))
    if any(node.tag in revision_tags for prop in properties for node in prop.iter()):
        raise ValueError('Cell run edits reject table and target-row revision properties')
    cell = index(row.findall(qn('w:tc')), operation['cell_index'])
    props = cell.findall(qn('w:tcPr'))
    if len(props) > 1 or any(node.tag in {qn('w:hMerge'), qn('w:tcPrChange'), qn('w:cellIns'), qn('w:cellDel'), qn('w:cellMerge')}
                            for prop in props for node in prop.iter()):
        raise ValueError('Cell run edits reject legacy horizontal merges and cell revision properties')
    if 'expected_merge' in operation:
        merged_cell_origin(table_element, operation['row_index'], operation['cell_index'], operation['expected_merge'])
    elif any(node.tag in {qn('w:gridSpan'), qn('w:vMerge')} for prop in props for node in prop.iter()):
        raise ValueError('Merged cell run edits require explicit expected_merge geometry')
    if any(child.tag not in {qn('w:tcPr'), qn('w:p')} for child in cell):
        raise ValueError('Cell run edits reject nested tables and non-paragraph content')
    paragraphs = cell.findall(qn('w:p'))
    ordinary_text_paragraphs(paragraphs)
    paragraph = Paragraph(index(paragraphs, operation['paragraph_index']), table)
    expect(paragraph.text, operation['expected_text'])
    run = index(paragraph.runs, operation['run_index'])
    expect(run.text, operation['expected_run_text'])
    return run


def edit_cell_run(document, operation):
    """Edit one explicitly anchored run, preserving the old whole-cell contract."""
    formatting = operation['type'] == 'format_cell_run'
    required = ('type', 'table_index', 'row_index', 'cell_index', 'paragraph_index',
                'expected_text', 'run_index', 'expected_run_text', 'format' if formatting else 'text')
    fields(operation, required, ('expected_merge',))
    run = addressed_cell_run(document, operation)
    if formatting:
        format_run(run, operation['format'])
    else:
        run.text = text(operation['text'], multiline=True)



def story_part(package, name):
    """Select one explicit, unshared default header/footer in a single section."""
    name = text(name)
    kinds = {f'application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml': kind
             for kind in ('header', 'footer')}
    kind = kinds.get(package.types.get(name))
    if kind is None or name not in package.names:
        raise ValueError('Story edits require an existing header or footer part')
    root = package.xml(name)
    if root.tag != qn('w:hdr' if kind == 'header' else 'w:ftr'):
        raise ValueError('Story part root does not match its declared content type')
    main = package.xml(package.main)
    body = main.find(qn('w:body'))
    sections = list(main.iter(qn('w:sectPr')))
    if (len(sections) != 1 or sections[0].getparent() is not body or body[-1] is not sections[0] or
            any(node.tag == qn('w:sectPrChange') for node in sections[0].iter())):
        raise ValueError('Story edits require one explicit final section without section revisions')
    references = list(main.iter(qn('w:headerReference'), qn('w:footerReference')))
    selected = []
    variants = set()
    for reference in references:
        reference_kind = 'header' if reference.tag == qn('w:headerReference') else 'footer'
        variant = reference.get(qn('w:type'))
        key = reference_kind, variant
        rid = reference.get(qn('r:id'))
        relation = package.related(package.main, rid)
        if (reference.getparent() is not sections[0] or variant not in ('default', 'first', 'even') or key in variants or
                relation is None or relation['external'] or relation['type'] != OFFICE_REL + reference_kind):
            raise ValueError('Story edits reject ambiguous, external or misplaced section references')
        variants.add(key)
        if relation['target'] == name:
            selected.append((rid, reference_kind, variant))
    if len(selected) != 1 or selected[0][1:] != (kind, 'default'):
        raise ValueError('Story edits require one explicit default reference; sharing and inheritance are unsupported')
    rid = selected[0][0]
    relationship_ns = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    uses = sum(value == rid for node in main.iter() for key, value in node.attrib.items() if key.startswith(relationship_ns))
    incoming = [(source, identifier) for source in package.names | {''}
                for identifier, relation in package.relations(source).items()
                if not relation['external'] and relation['target'] == name]
    if uses != 1 or incoming != [(package.main, rid)]:
        raise ValueError('Shared or multiply referenced header/footer parts cannot be edited')
    if len(root) == 0 or any(node.tag != qn('w:p') for node in root):
        raise ValueError('Story edits require a pure-text part without tables or content controls')
    ordinary_text_paragraphs(list(root))
    return root


def edit_story_run(root, operation):
    """Apply one text or format edit to a prevalidated ordinary header/footer."""
    formatting = operation['type'] == 'format_story_run'
    fields(operation, ('type', 'part', 'paragraph_index', 'expected_text', 'run_index',
                       'expected_run_text', 'format' if formatting else 'text'))
    paragraph = Paragraph(index(root.findall(qn('w:p')), operation['paragraph_index']), None)
    expect(paragraph.text, operation['expected_text'])
    run = index(paragraph.runs, operation['run_index'])
    expect(run.text, operation['expected_run_text'])
    if formatting:
        format_run(run, operation['format'])
    else:
        run.text = text(operation['text'], multiline=True)


def edit_stories(package, operations):
    """Keep story-only batches separate from operations that can change references."""
    roots = {}
    for operation in operations:
        # Validate fields before accepting any part locator from a request.
        formatting = operation['type'] == 'format_story_run'
        fields(operation, ('type', 'part', 'paragraph_index', 'expected_text', 'run_index',
                           'expected_run_text', 'format' if formatting else 'text'))
        name = text(operation['part'])
        if name not in roots:
            roots[name] = deepcopy(story_part(package, name))
            verify_xml_preserved(package.xml(name), roots[name])
        before, request = deepcopy(roots[name]), deepcopy(operation)
        edit_story_run(roots[name], operation)
        verifier = verify_format_change if formatting else verify_cell_text_change
        verifier(before, roots[name], request)
    approved = {name: deepcopy(root) for name, root in roots.items()}
    replacements = {name: serialize_part_xml(root) for name, root in roots.items()}
    candidate = package.overlay(replacements)
    verified = Package(candidate)
    verified.document()
    for name, root in approved.items():
        verify_xml_preserved(root, verified.xml(name))
    if verified.names != package.names:
        raise ValueError('Story editing cannot add or remove package members')
    for name in package.names:
        wanted = replacements[name] if name in replacements else package.read(name)
        if verified.read(name) != wanted:
            raise ValueError('Story edit failed the part preservation check')
    return candidate, {'operations': len(operations), 'changed_parts': sorted(
        name for name, data in replacements.items() if data != package.read(name))}


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
    if kind in CELL_RUN_KINDS:
        edit_cell_run(document, operation)
        return
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
    if STORY_RUN_KINDS.intersection(kinds):
        if set(kinds) - STORY_RUN_KINDS:
            raise ValueError('Header/footer run edits must be submitted as a separate batch')
        return edit_stories(package, operations)
    if GUARDED_KINDS.intersection(kinds):
        verify_xml_preserved(package.xml(package.main), doc.element)
    comment_kinds = {'add_comment', 'update_comment', 'delete_comment'} & set(kinds)
    if 'add_comment' in comment_kinds and len(comment_kinds) > 1:
        raise ValueError('Submit additions and updates/deletions of comments separately')
    comment_name = comments_member(package) if comment_kinds else None
    comment_xml = deepcopy(package.xml(comment_name)) if comment_name else None
    for operation in operations:
        kind = operation['type']
        guard = (deepcopy(doc.element), deepcopy(operation)) if kind in GUARDED_KINDS else None
        if kind == 'tracked_replace_run':
            tracked_replace(doc, operation)
        elif kind == 'replace_image':
            replace_image(package, operation, read_image, replacements)
        elif kind in ('update_comment', 'delete_comment'):
            change_comment(comment_xml, doc.element, operation)
        else:
            edit_body(doc, operation)
        if guard is not None:
            verifier = verify_cell_text_change if kind == 'replace_cell_run_text' else verify_format_change
            verifier(guard[0], doc.element, guard[1])
    approved_main = deepcopy(doc.element) if GUARDED_KINDS.intersection(kinds) else None
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
    if GUARDED_KINDS.intersection(kinds):
        verify_xml_preserved(approved_main, verified.xml(verified.main))
    for name in verified.names | package.names:
        wanted = replacements[name] if name in replacements else package.read(name)
        if verified.read(name) != wanted:
            raise ValueError('Edited document failed the part preservation check')
    return candidate, {'operations': len(operations), 'changed_parts': sorted(
        name for name, data in replacements.items() if name not in package.names or data != package.read(name)
    )}
