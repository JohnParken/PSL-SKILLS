"""Independent content gate for creation, before publication.

Consumes the already normalized, validated input and an immutable image snapshot.
It does not import or re-run the generator. Candidate content is read from OOXML,
not python-docx's flattened paragraphs/cells. This is a content/topology check,
not a visual, font, pagination or field recalculation certification.
"""

from hashlib import sha256

from docx.oxml.ns import qn

from tl_package import OFFICE_REL


class CreationMismatch(ValueError):
    """Candidate disagrees with the creation contract; publication must stop."""


def _require(condition, location):
    if not condition:
        raise CreationMismatch('Creation content verification failed: ' + location)


def _compact(tokens):
    result = []
    for token in tokens:
        if token[0] == 'text' and not token[1]:
            continue
        if token[0] == 'text' and result and result[-1][0] == 'text':
            result[-1] = ('text', result[-1][1] + token[1])
        else:
            result.append(token)
    return result


def _rich(value):
    if value is None:
        return []
    if isinstance(value, str):
        return _compact([('text', value)])
    result = []
    for run in value:
        if 'field' in run:
            result.append(('field', run['field'], run.get('display', '')))
        elif 'tab' in run:
            result.append(('tab',))
        else:
            result.append(('text', run['text']))
    return _compact(result)


def _image_expected(image, images):
    path = image['path']
    _require(path in images and isinstance(images[path], bytes), 'missing image input snapshot')
    return ('image', sha256(images[path]).hexdigest(), image.get('alt', ''))


def _image_actual(element, package, source):
    blips = list(element.iter(qn('a:blip')))
    properties = list(element.iter(qn('wp:docPr')))
    _require(len(blips) == len(properties) == 1, 'unsupported image representation')
    relation = package.related(source, blips[0].get(qn('r:embed')))
    _require(relation and not relation['external'] and relation['type'] == OFFICE_REL + 'image', 'image relationship')
    _require(not blips[0].get(qn('r:link')), 'linked image')
    return ('image', sha256(package.read(relation['target'])).hexdigest(), properties[0].get('descr', ''))


def _paragraph(element, package, source):
    tokens = []
    field = {'active': False, 'instruction': '', 'display': '', 'separated': False}
    bookmarks = {}
    for child in element:
        if child.tag == qn('w:pPr'):
            continue
        if child.tag == qn('w:bookmarkStart'):
            identifier = child.get(qn('w:id'))
            _require(identifier not in bookmarks, 'duplicate bookmark')
            bookmarks[identifier] = child.get(qn('w:name'))
            tokens.append(('bookmark_start', bookmarks[identifier]))
            continue
        if child.tag == qn('w:bookmarkEnd'):
            identifier = child.get(qn('w:id'))
            _require(identifier in bookmarks, 'unmatched bookmark')
            tokens.append(('bookmark_end', bookmarks.pop(identifier)))
            continue
        _require(child.tag == qn('w:r'), 'unsupported paragraph child')
        for item in child:
            if item.tag == qn('w:rPr'):
                continue
            if item.tag == qn('w:fldChar'):
                kind = item.get(qn('w:fldCharType'))
                if kind == 'begin':
                    _require(not field['active'], 'nested field')
                    field = {'active': True, 'instruction': '', 'display': '', 'separated': False}
                elif kind == 'separate':
                    _require(field['active'] and not field['separated'], 'field separator')
                    field['separated'] = True
                elif kind == 'end':
                    _require(field['active'] and field['separated'], 'field end')
                    tokens.append(('field', field['instruction'].strip(), field['display']))
                    field = {'active': False, 'instruction': '', 'display': '', 'separated': False}
                else:
                    raise CreationMismatch('Creation content verification failed: field marker')
            elif item.tag == qn('w:instrText'):
                _require(field['active'] and not field['separated'], 'field instruction')
                field['instruction'] += item.text or ''
            elif item.tag == qn('w:t'):
                if field['active']:
                    _require(field['separated'], 'field result before separator')
                    field['display'] += item.text or ''
                else:
                    tokens.append(('text', item.text or ''))
            else:
                _require(not field['active'], 'unsupported field result')
                if item.tag == qn('w:tab'):
                    tokens.append(('tab',))
                elif item.tag == qn('w:br'):
                    tokens.append(('break', item.get(qn('w:type'), 'textWrapping')))
                elif item.tag == qn('w:drawing'):
                    tokens.append(_image_actual(item, package, source))
                else:
                    raise CreationMismatch('Creation content verification failed: unsupported run content')
    _require(not field['active'] and not bookmarks, 'unclosed field or bookmark')
    return _compact(tokens)


def _expected_table(block, images):
    rows = block['rows']
    spans = {(merge['row'], merge['col']): (merge['rowspan'], merge['colspan']) for merge in block.get('merges', [])}
    for row_index, row in enumerate(rows):
        for column, value in enumerate(row):
            if isinstance(value, dict) and (value.get('row_span', 1), value.get('col_span', 1)) != (1, 1):
                spans[row_index, column] = (value.get('row_span', 1), value.get('col_span', 1))
    occupied = {}
    for (row_index, column), (height, width) in spans.items():
        for offset in range(height):
            for step in range(width):
                occupied[row_index + offset, column + step] = (row_index, column)
    result = []
    for row_index, row in enumerate(rows):
        cells = []
        for column, value in enumerate(row):
            anchor = occupied.get((row_index, column), (row_index, column))
            if anchor[1] != column:
                continue
            height, width = spans.get(anchor, (1, 1))
            continuation = anchor[0] != row_index
            merge = 'continue' if continuation else 'restart' if height > 1 else None
            if continuation:
                tokens = []
            elif isinstance(value, dict):
                tokens = _rich(value.get('text'))
                if 'image' in value:
                    tokens.append(_image_expected(value['image'], images))
            else:
                tokens = _rich(value)
            cells.append((width, merge, [tokens]))
        result.append(cells)
    return (len(rows[0]), result)


def _actual_table(element, package, source):
    _require(all(child.tag in {qn('w:tblPr'), qn('w:tblGrid'), qn('w:tr')} for child in element), 'unsupported table child')
    grid = element.find(qn('w:tblGrid'))
    _require(grid is not None, 'missing table grid')
    result = []
    for row in element.findall(qn('w:tr')):
        _require(all(child.tag in {qn('w:trPr'), qn('w:tc')} for child in row), 'unsupported table row')
        cells = []
        for cell in row.findall(qn('w:tc')):
            _require(all(child.tag in {qn('w:tcPr'), qn('w:p')} for child in cell), 'unsupported cell content')
            span = cell.find('w:tcPr/w:gridSpan', namespaces=cell.nsmap)
            merge = cell.find('w:tcPr/w:vMerge', namespaces=cell.nsmap)
            width = int(span.get(qn('w:val'))) if span is not None else 1
            vertical = merge.get(qn('w:val'), 'continue') if merge is not None else None
            paragraphs = [_paragraph(item, package, source) for item in cell.findall(qn('w:p'))]
            cells.append((width, vertical, paragraphs))
        result.append(cells)
    return (len(grid.findall(qn('w:gridCol'))), result)


def _expected_body(spec, images):
    result = []
    for block in spec['blocks']:
        kind = block['type']
        if kind in {'title', 'subtitle', 'heading', 'paragraph'}:
            tokens = _rich(block.get('text', block.get('runs')))
            if 'bookmark' in block:
                tokens = [('bookmark_start', block['bookmark']), *tokens, ('bookmark_end', block['bookmark'])]
            result.append(('paragraph', tokens))
        elif kind == 'list':
            result.extend(('paragraph', _rich(text)) for text in block['items'])
        elif kind == 'table':
            if 'caption' in block:
                result.append(('paragraph', _rich(block['caption'])))
            result.append(('table', _expected_table(block, images)))
        elif kind == 'image':
            result.append(('paragraph', [_image_expected(block, images)]))
        elif kind == 'page_break':
            result.append(('paragraph', [('break', 'page')]))
        elif kind == 'section_break':
            result.append(('section_break', []))
        elif kind == 'toc':
            result.append(('paragraph', _rich(block.get('title', '目录'))))
            instruction = 'TOC \\o "1-' + str(block.get('levels', 3)) + '" \\h \\z \\u'
            result.append(('paragraph', [('field', instruction, '请在 Word 中更新目录')]))
        elif kind == 'reference':
            instruction = ('PAGEREF' if block.get('kind') == 'page' else 'REF') + ' ' + block['bookmark']
            result.append(('paragraph', _compact([('text', block.get('prefix', '')), ('field', instruction, ''), ('text', block.get('suffix', ''))])))
        else:
            raise CreationMismatch('Creation content verification failed: unsupported input block')
    return result


def _actual_body(package):
    body = package.xml(package.main).find(qn('w:body'))
    result, sections = [], []
    for child in body:
        if child.tag == qn('w:p'):
            tokens = _paragraph(child, package, package.main)
            section = child.find('w:pPr/w:sectPr', namespaces=child.nsmap)
            result.append(('section_break' if section is not None else 'paragraph', tokens))
            if section is not None:
                sections.append(section)
        elif child.tag == qn('w:tbl'):
            result.append(('table', _actual_table(child, package, package.main)))
        elif child.tag == qn('w:sectPr'):
            _require(child is body[-1], 'final section position')
            sections.append(child)
        else:
            raise CreationMismatch('Creation content verification failed: unsupported body content')
    return result, sections


def _header_expectations(spec):
    """Resolve contract inheritance, including dormant first/even variants."""
    states = {part: {'default': None, 'first': None, 'even': None} for part in ('header', 'footer')}
    follows = {part: spec.get('odd_even_headers', False) for part in states}
    rendered = {(part, variant): [] for part in states for variant in ('default', 'first', 'even')}
    modes = {'different_first_page': False, 'page_numbers': False, 'first_page_numbers': False}
    output = []
    for options in [spec] + [item for item in spec['blocks'] if item['type'] == 'section_break']:
        old_first = modes['different_first_page']
        modes.update({key: options[key] for key in modes if key in options})
        if not modes['different_first_page'] and 'different_first_page' in options and 'first_page_numbers' not in options:
            modes['first_page_numbers'] = False
        for part, values in states.items():
            variants = options.get(part + 's', {})
            if part in options:
                values['default'] = options[part]
            values.update(variants)
            if 'even' in variants:
                follows[part] = False
            if follows[part]:
                values['even'] = values['default']
            if modes['different_first_page'] and not old_first and 'first' not in variants:
                values['first'] = None
            for variant, value in values.items():
                active = variant == 'default' or variant == 'first' and modes['different_first_page'] or variant == 'even' and spec.get('odd_even_headers', False)
                if not active:
                    continue
                tokens = _rich(value)
                automatic = part == 'footer' and modes['first_page_numbers' if variant == 'first' else 'page_numbers']
                if automatic:
                    official = spec.get('theme', 'official') == 'official'
                    if official or any(token[0] in {'text', 'tab'} or token[0] == 'field' and token[2] for token in tokens):
                        tokens.append(('text', ' '))
                    tokens.append(('field', 'PAGE', '1' if official else ''))
                rendered[part, variant] = _compact(tokens)
        output.append(dict(rendered))
    return output


def _verify_headers(package, spec, sections):
    expected = _header_expectations(spec)
    _require(len(expected) == len(sections), 'section count')
    effective = {}
    used = set()
    for index, (section, wanted) in enumerate(zip(sections, expected)):
        for part in ('header', 'footer'):
            references = section.findall(qn('w:' + part + 'Reference'))
            seen = set()
            for reference in references:
                variant = reference.get(qn('w:type'))
                _require(variant in {'default', 'first', 'even'} and variant not in seen, 'header/footer variant')
                seen.add(variant)
                relation = package.related(package.main, reference.get(qn('r:id')))
                _require(relation and not relation['external'] and relation['type'] == OFFICE_REL + part, 'header/footer relationship')
                effective[part, variant] = relation['target']
            for variant in ('default', 'first', 'even'):
                target = effective.get((part, variant))
                actual = []
                if target:
                    used.add(target)
                    root = package.xml(target)
                    _require(root.tag == qn('w:hdr' if part == 'header' else 'w:ftr') and len(root) == 1 and root[0].tag == qn('w:p'), 'header/footer structure')
                    actual = _paragraph(root[0], package, target)
                _require(actual == wanted[part, variant], f'section[{index}].{part}.{variant}')
    return len(used)


def _enabled(element):
    return element is not None and element.get(qn('w:val'), 'true') not in {'false', '0', 'off'}


def _verify_header_modes(package, spec, sections):
    settings = [relation['target'] for relation in package.relations(package.main).values()
                if relation['type'] == OFFICE_REL + 'settings' and not relation['external']]
    _require(len(settings) == 1, 'settings relationship')
    odd_even = package.xml(settings[0]).find(qn('w:evenAndOddHeaders'))
    _require(_enabled(odd_even) == spec.get('odd_even_headers', False), 'odd/even header mode')
    first_page = False
    options = [spec] + [block for block in spec['blocks'] if block['type'] == 'section_break']
    for index, (section, option) in enumerate(zip(sections, options)):
        first_page = option.get('different_first_page', first_page)
        _require(_enabled(section.find(qn('w:titlePg'))) == first_page, f'section[{index}].first_page_mode')


def _verify_metadata(package, spec):
    values = spec.get('metadata', {})
    if not values:
        return
    core_type = 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties'
    core = [relation['target'] for relation in package.relations('').values() if relation['type'] == core_type and not relation['external']]
    _require(len(core) == 1, 'core metadata relationship')
    root = package.xml(core[0])
    dc_names = {'title': 'title', 'subject': 'subject', 'author': 'creator', 'comments': 'description', 'identifier': 'identifier', 'language': 'language'}
    cp_names = {'keywords': 'keywords', 'category': 'category', 'last_modified_by': 'lastModifiedBy'}
    for name, value in values.items():
        tag = '{http://purl.org/dc/elements/1.1/}' + dc_names[name] if name in dc_names else (
            '{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}' + cp_names[name])
        found = root.findall(tag)
        _require(len(found) <= 1 and (found[0].text or '' if found else '') == value, 'metadata.' + name)


def verify_created(package, normalized_spec, image_bytes):
    """Raise CreationMismatch before publication on any content disagreement.

    ``normalized_spec`` is a defensive snapshot taken before generation, after
    public input normalization. ``image_bytes`` maps every image input path to
    exact bytes snapshotted independently of generator image requests. No paths
    are opened here. The caller must preserve ordinary package validation too.
    """
    expected = _expected_body(normalized_spec, image_bytes)
    actual, sections = _actual_body(package)
    _require(len(expected) == len(actual), 'body block count')
    for index, (wanted, found) in enumerate(zip(expected, actual)):
        _require(wanted == found, f'body[{index}]')
    header_parts = _verify_headers(package, normalized_spec, sections)
    _verify_header_modes(package, normalized_spec, sections)
    _verify_metadata(package, normalized_spec)
    return {'status': 'verified', 'body_blocks': len(actual), 'sections': len(sections), 'header_footer_parts': header_parts,
            'checks': ['ordered_body', 'text_fields_bookmarks', 'table_cells_merges', 'image_bytes_alt', 'section_header_footer', 'header_modes', 'requested_metadata'],
            'not_checked': ['visual_layout', 'formatting_fidelity', 'numbering_labels', 'field_recalculation']}
